"""Selected-area OCR, saved profiles and viewport interactions."""

import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow

from ludivoxa.config import Config, OverlayMode, PaddleSettings, normalize_ocr_region
from ludivoxa.gui.main_window import MainWindow
from ludivoxa.gui.ocr_config import OCRConfigDialog
from ludivoxa.gui.workers import ProcessWorker
from ludivoxa.ocr import OCRResult

REGION = {"x": 0.2, "y": 0.6, "width": 0.65, "height": 0.3}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    win = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(win)
    win._config = Config(source_language="English", output_mode="tts")
    win._current_window_title = "Game"
    win._ocr_config_dialog = None
    yield win
    win.close()


def test_region_profiles_roundtrip_and_exclusions_remain_compatible(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("exclusion_zones:\n  Game:\n  - {x: 0, y: 0, width: 1, height: 0.2}\n", encoding="utf-8")
    config = Config.load(str(path))
    assert config.get_ocr_region("Game") is None
    original = config.get_exclusion_zones("Game")
    config.set_ocr_region("Game", REGION)
    config.save()
    loaded = Config.load(str(path))
    assert loaded.get_ocr_region("Game") == pytest.approx(REGION)
    assert loaded.get_ocr_region("Other game") is None
    assert loaded.get_exclusion_zones("Game") == original
    copy = loaded.get_ocr_region("Game")
    copy["width"] = 0
    assert loaded.get_ocr_region("Game")["width"] > 0
    loaded.set_ocr_region("Game", None)
    loaded.save()
    assert Config.load(str(path)).get_ocr_region("Game") is None
    assert "ocr_regions" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {**REGION, "x": float("nan")},
        {**REGION, "height": 0},
        {**REGION, "y": float("inf")},
        {**REGION, "width": "bad"},
        {**REGION, "x": 2},
    ],
)
def test_invalid_region_is_rejected(value):
    assert normalize_ocr_region(value) is None
    assert Config(ocr_regions={"Game": value}).get_ocr_region("Game") is None


def test_out_of_bounds_region_is_clipped():
    assert normalize_ocr_region({"x": -0.2, "y": 0.8, "width": 0.5, "height": 0.5}) == pytest.approx(
        {"x": 0, "y": 0.8, "width": 0.3, "height": 0.2}
    )


@pytest.mark.parametrize("shape", [(800, 1200), (1600, 2400)])
def test_only_selected_pixels_reach_ocr_even_with_old_exclusions(window, shape):
    frame = np.arange(shape[0] * shape[1] * 4, dtype=np.uint8).reshape(*shape, 4)
    window._config.set_ocr_region("Game", REGION)
    window._config.set_exclusion_zones("Game", [{"x": 0, "y": 0, "width": 1, "height": 1}])
    crop, origin = window._prepare_ocr_frame(frame)
    h, w = shape
    assert origin == (int(w * 0.2), int(h * 0.6))
    assert crop.shape[:2] == (round(h * 0.3), round(w * 0.65))
    assert np.array_equal(crop, frame[int(h * 0.6) : round(h * 0.9), int(w * 0.2) : round(w * 0.85)])
    # Preparing the frame must not change the capture or make a full-frame copy.
    assert np.shares_memory(crop, frame)


def test_missing_editor_box_never_falls_back_to_scanning_whole_window(window):
    window._ocr_config_dialog = SimpleNamespace(get_scan_region=lambda: {})
    assert window._prepare_ocr_frame(np.ones((100, 200, 4), dtype=np.uint8)) == (None, (0, 0))


def test_legacy_full_frame_still_applies_exclusion_masks(window):
    frame = np.full((100, 200, 4), 255, dtype=np.uint8)
    window._config.set_exclusion_zones("Game", [{"x": 0, "y": 0, "width": 1, "height": 0.2}])
    masked, origin = window._prepare_ocr_frame(frame)
    assert origin == (0, 0)
    assert not masked[:20].any() and masked[20:].all()
    assert frame.all()


