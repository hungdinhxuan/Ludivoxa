from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from ludivoxa.config import Config, GameProfile, LLMSettings
from ludivoxa.gui.game_profile import GameProfileDialog
from ludivoxa.gui.workers import DialogueEntry, ProcessWorker
from ludivoxa.llm_translate import LLMTranslator
from ludivoxa.ocr import OCRResult


def test_game_profile_round_trip_preserves_legacy_window_configuration(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        "source_language: Japanese\nocr_confidence_per_window: {Retro: 0.8}\n"
        "game_profiles:\n  Broken: [1, 2]\n  Retro:\n    source_language: english\n"
        "    ignore_phrases: ['  Save Game  ', 1, 'Save Game']\n"
        "  Legacy:\n    system_prompt: Giữ nguyên tên nhân vật.\n",
        encoding="utf-8",
    )
    config = Config.load(str(path))
    assert config.game_profiles == {
        "Retro": GameProfile("English", "", ("Save Game",)),
        "Legacy": GameProfile(system_prompt="Giữ nguyên tên nhân vật.", prompt_mode="override"),
    }
    config.set_game_profile("Other", GameProfile(system_prompt="Giữ nguyên tên theo {source_language}."))
    config.save()
    restored = Config.load(str(path))
    assert restored.source_language == "Japanese"
    assert restored.get_ocr_confidence("Retro") == 0.8
    assert restored.game_profiles == config.game_profiles
    assert restored.game_profiles["Other"].prompt_mode == "append"
    restored.set_game_profile("Retro", GameProfile())
    restored.save()
    assert "Retro" not in Config.load(str(path)).game_profiles


def test_game_profile_dialog_validates_selected_language_before_saving():
    app = QApplication.instance() or QApplication([])
    dialog = GameProfileDialog("Retro", GameProfile(), "Japanese", lambda lang: "Select an LLM." if lang else "")
    try:
        assert dialog.prompt_mode.currentData() == "append"
        dialog.language.setCurrentIndex(dialog.language.findData("English"))
        dialog.accept()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert dialog._error.text() == "Select an LLM."
        dialog.language.setCurrentIndex(0)
        dialog.prompt_mode.setCurrentIndex(dialog.prompt_mode.findData("override"))
        dialog.prompt.setPlainText("  Giữ nguyên tên nhân vật.  ")
        dialog.ignored.setPlainText("  Continue\n\nSave Game")
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.profile() == GameProfile(
            system_prompt="Giữ nguyên tên nhân vật.", ignore_phrases=("Continue", "Save Game"), prompt_mode="override"
        )
    finally:
        dialog.close()
        app.processEvents()


@pytest.mark.parametrize("prompt_mode", ["append", "override"])
def test_game_profile_filters_ocr_before_translation_and_live_speech(monkeypatch, prompt_mode):
    now = [5.0]
    monkeypatch.setattr("ludivoxa.gui.workers.time.monotonic", lambda: now[0])
    llm = LLMSettings(model="local", system_prompt="Global {source_language}")
    config = Config(source_language="Japanese", llm=llm, output_mode="tts")
    config.set_game_profile(
        "Retro",
        GameProfile("English", "Dịch từ {source_language} sang {target_language}.", ("Save Game",), prompt_mode),
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
    custom_prompt = "Dịch từ English sang Vietnamese."
    expected_prompt = f"Global English\n\n{custom_prompt}" if prompt_mode == "append" else custom_prompt
    assert messages[0]["content"] == expected_prompt
    assert messages[-1]["content"] == "Save game before leaving."

    worker.set_capture_source("Different window")
    process(["こんにちは"], 2)
    now[0] += 0.5
    process(["こんにちは"], 2)
    messages = client._stream_chat.call_args.args[0]
    assert len(messages) == 2
    assert messages[0]["content"].startswith("Global Japanese")
    assert client._cache.get("Save game before leaving.") is None


@pytest.mark.parametrize("prompt_mode", ["append", "override"])
def test_history_retranslation_uses_its_own_profiles_current_prompt(prompt_mode):
    config = Config(llm=LLMSettings(model="local", system_prompt="Global {source_language}"), source_language="English")
    config.set_game_profile(
        "Older game", GameProfile(system_prompt="Giữ tên gốc {source_language}.", prompt_mode=prompt_mode)
    )
    worker = ProcessWorker(config)
    client = worker._translator = LLMTranslator(config.llm)
    client._loaded = True
    client.set_capture_profile("Current game", "English", "Current game {source_language}")
    client._remember("Current line", "Live result", None)
    client._chat = MagicMock(return_value="Chào bạn.")
    entry = DialogueEntry(("Welcome.",), "", "English", "Vietnamese", None, ("", 0, 1.0, ""), "Older game")
    worker._retranslate_dialogue(entry)
    expected_prompt = "Giữ tên gốc English."
    if prompt_mode == "append":
        expected_prompt = "Global English\n\n" + expected_prompt
    assert client._chat.call_args.args[0][0]["content"] == expected_prompt
    assert client.system_prompt == "Current game English"
    assert list(client._history) == [("Current line", "Live result")]


def test_changing_only_prompt_mode_invalidates_live_translation_context():
    client = LLMTranslator(LLMSettings(model="local", system_prompt="Global"))
    client.set_capture_profile("Game", "English", "Local", prompt_mode="append")
    client._remember("Current line", "Live result", None)
    assert client.system_prompt == "Global\n\nLocal"

    client.set_capture_profile("Game", "English", "Local", prompt_mode="override")
    assert client.system_prompt == "Local"
    assert client._cache.get("Current line") is None
    assert not client._history

    client.set_capture_profile("Game", "English", "", prompt_mode="override")
    assert client.system_prompt == "Global"
