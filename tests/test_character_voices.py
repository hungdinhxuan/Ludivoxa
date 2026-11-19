"""Per-game speaker routing without an extra LLM or a voice-setting reset."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from interpreter.config import CharacterVoices, Config, LLMSettings, TranslationBackend, character_name
from interpreter.gui.character_voices import CharacterVoicesDialog
from interpreter.gui.main_window import MainWindow
from interpreter.gui.workers import ProcessWorker
from interpreter.ocr import OCRResult
from interpreter.speech import SpeechController, SpeechStream

REGION = dict(x=0.1, y=0.1, width=0.3, height=0.1)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def test_legacy_config_and_per_game_assignments_roundtrip(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("tts_speaker_id: 7\n", encoding="utf8")
    config = Config.load(str(path))
    assert not config.get_character_voices("Game").enabled
    config.save()
    assert "character_voices" not in path.read_text(encoding="utf8")
    profile = CharacterVoices(True, REGION, "voice", {"Alice": 1, "ボブ": 2})
    config.set_character_voices("Game", profile)
    config.save()
    profile.voices["Alice"] = 9
    restored = Config.load(str(path))
    assert restored.get_character_voices("Game").voices == {"Alice": 1, "ボブ": 2}
    assert not restored.get_character_voices("Other game").enabled
    assert restored.tts_speaker_id == 7
    copy = restored.get_character_voices("Game")
    copy.voices.clear()
    assert restored.get_character_voices("Game").voices


def test_invalid_profiles_are_safe_and_names_are_not_gender_guesses():
    profile = CharacterVoices.from_dict(
        {"enabled": "yes", "name_region": {}, "voices": {"Alice": -1, "Bob": True, "Eve": "2"}}
    )
    assert not profile.enabled and profile.name_region is None
    assert profile.voices == {"Eve": 2}
    assert character_name("  「ＡＬＩＣＥ：」 ") == "ALICE"
    profile.model_path = "voice"
    assert profile.speaker_for("EVE:", "voice") == 2
    assert profile.speaker_for("Unknown", "voice") is None
    assert profile.speaker_for("Eve", "another-model") is None


@pytest.fixture
def routed_worker(qapp, monkeypatch):
    from interpreter.gui import workers

    clock = [0.0]
    monkeypatch.setattr(workers, "time", SimpleNamespace(monotonic=lambda: clock[0], perf_counter=time.perf_counter))
    monkeypatch.setattr(workers, "voice_details", lambda _: [("Default", 0), ("One", 1), ("Two", 2)])
    config = Config(
        source_language="English", output_mode="tts", tts_model_path="voice", tts_reference_audio="default.wav"
    )
    profile = CharacterVoices(True, REGION, "voice", {"Alice": 1, "Bob": 2})
    worker = ProcessWorker(config)
    worker._ocr, worker._translator = MagicMock(), MagicMock()
    worker._translator.translate.side_effect = lambda text: ("VI: " + text, False)
    streams, names, name_calls = [], [], []
    worker.speech_stream_ready.connect(lambda stream, _: streams.append(stream))
    worker.character_detected.connect(lambda *args: names.append(args))
    current = [""]
    labels = {20: "Alice", 40: "Bob", 60: "Eve", 0: ""}

    def ocr(frame):
        if isinstance(frame, np.ndarray) and frame.shape[:2] == (60, 200):
            name_calls.append(frame.copy())
            label = labels[int(frame[0, 0, 0])]
            return [OCRResult(label, dict(x=5, y=5, width=180, height=40))] if label else []
        return [OCRResult(current[0], dict(x=15, y=15, width=450, height=40))]

    worker._ocr.extract_text_regions.side_effect = ocr

    def observe(text, name="Alice", at=0.0, generation=1, use_profile=True, *, captured_at=None):
        clock[0], current[0] = at, text
        pixels = np.full((60, 200, 4), (next(k for k, v in labels.items() if v == name),) * 4, dtype=np.uint8)
        if name:
            cv2.putText(pixels, name, (8, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (235,) * 4, 2)
        body = np.full((100, 500, 4), 20, dtype=np.uint8)
        cv2.putText(body, text, (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235,) * 4, 2)
        worker._process_frame(
            body,
            generation,
            speaker_context=("Game", profile, pixels) if use_profile else None,
            captured_at=captured_at,
        )

    return worker, profile, observe, streams, names, name_calls, labels


def test_names_select_presets_without_changing_global_voice(routed_worker):
    worker, _, observe, streams, names, _, _ = routed_worker
    for index, (name, line) in enumerate((("Alice", "Hello."), ("Bob", "Come here."))):
        for delta in (0.0, 0.15, 0.3, 0.45):
            observe(line, name, index + delta)
    assert [(s.speaker_name, s.speaker_id, s.reference_audio) for s in streams] == [("Alice", 1, ""), ("Bob", 2, "")]
    assert [list(s) for s in streams] == [["VI: Hello."], ["VI: Come here."]]
    assert worker._config.tts_speaker_id == 0 and worker._config.tts_reference_audio == "default.wav"
    assert names[-1] == ("Bob", True, 1)


def test_new_confirmed_speaker_can_say_identical_words_once(routed_worker):
    _, _, observe, streams, _, _, _ = routed_worker
    for index, name in enumerate(("Alice", "Bob")):
        for delta in (0.0, 0.15, 0.3, 0.45, 0.6):
            observe("Yes.", name, index + delta)
    assert [s.speaker_id for s in streams] == [1, 2]


def test_native_name_samples_settle_by_capture_time_without_repeating_ocr(routed_worker):
    _, _, observe, streams, names, name_calls, _ = routed_worker
    observe("Hello.", at=0.10, captured_at=0.0)
    observe("Hello.", at=0.26, captured_at=0.24)
    assert names == [("Alice", True, 1)]
    assert len(name_calls) == 1 and not streams
    observe("Hello.", at=0.51, captured_at=0.5)
    assert len(streams) == 1 and streams[0].speaker_id == 1


def test_name_ocr_jitter_on_unchanged_pixels_cannot_replay_a_line(routed_worker):
    _, _, observe, streams, _, calls, labels = routed_worker
    for delta in (0.0, 0.15, 0.3, 0.45):
        observe("Wait here.", at=delta)
    labels[20] = "Bob"  # Simulated OCR disagreement; the visible name did not change.
    for index in range(4, 40):
        # Keep the same name pixels even though the engine would now report Bob.
        labels[20] = "Alice"
        observe("Wait here.", at=index * 0.15)
        labels[20] = "Bob"
    assert len(streams) == 1 and len(calls) == 1


@pytest.mark.parametrize("name", ["", "Eve"])
def test_narration_and_unknown_names_keep_default_clone(routed_worker, name):
    _, _, observe, streams, _, _, _ = routed_worker
    for delta in (0.0, 0.15, 0.3, 0.45):
        observe("A new line.", name, delta)
    assert len(streams) == 1
    assert streams[0].speaker_id == 0 and streams[0].reference_audio == "default.wav"


def test_editing_a_mapping_only_affects_future_dialogue(routed_worker):
    worker, profile, observe, streams, _, _, _ = routed_worker
    for delta in (0.0, 0.15, 0.3, 0.45):
        observe("Hello.", at=delta)
    profile.voices["Alice"] = 2
    for delta in (1.0, 1.3, 1.6):
        observe("Hello.", at=delta)
    assert len(streams) == 1 and streams[0].speaker_id == 1
    for delta in (2.0, 2.3, 2.6):
        observe("Goodbye.", at=delta)
    assert [s.speaker_id for s in streams] == [1, 2]
    assert worker._config.tts_speaker_id == 0


def test_late_initial_name_does_not_replay_already_spoken_text(routed_worker):
    _, _, observe, streams, _, _, _ = routed_worker
    for delta in (0.0, 0.15, 0.3, 0.45):
        observe("Hello.", "", delta)
    for delta in (1.0, 1.3, 1.6):
        observe("Hello.", "Alice", delta)
    assert len(streams) == 1


@pytest.mark.parametrize("previous_character", [False, True])
def test_flickering_name_cannot_starve_dialogue_or_reuse_previous_voice(routed_worker, previous_character):
    _, _, observe, streams, _, _, _ = routed_worker
    if previous_character:
        for at in (0.0, 0.3, 0.6):
            observe("Earlier line.", "Alice", at)
    for index in range(6):
        observe("Speak now.", "Bob" if index % 2 else "Eve", 1 + index * 0.3)
    assert len(streams) == (2 if previous_character else 1)
    assert streams[-1].speaker_name == "" and streams[-1].speaker_id == 0
    assert streams[-1].reference_audio == "default.wav"
    for at in (3.0, 3.3, 3.6):
        observe("Speak now.", "Bob", at)
    assert len(streams) == (2 if previous_character else 1)


@pytest.fixture
def main(qapp):
    win = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(win)
    win._config = Config(
        source_language="English",
        output_mode="tts",
        translation_backend=TranslationBackend.LLM,
        llm=LLMSettings(target_language="Vietnamese"),
        tts_speaker_id=7,
        tts_reference_audio="default.wav",
    )
    win._current_window_title = "Game"
    win._ocr_config_dialog = None
    win._capturing, win._capture_generation = True, 3
    win._speech = MagicMock()
    win._character_dialog = None
    win._character_status = QLabel()
    win._preview_pixmap = None
    yield win
    win.close()


@pytest.mark.parametrize("capturing", [True, False])
def test_character_editor_uses_active_capture_or_selected_source(main, monkeypatch, capturing):
    from interpreter.gui import main_window

    main._capturing = capturing
    main._detected_character = "Alice"
    main._last_frame = object()
    main._config.window_title = "Previously played game"
    main._windows_list = [{"title": "Another game"}]
    main._window_combo = QComboBox(main)
    main._window_combo.addItem("Another game (800x600)", ("window", 0))
    dialog = MagicMock()
    dialog.exec.return_value = main_window.QDialog.DialogCode.Accepted
    dialog.get_profile.return_value = CharacterVoices(True, REGION, "voice", {"Bob": 2})
    factory = MagicMock(return_value=dialog)
    monkeypatch.setattr(main_window, "CharacterVoicesDialog", factory)
    monkeypatch.setattr(main._config, "save", MagicMock())

    main._open_character_voices()

    title = "Game" if capturing else "Another game"
    assert factory.call_args.args[1] == title
    assert factory.call_args.kwargs["frame"] is (main._last_frame if capturing else None)
    assert factory.call_args.kwargs["detected_name"] == ("Alice" if capturing else "")
    assert main._config.get_character_voices(title).voices == {"Bob": 2}
    assert not main._config.get_character_voices("Previously played game").enabled


def test_name_crop_reads_original_pixels_even_when_excluded_from_dialogue(main):
    image = np.full((200, 400, 4), 255, dtype=np.uint8)
    main._config.set_character_voices("Game", CharacterVoices(True, REGION, "voice", {"Alice": 1}))
    main._config.set_exclusion_zones("Game", [REGION])
    dialogue, origin = main._prepare_ocr_frame(image)
    dialogue, context = main._prepare_character_ocr(image, dialogue, origin)
    assert context[2].shape[:2] == (20, 120) and context[2].all()
    assert not dialogue[20:40, 40:160].any() and image.all()


def test_name_crop_remains_available_outside_selected_dialogue_box(main):
    image = np.full((200, 400, 4), 255, dtype=np.uint8)
    main._config.set_character_voices("Game", CharacterVoices(True, REGION, "voice", {"Alice": 1}))
    main._config.set_ocr_region("Game", dict(x=0.2, y=0.6, width=0.7, height=0.3))
    dialogue, origin = main._prepare_ocr_frame(image)
    prepared, context = main._prepare_character_ocr(image, dialogue, origin)
    assert np.shares_memory(prepared, dialogue)
    assert context[2].shape[:2] == (20, 120)
    assert origin == (80, 120)


def test_gui_uses_frozen_voice_and_rejects_stale_generation(main):
    stream = SpeechStream(key="line", speaker_id=2, speaker_name="Alice", reference_audio="")
    main._on_speech_ready(stream, 3)
    assert main._speech.submit.call_args.args[2] == 2
    assert main._speech.submit.call_args.kwargs["reference_audio"] == ""
    assert main._config.tts_speaker_id == 7
    stale = SpeechStream(key="old", speaker_id=1)
    main._on_speech_ready(stale, 2)
    assert stale._cancelled.is_set() and main._speech.submit.call_count == 1


def test_gui_discards_automatic_voice_bound_before_a_manual_model_change(main):
    stream = SpeechStream(
        key="old", speaker_id=2, speaker_name="Alice", voice_settings=("old-model", 7, 1.0, "default.wav")
    )
    main._config.tts_model_path = "new-model"
    main._on_speech_ready(stream, 3)
    assert stream._cancelled.is_set()
    main._speech.submit.assert_not_called()


def test_model_mismatch_and_invalid_preset_use_default_voice(routed_worker):
    _, profile, observe, streams, _, _, _ = routed_worker
    profile.voices["Alice"] = 999
    for at in (0.0, 0.3, 0.6):
        observe("Hello.", at=at)
    assert streams[-1].speaker_id == 0 and streams[-1].reference_audio == "default.wav"
    profile.model_path = "another-model"
    for at in (1.0, 1.3, 1.6):
        observe("Goodbye.", at=at)
    assert streams[-1].speaker_id == 0


class Player(QObject):
    finished = Signal()
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.play = MagicMock()
        self.stop = MagicMock()


def wait_until(predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        QTest.qWait(5)
        time.sleep(0.002)
    assert predicate()


def test_identical_translation_uses_each_characters_preset_without_interrupting(qapp):
    synth, player = MagicMock(), Player()
    calls = []

    def audio(text, model, speaker, *args, **kwargs):
        calls.append(speaker)
        yield text.encode()

    synth.iter_audio.side_effect = audio
    controller = SpeechController(synthesizer=synth, player=player)

    def submit(name, speaker):
        stream = SpeechStream(key="yes", speaker_name=name, speaker_id=speaker, reference_audio="")
        stream.put("Vâng.")
        stream.finish()
        controller.submit(stream, "voice", speaker)

    try:
        submit("Alice", 1)
        wait_until(lambda: player.play.call_count == 1)
        assert list(player.play.call_args.args[0]) == ["Vâng.".encode()]
        wait_until(lambda: not controller._working)
        submit("Bob", 2)
        wait_until(lambda: not controller._working)
        assert player.play.call_count == 1  # Alice is still playing.
        player.finished.emit()
        assert player.play.call_count == 2
        assert list(player.play.call_args.args[0]) == ["Vâng.".encode()]
        assert calls == [1, 2]
        player.stop.assert_not_called()
    finally:
        controller.close()


def test_complete_ocr_cached_llm_gui_and_speech_route_same_words_to_two_voices(routed_worker, main):
    from interpreter.llm_translate import LLMTranslator

    worker, _, observe, _, _, _, _ = routed_worker
    worker._config.translation_backend = TranslationBackend.LLM
    client = LLMTranslator(worker._config.llm, source_language="English")
    client._loaded = True
    for name in ("Alice", "Bob"):
        client._cache.put("Yes.", "Vâng.", scope=(name, "unknown"))
    client._session.post = MagicMock(side_effect=AssertionError("No extra LLM call is allowed"))
    worker._translator = client
    synth, player = MagicMock(), Player()
    synth.iter_audio.side_effect = lambda text, *_args, **_kwargs: iter([text.encode()])
    controller = SpeechController(synthesizer=synth, player=player)
    main._config, main._speech, main._capture_generation = worker._config, controller, 1
    worker.speech_stream_ready.connect(main._on_speech_ready)
    try:
        for index, name in enumerate(("Alice", "Bob")):
            for delta in (0.0, 0.15, 0.3, 0.45, 0.6):
                observe("Yes.", name, index + delta)
            wait_until(lambda count=index + 1: player.play.call_count == count)
            assert list(player.play.call_args.args[0]) == ["Vâng.".encode()]
            wait_until(lambda: not controller._working)
            player.finished.emit()
        assert [call.args[2] for call in synth.iter_audio.call_args_list] == [1, 2]
        assert worker._config.tts_speaker_id == 0
        client._session.post.assert_not_called()
    finally:
        controller.close()
        client._session.close()


def test_character_metadata_from_preset_and_override_reaches_overlay_translation(routed_worker, monkeypatch):
    import json

    from interpreter.config import OutputMode
    from interpreter.gui import workers
    from interpreter.llm_translate import LLMTranslator

    worker, profile, observe, _, _, _, _ = routed_worker
    worker._config.output_mode = OutputMode.OVERLAY
    profile.genders = {"bob": "nonbinary"}
    monkeypatch.setattr(workers, "voice_gender", lambda _, label: {"One": "female", "Two": "male"}.get(label))
    client = LLMTranslator(worker._config.llm, source_language="English")
    client._loaded = True
    client._chat = MagicMock(side_effect=["Vâng.", "Được rồi."])
    worker._translator = client
    try:
        for index, name in enumerate(("Alice", "Bob")):
            for delta in (0.0, 0.15, 0.3, 0.45):
                observe("Yes.", name, index + delta)
        payloads = [json.loads(call.args[0][-1]["content"]) for call in client._chat.call_args_list]
        assert payloads == [
            {"speaker": "Alice", "gender": "female", "dialogue": "Yes."},
            {"speaker": "Bob", "gender": "nonbinary", "dialogue": "Yes."},
        ]
    finally:
        client._session.close()


def test_dialog_requires_name_area_and_saves_assignments_without_mutating_input(qapp, monkeypatch):
    from interpreter.gui import character_voices

    monkeypatch.setattr(character_voices, "voice_details", lambda _: [("One", 1), ("Two", 2)])
    original = CharacterVoices()
    dialog = CharacterVoicesDialog(None, "Game", original, "voice", frame=np.zeros((100, 200, 4), np.uint8))
    try:
        dialog._enabled.setChecked(True)
        dialog.accept()
        assert dialog.result() == 0 and "area" in dialog._error.text()
        dialog._profile.name_region = REGION
        dialog._name.setText("Alice")
        dialog._preset.setCurrentIndex(dialog._preset.findData(2))
        dialog._gender.setCurrentIndex(dialog._gender.findData("female"))
        dialog._add_name()
        dialog.accept()
        assert dialog.result() == dialog.DialogCode.Accepted
        assert dialog.get_profile().voices == {"Alice": 2}
        assert dialog.get_profile().gender_for("ALICE") == "female"
        assert original.voices == {} and original.name_region is None
    finally:
        dialog.close()


@pytest.mark.parametrize("size", [(640, 360), (800, 450)])
def test_character_dialog_actions_remain_reachable_on_small_remote_screen(qapp, monkeypatch, size):
    from interpreter.gui import character_voices

    monkeypatch.setattr(character_voices, "voice_details", lambda _: [("One", 1)])
    dialog = CharacterVoicesDialog(None, "Game", CharacterVoices(), "voice")
    try:
        dialog.resize(size[0] - 24, size[1] - 40)
        dialog.show()
        qapp.processEvents()
        scroll = dialog.findChild(QScrollArea)
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        assert dialog.width() <= size[0] and dialog.height() <= size[1]
        for button in dialog.findChildren(QPushButton):
            if button.text() in ("OK", "Cancel"):
                rect = QRect(button.mapTo(dialog, button.rect().topLeft()), button.size())
                assert dialog.rect().contains(rect)
                assert dialog.childAt(rect.center()) is button
    finally:
        dialog.close()


def test_existing_box_editor_can_select_name_area_without_ocr_settings(qapp):
    from interpreter.gui.ocr_config import OCRConfigDialog

    dialog = OCRConfigDialog(initial_region=REGION, selection_only=True)
    try:
        dialog.update_frame(np.zeros((500, 900, 4), np.uint8))
        assert dialog.get_scan_region() == pytest.approx(REGION)
        assert not dialog._scan_mode.isEnabled()
        assert dialog._preset_btn.isHidden() and dialog._confidence_slider.isHidden()
    finally:
        dialog.close()


def test_main_header_keeps_character_and_ocr_actions_at_top(main, qapp):
    main._ocr_config_btn = QPushButton("Configure OCR")
    header = main._build_header()
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.addWidget(header)
    layout.addStretch()
    container.resize(850, 400)
    container.show()
    qapp.processEvents()
    for button in (main._ocr_config_btn, main._character_voices_btn):
        assert button.isVisible()
        assert button.mapTo(container, button.rect().center()).y() < 160
    container.close()
