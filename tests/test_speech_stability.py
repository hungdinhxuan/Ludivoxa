"""Typewriter animation and OCR jitter must not produce repeated speech."""

from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from ludivoxa.config import Config, OutputMode, OverlayMode
from ludivoxa.gui.workers import ProcessWorker
from ludivoxa.ocr import OCRResult


@pytest.fixture
def dialogue(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("ludivoxa.gui.workers.time.monotonic", lambda: clock[0])
    worker = ProcessWorker(Config(source_language="Auto", output_mode="tts"))
    worker._ocr = MagicMock()
    worker._translator = MagicMock()
    worker._translator.translate.side_effect = lambda text: (f"VI: {text}", False)
    spoken = []
    worker.speech_ready.connect(lambda text, generation: spoken.append((text, generation)))

    def frame(text, at, generation=1, *, captured_at=None):
        clock[0] = at
        worker._ocr.extract_text_regions.return_value = [OCRResult(text)] if text else []
        worker._process_frame("frame", generation, captured_at=captured_at)

    return worker, frame, spoken


@pytest.mark.parametrize("text", ["Where are you going?", "どこへ行くの？"])
def test_waits_for_typewriter_text_and_translates_final_line_once(dialogue, text):
    worker, frame, spoken = dialogue
    for index in range(1, len(text) + 1):
        frame(text[:index], index * 0.05)
    assert spoken == []
    worker._translator.translate.assert_not_called()
    frame(text, len(text) * 0.05 + 0.4)
    assert spoken == []  # An observed reveal needs a longer quiet window.
    frame(text, len(text) * 0.05 + 1)
    assert spoken == [(f"VI: {text}", 1)]
    worker._translator.translate.assert_called_once_with(text)


def test_word_by_word_render_pauses_do_not_speak_partial_sentences(dialogue):
    worker, frame, spoken = dialogue
    for text, at in [("Hello", 0), ("Hello brave", 0.15), ("Hello brave", 0.55), ("Hello brave hero.", 0.7)]:
        frame(text, at)
    frame("Hello brave hero.", 1.1)
    assert spoken == []
    frame("Hello brave hero.", 1.8)
    assert spoken == [("VI: Hello brave hero.", 1)]
    worker._translator.translate.assert_called_once_with("Hello brave hero.")


def test_waiting_until_deadline_cannot_confirm_an_earlier_capture(dialogue):
    _, frame, spoken = dialogue
    frame("Wait.", 0.03, captured_at=0.0)
    frame("Wait.", 0.28, captured_at=0.25)
    frame("Wait.", 0.46, captured_at=0.25)
    assert not spoken
    frame("Wait. I heard a noise.", 0.53, captured_at=0.5)
    frame("Wait. I heard a noise.", 1.56, captured_at=1.0)
    assert not spoken
    frame("Wait. I heard a noise.", 1.78, captured_at=1.75)
    frame("Wait. I heard a noise.", 2.03, captured_at=2.0)
    assert spoken == [("VI: Wait. I heard a noise.", 1)]


def test_idle_capture_fallback_can_confirm_static_dialogue(dialogue):
    _, frame, spoken = dialogue
    frame("Still here.", 0.03, captured_at=0.0)
    frame("Still here.", 0.43, captured_at=0.0)
    assert not spoken
    frame("Still here.", 0.8)
    assert spoken == [("VI: Still here.", 1)]


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
    monkeypatch.setattr("ludivoxa.gui.workers.time.monotonic", lambda: clock[0])
    worker = ProcessWorker(Config(source_language="English", output_mode="tts"))
    worker._ocr = MagicMock()
    worker._translator = MagicMock()
    worker._translator.translate.side_effect = lambda text: (f"VI: {text}", False)
    spoken = []
    worker.speech_ready.connect(lambda text, generation: spoken.append(text))
    image = np.zeros((140, 460, 4), dtype=np.uint8)
    image[25:45, 20:380, :3] = np.random.default_rng(5).integers(0, 255, (20, 360, 3), dtype=np.uint8)
    box = {"x": 20, "y": 25, "width": 360, "height": 20}

    def observe(text, at, frame=None, extra=None, generation=1, origin=(0, 0)):
        clock[0] = at
        worker._ocr.extract_text_regions.return_value = ([OCRResult(text, box.copy())] if text else []) + (extra or [])
        worker._process_frame(image if frame is None else frame, generation, origin=origin)

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


@pytest.mark.parametrize("text", ["Wait by the gate.", "ここで待っていて。"])
@pytest.mark.parametrize("mode", [OutputMode.TTS, OutputMode.BOTH])
def test_ocr_oscillation_before_first_read_does_not_starve_visible_dialogue(visual_dialogue, text, mode):
    worker, observe, spoken, _, _ = visual_dialogue
    worker._config.output_mode = mode
    worker._config.source_language = "English" if text.isascii() else "Japanese"
    for i in range(25):
        observe(text if i % 2 == 0 else text + "V", i * 0.15)
    assert spoken == ["VI: " + text]


def test_missing_ocr_after_one_good_read_can_speak_unchanged_visible_text(visual_dialogue):
    _, observe, spoken, _, _ = visual_dialogue
    observe("Wait by the gate.", 0)
    observe("", 0.15)
    observe("", 0.5)
    observe("", 2.0)
    assert spoken == ["VI: Wait by the gate."]


def test_real_blank_before_settle_does_not_speak_removed_text(visual_dialogue):
    _, observe, spoken, image, _ = visual_dialogue
    observe("Wait by the gate.", 0)
    blank = np.zeros_like(image)
    observe("", 0.15, blank)
    observe("", 0.5, blank)
    observe("", 2.0, blank)
    assert spoken == []


def test_white_completion_arrow_does_not_starve_or_replay_dialogue(visual_dialogue):
    worker, observe, spoken, image, box = visual_dialogue
    plain = np.full_like(image, 20)
    cv2.putText(plain, "Wait by the gate.", (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (235,) * 3, 2)
    arrow = plain.copy()
    cv2.fillPoly(arrow, [np.array([[398, 29], [414, 29], [406, 41]])], (255,) * 4)
    assert worker._cursor_rectangles(arrow, {**box, "width": 400})
    for i in range(25):
        box["width"] = 400 if i % 2 else 360
        observe("Wait by the gate.V" if i % 2 else "Wait by the gate.", i * 0.15, arrow if i % 2 else plain)
    assert spoken == ["VI: Wait by the gate."]


@pytest.mark.parametrize("next_text", ["You need 20 gold.", "You do not need gold."])
def test_real_changed_dialogue_before_first_read_is_not_pinned_to_old_candidate(visual_dialogue, next_text):
    _, observe, spoken, image, _ = visual_dialogue

    def render(text):
        pixels = np.full_like(image, 20)
        cv2.putText(pixels, text, (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (235,) * 3, 2)
        return pixels

    observe("You need 10 gold.", 0, render("You need 10 gold."))
    changed = render(next_text)
    observe(next_text, 0.15, changed)
    observe(next_text, 0.6, changed)
    assert spoken == [f"VI: {next_text}"]


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
    observe("Hello world", 3, changed, extra)
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


@pytest.mark.parametrize("color", [(0, 0, 255, 255), (255,) * 4])
def test_colored_letter_is_not_treated_as_filled_cursor(visual_dialogue, color):
    worker, _, _, image, box = visual_dialogue
    letter = image.copy()
    cv2.polylines(letter, [np.array([[398, 29], [406, 41], [414, 29]])], False, color, 2)
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
    observe("Hello world V", 3, changed)
    assert len(spoken) == 2


def test_pokemon_ocr_variants_do_not_repeat_when_only_box_padding_animates(visual_dialogue):
    worker, observe, spoken, image, _ = visual_dialogue
    animated = image.copy()
    animated[10:18, 25:200, :3] = (10, 80, 180)
    for text, pixels, at in [
        ("Pokemon", image, 0),
        ("Pokamon", animated, 1),
        ("Pokemon", image, 2),
        ("Pokamon", animated, 3),
    ]:
        observe(text, at, pixels)
        observe(text, at + 0.4, pixels)
    assert spoken == ["VI: Pokemon"]
    worker._translator.translate.assert_called_once_with("Pokemon")


@pytest.mark.parametrize("effect", ["antialias", "colored_panel", "shift"])
@pytest.mark.parametrize("mode", [OutputMode.TTS, OutputMode.BOTH])
@pytest.mark.parametrize("origin", [(0, 0), (950, 1400)])
def test_pixel_font_render_changes_do_not_restart_pokemon_speech(visual_dialogue, effect, mode, origin):
    worker, observe, spoken, image, box = visual_dialogue
    worker._config.output_mode = mode
    box["y"], box["height"] = 20, 32  # Include ascenders/descenders and antialiased edges.
    first = np.full_like(image, 235)
    if effect == "colored_panel":
        first[:, :, :3] = (100, 40, 20)
    color = (235, 235, 235, 255) if effect == "colored_panel" else (15, 15, 15, 255)
    cv2.putText(first, "Pokemon are waiting.", (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
    changed = first.copy()
    if effect == "antialias":
        gray = first[:, :, 0]
        edges = (gray > 60) & (gray < 170)
        values = gray.astype(np.int16)
        values = np.where(values <= 125, values - 48, values + 48).clip(0, 255).astype(np.uint8)
        changed[edges, :3] = values[edges, None]
    elif effect == "colored_panel":
        changed[:, :, :3] -= 8
    else:
        changed = cv2.warpAffine(first, np.float32([[1, 0, 1], [0, 1, 1]]), (460, 140), borderValue=(235,) * 4)
    observe("Pokemon are waiting.", 0, first, origin=origin)
    observe("Pokemon are waiting.", 0.4, first, origin=origin)
    if effect == "shift":
        box["x"] += 1
        box["y"] += 1
    for text, at in [("Pokamon are waiting.", 1), ("Pokemon are waiting.", 2), ("Pokamon are waiting.", 3)]:
        observe(text, at, changed, origin=origin)
        observe(text, at + 0.4, changed, origin=origin)
    assert spoken == ["VI: Pokemon are waiting."]


@pytest.mark.parametrize(
    ("first_text", "next_text"),
    [
        ("Pokemon", "Pokamon"),
        ("HP: 10", "HP: 20"),
        ("Take the potion.", "Do not take the potion."),
    ],
)
def test_similar_but_visibly_different_dialogue_is_still_read(visual_dialogue, first_text, next_text):
    _, observe, spoken, image, _ = visual_dialogue
    for index, text in enumerate((first_text, next_text)):
        pixels = np.full_like(image, 235)
        cv2.putText(pixels, text, (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (15,) * 3, 2, cv2.LINE_AA)
        observe(text, index, pixels)
        observe(text, index + 0.4, pixels)
    assert spoken == [f"VI: {first_text}", f"VI: {next_text}"]


def test_proven_render_variant_is_cached_and_cache_resets_on_new_dialogue(visual_dialogue, monkeypatch):
    worker, observe, spoken, image, box = visual_dialogue
    box["y"], box["height"] = 20, 32
    first = np.full_like(image, 235)
    cv2.putText(first, "Pokemon", (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (15,) * 3, 2, cv2.LINE_AA)
    shifted = cv2.warpAffine(first, np.float32([[1, 0, 1], [0, 1, 1]]), (460, 140), borderValue=(235,) * 4)
    helper = MagicMock(wraps=ProcessWorker._same_pixel_text)
    monkeypatch.setattr(ProcessWorker, "_same_pixel_text", staticmethod(helper))
    observe("Pokemon", 0, first)
    observe("Pokemon", 0.4, first)
    comparison = MagicMock(wraps=worker._same_spoken_image)
    monkeypatch.setattr(worker, "_same_spoken_image", comparison)
    observe("Pokemon", 0.6, first)
    comparison.assert_not_called()
    observe("Pokamon", 1, shifted)
    comparison.assert_called_once()
    assert helper.call_count == 1
    for at in (1.4, 2, 2.4, 3, 3.4):
        observe("Pokamon", at, shifted)
    assert helper.call_count == 1 and len(spoken) == 1
    assert len(worker._spoken_image_cache) == 1
    second = np.full_like(image, 235)
    cv2.putText(second, "Welcome", (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (15,) * 3, 2, cv2.LINE_AA)
    observe("Welcome", 4, second)
    observe("Welcome", 4.4, second)
    observe("Welcome", 5, second)
    assert len(spoken) == 2 and not worker._spoken_image_cache
    observe("Pokemon", 6, first)
    observe("Pokemon", 6.4, first)
    assert len(spoken) == 3


@pytest.mark.parametrize("mode", [OutputMode.TTS, OutputMode.BOTH])
def test_ocr_variant_does_not_start_another_translation_speech_stream(visual_dialogue, mode):
    worker, observe, spoken, image, box = visual_dialogue
    worker._config.output_mode = mode
    box["y"], box["height"] = 20, 32
    first = np.full_like(image, 235)
    cv2.putText(first, "Pokemon", (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (15,) * 3, 2, cv2.LINE_AA)
    shaded = first.copy()
    shaded[:, :, :3] -= 8

    class Translator:
        def translate(self, text):
            return f"VI: {text}", False

        def translate_stream(self, text, on_sentence):
            on_sentence(f"VI: {text}")
            return f"VI: {text}", False

    worker._translator = Translator()
    streams = []
    worker.speech_stream_ready.connect(lambda stream, _: streams.append(stream))
    for text, pixels, at in [("Pokemon", first, 0), ("Pokamon", shaded, 1), ("Pokemon", first, 2)]:
        observe(text, at, pixels, origin=(950, 1400))
        observe(text, at + 0.4, pixels, origin=(950, 1400))
    assert len(streams) == 1 and list(streams[0]) == ["VI: Pokemon"]
    assert not spoken


def test_visual_text_growth_resets_settle_even_when_ocr_key_does_not_change(visual_dialogue):
    _, observe, spoken, image, box = visual_dialogue
    box["y"], box["height"] = 20, 32
    final = "Pokemon are waiting."
    for text, at in [("Pokemon", 0), ("Pokemon are", 0.2), (final, 0.4)]:
        pixels = np.full_like(image, 235)
        cv2.putText(pixels, text, (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (15,) * 3, 2)
        observe(final, at, pixels)
    observe(final, 0.8, pixels)
    assert not spoken
    observe(final, 1.3, pixels)
    assert spoken == [f"VI: {final}"]


@pytest.mark.parametrize("show_final_cursor", [False, True])
def test_learned_dialogue_cursor_defers_partial_next_page(visual_dialogue, show_final_cursor):
    _, observe, spoken, image, _ = visual_dialogue

    def rendered(text, cursor):
        pixels = np.full_like(image, 235)
        cv2.putText(pixels, text, (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (15,) * 3, 2)
        if cursor:
            cv2.fillPoly(pixels, [np.array([[398, 29], [414, 29], [406, 41]])], (0, 0, 255, 255))
        return pixels

    first = rendered("Hello world", True)
    observe("Hello world", 0, first)
    observe("Hello world", 0.4, first)
    assert len(spoken) == 1
    pending = rendered("Let us", False)
    observe("Let us", 1, pending)
    observe("Let us", 1.4, pending)
    assert len(spoken) == 1
    final = rendered("Let us begin.", show_final_cursor)
    observe("Let us begin.", 1.6, final)
    observe("Let us begin.", 2.0, final)
    assert len(spoken) == (2 if show_final_cursor else 1)
    if not show_final_cursor:
        observe("Let us begin.", 3.2, final)  # Cue-less labels never wait forever.
    assert len(spoken) == 2


def test_cursor_blink_before_first_read_does_not_restart_settle(visual_dialogue):
    _, observe, spoken, image, _ = visual_dialogue
    plain = np.full_like(image, 235)
    cv2.putText(plain, "Pokemon are waiting.", (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (15,) * 3, 2)
    cursor = plain.copy()
    cv2.fillPoly(cursor, [np.array([[398, 29], [414, 29], [406, 41]])], (0, 0, 255, 255))
    observe("Pokemon are waiting.T", 0, cursor)
    observe("Pokemon are waiting.", 0.15, plain)
    observe("Pokemon are waiting.T", 0.4, cursor)
    assert spoken == ["VI: Pokemon are waiting."]


@pytest.mark.parametrize(
    ("old", "new", "remaining"),
    [
        ("Hello brave hero.", "Hello brave hero. Find the key.", "Find the key."),
        ("Welcome professor. This is Pokemon.", "This is Pokemon. What is your name?", "What is your name?"),
    ],
)
def test_completed_source_overlap_reads_only_new_dialogue_tail(dialogue, old, new, remaining):
    worker, frame, spoken = dialogue
    frame(old, 0)
    frame(old, 0.4)
    frame(new, 1)
    frame(new, 3)
    assert spoken == [(f"VI: {old}", 1), (f"VI: {remaining}", 1)]
    assert worker._translator.translate.call_args.args[0] == remaining


def test_word_boxes_are_read_by_row_instead_of_detector_y_jitter():
    regions = [
        OCRResult("someday.", dict(x=320, y=20, width=90, height=30)),
        OCRResult("All", dict(x=20, y=26, width=35, height=24)),
        OCRResult("boys", dict(x=70, y=22, width=50, height=29)),
        OCRResult("leave", dict(x=140, y=30, width=65, height=21)),
        OCRResult("home", dict(x=225, y=28, width=65, height=24)),
        OCRResult("It said so on TV.", dict(x=20, y=70, width=280, height=30)),
    ]
    ordered = ProcessWorker._dialogue_regions(regions)
    assert " ".join(r.text for r in ordered) == "All boys leave home someday. It said so on TV."


def test_word_box_split_merge_does_not_repeat_unchanged_line(visual_dialogue):
    worker, _observe, spoken, image, _box = visual_dialogue
    first = np.full_like(image, 235)
    cv2.putText(first, "All boys leave home", (25, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (15,) * 3, 2)
    worker._ocr.extract_text_regions.return_value = [
        OCRResult("All", dict(x=20, y=25, width=35, height=25)),
        OCRResult("boys", dict(x=65, y=23, width=65, height=27)),
        OCRResult("leave home", dict(x=145, y=25, width=145, height=25)),
    ]
    clock = [0.0]
    from ludivoxa.gui import workers

    original_time = workers.time
    workers.time = type("Clock", (), {"monotonic": lambda: clock[0], "perf_counter": original_time.perf_counter})
    try:
        worker._process_frame(first, 1)
        clock[0] = 0.4
        worker._process_frame(first, 1)
        worker._ocr.extract_text_regions.return_value = [
            OCRResult("Al1 boys", dict(x=20, y=23, width=110, height=27)),
            OCRResult("leave", dict(x=145, y=24, width=60, height=26)),
            OCRResult("home", dict(x=220, y=25, width=70, height=25)),
        ]
        for at in (1.0, 1.5, 2.0, 3.0):
            clock[0] = at
            worker._process_frame(first, 1)
    finally:
        workers.time = original_time
    assert len(spoken) == 1


def test_vertical_text_keeps_engine_column_order():
    right = OCRResult("こんにちは", dict(x=220, y=20, width=20, height=120))
    left = OCRResult("世界", dict(x=180, y=20, width=20, height=80))
    assert ProcessWorker._dialogue_regions([right, left]) == [right, left]


def test_bouncing_cursor_shadow_is_masked_but_real_text_changes_are_not():
    worker = ProcessWorker(Config(source_language="English", output_mode="tts"))
    box = dict(x=20, y=30, width=500, height=100)

    def render(number, bounce):
        pixels = np.full((200, 800, 4), 235, dtype=np.uint8)
        cv2.putText(pixels, f"HP {number}", (350, 105), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (15,) * 3, 2)
        triangle = np.array([[550, 60], [600, 60], [575, 95]])
        triangle[:, 1] += bounce
        cv2.fillPoly(pixels, [triangle + np.array([0, 9])], (70, 70, 70, 255))
        cv2.fillPoly(pixels, [triangle], (0, 0, 255, 255))
        return pixels

    original = render(10, 0)
    worker._spoken_snapshot = worker._text_snapshot(original, [OCRResult("HP 10", box)])
    assert worker._same_spoken_image(render(10, 8), [OCRResult("HP 1O", box)])
    assert not worker._same_spoken_image(render(20, 8), [OCRResult("HP 20", box)])


def _outlined_dialogue(text, background_seed):
    image = np.full((150, 700, 4), 255, dtype=np.uint8)
    # A moving game scene remains visible underneath outlined dialogue.
    image[:, :, :3] = np.random.default_rng(background_seed).integers(24, 150, (150, 700, 3), dtype=np.uint8)
    cv2.putText(image, text, (25, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0, 255), 7, cv2.LINE_AA)
    cv2.putText(image, text, (25, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255,) * 4, 2, cv2.LINE_AA)
    return image


@pytest.mark.parametrize("mode", [OutputMode.TTS, OutputMode.BOTH])
def test_animated_game_background_and_stable_ocr_variants_do_not_reread_outlined_text(monkeypatch, mode):
    clock = [0.0]
    monkeypatch.setattr("ludivoxa.gui.workers.time.monotonic", lambda: clock[0])
    worker = ProcessWorker(Config(source_language="English", output_mode=mode))
    worker._ocr, worker._translator = MagicMock(), MagicMock()
    worker._translator.translate.side_effect = lambda text: ("VI: " + text, False)
    spoken = []
    worker.speech_ready.connect(lambda text, _: spoken.append(text))
    box = dict(x=20, y=45, width=620, height=40)
    visible = "Where are you going?"
    for index, recognized in enumerate((visible, "Where are you go1ng?", "Where are you goIng?", visible)):
        pixels = _outlined_dialogue(visible, index)
        worker._ocr.extract_text_regions.return_value = [OCRResult(recognized, box)]
        for delay in (0, 0.4, 1.4):
            clock[0] = index * 2 + delay
            worker._process_frame(pixels, 1)
    assert spoken == ["VI: " + visible]


@pytest.mark.parametrize("next_text", ["You need 20 gold.", "You need 100 gold.", "You need no gold."])
def test_real_outlined_text_changes_are_read_over_an_animated_background(monkeypatch, next_text):
    clock = [0.0]
    monkeypatch.setattr("ludivoxa.gui.workers.time.monotonic", lambda: clock[0])
    worker = ProcessWorker(Config(source_language="English", output_mode="tts"))
    worker._ocr, worker._translator = MagicMock(), MagicMock()
    worker._translator.translate.side_effect = lambda text: ("VI: " + text, False)
    spoken = []
    worker.speech_ready.connect(lambda text, _: spoken.append(text))
    for index, text in enumerate(("You need 10 gold.", next_text)):
        worker._ocr.extract_text_regions.return_value = [OCRResult(text, dict(x=20, y=45, width=620, height=40))]
        image = _outlined_dialogue(text, index)
        for delay in (0, 0.4, 1.4):
            clock[0] = index * 2 + delay
            worker._process_frame(image, 1)
    assert spoken == ["VI: You need 10 gold.", "VI: " + next_text]


def test_punctuation_only_detections_do_not_become_dialogue_image_baselines():
    body = OCRResult("Wait here.", dict(x=20, y=100, width=500, height=40))
    decoration = OCRResult("•", dict(x=220, y=40, width=20, height=20))
    assert ProcessWorker._dialogue_regions([decoration, body]) == [body]


@pytest.mark.parametrize("recognized", ["X", "Y", "I", "1", "II"])
@pytest.mark.parametrize("highlight", [False, True])
def test_detached_completion_arrow_recognized_as_a_letter_does_not_reread_dialogue(
    visual_dialogue, recognized, highlight
):
    _, observe, spoken, image, box = visual_dialogue
    box["height"] = 30
    arrow = image.copy()
    cv2.fillPoly(arrow, [np.array([[214, 100], [226, 100], [220, 108]])], (0, 210, 255, 255))
    if highlight:
        cv2.rectangle(arrow, (216, 96), (224, 100), (255,) * 4, -1)
    extra = [OCRResult(recognized, dict(x=212, y=96, width=16, height=14))]
    # This cursor is below the line, outside its remembered image patch.
    observe("Wait here.", 0)
    observe("Wait here.", 0.4)
    for at in (1.0, 1.5, 3.0):
        observe("Wait here.", at, arrow, extra)
    observe("Wait here.", 4)
    observe("Wait here.", 4.5)
    assert spoken == ["VI: Wait here."]


@pytest.mark.parametrize("text", ["X", "1", "No"])
def test_small_real_letters_near_a_completion_arrow_remain_readable(visual_dialogue, text):
    _, observe, spoken, image, _ = visual_dialogue
    changed = image.copy()
    cv2.putText(changed, text, (280, 108), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 210, 255, 255), 1)
    cv2.fillPoly(changed, [np.array([[214, 100], [226, 100], [220, 108]])], (0, 210, 255, 255))
    extra = [OCRResult(text, dict(x=278, y=96, width=20, height=12))]
    observe("Wait here.", 0)
    observe("Wait here.", 0.4)
    observe("Wait here.", 1, changed, extra)
    observe("Wait here.", 1.5, changed, extra)
    observe("Wait here.", 3, changed, extra)
    assert len(spoken) == 2
