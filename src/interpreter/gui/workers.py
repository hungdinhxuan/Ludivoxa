"""Background workers for OCR and translation."""

import hashlib
import re
import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import QObject, Signal

from .. import log
from ..config import Config, OutputMode, OverlayMode
from ..languages import contains_japanese, contains_script  # noqa: F401  (contains_japanese kept for callers)
from ..ocr import OCREngine, OCRResult, create_ocr
from ..translate import TranslationEngine, create_translator

logger = log.get_logger()

# After this many consecutive runtime failures an engine is marked as failed
# (status "Error" + Fix Models) instead of retrying on every frame forever.
MAX_CONSECUTIVE_TRANSLATION_FAILURES = 3
MAX_CONSECUTIVE_OCR_FAILURES = 3
SPEECH_SETTLE_SECONDS = 0.35


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
        self._speech_candidate_since = 0.0
        self._spoken_source = ""
        self._spoken_snapshot = None
        self._suppressed_source = ""
        self._speech_session = None

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

    def submit_frame(self, frame, generation: int = 0, origin=(0, 0), preview_only=False):
        """Send a frame for processing (non-blocking)."""
        if self._running:
            self._frame_buffer.put((frame, generation, origin, preview_only))

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
    def _dialogue_regions(regions):
        """Ignore small, detached cursor glyphs beside normal-size text."""
        heights = [r.bbox["height"] for r in regions if r.bbox and len(re.findall(r"\w", r.text)) > 1]
        if not heights:
            return regions
        height = float(np.median(heights))
        markers = {"v", "V", "√", "▼", "▽", "▾", "▿", "▲", "△", "◀", "▶", "►", "◄", "✓", "✔"}
        return [
            r
            for r in regions
            if not (
                r.text.strip() in markers and r.bbox and r.bbox["height"] < height * 0.75 and r.bbox["width"] <= height
            )
        ]

    @staticmethod
    def _cursor_rectangles(frame, box):
        """Find small filled, colored down-triangles at a text line's tail.

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
        mask = ((maximum.astype(np.int16) - minimum > 80) & (maximum > 120)).astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
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
            rectangles.append(
                (
                    max(0, x + left - pad),
                    max(0, y + top - pad),
                    min(width, x + left + w + pad),
                    min(height, y + top + h + pad),
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
        for region in regions:
            box = region.bbox
            if not box:
                return None  # Engines without positions keep the exact-text guard.
            margin = max(16, min(32, round(box["height"] * 0.25)))
            x, y = max(0, int(box["x"]) - margin), max(0, int(box["y"]) - margin)
            # A little right-side context also covers a continuation
            # arrow that was absent when this line was first accepted.
            right = min(width, int(box["x"] + box["width"]) + max(8, 2 * int(box["height"])))
            bottom = min(height, int(box["y"] + box["height"]) + margin)
            if right <= x or bottom <= y:
                return None
            cursors = ProcessWorker._cursor_rectangles(frame, box)
            patches.append((x, y, right, bottom, frame[y:bottom, x:right, :3].copy(), cursors, box.copy()))
        return (height, width), patches

    def _same_spoken_image(self, frame, regions):
        snapshot = self._spoken_snapshot
        if not snapshot or not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] < 3:
            return False
        shape, patches = snapshot
        if frame.shape[:2] != shape:
            return False
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
        for x, y, right, bottom, pixels, old_cursors, box in patches:
            current = frame[y:bottom, x:right, :3]
            if np.array_equal(current, pixels):
                continue
            cursors = old_cursors + self._cursor_rectangles(frame, box)
            changed = np.any(current != pixels, axis=2)
            for left, top, cursor_right, cursor_bottom in cursors:
                changed[max(0, top - y) : max(0, cursor_bottom - y), max(0, left - x) : max(0, cursor_right - x)] = (
                    False
                )
            if changed.any():
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
        return True

    @staticmethod
    def _offset_box(box, origin):
        if not box or origin == (0, 0):
            return box
        return {**box, "x": box["x"] + origin[0], "y": box["y"] + origin[1]}

    def _process_frame(self, frame, generation: int = 0, origin=(0, 0), preview_only=False):
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
        regions = self._dialogue_regions(regions)
        text = " ".join(r.text for r in regions if r.text)

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
            self._speech_session = session
            self._spoken_source = ""
            self._spoken_snapshot = None
            self._suppressed_source = ""
            self._speech_candidate = ""
        if self._same_spoken_image(frame, regions):
            # A fresh OCR pass can change letters/order while recognizing the
            # same pixels. It must not manufacture a second translation/TTS.
            if source_key and source_key != self._spoken_source and source_key != self._suppressed_source:
                logger.debug("speech repeat suppressed", reason="unchanged dialogue pixels", generation=generation)
                self._suppressed_source = source_key
            source_key = self._spoken_source
        if source_key != self._speech_candidate:
            self._speech_candidate = source_key
            self._speech_candidate_since = now
        speech_stable = bool(source_key) and now - self._speech_candidate_since >= SPEECH_SETTLE_SECONDS
        if not source_key and now - self._speech_candidate_since >= 1.5:
            self._spoken_source = ""  # A sustained scene break can start a new identical line.
            self._spoken_snapshot = None

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
        if self._config.output_mode == OutputMode.TTS and (not speech_stable or source_key == self._spoken_source):
            return
        # Take the copy before a potentially slow translation, not afterwards.
        snapshot = self._text_snapshot(frame, regions) if speech_stable and source_key != self._spoken_source else None
        translate_start = time.perf_counter()
        speech_parts = []
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
                try:
                    translated, was_cached = self._translator.translate(text)
                    if translated:
                        speech_parts.append(translated)
                    self._translation_failures = 0
                except Exception as e:
                    logger.warning("Translation error", error=str(e), text=text[:50])
                    translated = f"[{text}]"
                    self._record_translation_failure(e)
            else:
                translated = text
            translate_ms = int((time.perf_counter() - translate_start) * 1000)

            self._emit(self.text_ready, translated, generation)

        if (
            speech_parts
            and speech_stable
            and source_key != self._spoken_source
            and self._config.tts_enabled
            and self._config.output_mode != OutputMode.OVERLAY
        ):
            self._spoken_source = source_key
            self._spoken_snapshot = snapshot
            self._suppressed_source = ""
            logger.debug(
                "speech source accepted",
                source_id=hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:12],
                source_chars=len(text),
                generation=generation,
            )
            self._emit(self.speech_ready, " ".join(speech_parts), generation)
        total_ms = int((time.perf_counter() - frame_start) * 1000)
        translate_str = f"{translate_ms} (cached)" if was_cached else str(translate_ms)
        logger.debug(
            "frame processed",
            ocr_ms=ocr_ms,
            translate_ms=translate_str,
            total_ms=total_ms,
        )
