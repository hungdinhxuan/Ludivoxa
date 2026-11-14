"""Persistent Paddle process lifecycle and cache invalidation contracts."""

from unittest.mock import MagicMock

import numpy as np
import pytest

from interpreter.config import Config, OCRBackend, PaddleSettings
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
