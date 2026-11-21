from unittest.mock import MagicMock

from PySide6.QtWidgets import QApplication, QDialog

from interpreter.config import Config, GameProfile, LLMSettings
from interpreter.gui.game_profile import GameProfileDialog
from interpreter.gui.workers import DialogueEntry, ProcessWorker
from interpreter.llm_translate import LLMTranslator
from interpreter.ocr import OCRResult


def test_game_profile_round_trip_preserves_legacy_window_configuration(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        "source_language: Japanese\nocr_confidence_per_window: {Retro: 0.8}\n"
        "game_profiles:\n  Broken: [1, 2]\n  Retro:\n    source_language: english\n"
        "    ignore_phrases: ['  Save Game  ', 1, 'Save Game']\n",
        encoding="utf-8",
    )
    config = Config.load(str(path))
    assert config.game_profiles == {"Retro": GameProfile("English", "", ("Save Game",))}
    config.set_game_profile("Other", GameProfile(system_prompt="Keep titles in {source_language}."))
    config.save()
    restored = Config.load(str(path))
    assert restored.source_language == "Japanese"
    assert restored.get_ocr_confidence("Retro") == 0.8
    assert restored.game_profiles == config.game_profiles
    restored.set_game_profile("Retro", GameProfile())
    restored.save()
    assert "Retro" not in Config.load(str(path)).game_profiles


def test_game_profile_dialog_validates_selected_language_before_saving():
    app = QApplication.instance() or QApplication([])
    dialog = GameProfileDialog("Retro", GameProfile(), "Japanese", lambda lang: "Select an LLM." if lang else "")
    try:
        dialog.language.setCurrentIndex(dialog.language.findData("English"))
        dialog.accept()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert dialog._error.text() == "Select an LLM."
        dialog.language.setCurrentIndex(0)
        dialog.ignored.setPlainText("  Continue\n\nSave Game")
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.profile().ignore_phrases == ("Continue", "Save Game")
    finally:
        dialog.close()
        app.processEvents()


def test_game_profile_filters_ocr_before_translation_and_live_speech(monkeypatch):
    now = [5.0]
    monkeypatch.setattr("interpreter.gui.workers.time.monotonic", lambda: now[0])
    llm = LLMSettings(model="local", system_prompt="Global {source_language}")
    config = Config(source_language="Japanese", llm=llm, output_mode="tts")
    config.set_game_profile(
        "Retro", GameProfile("English", "Translate {source_language} to {target_language}", ("Save Game",))
    )
    worker = ProcessWorker(config)
    client = worker._translator = LLMTranslator(llm, source_language="Japanese")
    client._loaded = True
    client._stream_chat = MagicMock(side_effect=lambda _: iter([("Chào bạn.", True)]))
    worker._ocr = MagicMock()
    worker._running = True
    worker.set_capture_source("Retro")
    speech, history = [], []
    worker.speech_stream_ready.connect(lambda stream, _: speech.append(stream))
    worker.dialogue_translated.connect(lambda entry, _: history.append(entry))

    def process(regions, generation):
        worker._ocr.extract_text_regions.return_value = [OCRResult(text, {}) for text in regions]
        worker.submit_frame("frame", generation)
        worker._process_frame(*worker._frame_buffer.get(timeout=0))

    process(["ＳＡＶＥ   GAME"], 1)
    assert worker.ocr_diagnostics[-1] == "All dialogue ignored"
    client._stream_chat.assert_not_called()

    process(["SAVE GAME", "Save game before leaving."], 1)
    now[0] += 0.5
    process(["SAVE GAME", "Save game before leaving."], 1)
    assert len(speech) == len(history) == 1
    assert list(speech[0]) == ["Chào bạn."]
    assert history[0].source == ("Save game before leaving.",)
    assert history[0].profile_title == "Retro"
    assert history[0].source_language == "English"
    messages = client._stream_chat.call_args.args[0]
    assert messages[0]["content"].startswith("Translate English to Vietnamese")
    assert messages[-1]["content"] == "Save game before leaving."

    worker.set_capture_source("Different window")
    process(["こんにちは"], 2)
    now[0] += 0.5
    process(["こんにちは"], 2)
    messages = client._stream_chat.call_args.args[0]
    assert len(messages) == 2
    assert messages[0]["content"].startswith("Global Japanese")
    assert client._cache.get("Save game before leaving.") is None


def test_history_retranslation_uses_its_own_profiles_current_prompt():
    config = Config(llm=LLMSettings(model="local"), source_language="English")
    config.set_game_profile("Older game", GameProfile(system_prompt="Old game {source_language}"))
    worker = ProcessWorker(config)
    client = worker._translator = LLMTranslator(config.llm)
    client._loaded = True
    client.set_capture_profile("Current game", "English", "Current game {source_language}")
    client._remember("Current line", "Live result", None)
    client._chat = MagicMock(return_value="Chào bạn.")
    entry = DialogueEntry(("Welcome.",), "", "English", "Vietnamese", None, ("", 0, 1.0, ""), "Older game")
    worker._retranslate_dialogue(entry)
    assert client._chat.call_args.args[0][0]["content"].startswith("Old game English")
    assert client.system_prompt == "Current game English"
    assert list(client._history) == [("Current line", "Live result")]
