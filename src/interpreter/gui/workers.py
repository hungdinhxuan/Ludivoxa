"""Background workers for OCR and translation."""

import hashlib
import re
import threading
import time
from concurrent.futures import CancelledError

import cv2
import numpy as np
from PySide6.QtCore import QObject, Signal

from .. import log
from ..config import Config, OutputMode, OverlayMode, character_name
from ..languages import contains_japanese, contains_script  # noqa: F401  (contains_japanese kept for callers)
from ..ocr import OCREngine, OCRResult, create_ocr, group_ocr_rows
from ..speech import SpeechStream, voice_details
from ..translate import TranslationEngine, create_translator

logger = log.get_logger()

# After this many consecutive runtime failures an engine is marked as failed
# (status "Error" + Fix Models) instead of retrying on every frame forever.
MAX_CONSECUTIVE_TRANSLATION_FAILURES = 3
MAX_CONSECUTIVE_OCR_FAILURES = 3
SPEECH_SETTLE_SECONDS = 0.35
SPEECH_REVEAL_SETTLE_SECONDS = 0.8
SPEECH_CURSOR_FALLBACK_SECONDS = 1.2


class FrameBuffer:
    """Thread-safe 'latest frame' buffer with signaling."""

    def __init__(self):
        self._frame = None
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._closed = False

    def put(self, frame):
        """Store new frame and signal worker."""
        with self._condition:
            self._frame = frame
            self._condition.notify()

    def get(self, timeout=None):
        """Wait for and retrieve frame. Returns None on timeout or close."""
        with self._condition:
            if self._condition.wait_for(lambda: self._frame is not None or self._closed, timeout):
                if self._closed:
                    return None
                frame = self._frame
                self._frame = None
                return frame
            return None

    def close(self):
        """Signal worker to stop waiting."""
        with self._condition:
            self._closed = True
            self._condition.notify()

    def clear(self):
        """Discard a frame that has not been picked up by the worker yet."""
        with self._condition:
            self._frame = None


