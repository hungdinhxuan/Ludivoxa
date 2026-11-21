"""Optional PaddleOCR 3 backend using explicitly provisioned local inference models."""

import multiprocessing
import os
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import yaml

from .models import ModelLoadError
from .ocr import DEFAULT_CONFIDENCE_THRESHOLD, PADDLE_LANGUAGE_MODELS, OCRResult

_ONNX_WEIGHTS = ("inference.onnx",)
_PADDLE_WEIGHTS = ("inference.json", "inference.pdiparams")


def _has_model(path, weights):
    return all((path / name).is_file() for name in ("inference.yml", *weights))


def select_paddle_recognizer(settings, language):
    """Select the local recognizer for an explicit Chinese or Korean source."""
    name = PADDLE_LANGUAGE_MODELS.get(language)
    if name is None:
        return settings
    detector = Path(settings.detection_model_dir).expanduser()
    configured = Path(settings.recognition_model_dir).expanduser()
    candidates = []
    if settings.recognition_model_dir:
        try:
            metadata = yaml.safe_load((configured / "inference.yml").read_text(encoding="utf-8"))
            if metadata["Global"]["model_name"] == name:
                candidates.append(configured)
        except (OSError, TypeError, KeyError, ValueError, yaml.YAMLError):
            pass
    for root in dict.fromkeys((detector.parent, configured.parent)):
        candidates.extend((root / name, root / f"{name}_onnx"))
    if bundle_dir := getattr(sys, "_MEIPASS", None):
        candidates.append(Path(bundle_dir) / "ocr-models" / name)

    runtimes = (_ONNX_WEIGHTS, _PADDLE_WEIGHTS) if settings.device == "cpu" else (_PADDLE_WEIGHTS,)
    for weights in runtimes:
        if settings.detection_model_dir and _has_model(detector, weights):
            for directory in candidates:
                if _has_model(directory, weights):
                    return replace(settings, recognition_model_dir=str(directory))
    raise ModelLoadError(
        f"{language} OCR requires a local {name} recognizer and a detector with matching weights. "
        "Place the recognizer next to the detector. For CPU ONNX models, run "
        "packaging/provision_ocr_onnx.py during setup. The app never downloads models."
    )


def _ocr_process(connection, settings):
    """Keep Paddle's GIL-holding native inference away from UI/audio/hotkeys."""
    # Only the OCR child gets these limits and lower scheduling priority.
    os.environ["OMP_NUM_THREADS"] = str(settings.cpu_threads)
    os.environ["MKL_NUM_THREADS"] = str(settings.cpu_threads)
    os.environ["OMP_WAIT_POLICY"] = "PASSIVE"
    os.environ["KMP_BLOCKTIME"] = "0"
    if os.name == "nt":
        import ctypes

        # Explicit pointer width matters: ctypes otherwise truncates Windows'
        # 64-bit pseudo-handle, making SetPriorityClass silently fail.
        ctypes.windll.kernel32.SetPriorityClass(ctypes.c_void_p(-1), 0x4000)
    # Local model paths must never turn into network requests, including in a
    # spawned child where the parent's offline test guards aren't inherited.
    import socket

    def offline(*args, **kwargs):
        raise RuntimeError("Network access is disabled in offline OCR.")

    socket.socket.connect = offline
    try:
        engine = PaddleOCR(settings)
        engine.load()
        connection.send((True, None))
        while True:
            request = connection.recv()
            if request is None:
                break
            image, confidence = request
            engine.confidence_threshold = confidence
            try:
                connection.send((True, engine.extract_text_regions(image)))
            except Exception as exc:
                connection.send((False, str(exc)))
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            connection.send((False, str(exc)))
        except (EOFError, BrokenPipeError):
            pass
    finally:
        connection.close()


