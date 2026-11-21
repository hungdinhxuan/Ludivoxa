"""Persistent Paddle process lifecycle and cache invalidation contracts."""

from unittest.mock import MagicMock

import numpy as np
import pytest

from interpreter.config import Config, GameProfile, OCRBackend, PaddleSettings
from interpreter.gui.workers import ProcessWorker
from interpreter.models import ModelLoadError
from interpreter.ocr import OCRResult, create_ocr
from interpreter.paddle_ocr import IsolatedPaddleOCR


def loaded_engine():
    engine = IsolatedPaddleOCR(PaddleSettings())
    engine._loaded = True
    engine._process = MagicMock()
    engine._process.is_alive.return_value = True
    engine._connection = MagicMock()
    engine._connection.recv.return_value = (True, [OCRResult("Hello")])
    return engine


def test_factory_isolates_only_paddle():
    assert isinstance(create_ocr(Config(ocr_backend=OCRBackend.PADDLE)), IsolatedPaddleOCR)


def local_onnx_model(root, name):
    folder = root / name
    folder.mkdir()
    (folder / "inference.yml").write_text(f"Global:\n  model_name: {name.removesuffix('_onnx')}\n", encoding="utf-8")
    (folder / "inference.onnx").touch()
    return str(folder)


def test_cjk_model_switches_once_per_profile_and_recovers_after_missing_model(tmp_path, monkeypatch):
    detector = local_onnx_model(tmp_path, "PP-OCRv5_mobile_det_onnx")
    english = local_onnx_model(tmp_path, "en_PP-OCRv5_mobile_rec_onnx")
    korean = local_onnx_model(tmp_path, "korean_PP-OCRv5_mobile_rec")
    config = Config(ocr_backend=OCRBackend.PADDLE, source_language="English", paddle=PaddleSettings(detector, english))
    worker = ProcessWorker(config)
    loaded, closed, frames = [], [], []
    monkeypatch.setattr(IsolatedPaddleOCR, "load", lambda engine: loaded.append(engine._settings.recognition_model_dir))
    monkeypatch.setattr(
        IsolatedPaddleOCR, "close", lambda engine: closed.append(engine._settings.recognition_model_dir)
    )
    monkeypatch.setattr(worker, "_load_translator", lambda: None)
    monkeypatch.setattr(worker, "_process_frame", lambda frame, *_: frames.append(frame))
    profiles = iter(["English", "Chinese", "Chinese", "Korean", "Korean", "English"])

    def next_frame(timeout):
        language = next(profiles, None)
        if language is None:
            worker._running = False
            return None
        return (language, 1, (0, 0), False, None, None, "Game", GameProfile(source_language=language))

    monkeypatch.setattr(worker._frame_buffer, "get", next_frame)
    worker._running = True
    worker._run()

    assert loaded == closed == [english, korean, english]
    assert frames == ["English", "Korean", "Korean", "English"]
    assert config.paddle.recognition_model_dir == english


def test_custom_named_local_recognizer_is_kept_if_it_matches_language(tmp_path):
    detector = local_onnx_model(tmp_path, "PP-OCRv5_mobile_det_onnx")
    local_onnx_model(tmp_path, "PP-OCRv5_mobile_rec_onnx")
    renamed = (tmp_path / "PP-OCRv5_mobile_rec_onnx").rename(tmp_path / "custom-rec")
    settings = PaddleSettings(detector, str(renamed))
    engine = create_ocr(Config(ocr_backend=OCRBackend.PADDLE, source_language="Chinese", paddle=settings))
    assert engine._settings == settings


def test_windows_bundle_supplies_korean_model_without_changing_saved_paths(tmp_path, monkeypatch):
    import sys

    detector = local_onnx_model(tmp_path, "PP-OCRv5_mobile_det")
    bundle = tmp_path / "_internal"
    bundled_models = bundle / "ocr-models"
    bundled_models.mkdir(parents=True)
    recognizer = local_onnx_model(bundled_models, "korean_PP-OCRv5_mobile_rec")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    settings = PaddleSettings(detector, str(tmp_path / "en_PP-OCRv5_mobile_rec"))
    engine = create_ocr(Config(ocr_backend=OCRBackend.PADDLE, source_language="Korean", paddle=settings))
    assert engine._settings.recognition_model_dir == recognizer
    assert settings.recognition_model_dir.endswith("en_PP-OCRv5_mobile_rec")