def test_crop_offsets_restore_both_preview_and_overlay_without_mutating_ocr_cache():
    worker = ProcessWorker(Config(source_language="English", output_mode="both"))
    worker.set_mode(OverlayMode.INPLACE)
    box = {"x": 5, "y": 8, "width": 90, "height": 22}
    worker._ocr = MagicMock()
    worker._ocr.extract_text_regions.return_value = [OCRResult("Hello world", box)]
    worker._translator = MagicMock()
    worker._translator.translate.return_value = ("Xin chào", False)
    raw, translated = [], []
    worker.ocr_results_ready.connect(lambda r, g: raw.append(r))
    worker.regions_ready.connect(lambda r, g: translated.append(r))
    for _ in range(2):
        worker._process_frame("cropped frame", 1, origin=(200, 480))
    expected = {**box, "x": 205, "y": 488}
    assert raw[-1][0].bbox == expected and translated[-1] == [["Xin chào", expected]]
    assert box == {"x": 5, "y": 8, "width": 90, "height": 22}


def test_editor_preview_performs_ocr_without_llm_or_speech():
    worker = ProcessWorker(Config(source_language="English", output_mode="both"))
    worker._ocr, worker._translator = MagicMock(), MagicMock()
    worker._ocr.extract_text_regions.return_value = [OCRResult("Hello world")]
    spoken, raw = [], []
    worker.speech_ready.connect(lambda text, generation: spoken.append(text))
    worker.ocr_results_ready.connect(lambda regions, generation: raw.append(regions))
    worker._process_frame("frame", 3, preview_only=True)
    worker._translator.translate.assert_not_called()
    assert raw and not spoken


@pytest.mark.parametrize("busy", [False, True])
@pytest.mark.parametrize(("accepted", "changed"), [(False, False), (True, False), (True, True)])
def test_unchanged_editor_does_not_forget_completed_speech(window, monkeypatch, busy, accepted, changed):
    from ludivoxa.gui import main_window

    window._capturing = True
    window._capture_generation = 3
    window._last_frame = np.zeros((80, 120, 4), dtype=np.uint8)
    window._last_ocr_results = []
    window._config.save = MagicMock()
    window._speech = SimpleNamespace(_busy=busy, stop=MagicMock())
    window._process_worker = MagicMock()
    dialog = MagicMock()
    dialog.exec.return_value = accepted
    dialog.get_zones.return_value = []
    dialog.get_confidence.return_value = window._config.get_ocr_confidence("Game")
    dialog.get_scan_region.return_value = REGION if changed else None
    monkeypatch.setattr(main_window, "OCRConfigDialog", MagicMock(return_value=dialog))
    window._open_ocr_config()
    resets = [call.kwargs["reset"] for call in window._speech.stop.call_args_list]
    assert resets == ([False, True] if accepted and changed and not busy else [busy])
    assert window._capture_generation == 5  # Stale preview results remain rejected.
    assert window._process_worker.discard_pending_frames.call_count == 2
    assert window._ocr_config_dialog is None


@pytest.fixture
def dialog(qapp):
    instance = OCRConfigDialog(initial_zones=[{"x": 0, "y": 0, "width": 1, "height": 0.2}])
    instance.update_frame(np.full((600, 1000, 4), 255, dtype=np.uint8))
    instance.show()
    QTest.qWait(10)
    yield instance
    instance.close()


def test_selected_mode_requires_box_and_preserves_hidden_exclusions(dialog):
    exclusions = dialog.get_zones()
    dialog._scan_mode.setCurrentIndex(1)
    assert dialog.get_scan_region() == {}
    assert not dialog._ok_btn.isEnabled()
    dialog.accept()
    assert dialog.result() == 0
    assert not dialog._zones[0].isVisible()
    dialog._use_bottom_box()
    assert dialog.get_scan_region() == pytest.approx({"x": 0, "y": 0.6, "width": 1, "height": 0.35})
    assert dialog._ok_btn.isEnabled()
    dialog._scan_mode.setCurrentIndex(0)
    assert dialog.get_scan_region() is None
    assert dialog.get_zones() == exclusions and dialog._zones[0].isVisible()