class IsolatedPaddleOCR:
    """Existing Paddle backend in one persistent local child, with the same API."""

    name = "PaddleOCR (offline)"

    def __init__(self, settings):
        self._settings = settings
        self.confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD
        self._process = self._connection = None
        self._loaded = False
        self._last_frame = self._last_regions = self._last_confidence = None
        self._frame_cache = []

    def is_loaded(self):
        return self._loaded and self._process.is_alive()

    def _receive(self, timeout):
        if not self._connection.poll(timeout):
            self.close()
            raise ModelLoadError("Offline OCR worker timed out. Reload the OCR engine to retry.")
        try:
            success, value = self._connection.recv()
        except (EOFError, OSError) as exc:
            self.close()
            raise ModelLoadError("Offline OCR worker exited. Reload the OCR engine to retry.") from exc
        if not success:
            raise ModelLoadError(value)
        return value

    def load(self):
        if self.is_loaded():
            return
        self.close()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        self._connection = parent
        self._process = context.Process(target=_ocr_process, args=(child, self._settings), daemon=True)
        try:
            self._process.start()
            child.close()
            self._receive(90)
            self._loaded = True
        except Exception:
            child.close()
            self.close()
            raise

    def extract_text_regions(self, image):
        if not self.is_loaded():
            self.load()
        if self._last_confidence != self.confidence_threshold:
            self._frame_cache.clear()
        for index in range(len(self._frame_cache) - 1, -1, -1):
            pixels, cached_regions = self._frame_cache[index]
            if np.array_equal(image, pixels):
                self._frame_cache.append(self._frame_cache.pop(index))
                self._last_frame, self._last_regions = pixels, cached_regions
                return cached_regions
        crop, left, top = self._changed_region(image)
        try:
            self._connection.send((crop, self.confidence_threshold))
        except (BrokenPipeError, OSError) as exc:
            self.close()
            raise ModelLoadError("Offline OCR worker exited. Reload the OCR engine to retry.") from exc
        regions = self._receive(30)
        if left or top:
            for region in regions:
                if region.bbox:
                    region.bbox = {**region.bbox, "x": region.bbox["x"] + left, "y": region.bbox["y"] + top}
        self._last_frame = image.copy()
        self._last_confidence = self.confidence_threshold
        self._last_regions = regions
        # Cursor blinking commonly alternates between a few exact frames.
        # Reuse their original recognition rather than running OCR again with
        # a different crop (which can change spelling/order). Bound total frame
        # storage, including the current frame, to 64 MiB or three entries.
        self._frame_cache.append((self._last_frame, regions))
        while len(self._frame_cache) > 1 and (
            len(self._frame_cache) > 3 or sum(pixels.nbytes for pixels, _ in self._frame_cache) > 64 * 1024 * 1024
        ):
            self._frame_cache.pop(0)
        return regions

    def _changed_region(self, image):
        """Include every changed pixel and every prior text box, with context.

        This is a conservative crop, not a fixed dialogue location: text that
        appears elsewhere expands it, and scene/shape/confidence changes use a
        full frame. Until text is found, always scan the full image.
        """
        if (
            self._last_frame is None
            or image.shape != self._last_frame.shape
            or not self._last_regions
            or self._last_confidence != self.confidence_threshold
        ):
            return image, 0, 0
        boxes = [r.bbox for r in self._last_regions]
        if any(not box for box in boxes):
            return image, 0, 0
        changed = np.any(image != self._last_frame, axis=2)
        rows = np.flatnonzero(changed.any(axis=1))
        columns = np.flatnonzero(changed.any(axis=0))
        if not len(rows) or not len(columns):
            return image, 0, 0
        height, width = image.shape[:2]
        left = max(0, min(int(columns[0]), *(b["x"] for b in boxes)) - 32)
        top = max(0, min(int(rows[0]), *(b["y"] for b in boxes)) - 32)
        right = min(width, max(int(columns[-1]) + 1, *(b["x"] + b["width"] for b in boxes)) + 32)
        bottom = min(height, max(int(rows[-1]) + 1, *(b["y"] + b["height"] for b in boxes)) + 32)
        if (right - left) * (bottom - top) >= width * height * 0.65:
            return image, 0, 0
        return image[top:bottom, left:right], left, top

    def close(self):
        self._loaded = False
        self._last_frame = self._last_regions = self._last_confidence = None
        self._frame_cache.clear()
        connection, self._connection = self._connection, None
        process, self._process = self._process, None
        if connection is not None:
            try:
                connection.send(None)
            except (BrokenPipeError, OSError):
                pass
            connection.close()
        if process is not None:
            if process.pid is not None:
                process.join(1)
                if process.is_alive():
                    process.terminate()
                    process.join(1)
            process.close()