def test_unchanged_frame_reuses_regions_but_pixel_and_confidence_changes_invalidate():
    engine = loaded_engine()
    frame = np.zeros((20, 40, 4), dtype=np.uint8)
    first = engine.extract_text_regions(frame)
    assert engine.extract_text_regions(frame.copy()) is first
    assert engine._connection.send.call_count == 1
    frame[3, 8, 0] = 1  # Even a single changed pixel must trigger fresh OCR.
    engine.extract_text_regions(frame)
    engine.confidence_threshold = 0.9
    engine.extract_text_regions(frame)
    assert engine._connection.send.call_count == 3


def test_worker_error_is_reported_without_caching_failed_frame():
    engine = loaded_engine()
    engine._connection.recv.return_value = (False, "native inference failed")
    with pytest.raises(ModelLoadError, match="native inference failed"):
        engine.extract_text_regions(np.zeros((10, 10, 4), dtype=np.uint8))
    assert engine._last_frame is None


def test_cursor_blink_returning_to_exact_frame_reuses_original_recognition():
    engine = loaded_engine()
    frame = np.zeros((100, 200, 4), dtype=np.uint8)
    original = engine.extract_text_regions(frame)
    cursor = frame.copy()
    cursor[70:80, 150:160, :3] = 255
    engine._connection.recv.return_value = (True, [OCRResult("Hello T")])
    engine.extract_text_regions(cursor)
    assert engine.extract_text_regions(frame.copy()) is original
    assert engine._connection.send.call_count == 2


def test_timeout_closes_only_owned_child_and_is_idempotent():
    engine = loaded_engine()
    process, connection = engine._process, engine._connection
    connection.poll.return_value = False
    with pytest.raises(ModelLoadError, match="timed out"):
        engine.extract_text_regions(np.zeros((10, 10, 4), dtype=np.uint8))
    assert not engine.is_loaded()
    process.terminate.assert_called_once()
    process.close.assert_called_once()
    connection.close.assert_called_once()
    engine.close()
    process.close.assert_called_once()


def test_child_crash_closes_resources_without_fallback():
    engine = loaded_engine()
    engine._connection.recv.side_effect = EOFError
    with pytest.raises(ModelLoadError, match="exited"):
        engine.extract_text_regions(np.zeros((10, 10, 4), dtype=np.uint8))
    assert not engine.is_loaded()


def test_crop_contains_all_changed_pixels_and_previous_text_and_restores_coordinates():
    engine = loaded_engine()
    frame = np.zeros((400, 600, 4), dtype=np.uint8)
    engine._connection.recv.return_value = (
        True,
        [OCRResult("Hello", {"x": 100, "y": 300, "width": 100, "height": 20})],
    )
    engine.extract_text_regions(frame)
    frame[310:320, 200:240, 0] = 255  # Typewriter grows beyond the last box.
    engine._connection.recv.return_value = (
        True,
        [OCRResult("Hello world", {"x": 32, "y": 32, "width": 140, "height": 20})],
    )
    regions = engine.extract_text_regions(frame)
    sent = engine._connection.send.call_args.args[0][0]
    assert sent.shape == (84, 204, 4)
    assert regions[0].bbox == {"x": 100, "y": 300, "width": 140, "height": 20}


def test_new_text_elsewhere_expands_crop_and_scene_changes_use_full_frame():
    engine = loaded_engine()
    frame = np.zeros((400, 600, 4), dtype=np.uint8)
    engine._connection.recv.return_value = (
        True,
        [OCRResult("Hello", {"x": 100, "y": 300, "width": 100, "height": 20})],
    )
    engine.extract_text_regions(frame)
    frame[100, 400] = 255
    crop, left, top = engine._changed_region(frame)
    assert left <= 100 and top <= 100
    assert left + crop.shape[1] > 400 and top + crop.shape[0] >= 320
    frame[0, 599] = 255
    frame[399, 0] = 255
    crop, left, top = engine._changed_region(frame)
    assert crop is frame and (left, top) == (0, 0)


def test_no_prior_text_or_changed_confidence_requires_full_frame():
    engine = loaded_engine()
    frame = np.zeros((100, 200, 4), dtype=np.uint8)
    engine._connection.recv.return_value = (True, [])
    engine.extract_text_regions(frame)
    frame[10, 10] = 1
    assert engine._changed_region(frame)[0] is frame
    engine._last_regions = [OCRResult("Hello", {"x": 10, "y": 10, "width": 30, "height": 15})]
    engine.confidence_threshold = 0.99
    assert engine._changed_region(frame)[0] is frame