class ProcessWorker(QObject):
    """Worker for OCR and translation processing.

    Uses Python threading (not QThread) to avoid conflicts between
    ONNX runtime and windows-capture on Windows.

    Frames are sent via FrameBuffer, results emitted via Qt signals.
    """

    # Each frame result carries its capture generation so queued signals from a
    # stopped session cannot update the next session's overlays.
    text_ready = Signal(str, int)
    speech_ready = Signal(str, int)  # Successful translations only; never speak source/error fallback text
    speech_stream_ready = Signal(object, int)
    speech_scene_reset = Signal(int)  # A confirmed visible scene break, not an OCR-only miss.
    character_detected = Signal(str, bool, int)

    # Inplace mode: list of (text, bbox) regions
    regions_ready = Signal(list, int)

    # Raw OCR results (list of OCRResult) - emitted before translation for visualization
    ocr_results_ready = Signal(list, int)

    # Emitted when models are loaded and ready
    models_ready = Signal()

    # Emitted when model loading fails, with a user-readable description
    models_failed = Signal(str)

    # Per-model status signals: status can be "loading", "downloading", "ready", "error"
    ocr_status = Signal(str)
    translation_status = Signal(str)

    def __init__(self, config: Config):
        super().__init__()
        self._config = config
        self._ocr: OCREngine | None = None
        self._translator: TranslationEngine | None = None
        self._mode = OverlayMode.BANNER
        self._confidence_threshold = 0.6
        self._speech_candidate = ""
        self._speech_candidate_snapshot = None
        self._speech_candidate_text = ""
        self._speech_candidate_regions = []
        self._speech_candidate_image_cache = []
        self._speech_candidate_since = 0.0
        self._speech_visual_since = 0.0
        self._speech_candidate_ink = None
        self._speech_revealing = False
        self._speech_reveal_gap = 0.0
        self._speech_candidate_cursor = False
        self._speech_cursor_expected = False
        self._spoken_complete = False
        self._spoken_source = ""
        self._spoken_snapshot = None
        self._spoken_image_cache_snapshot = None
        self._spoken_image_cache = []
        self._suppressed_source = ""
        self._speech_session = None
        self.stream_speech = True
        self._previewed_source = ""
        self._last_preview_time = float("-inf")
        self._character_context_key = None
        self._character_name = ""
        self._character_candidate = None
        self._character_candidate_since = 0.0
        self._character_observations = 0
        self._character_next_poll = 0.0
        self._character_pixels = None
        self._character_regions = []
        self._character_snapshot = None
        self._character_image_cache = []
        self._character_settling = False
        self._character_valid_speakers = set()
        self._character_signal = None
        self._spoken_character = ""

        # Track which models failed (for "Fix Models" button) and why
        self._ocr_failed = False
        self._translation_failed = False
        self._ocr_error = ""
        self._translation_error = ""
        # Consecutive runtime failures since the last success
        self._ocr_failures = 0
        self._translation_failures = 0

        # Set when the UI changes a backend; handled on the worker thread
        self._reload_ocr = False
        self._reload_translation = False

        # Threading
        self._frame_buffer = FrameBuffer()
        self._thread: threading.Thread | None = None
        self._running = False
        self._stopped = False  # set by stop(); signals are no longer emitted after that

    def set_mode(self, mode: OverlayMode):
        """Set the overlay mode."""
        self._mode = mode

    def set_confidence_threshold(self, threshold: float):
        """Set the OCR confidence threshold."""
        self._confidence_threshold = threshold

    def start(self, ocr_confidence: float):
        """Start the worker thread and load models."""
        if self._thread is not None:
            return

        self._confidence_threshold = ocr_confidence
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0):
        """Stop the worker thread and wait for it to finish the frame in progress.

        Waiting matters: the caller typically drops this object right after (quit,
        Fix Models), and a frame still being processed would otherwise emit on a
        deleted QObject. A thread stuck in a slow engine call is abandoned after
        ``timeout`` seconds; it is a daemon thread and its emits are suppressed.
        """
        self._stopped = True
        self._running = False
        self._frame_buffer.close()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("worker thread still busy after stop", timeout=timeout)

    def _emit(self, signal, *args):
        """Emit a signal unless the worker was stopped (the receivers may be gone by then)."""
        if self._stopped:
            return
        try:
            signal.emit(*args)
        except RuntimeError:  # "Signal source has been deleted": raced with teardown
            logger.debug("signal dropped after teardown")

    def submit_frame(self, frame, generation: int = 0, origin=(0, 0), preview_only=False, speaker_context=None):
        """Send a frame for processing (non-blocking)."""
        if self._running:
            self._frame_buffer.put((frame, generation, origin, preview_only, speaker_context))

    def discard_pending_frames(self):
        """Drop queued work when capture stops; in-flight results retain their generation."""
        self._frame_buffer.clear()

    def reload_ocr(self):
        """Rebuild the OCR engine from the current config (after a settings change).

        Runs on the worker thread between frames, so the translation engine stays
        loaded and capture is not interrupted.
        """
        self._reload_ocr = True  # picked up within the loop's 0.5 s poll interval

    def reload_translation(self):
        """Rebuild the translation engine from the current config (after a settings change).

        Runs on the worker thread between frames, so OCR keeps its loaded model and
        capture is not interrupted.
        """
        self._reload_translation = True  # picked up within the loop's 0.5 s poll interval

    def has_failed_models(self) -> bool:
        """Check if any models failed to load."""
        return self._ocr_failed or self._translation_failed

    def get_failed_models(self) -> list[str]:
        """Get list of model types that failed to load."""
        failed = []
        if self._ocr_failed:
            failed.append("ocr")
        if self._translation_failed:
            failed.append("translation")
        return failed

    def get_failure_message(self) -> str:
        """Describe why loading failed, for the status bar."""
        return "; ".join(m for m in (self._ocr_error, self._translation_error) if m)

    def _run(self):
        """Worker thread main loop."""
        logger.debug("worker thread starting")

        self._load_ocr()
        self._load_translator()
        self._emit_models_status()

        while self._running:
            if self._reload_ocr:
                self._reload_ocr = False
                self._load_ocr()
                self._emit_models_status()
            if self._reload_translation:
                self._reload_translation = False
                self._load_translator()
                self._emit_models_status()

            pending = self._frame_buffer.get(timeout=0.5)
            # Only process frames if both models loaded successfully
            if pending is not None and not (self._ocr_failed or self._translation_failed):
                self._process_frame(*pending)

        self._discard_ocr()
        logger.debug("worker thread stopped")

    def _discard_ocr(self):
        """Drop the current OCR engine, releasing any connection it holds."""
        ocr, self._ocr = self._ocr, None
        close = getattr(ocr, "close", None)
        if callable(close):
            close()

    def _load_ocr(self):
        """Build and load the configured OCR engine, recording failure for the Fix Models flow."""
        self._emit(self.ocr_status, "loading")
        self._discard_ocr()
        try:
            ocr = create_ocr(self._config)
            ocr.confidence_threshold = self._confidence_threshold
            ocr.load()
            self._ocr = ocr
            self._ocr_failed = False
            self._ocr_error = ""
            self._ocr_failures = 0
            self._emit(self.ocr_status, "ready")
            logger.debug("OCR engine loaded", engine=ocr.name)
        except Exception as e:
            self._ocr_failed = True
            self._ocr_error = str(e)
            self._emit(self.ocr_status, "error")
            logger.error("failed to load OCR engine", error=str(e))

    def _load_translator(self):
        """Build and load the configured translation engine."""
        self._emit(self.translation_status, "loading")
        self._translator = None
        self._speech_session = None
        try:
            translator = create_translator(self._config)
            translator.load()
            self._translator = translator
            self._translation_failed = False
            self._translation_error = ""
            self._translation_failures = 0
            self._emit(self.translation_status, "ready")
            logger.debug("translation engine loaded", engine=translator.name)
        except Exception as e:
            self._translation_failed = True
            self._translation_error = str(e)
            self._emit(self.translation_status, "error")
            logger.error("failed to load translation engine", error=str(e))

    def _record_ocr_failure(self, error: Exception) -> bool:
        """Count a runtime OCR failure; mark the engine failed after repeated ones.

        Only the owocr backend fails at runtime (server stopped or paused). Once
        marked failed, frames are skipped until the UI reloads the engine.

        Returns:
            True if the engine has just been marked as failed.
        """
        self._ocr_failures += 1
        if self._ocr_failures < MAX_CONSECUTIVE_OCR_FAILURES:
            return False
        self._ocr_failed = True
        self._ocr_error = str(error)
        self._emit(self.ocr_status, "error")
        self._emit(self.models_failed, self._ocr_error)
        logger.error("OCR engine marked as failed", failures=self._ocr_failures, error=self._ocr_error)
        return True

    def _record_translation_failure(self, error: Exception) -> bool:
        """Count a runtime translation failure; mark the engine failed after repeated ones.

        A dead endpoint would otherwise keep showing "Ready" while every frame fails.
        Once marked failed, frames are skipped until the UI reloads the engine
        (Apply or Fix Models), which is the recovery path.

        Returns:
            True if the engine has just been marked as failed.
        """
        self._translation_failures += 1
        if self._translation_failures < MAX_CONSECUTIVE_TRANSLATION_FAILURES:
            return False
        self._translation_failed = True
        self._translation_error = str(error)
        self._emit(self.translation_status, "error")
        self._emit(self.models_failed, self._translation_error)
        logger.error(
            "translation engine marked as failed",
            failures=self._translation_failures,
            error=self._translation_error,
        )
        return True

    def _emit_models_status(self):
        if self._ocr_failed or self._translation_failed:
            self._emit(self.models_failed, self.get_failure_message() or "One or more models failed to load")
        else:
            self._emit(self.models_ready)

    @staticmethod
    def _dialogue_regions(regions, frame=None):
        """Ignore small, detached cursor glyphs beside normal-size text."""
        heights = [r.bbox["height"] for r in regions if r.bbox and len(re.findall(r"\w", r.text)) > 1]
        if not heights:
            return regions
        height = float(np.median(heights))
        markers = {"v", "V", "√", "▼", "▽", "▾", "▿", "▲", "△", "◀", "▶", "►", "◄", "✓", "✔"}

        def is_cursor(region):
            box = region.bbox
            if not box or box["height"] >= height * 0.75 or box["width"] > height:
                return False
            if region.text.strip() in markers:
                return True
            if (
                len(region.text.strip()) <= 2
                and isinstance(frame, np.ndarray)
                and frame.ndim == 3
                and frame.shape[2] >= 3
            ):
                # A detached footer arrow may be recognized as X, I or a digit.
                # Use the dialogue's font height for the shape check: the arrow
                # itself is too small to be treated as a normal text line.
                center_x = box["x"] + box["width"] / 2
                center_y = box["y"] + box["height"] / 2
                return any(
                    left <= center_x <= right and top <= center_y <= bottom
                    for left, top, right, bottom in ProcessWorker._cursor_rectangles(frame, {**box, "height": height})
                )
            return False

        filtered = [region for region in regions if not is_cursor(region)]
        if isinstance(frame, np.ndarray) and frame.ndim == 3 and frame.shape[2] >= 3:
            for index, region in enumerate(filtered):
                if (
                    region.bbox
                    and re.search(r"[.!?。！？]\s*[tTvVyY]\s*$", region.text)
                    and ProcessWorker._cursor_rectangles(frame, region.bbox)
                ):
                    # A filled GBA continuation arrow can be recognized as T.
                    # Remove only a suffix backed by the actual cursor pixels.
                    filtered[index] = OCRResult(
                        re.sub(r"(?<=[.!?。！？])\s*[tTvVyY]\s*$", "", region.text), region.bbox
                    )
        return [region for row in group_ocr_rows(filtered) for region in row if re.search(r"\w", region.text)]

    @staticmethod
    def _cursor_rectangles(frame, box):
        """Find small filled, colored or bright down-triangles at a line's tail.

        GBA continuation arrows can be merged into the line and recognized as
        a final T. Inspect a small tail crop; do not mask whole screen areas
        or ignore ordinary changed letters/numbers based on text similarity.
        """
        height, width = frame.shape[:2]
        line_height = int(box["height"])
        right = int(box["x"] + box["width"])
        x, y = max(0, right - 2 * line_height), max(0, int(box["y"]) - 8)
        x2, y2 = min(width, right + 2 * line_height), min(height, int(box["y"] + line_height) + 8)
        crop = frame[y:y2, x:x2, :3]
        if not crop.size:
            return []
        maximum, minimum = crop.max(axis=2), crop.min(axis=2)
        colored = (maximum.astype(np.int16) - minimum > 80) & (maximum > 120)
        # RPGMaker also uses white continuation arrows. Keep the same shape,
        # size and location checks as for GBA; do not strip arbitrary letters.
        masks = [colored]
        if np.mean(maximum < 160) > 0.4:
            masks.append((minimum > 200) & (maximum - minimum < 40))
        # A bright cap/shadow may touch a colored arrow and look like X when
        # merged. Inspect the colors separately to retain the real triangle.
        contours = [
            contour
            for mask in masks
            for contour in cv2.findContours(mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
        ]
        rectangles = []
        for contour in contours:
            left, top, w, h = cv2.boundingRect(contour)
            if not (3 <= w <= line_height and 3 <= h < line_height * 0.8):
                continue
            polygon = cv2.approxPolyDP(contour, 0.04 * cv2.arcLength(contour, True), True).reshape(-1, 2)
            if len(polygon) != 3 or cv2.contourArea(contour) < w * h * 0.32:
                continue
            tip = polygon[np.argmax(polygon[:, 1])]
            others = np.delete(polygon, np.argmax(polygon[:, 1]), axis=0)
            if abs(int(others[0, 1]) - int(others[1, 1])) > h * 0.3 or tip[1] - max(others[:, 1]) < h * 0.6:
                continue
            if not left + w * 0.2 <= tip[0] <= left + w * 0.8:
                continue
            # Include the small dark/antialiased shadow around a pixel arrow.
            pad = max(2, min(8, round(line_height * 0.08)))
            vertical_pad = max(pad, min(16, int(np.ceil(line_height * 0.1))))
            rectangles.append(
                (
                    max(0, x + left - pad),
                    max(0, y + top - vertical_pad),
                    min(width, x + left + w + pad),
                    min(height, y + top + h + vertical_pad),
                )
            )
        return rectangles

    @staticmethod
    def _text_snapshot(frame, regions):
        """Remember only the accepted text pixels, with a small box margin."""
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] < 3 or not regions:
            return None
        height, width = frame.shape[:2]
        patches = []
        for row in group_ocr_rows(regions):
            if any(not region.bbox for region in row):
                return None  # Engines without positions keep the exact-text guard.
            left = min(r.bbox["x"] for r in row)
            top = min(r.bbox["y"] for r in row)
            end_x = max(r.bbox["x"] + r.bbox["width"] for r in row)
            end_y = max(r.bbox["y"] + r.bbox["height"] for r in row)
            box = dict(x=left, y=top, width=end_x - left, height=end_y - top)
            margin = max(16, min(32, round(box["height"] * 0.25)))
            x, y = max(0, int(box["x"]) - margin), max(0, int(box["y"]) - margin)
            # A little right-side context also covers a continuation
            # arrow that was absent when this line was first accepted.
            right = min(width, int(box["x"] + box["width"]) + max(8, 2 * int(box["height"])))
            bottom = min(height, int(box["y"] + box["height"]) + margin)
            if right <= x or bottom <= y:
                return None
            cursors = ProcessWorker._cursor_rectangles(frame, box)
            for region in row:
                cursors.extend(ProcessWorker._cursor_rectangles(frame, region.bbox))
            patches.append((x, y, right, bottom, frame[y:bottom, x:right, :3].copy(), cursors, box.copy()))
        return (height, width), patches

    @staticmethod
    def _same_pixel_text(pixels, current, focus):
        """Allow flat-panel shading and resampling only with identical ink."""
        masks = []
        old_gray = None
        for image in (pixels, current):
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            histogram = cv2.calcHist([gray], [0], focus.astype(np.uint8), [256], [0, 256]).ravel()
            total = histogram.sum()
            if not total:
                return False
            cumulative = histogram.cumsum()
            low, high = (np.searchsorted(cumulative, total * p) for p in (0.01, 0.99))
            if high - low < 96:
                return False
            if old_gray is None:
                old_gray, old_low, old_high = gray, low, high
            # A solid dialogue panel is eligible; textures/random pixels are
            # not. Two adjacent bins tolerate a small background gradient.
            bins = histogram.reshape(16, 16).sum(axis=1)
            if max(bins[:-1] + bins[1:]) < 0.55 * total:
                return False
            ink = gray > (float(low) + float(high)) / 2
            if np.count_nonzero(ink & focus) > 0.5 * total:
                ink = ~ink
            masks.append((ink & focus).astype(np.uint8))
        old, new = masks
        old_moments, new_moments = (cv2.moments(mask, binaryImage=True) for mask in masks)
        if not old_moments["m00"] or not new_moments["m00"]:
            return False
        dx, dy = (
            round(new_moments[key] / new_moments["m00"] - old_moments[key] / old_moments["m00"])
            for key in ("m10", "m01")
        )
        if abs(dx) > 2 or abs(dy) > 2:
            return False
        # Excluded cursor pixels move with the image too. Only compare pixels
        # valid in both alignments, including when an arrow touches the box.
        aligned_focus = focus & np.roll(focus, (dy, dx), (0, 1))
        if np.any((np.roll(old, (dy, dx), (0, 1)) != new) & aligned_focus):
            return False
        # Antialias changes are limited to existing glyph edges. A new dim or
        # colored glyph away from those edges must remain a real text change.
        kernel = np.ones((3, 3), dtype=np.uint8)
        edges = cv2.dilate(new, kernel) != cv2.erode(new, kernel)
        aligned_gray = np.roll(old_gray, (dy, dx), (0, 1))
        # Small glyphs can have antialias pixels inside a filled stroke too.
        # Only pre-existing intermediate tones qualify, never fresh marks on
        # the solid background; the binary glyph geometry still matches exactly.
        edges |= (aligned_gray > old_low + (old_high - old_low) * 0.08) & (
            aligned_gray < old_high - (old_high - old_low) * 0.08
        )
        interior = aligned_focus & ~edges
        if not interior.any():
            return False
        aligned_pixels = np.roll(pixels, (dy, dx), (0, 1))
        indices = np.flatnonzero(interior)
        indices = indices[:: max(1, len(indices) // 2048)]
        samples = current.reshape(-1, 3)[indices].astype(np.int16) - aligned_pixels.reshape(-1, 3)[indices]
        offset = np.rint(np.median(samples, axis=0)).astype(np.int16)
        # OpenCV's channel comparisons avoid full-frame NumPy RGB reductions.
        unchanged = cv2.inRange(cv2.absdiff(current, aligned_pixels), (0, 0, 0), (2, 2, 2)) != 0
        if np.max(np.abs(offset)) <= 32:
            shifted = cv2.add(aligned_pixels, (*map(int, offset), 0))
            shading = cv2.inRange(cv2.absdiff(current, shifted), (0, 0, 0), (2, 2, 2)) != 0
        else:
            shading = False
        return not np.any(interior & ~unchanged & ~shading)

    @staticmethod
    def _same_outlined_text(pixels, current, focus):
        """Compare bright glyph geometry independently of a moving dark backdrop.

        Require several white glyphs with dark outlines before using this path.
        Compare all bright colors, so new colored letters remain real changes.
        """
        white = cv2.inRange(pixels, (208, 208, 208), (255, 255, 255))
        white[~focus] = 0
        count, labels, stats, _ = cv2.connectedComponentsWithStats(white, 8)
        dark = cv2.inRange(pixels, (0, 0, 0), (96, 96, 96))
        outlined, area = 0, 0
        for index in range(1, count):
            x, y, w, h, size = stats[index]
            if h < 4 or size < 8 or h >= pixels.shape[0] - 2:
                continue
            left, top = max(0, x - 3), max(0, y - 3)
            right, bottom = min(pixels.shape[1], x + w + 3), min(pixels.shape[0], y + h + 3)
            component = (labels[top:bottom, left:right] == index).astype(np.uint8)
            ring = cv2.dilate(component, np.ones((7, 7), np.uint8)) != cv2.dilate(component, np.ones((3, 3), np.uint8))
            if ring.any() and np.mean(dark[top:bottom, left:right][ring] != 0) >= 0.55:
                outlined += 1
                area += size
        if outlined < 3 or area < 0.75 * cv2.countNonZero(white):
            return False
        # A bright foreground core is unaffected by the dim scene under its
        # black outline. Exact geometry still distinguishes numbers/negation.
        masks = [(cv2.inRange(image, (0, 0, 0), (207, 207, 207)) == 0) & focus for image in (pixels, current)]
        old_moments, new_moments = (cv2.moments(mask.astype(np.uint8), binaryImage=True) for mask in masks)
        if not old_moments["m00"] or not new_moments["m00"]:
            return False
        dx, dy = (
            round(new_moments[key] / new_moments["m00"] - old_moments[key] / old_moments["m00"])
            for key in ("m10", "m01")
        )
        if abs(dx) > 2 or abs(dy) > 2:
            return False
        aligned_focus = focus & np.roll(focus, (dy, dx), (0, 1))
        return not np.any((np.roll(masks[0], (dy, dx), (0, 1)) != masks[1]) & aligned_focus)

    def _same_spoken_image(self, frame, regions):
        snapshot = self._spoken_snapshot
        if snapshot is not self._spoken_image_cache_snapshot:
            self._spoken_image_cache_snapshot = snapshot
            self._spoken_image_cache.clear()
        return self._same_dialogue_image(frame, regions, snapshot, self._spoken_image_cache, self._spoken_source)

    def _same_dialogue_image(self, frame, regions, snapshot, image_cache, reference_key=""):
        """Compare positioned glyphs for either pending or already spoken text."""
        if not snapshot or not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] < 3:
            return False
        shape, patches = snapshot
        if frame.shape[:2] != shape:
            return False
        key = " ".join(re.findall(r"\w+", " ".join(r.text for r in regions).casefold()))
        # Restrict the outlined-text fallback to unchanged word lengths (or an
        # OCR miss). Extra/dim words and growing text must retain strict checks.
        outlined_eligible = bool(reference_key) and (
            not key or list(map(len, key.split())) == list(map(len, reference_key.split()))
        )
        # Extra text elsewhere must still start a new utterance, even if the
        # previously spoken text remains on screen. Minor box jitter is safe
        # inside the remembered margin, whose pixels are also compared.
        for region in regions:
            box = region.bbox
            if not box or not any(
                x <= box["x"]
                and y <= box["y"]
                and box["x"] + box["width"] <= right
                and box["y"] + box["height"] <= bottom
                for x, y, right, bottom, _, _, _ in patches
            ):
                return False
        if all(np.array_equal(frame[y:bottom, x:right, :3], pixels) for x, y, right, bottom, pixels, _, _ in patches):
            return True
        # Cursor/filter states often alternate. Remember only hashes of up to
        # four proven variants for this snapshot, not extra copies of frames.
        digest = hashlib.blake2b(digest_size=16)
        for x, y, right, bottom, _, _, _ in patches:
            digest.update(np.ascontiguousarray(frame[y:bottom, x:right, :3]))
        image_key = digest.digest()
        if image_key in image_cache:
            return True
        for x, y, right, bottom, pixels, old_cursors, box in patches:
            current = frame[y:bottom, x:right, :3]
            if np.array_equal(current, pixels):
                continue
            # Margins locate jittered boxes/cursors, but animation in those
            # margins is not a change to the accepted dialogue itself.
            focus = np.zeros(pixels.shape[:2], dtype=bool)
            boxes = [box] + [
                r.bbox
                for r in regions
                if r.bbox
                and x <= r.bbox["x"]
                and y <= r.bbox["y"]
                and r.bbox["x"] + r.bbox["width"] <= right
                and r.bbox["y"] + r.bbox["height"] <= bottom
            ]
            for area in boxes:
                fringe = max(2, min(8, round(area["height"] * 0.1)))
                left, top = max(0, int(area["x"]) - x - fringe), max(0, int(area["y"]) - y - fringe)
                end_x = min(right - x, int(area["x"] + area["width"]) - x + fringe)
                end_y = min(bottom - y, int(area["y"] + area["height"]) - y + fringe)
                focus[top:end_y, left:end_x] = True
            cursors = old_cursors + self._cursor_rectangles(frame, box)
            for left, top, cursor_right, cursor_bottom in cursors:
                focus[max(0, top - y) : max(0, cursor_bottom - y), max(0, left - x) : max(0, cursor_right - x)] = False
            changed = np.any(current != pixels, axis=2) & focus
            if changed.any():
                if self._same_pixel_text(pixels, current, focus) or (
                    outlined_eligible and self._same_outlined_text(pixels, current, focus)
                ):
                    continue
                # A flat dialogue panel may shade/fade while its glyphs stay
                # identical. Compare exact high-contrast ink geometry, not RGB
                # brightness or approximate OCR strings. Textured/colored crops
                # retain the strict pixel comparison above.
                gray = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
                low, high = np.percentile(gray, [1, 99])
                neutral = pixels.max(axis=2).astype(np.int16) - pixels.min(axis=2)
                if high - low < 96 or np.mean(neutral <= 24) < 0.96:
                    return False
                threshold = (float(low) + float(high)) / 2
                current_gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
                current_neutral = current.max(axis=2).astype(np.int16) - current.min(axis=2)
                # Only minor neutral shading is eligible. Colored additions
                # and substantial new low-contrast strokes remain real changes
                # even when they happen to lie on one side of the ink threshold.
                delta = np.abs(current_gray.astype(np.int16) - gray)
                if np.any(changed & ((neutral > 24) | (current_neutral > 24) | (delta > 32))):
                    return False
                ink_changed = (gray > threshold) != (current_gray > threshold)
                if np.any(ink_changed & changed):
                    # Antialiased edge pixels also shift under uniform panel
                    # shading. Test the same exact geometry with the measured
                    # brightness offset; do not erase edges with dilation.
                    offset = float(np.median(current_gray.astype(np.int16) - gray))
                    shifted_ink = (gray > threshold) != (current_gray > threshold + offset)
                    if np.any(shifted_ink & changed):
                        return False
        image_cache.append(image_key)
        del image_cache[:-4]
        return True

    @staticmethod
    def _offset_box(box, origin):
        if not box or origin == (0, 0):
            return box
        return {**box, "x": box["x"] + origin[0], "y": box["y"] + origin[1]}

    @staticmethod
    def _reveal_ink(frame, regions):
        """Count flat-panel text ink, including a little still-unrecognized tail."""
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] < 3 or not regions:
            return None, False
        total, measured, cursor = 0, False, False
        h, w = frame.shape[:2]
        for region in regions:
            box = region.bbox
            if not box:
                continue
            arrows = ProcessWorker._cursor_rectangles(frame, box)
            cursor |= bool(arrows)
            x, y = max(0, int(box["x"]) - 2), max(0, int(box["y"]) - 2)
            right = min(w, int(box["x"] + box["width"] + 2 * box["height"]))
            bottom = min(h, int(box["y"] + box["height"]) + 2)
            if right <= x or bottom <= y:
                continue
            gray = cv2.cvtColor(frame[y:bottom, x:right, :3], cv2.COLOR_BGR2GRAY)
            histogram = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
            cumulative = histogram.cumsum()
            low, high = (np.searchsorted(cumulative, gray.size * p) for p in (0.01, 0.99))
            bins = histogram.reshape(16, 16).sum(axis=1)
            if high - low < 96 or max(bins[:-1] + bins[1:]) < 0.55 * gray.size:
                continue
            ink = gray > (float(low) + float(high)) / 2
            if np.count_nonzero(ink) > gray.size / 2:
                ink = ~ink
            for left, top, end_x, end_y in arrows:
                ink[max(0, top - y) : max(0, end_y - y), max(0, left - x) : max(0, end_x - x)] = False
            total += np.count_nonzero(ink)
            measured = True
        return (total if measured else None), cursor

    def _unread_source_tail(self, text):
        """Keep completed dialogue carried onto a new GBA line out of TTS."""
        if self._config.output_mode != OutputMode.TTS or not self._spoken_complete:
            return text
        previous = self._spoken_source.split()
        tokens = list(re.finditer(r"\w+", text))
        current = [m.group().casefold() for m in tokens]
        for count in range(min(len(previous), len(current)), 2, -1):
            overlap = previous[-count:]
            if sum(map(len, overlap)) >= 12 and overlap == current[:count]:
                return re.sub(r"^[\s,.!?。！？…:;]+", "", text[tokens[count - 1].end() :])
        return text

    def _character_voice(self, context, generation, now, force=False):
        """Read a small name crop on this OCR worker, never on the UI thread."""
        if context is None:
            self._character_settling = False
            self._character_context_key = None
            return None
        title, profile, frame = context
        if not profile.enabled or not isinstance(frame, np.ndarray) or not frame.size:
            self._character_settling = False
            return None
        settings = (title, repr(profile.to_dict()), self._config.tts_model_path)
        if settings != self._character_context_key:
            self._character_context_key = settings
            self._character_name, self._character_candidate = "", None
            self._character_snapshot = self._character_pixels = None
            self._character_image_cache.clear()
            self._character_next_poll = 0.0
            self._character_signal = None
            try:
                self._character_valid_speakers = {index for _, index in voice_details(self._config.tts_model_path)}
            except (OSError, ValueError, TypeError, KeyError):
                self._character_valid_speakers = set()
        if now >= self._character_next_poll or force:
            self._character_next_poll = now + 0.25
            if self._character_pixels is not None and np.array_equal(frame, self._character_pixels):
                regions = self._character_regions
            else:
                try:
                    regions = self._ocr.extract_text_regions(frame)
                except Exception as exc:
                    logger.debug("character name OCR failed", error=str(exc))
                    self._character_name = ""
                    self._character_settling = False
                    return "", self._config.tts_speaker_id, self._config.tts_reference_audio, self._voice_settings_key()
                self._character_pixels, self._character_regions = frame.copy(), regions
            label = " ".join(r.text for row in group_ocr_rows(regions) for r in row if r.text)
            name = character_name(label) if len(label) <= 64 else ""
            if name.casefold() != self._character_name.casefold() and self._same_dialogue_image(
                frame, regions, self._character_snapshot, self._character_image_cache, self._character_name.casefold()
            ):
                name = self._character_name
            if name.casefold() != self._character_candidate:
                self._character_candidate = name.casefold()
                self._character_candidate_since = now
                self._character_observations = 1
            else:
                self._character_observations += 1
            self._character_settling = (
                self._character_candidate != self._character_name.casefold() or self._character_signal is None
            )
            if self._character_observations >= 2 and now - self._character_candidate_since >= 0.2:
                if name.casefold() != self._character_name.casefold() or self._character_snapshot is None:
                    self._character_snapshot = self._text_snapshot(frame, regions) if name else None
                    self._character_image_cache.clear()
                self._character_name = name
                self._character_settling = False
        preset = profile.speaker_for(self._character_name, self._config.tts_model_path)
        mapped = preset is not None and preset in self._character_valid_speakers
        state = self._character_name, mapped, generation
        if not self._character_settling and state != self._character_signal:
            self._character_signal = state
            self._emit(self.character_detected, *state)
        return (
            self._character_name,
            preset if mapped else self._config.tts_speaker_id,
            "" if mapped else self._config.tts_reference_audio,
            self._voice_settings_key(),
        )

    def _voice_settings_key(self):
        return (
            self._config.tts_model_path,
            self._config.tts_speaker_id,
            self._config.tts_rate,
            self._config.tts_reference_audio,
        )

    @staticmethod
    def _character_stream(source_key, binding):
        if binding is None:
            return SpeechStream(key=source_key)
        name, preset, reference, voice_settings = binding
        return SpeechStream(
            key=("character", name.casefold(), source_key),
            speaker_name=name,
            speaker_id=preset,
            reference_audio=reference,
            voice_settings=voice_settings,
        )

    def _process_frame(self, frame, generation: int = 0, origin=(0, 0), preview_only=False, speaker_context=None):
        """Process a frame through OCR and translation."""
        if self._ocr is None:
            return

        frame_start = time.perf_counter()
        ocr_ms = 0
        translate_ms = 0
        was_cached = False

        # Update OCR threshold
        self._ocr.confidence_threshold = self._confidence_threshold

        # OCR - always extract regions to get bboxes for visualization
        try:
            ocr_start = time.perf_counter()
            regions = self._ocr.extract_text_regions(frame)
            ocr_ms = int((time.perf_counter() - ocr_start) * 1000)
            self._ocr_failures = 0
        except Exception as e:
            logger.error("OCR error", error=str(e))
            self._record_ocr_failure(e)
            return
        if self._stopped:
            return  # stopped while the engine was busy: the receivers are being torn down

        # Emit raw OCR results for visualization (e.g., OCR config dialog)
        display_regions = (
            [OCRResult(r.text, self._offset_box(r.bbox, origin)) for r in regions] if origin != (0, 0) else regions
        )
        self._emit(self.ocr_results_ready, display_regions, generation)
        if preview_only:
            return  # The OCR editor previews detection without translating/speaking edits.
        regions = self._dialogue_regions(regions, frame)
        text = " ".join(r.text for r in regions if r.text)
        captured_text = text

        # Speech follows stable source text, not each intermediate typewriter
        # frame or a new wording returned by the translator. A transient empty
        # or noisy frame must not make the same dialogue speak again.
        source_key = " ".join(re.findall(r"\w+", text.casefold()))
        now = time.monotonic()
        session = (
            generation,
            origin,
            frame.shape[:2] if isinstance(frame, np.ndarray) else None,
            self._config.output_mode,
            self._config.tts_enabled,
            self._config.tts_model_path,
            self._config.tts_speaker_id,
            self._config.tts_rate,
            self._config.tts_reference_audio,
        )
        if session != self._speech_session:
            logger.info("speech session reset", generation=generation, reason="capture or voice settings changed")
            self._speech_session = session
            self._spoken_source = ""
            self._spoken_snapshot = None
            self._suppressed_source = ""
            self._speech_candidate = ""
            self._speech_candidate_snapshot = None
            self._speech_candidate_text = ""
            self._speech_candidate_regions = []
            self._speech_candidate_image_cache.clear()
            self._speech_revealing = self._speech_candidate_cursor = self._speech_cursor_expected = False
            self._speech_reveal_gap = 0.0
            self._speech_candidate_ink = None
            self._spoken_complete = False
            self._spoken_character = ""
        name_start = time.perf_counter()
        voice_binding = self._character_voice(
            speaker_context, generation, now, force=source_key != self._speech_candidate
        )
        name_ocr_ms = int((time.perf_counter() - name_start) * 1000)
        ocr_ms += name_ocr_ms
        speaker = voice_binding[0].casefold() if voice_binding else ""
        if (
            not self._character_settling
            and speaker
            and self._spoken_character
            and speaker != self._spoken_character
            and self._spoken_source
        ):
            # A confirmed new speaker may genuinely say the same short line.
            # Unknown/late name recognition never replays an already read line.
            self._spoken_source = ""
            self._spoken_snapshot = None
            self._spoken_complete = False
        if self._same_spoken_image(frame, regions):
            # A fresh OCR pass can change letters/order while recognizing the
            # same pixels. It must not manufacture a second translation/TTS.
            if source_key and source_key != self._spoken_source and source_key != self._suppressed_source:
                logger.debug("speech repeat suppressed", reason="unchanged dialogue pixels", generation=generation)
                self._suppressed_source = source_key
            source_key = self._spoken_source
        if (
            source_key != self._speech_candidate
            and self._speech_candidate
            and self._config.tts_enabled
            and self._config.output_mode != OutputMode.OVERLAY
            and self._same_dialogue_image(
                frame,
                regions,
                self._speech_candidate_snapshot,
                self._speech_candidate_image_cache,
                self._speech_candidate,
            )
        ):
            # OCR flicker must not keep resetting the first-read settle timer.
            # Reuse text only after the existing strict image guard proves the
            # visible glyphs unchanged, including when detection misses a frame.
            source_key = self._speech_candidate
            text = captured_text = self._speech_candidate_text
            regions = self._speech_candidate_regions
        if source_key != self._speech_candidate:
            previous = self._speech_candidate
            growing = (
                bool(previous)
                and len(source_key) > len(previous)
                and (source_key.startswith(previous) or (len(previous) >= 6 and source_key.startswith(previous[:-2])))
            )
            if growing:
                self._speech_revealing = True
                self._speech_reveal_gap = max(self._speech_reveal_gap, now - self._speech_candidate_since)
            else:
                self._speech_revealing = False
                self._speech_reveal_gap = 0.0
            self._speech_candidate = source_key
            self._speech_candidate_snapshot = self._text_snapshot(frame, regions) if source_key else None
            self._speech_candidate_text = text
            self._speech_candidate_regions = regions
            self._speech_candidate_image_cache.clear()
            self._speech_candidate_since = now
            self._speech_visual_since = now
            self._speech_candidate_ink = None
            self._speech_candidate_cursor = False
            self._previewed_source = ""
            logger.debug(
                "speech candidate changed",
                source_id=hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:12],
                source_chars=len(captured_text),
                growing=growing,
                generation=generation,
            )
        if (
            source_key
            and source_key != self._spoken_source
            and self._config.tts_enabled
            and self._config.output_mode != OutputMode.OVERLAY
        ):
            ink, cursor = self._reveal_ink(frame, regions)
            if cursor:
                if not self._speech_cursor_expected:
                    logger.debug("speech completion cursor learned", generation=generation)
                self._speech_cursor_expected = self._speech_candidate_cursor = True
            previous_ink = self._speech_candidate_ink
            if ink is not None and previous_ink is not None and ink > previous_ink + max(4, previous_ink * 0.005):
                if not self._speech_revealing:
                    logger.debug("speech reveal detected", reason="visible ink growth", generation=generation)
                self._speech_revealing = True
                self._speech_reveal_gap = max(self._speech_reveal_gap, now - self._speech_visual_since)
                self._speech_visual_since = now
                if not cursor:
                    self._speech_candidate_cursor = False
            self._speech_candidate_ink = ink
        settle_seconds = SPEECH_SETTLE_SECONDS
        if not self._speech_candidate_cursor:
            if self._speech_cursor_expected:
                settle_seconds = SPEECH_CURSOR_FALLBACK_SECONDS
            if self._speech_revealing:
                settle_seconds = max(
                    settle_seconds, SPEECH_REVEAL_SETTLE_SECONDS, min(1.5, self._speech_reveal_gap * 1.5 + 0.15)
                )
        # Bound extra visual-only waiting under persistent shader/edge noise.
        since = max(self._speech_candidate_since, self._speech_visual_since)
        if now - self._speech_candidate_since >= 2.0:
            since = self._speech_candidate_since
        name_wait_expired = now - self._speech_candidate_since >= 0.6
        speech_stable = (
            bool(source_key) and now - since >= settle_seconds and (not self._character_settling or name_wait_expired)
        )
        if voice_binding is not None and self._character_settling and name_wait_expired:
            # An unreadable/flickering name must never starve dialogue or reuse
            # the previous character's voice. Fall back for this utterance only.
            voice_binding = (
                "",
                self._config.tts_speaker_id,
                self._config.tts_reference_audio,
                self._voice_settings_key(),
            )
            speaker = ""
        source_complete = self._speech_candidate_cursor or bool(re.search(r"[.!?。！？…][\"'”’)]*\s*$", captured_text))
        if not source_key and self._spoken_source and now - self._speech_candidate_since >= 1.5:
            had_visible_baseline = self._spoken_snapshot is not None
            logger.info("speech scene reset", generation=generation, reason="sustained visible blank")
            self._spoken_source = ""  # A sustained scene break can start a new identical line.
            self._spoken_snapshot = None
            self._spoken_complete = False
            if had_visible_baseline:
                self._emit(self.speech_scene_reset, generation)

        if not text:
            if self._mode == OverlayMode.INPLACE:
                self._emit(self.regions_ready, [], generation)
            else:
                self._emit(self.text_ready, "", generation)
            return

        # Skip translation when nothing looks like the source language (OCR garbage)
        language = self._config.source_language
        if not contains_script(text, language):
            logger.debug("skipping translation - no source-language text detected", language=language)
            if self._mode == OverlayMode.INPLACE:
                self._emit(self.regions_ready, [], generation)
            else:
                self._emit(self.text_ready, "", generation)
            return

        # Translation
        text = self._unread_source_tail(text)
        if not text:
            return
        can_preview = (
            self.stream_speech
            and self._config.tts_enabled
            and self._config.output_mode == OutputMode.TTS
            and self._config.llm.target_language.strip().casefold() in ("vietnamese", "vi", "vi-vn", "tiếng việt", "")
            and callable(getattr(type(self._translator), "preview_translation", None))
        )
        if can_preview and not speech_stable:
            # Use the existing settle window, not a shorter duplicate guard.
            # Only speculate on terminal punctuation observed on multiple frames,
            # at most once per 750 ms. A changed candidate never commits context.
            if (
                now - self._speech_candidate_since >= 0.1
                and len(text) <= 120
                and now - self._last_preview_time >= 0.75
                and source_key != self._previewed_source
                and re.search(r"[.!?。！？][\"'”’]*$", text.rstrip())
            ):
                self._previewed_source, self._last_preview_time = source_key, now
                try:
                    self._translator.preview_translation(text)
                    self._translation_failures = 0
                except Exception as exc:
                    self._record_translation_failure(exc)
            return
        if self._config.output_mode == OutputMode.TTS and (not speech_stable or source_key == self._spoken_source):
            return
        # Take the copy before a potentially slow translation, not afterwards.
        snapshot = self._text_snapshot(frame, regions) if speech_stable and source_key != self._spoken_source else None
        translate_start = time.perf_counter()
        speech_parts = []
        streamed = False
        if self._mode == OverlayMode.INPLACE and self._config.output_mode != OutputMode.TTS:
            translated_regions = []
            all_cached = True
            for region in regions:
                if self._translator and region.text:
                    # Skip regions with nothing in the source language's script
                    if not contains_script(region.text, language):
                        logger.debug("skipping region - no source-language text", text=region.text[:30])
                        continue
                    try:
                        translated, cached = self._translator.translate(region.text)
                        if translated:
                            speech_parts.append(translated)
                        if not cached:
                            all_cached = False
                        self._translation_failures = 0
                    except Exception as e:
                        logger.warning("Translation error", error=str(e), text=region.text[:50])
                        translated = region.text
                        all_cached = False
                        if self._record_translation_failure(e):
                            break
                else:
                    continue
                translated_regions.append((translated, self._offset_box(region.bbox, origin)))
            translate_ms = int((time.perf_counter() - translate_start) * 1000)
            was_cached = all_cached and len(translated_regions) > 0

            self._emit(self.regions_ready, translated_regions, generation)
        else:
            if self._translator:
                stream = None
                try:
                    can_stream = (
                        self.stream_speech
                        and speech_stable
                        and source_key != self._spoken_source
                        and self._config.tts_enabled
                        and self._config.output_mode != OutputMode.OVERLAY
                        and callable(getattr(type(self._translator), "translate_stream", None))
                        and self._config.llm.target_language.strip().casefold()
                        in ("vietnamese", "vi", "vi-vn", "tiếng việt", "")
                    )
                    if can_stream:
                        stream = self._character_stream(source_key, voice_binding)

                        def sentence_ready(sentence):
                            nonlocal streamed
                            stream.put(sentence, lambda: self._stopped)
                            if not streamed:
                                streamed = True
                                self._spoken_source = source_key
                                self._spoken_character = speaker
                                self._spoken_snapshot = snapshot
                                self._spoken_complete = source_complete
                                self._suppressed_source = ""
                                logger.info(
                                    "speech stream source accepted",
                                    source_id=hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:12],
                                    source_chars=len(captured_text),
                                    reveal_wait_ms=round(settle_seconds * 1000),
                                    generation=generation,
                                )
                                self._emit(self.speech_stream_ready, stream, generation)

                        translated, was_cached = self._translator.translate_stream(text, sentence_ready)
                    else:
                        translated, was_cached = self._translator.translate(text)
                    if translated:
                        speech_parts.append(translated)
                    self._translation_failures = 0
                except CancelledError:
                    return  # Session/output stopped; this is not an endpoint failure.
                except Exception as e:
                    if stream is not None:
                        stream.finish(e)
                    logger.warning("Translation error", error=str(e), text=text[:50])
                    translated = f"[{text}]"
                    self._record_translation_failure(e)
                finally:
                    if stream is not None:
                        stream.finish()
            else:
                translated = text
            translate_ms = int((time.perf_counter() - translate_start) * 1000)

            self._emit(self.text_ready, translated, generation)

        if (
            speech_parts
            and not streamed
            and speech_stable
            and source_key != self._spoken_source
            and self._config.tts_enabled
            and self._config.output_mode != OutputMode.OVERLAY
        ):
            self._spoken_source = source_key
            self._spoken_character = speaker
            self._spoken_snapshot = snapshot
            self._spoken_complete = source_complete
            self._suppressed_source = ""
            logger.info(
                "speech source accepted",
                source_id=hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:12],
                source_chars=len(captured_text),
                reveal_wait_ms=round(settle_seconds * 1000),
                generation=generation,
            )
            if voice_binding is not None:
                utterance = self._character_stream(source_key, voice_binding)
                utterance.put(" ".join(speech_parts))
                utterance.finish()
                self._emit(self.speech_stream_ready, utterance, generation)
            else:
                self._emit(self.speech_ready, " ".join(speech_parts), generation)
        total_ms = int((time.perf_counter() - frame_start) * 1000)
        translate_str = f"{translate_ms} (cached)" if was_cached else str(translate_ms)
        logger.debug(
            "frame processed",
            ocr_ms=ocr_ms,
            name_ocr_ms=name_ocr_ms,
            translate_ms=translate_str,
            total_ms=total_ms,
        )