class PaddleOCR:
    """One resident recognizer; no model search, download or per-frame engine switching."""

    name = "PaddleOCR (offline)"

    def __init__(self, settings):
        self._settings = settings
        self._model = None
        self.confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD

    def is_loaded(self):
        return self._model is not None

    def load(self):
        if self.is_loaded():
            return
        settings = self._settings
        paths = [Path(value).expanduser() for value in (settings.detection_model_dir, settings.recognition_model_dir)]
        onnx = settings.device == "cpu" and all((path / "inference.onnx").is_file() for path in paths)
        weights = _ONNX_WEIGHTS if onnx else _PADDLE_WEIGHTS
        names = []
        for value, path in zip((settings.detection_model_dir, settings.recognition_model_dir), paths, strict=True):
            if not value or not _has_model(path, weights):
                raise ModelLoadError(
                    "PaddleOCR requires local PaddleOCR 3 inference model directories "
                    "containing inference.yml and either both ONNX models (inference.onnx), "
                    "or Paddle weights (inference.json and inference.pdiparams). "
                    "Set paddle.detection_model_dir and paddle.recognition_model_dir; models are never downloaded."
                )
            try:
                metadata = yaml.safe_load((path / "inference.yml").read_text(encoding="utf-8"))
                name = metadata["Global"]["model_name"]
                if not isinstance(name, str) or not name:
                    raise ValueError("empty model name")
                names.append(name)
            except (TypeError, KeyError, ValueError, yaml.YAMLError) as exc:
                raise ModelLoadError(f"Invalid PaddleOCR inference.yml in {path}: {exc}") from exc
        # Set before importing PaddleX: otherwise it probes external model hosts.
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        try:
            from paddleocr import PaddleOCR as Pipeline

            # Use PaddleOCR's existing public ONNX engine with explicitly
            # provisioned exports. Original Paddle model folders retain their
            # current runtime; inference errors never trigger a download or
            # switch runtimes. Keep execution on the isolated CPU worker.
            runtime = {}
            if onnx:
                runtime = {
                    "engine": "onnxruntime",
                    "engine_config": {
                        "providers": ["CPUExecutionProvider"],
                        "intra_op_num_threads": settings.cpu_threads,
                        "inter_op_num_threads": 1,
                        "log_severity_level": 3,
                    },
                }
            model = Pipeline(
                text_detection_model_dir=str(paths[0].resolve()),
                text_detection_model_name=names[0],
                text_recognition_model_dir=str(paths[1].resolve()),
                text_recognition_model_name=names[1],
                device=settings.device,
                cpu_threads=settings.cpu_threads,
                enable_mkldnn=True,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                text_det_limit_side_len=settings.max_side,
                text_det_limit_type="max",
                text_recognition_batch_size=1,
                **runtime,
            )
        except Exception as exc:
            raise ModelLoadError(f"Cannot load local PaddleOCR models: {exc}. Install the paddle extra.") from exc
        self._model = model

    def extract_text_regions(self, image):
        if not self.is_loaded():
            self.load()
        height, width = image.shape[:2]
        # Captures are BGRA; Paddle expects BGR. Its polygons are in input pixels.
        bgr = np.ascontiguousarray(image[:, :, :3])
        regions = []
        for result in self._model.predict(bgr, text_rec_score_thresh=self.confidence_threshold):
            for text, score, polygon in zip(
                result["rec_texts"], result["rec_scores"], result["rec_polys"], strict=True
            ):
                if not text.strip() or not np.isfinite(score) or score < self.confidence_threshold:
                    continue
                points = np.asarray(polygon)
                if points.shape != (4, 2) or not np.isfinite(points).all():
                    continue
                x1, y1 = np.maximum(np.floor(points.min(axis=0)), [0, 0]).astype(int)
                x2, y2 = np.minimum(np.ceil(points.max(axis=0)), [width, height]).astype(int)
                if x2 > x1 and y2 > y1:
                    regions.append(
                        OCRResult(
                            text.strip(), {"x": int(x1), "y": int(y1), "width": int(x2 - x1), "height": int(y2 - y1)}
                        )
                    )
        return regions