def test_drag_draw_move_resize_and_clear_selected_box(dialog):
    dialog._scan_mode.setCurrentIndex(1)
    view = dialog._view
    # Mouse positions are integer viewport pixels after the preview is scaled.
    pixel = 1 / (view.transform().m11() * min(dialog._preview_size))
    start, end = view.mapFromScene(100, 200), view.mapFromScene(600, 400)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert dialog.get_scan_region() == pytest.approx(
        {"x": 0.125, "y": 200 / 480, "width": 0.625, "height": 200 / 480}, abs=pixel
    )
    item = dialog._region_item
    center = view.mapFromScene(item.mapToScene(item.rect().center()))
    moved = center + view.mapFromScene(40, 20) - view.mapFromScene(0, 0)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseMove(view.viewport(), moved)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=moved)
    assert dialog.get_scan_region()["x"] == pytest.approx(0.175, abs=2 * pixel)
    corner = view.mapFromScene(item.mapToScene(item.rect().bottomRight()))
    larger = corner + view.mapFromScene(40, 20) - view.mapFromScene(0, 0)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=corner)
    QTest.mouseMove(view.viewport(), larger)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=larger)
    assert dialog.get_scan_region()["width"] == pytest.approx(0.675, abs=2 * pixel)
    dialog._clear_current()
    assert dialog.get_scan_region() == {} and not dialog._ok_btn.isEnabled()


@pytest.mark.parametrize("size", [(640, 360), (800, 450), (1024, 600)])
def test_small_remote_desktop_keeps_actions_clickable_and_selection_intact(qapp, monkeypatch, size):
    screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, *size))
    monkeypatch.setattr(OCRConfigDialog, "screen", lambda self: screen)
    instance = OCRConfigDialog(initial_region=REGION)
    try:
        instance.update_frame(np.full((1080, 1920, 4), 255, dtype=np.uint8))
        instance.show()
        QTest.qWait(10)
        assert instance.width() <= size[0] and instance.height() <= size[1]
        assert instance.get_scan_region() == pytest.approx(REGION)
        # Scrolling a large editor must never move its actions out of reach.
        scroll = instance._editor_scroll.verticalScrollBar()
        scroll.setValue(scroll.maximum())
        for button in (instance._clear_btn, instance._ok_btn, instance._cancel_btn):
            top_left = button.mapTo(instance, button.rect().topLeft())
            bounds = QRect(top_left, button.size())
            assert instance.rect().contains(bounds)
            assert bounds.bottom() < instance._editor_scroll.y()
            assert instance.childAt(bounds.center()) is button
        instance.resize(max(480, size[0] - 180), max(300, size[1] - 100))
        qapp.processEvents()
        assert instance.get_scan_region() == pytest.approx(REGION)
        QTest.mouseClick(instance._ok_btn, Qt.MouseButton.LeftButton)
        assert instance.result() == instance.DialogCode.Accepted
    finally:
        instance.close()


def test_numeric_selection_clips_and_saved_selection_scales_with_preview(dialog):
    dialog._scan_mode.setCurrentIndex(1)
    for key, value in {"x": 80, "y": 60, "width": 50, "height": 30}.items():
        dialog._region_fields[key].setValue(value)
    dialog._region_from_fields()
    expected = {"x": 0.8, "y": 0.6, "width": 0.2, "height": 0.3}
    assert dialog.get_scan_region() == pytest.approx(expected)
    dialog.update_frame(np.full((1000, 1000, 4), 255, dtype=np.uint8))
    assert dialog.get_scan_region() == pytest.approx(expected)


def test_saved_box_is_restored_on_first_frame(qapp):
    instance = OCRConfigDialog(initial_region=REGION)
    try:
        instance.update_frame(np.full((600, 1000, 4), 255, dtype=np.uint8))
        instance.apply_pending_zones()  # Repeated frame/explicit calls do not duplicate the box.
        assert instance.get_scan_region() == pytest.approx(REGION)
    finally:
        instance.close()


