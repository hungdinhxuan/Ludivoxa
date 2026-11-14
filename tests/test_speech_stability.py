"""Typewriter animation and OCR jitter must not produce repeated speech."""

from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from interpreter.config import Config, OutputMode, OverlayMode
from interpreter.gui.workers import ProcessWorker
from interpreter.ocr import OCRResult


@pytest.fixture
def dialogue(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("interpreter.gui.workers.time.monotonic", lambda: clock[0])
    worker = ProcessWorker(Config(source_language="Auto", output_mode="tts"))
    worker._ocr = MagicMock()
    worker._translator = MagicMock()
    worker._translator.translate.side_effect = lambda text: (f"VI: {text}", False)
    spoken = []
    worker.speech_ready.connect(lambda text, generation: spoken.append((text, generation)))

    def frame(text, at, generation=1):
        clock[0] = at
        worker._ocr.extract_text_regions.return_value = [OCRResult(text)] if text else []
        worker._process_frame("frame", generation)

    return worker, frame, spoken


@pytest.mark.parametrize("text", ["Where are you going?", "どこへ行くの？"])
def test_waits_for_typewriter_text_and_translates_final_line_once(dialogue, text):
    worker, frame, spoken = dialogue
    for index in range(1, len(text) + 1):
        frame(text[:index], index * 0.05)
    assert spoken == []
    worker._translator.translate.assert_not_called()
    frame(text, len(text) * 0.05 + 0.4)
    frame(text, len(text) * 0.05 + 1)
    assert spoken == [(f"VI: {text}", 1)]
    worker._translator.translate.assert_called_once_with(text)


def test_ocr_flicker_and_punctuation_do_not_repeat_spoken_source(dialogue):
    worker, frame, spoken = dialogue
    frame("Hello world!", 0)
    frame("Hello world!", 0.4)
    frame("", 0.5)
    frame("He11o world", 0.6)
    frame(" hello   WORLD... ", 0.7)
    frame("hello WORLD", 1.2)
    assert len(spoken) == 1
    assert worker._translator.translate.call_count == 1


def test_same_line_can_be_read_after_other_dialogue_or_new_capture(dialogue):
    _, frame, spoken = dialogue
    for i, text in enumerate(("Hello world", "Goodbye world", "Hello world")):
        frame(text, i)
        frame(text, i + 0.4)
    assert len(spoken) == 3
    frame("Hello world", 4, generation=2)
    frame("Hello world", 4.4, generation=2)
    assert len(spoken) == 4 and spoken[-1][1] == 2


def test_same_line_after_sustained_blank_is_new_dialogue(dialogue):
    _, frame, spoken = dialogue
    frame("Hello world", 0)
    frame("Hello world", 0.4)
    frame("", 0.5)
    frame("", 2.1)
    frame("Hello world", 2.2)
    frame("Hello world", 2.6)
    assert len(spoken) == 2


def test_failed_translation_can_retry_without_marking_line_spoken(dialogue):
    worker, frame, spoken = dialogue
    worker._translator.translate.side_effect = RuntimeError("offline")
    frame("Hello world", 0)
    frame("Hello world", 0.4)
    assert spoken == []
    worker._translator.translate.side_effect = None
    worker._translator.translate.return_value = ("Xin chào", False)
    frame("Hello world", 0.8)
    assert spoken == [("Xin chào", 1)]


def test_overlay_updates_immediately_and_speech_waits_for_stable_source(dialogue):
    worker, frame, spoken = dialogue
    worker._config.output_mode = OutputMode.BOTH
    frame("Hello world", 0)
    assert worker._translator.translate.call_count == 1
    assert spoken == []
    frame("Hello world", 0.4)
    frame("hello WORLD!", 0.8)
    assert len(spoken) == 1


def test_enabling_speech_and_changing_voice_resets_dialogue_guard(dialogue):
    worker, frame, spoken = dialogue
    worker._config.output_mode = OutputMode.OVERLAY
    frame("Hello world", 0)
    frame("Hello world", 0.4)
    assert spoken == []
    worker._config.output_mode = OutputMode.TTS
    frame("Hello world", 0.5)
    frame("Hello world", 0.9)
    assert len(spoken) == 1
    worker._config.tts_speaker_id = 1
    frame("Hello world", 1)
    frame("Hello world", 1.4)
    assert len(spoken) == 2


@pytest.fixture
def visual_dialogue(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("interpreter.gui.workers.time.monotonic", lambda: clock[0])
    worker = ProcessWorker(Config(source_language="English", output_mode="tts"))
    worker._ocr = MagicMock()
    worker._translator = MagicMock()
    worker._translator.translate.side_effect = lambda text: (f"VI: {text}", False)
    spoken = []
    worker.speech_ready.connect(lambda text, generation: spoken.append(text))
    image = np.zeros((140, 460, 4), dtype=np.uint8)
    image[25:45, 20:380, :3] = np.random.default_rng(5).integers(0, 255, (20, 360, 3), dtype=np.uint8)
    box = {"x": 20, "y": 25, "width": 360, "height": 20}

    def observe(text, at, frame=None, extra=None, generation=1):
        clock[0] = at
        worker._ocr.extract_text_regions.return_value = ([OCRResult(text, box.copy())] if text else []) + (extra or [])
        worker._process_frame(image if frame is None else frame, generation)

    return worker, observe, spoken, image, box


@pytest.mark.parametrize("mode", [OutputMode.TTS, OutputMode.BOTH])
def test_stable_ocr_typo_does_not_repeat_unchanged_visible_dialogue(visual_dialogue, mode):
    worker, observe, spoken, image, _ = visual_dialogue
    worker._config.output_mode = mode
    if mode == OutputMode.BOTH:
        worker._mode = OverlayMode.INPLACE
    observe("Where are you going?", 0)
    observe("Where are you going?", 0.4)
    animated = image.copy()
    animated[110:130, 400:440, :3] = 255  # Background animation outside the dialogue.
    observe("Where are you go1ng?", 1, animated)
    observe("Where are you go1ng?", 1.5, animated)
    observe("Where are you going?", 2)
    observe("Where are you going?", 2.5)
    assert len(spoken) == 1
    if mode == OutputMode.TTS:
        worker._translator.translate.assert_called_once()


def test_empty_ocr_on_unchanged_text_pixels_does_not_reset_spoken_guard(visual_dialogue):
    _, observe, spoken, _, _ = visual_dialogue
    observe("Hello world", 0)
    observe("Hello world", 0.4)
    observe("", 0.5)
    observe("", 2.2)
    observe("Hello world", 2.3)
    observe("Hello world", 2.8)
    assert len(spoken) == 1


def test_real_single_character_change_in_visible_text_is_read(visual_dialogue):
    _, observe, spoken, image, _ = visual_dialogue
    observe("You need 10 gold to open this gate.", 0)
    observe("You need 10 gold to open this gate.", 0.4)
    changed = image.copy()
    changed[30, 40, 0] ^= 255
    observe("You need 20 gold to open this gate.", 1, changed)
    observe("You need 20 gold to open this gate.", 1.4, changed)
    assert len(spoken) == 2


def test_new_text_outside_old_boxes_is_not_hidden_by_visual_guard(visual_dialogue):
    _, observe, spoken, image, _ = visual_dialogue
    observe("Hello world", 0)
    observe("Hello world", 0.4)
    changed = image.copy()
    changed[90:110, 20:200, :3] = 255
    extra = [OCRResult("New quest unlocked", {"x": 20, "y": 90, "width": 180, "height": 20})]
    observe("Hello world", 1, changed, extra)
    observe("Hello world", 1.4, changed, extra)
    assert len(spoken) == 2


def test_real_blank_and_new_capture_allow_same_line_again(visual_dialogue):
    _, observe, spoken, image, _ = visual_dialogue
    observe("Hello world", 0)
    observe("Hello world", 0.4)
    blank = np.zeros_like(image)
    observe("", 0.5, blank)
    observe("", 2.2, blank)
    observe("Hello world", 2.3)
    observe("Hello world", 2.8)
    assert len(spoken) == 2
    observe("Hello world", 3, generation=2)
    observe("Hello world", 3.4, generation=2)
    assert len(spoken) == 3


def test_small_detached_cursor_does_not_change_the_spoken_dialogue(visual_dialogue):
    worker, observe, spoken, _, _ = visual_dialogue
    cursor_box = {"x": 220, "y": 90, "width": 8, "height": 8}
    observe("Hello world", 0, extra=[OCRResult("V", cursor_box)])
    observe("Hello world", 0.4, extra=[OCRResult("V", cursor_box)])
    observe("Hello world", 1, extra=[OCRResult("√", cursor_box)])
    observe("Hello world", 1.5)
    assert spoken == ["VI: Hello world"]
    worker._translator.translate.assert_called_once_with("Hello world")
    # A normal-size letter and a one-letter answer remain readable.
    letter = OCRResult("V", {"x": 400, "y": 25, "width": 15, "height": 20})
    main = OCRResult("Hello world", {"x": 20, "y": 25, "width": 360, "height": 20})
    assert worker._dialogue_regions([main, letter]) == [main, letter]
    assert worker._dialogue_regions([letter]) == [letter]


@pytest.mark.parametrize("arrow_first", [False, True])
def test_merged_red_cursor_does_not_repeat_whole_line(visual_dialogue, arrow_first):
    _, observe, spoken, image, box = visual_dialogue
    arrow = image.copy()
    cv2.fillPoly(arrow, [np.array([[398, 29], [414, 29], [406, 41]])], (0, 0, 255, 255))
    states = [("Where are you going?", image, 360), ("Where are you going?T", arrow, 400)]
    if arrow_first:
        states.reverse()
    for i, (text, pixels, width) in enumerate(states):
        box["width"] = width
        observe(text, i, pixels)
        observe(text, i + 0.4, pixels)
    assert len(spoken) == 1


def test_colored_letter_is_not_treated_as_filled_cursor(visual_dialogue):
    worker, _, _, image, box = visual_dialogue
    letter = image.copy()
    cv2.polylines(letter, [np.array([[398, 29], [406, 41], [414, 29]])], False, (0, 0, 255, 255), 2)
    assert worker._cursor_rectangles(letter, box) == []


@pytest.mark.parametrize("line_type", [cv2.LINE_8, cv2.LINE_AA])
def test_background_shading_and_ocr_typo_do_not_repeat_glyphs(visual_dialogue, line_type):
    _, observe, spoken, image, _ = visual_dialogue
    first = image.copy()
    first[15:65, 10:430, :3] = 235
    cv2.putText(first, "Hello world", (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (15, 15, 15), 2, line_type)
    shaded = first.copy()
    shaded[15:65, 10:430, :3] -= 8
    for text, pixels, at in [("Hello world", first, 0), ("Hello worid", shaded, 1), ("Hello world", first, 2)]:
        observe(text, at, pixels)
        observe(text, at + 0.4, pixels)
    assert len(spoken) == 1


def test_changed_numeric_glyphs_remain_readable_under_shading(visual_dialogue):
    _, observe, spoken, image, _ = visual_dialogue
    for index, number in enumerate((10, 20)):
        pixels = image.copy()
        pixels[15:65, 10:430, :3] = 235 - index * 8
        cv2.putText(pixels, f"You need {number} gold", (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (15, 15, 15), 2)
        observe(f"You need {number} gold", index, pixels)
        observe(f"You need {number} gold", index + 0.4, pixels)
    assert len(spoken) == 2


def test_crop_to_full_scan_box_growth_does_not_repeat_shaded_dialogue(visual_dialogue):
    _, observe, spoken, image, box = visual_dialogue
    frame = np.full_like(image, 235)
    cv2.putText(frame, "Hello world", (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (15, 15, 15, 255), 2, cv2.LINE_AA)
    observe("Hello world", 0, frame)
    observe("Hello world", 0.4, frame)
    shaded = frame.copy()
    shaded[:, :, :3] -= 8
    # Full-frame detector can return a taller box than the previous crop scan
    # for exactly the same glyphs (reproduced with the real JP recognizer).
    box["y"], box["height"] = 23, 35
    observe("Hello worid", 1, shaded)
    observe("Hello worid", 1.4, shaded)
    assert len(spoken) == 1


@pytest.mark.parametrize("color", [(0, 0, 255, 255), (80, 80, 80, 255)])
def test_new_colored_or_dim_glyph_is_not_mistaken_for_background_shading(visual_dialogue, color):
    _, observe, spoken, image, _ = visual_dialogue
    first = np.zeros_like(image)
    cv2.putText(first, "Hello world", (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (235, 235, 235, 255), 2)
    changed = first.copy()
    cv2.putText(changed, " V", (170, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
    observe("Hello world", 0, first)
    observe("Hello world", 0.4, first)
    observe("Hello world V", 1, changed)
    observe("Hello world V", 1.4, changed)
    assert len(spoken) == 2