def test_drag_multiple_boxes_save_and_keep_gaps_out_of_ocr(dialog, window, tmp_path):
    dialog._scan_mode.setCurrentIndex(1)
    view = dialog._view
    for start, end in (((100, 100), (320, 200)), ((490, 270), (690, 380))):
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(*start))
        QTest.mouseMove(view.viewport(), view.mapFromScene(*end))
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(*end))
    regions = dialog.get_scan_region()
    assert len(regions) == 2
    window._config.set_ocr_region("Game", regions)
    path = tmp_path / "config.yml"
    window._config.save(str(path))
    window._config = Config.load(str(path))
    frame = np.full((600, 1000, 4), 255, dtype=np.uint8)
    cropped, (left, top) = window._prepare_ocr_frame(frame)
    assert cropped[160 - top, 150 - left].all()
    assert cropped[400 - top, 800 - left].all()
    assert not cropped[280 - top, 570 - left].any()
    assert frame.all()
    dialog._clear_current()
    assert dialog.get_scan_region() == regions[0]


@pytest.mark.skipif(
    not os.environ.get("LUDIVOXA_TEST_MODEL_ROOT") or os.name != "nt", reason="Local OCR and Windows fonts required"
)
@pytest.mark.parametrize(
    "language,text,font_name,recognizer,multiple_boxes",
    [
        ("English", "Welcome brave hero", "arial.ttf", "en_PP-OCRv5_mobile_rec_onnx", False),
        ("Japanese", "こんにちは勇者", "msgothic.ttc", "PP-OCRv5_mobile_rec_onnx", False),
        ("English", "Welcome brave hero", "arial.ttf", "en_PP-OCRv5_mobile_rec_onnx", True),
    ],
)
def test_real_selected_box_pipeline_ignores_outside_text_and_speaks_once(
    window, monkeypatch, language, text, font_name, recognizer, multiple_boxes
):
    from ludivoxa.paddle_ocr import IsolatedPaddleOCR

    root = Path(os.environ["LUDIVOXA_TEST_MODEL_ROOT"])
    engine = IsolatedPaddleOCR(
        PaddleSettings(str(root / "PP-OCRv5_mobile_det_onnx"), str(root / recognizer), max_side=640)
    )
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(Path("C:/Windows/Fonts") / font_name), 36)
    draw.text((40, 30), "Do not translate this menu", fill="black", font=font)
    draw.text((280, 570), text, fill="black", font=font)
    if multiple_boxes:
        draw.text((130, 270), "Hello traveller", fill="black", font=font)
        draw.text((260, 405), "Ignore gap text", fill="black", font=font)
    areas = [REGION, dict(x=0.08, y=0.30, width=0.4, height=0.14)] if multiple_boxes else REGION
    rgb = np.array(image)
    frame = np.concatenate((rgb[:, :, ::-1], np.full((800, 1200, 1), 255, dtype=np.uint8)), axis=2)
    window._config.set_ocr_region("Game", areas)
    window._config.source_language = language
    cropped, origin = window._prepare_ocr_frame(frame)
    worker = ProcessWorker(window._config)
    worker._ocr, worker._translator = engine, MagicMock()
    worker._translator.translate.return_value = ("Chào mừng dũng sĩ", False)
    spoken, boxes = [], []
    worker.speech_ready.connect(lambda text, generation: spoken.append(text))
    worker.ocr_results_ready.connect(lambda regions, generation: boxes.extend(r.bbox for r in regions))
    clock = [0.0]
    monkeypatch.setattr(
        "ludivoxa.gui.workers.time", SimpleNamespace(monotonic=lambda: clock[0], perf_counter=time.perf_counter)
    )
    try:
        engine.load()
        worker._process_frame(cropped, 1, origin)
        clock[0] = 0.4
        worker._process_frame(cropped, 1, origin)
        worker._process_frame(cropped, 1, origin)
        worker._translator.translate.assert_called_once_with(f"Hello traveller {text}" if multiple_boxes else text)
        assert spoken == ["Chào mừng dũng sĩ"]
        assert boxes and all(b["x"] >= origin[0] and b["y"] >= origin[1] for b in boxes)
    finally:
        engine.close()
