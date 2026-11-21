"""History records completed dialogue and keeps manual actions out of live translation."""

from dataclasses import replace
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from ludivoxa.config import Config, LLMSettings, OutputMode, OverlayMode
from ludivoxa.gui.history_page import HISTORY_LIMIT, HistoryPage
from ludivoxa.gui.workers import DialogueEntry, ProcessWorker
from ludivoxa.llm_translate import LLMTranslator
from ludivoxa.ocr import OCRResult


@pytest.mark.parametrize(
    ("output", "overlay"),
    [
        (OutputMode.BOTH, OverlayMode.BANNER),
        (OutputMode.TTS, OverlayMode.BANNER),
        (OutputMode.OVERLAY, OverlayMode.INPLACE),
    ],
)
def test_worker_records_stable_success_once_and_can_record_same_line_after_scene_break(monkeypatch, output, overlay):
    clock = [5.0]
    monkeypatch.setattr("ludivoxa.gui.workers.time.monotonic", lambda: clock[0])
    worker = ProcessWorker(Config(source_language="English", output_mode=output))
    worker.set_mode(overlay)
    worker._ocr = MagicMock()
    dialogue = [OCRResult("Welcome, traveler.", {"x": 0, "y": 0, "width": 12, "height": 6})]
    worker._ocr.extract_text_regions.return_value = dialogue
    worker._translator = LLMTranslator(LLMSettings(model="local"))
    worker._translator._loaded = True
    worker._translator._chat = MagicMock(return_value="Chào mừng, lữ khách.")
    worker._translator._stream_chat = MagicMock(
        side_effect=lambda _: iter([("Chào mừng, lữ khách. ", False), ("", True)])
    )
    published = []
    worker.speech_stream_ready.connect(lambda *_: published.append("speech"))
    history = []
    worker.dialogue_translated.connect(lambda entry, generation: history.append((entry, generation)))
    worker.dialogue_translated.connect(lambda *_: published.append("history"))

    worker._process_frame("frame", 2, preview_only=True)
    worker._process_frame("frame", 2)
    assert not history
    clock[0] += 0.5
    worker._process_frame("frame", 2)
    worker._process_frame("frame", 2)
    assert len(history) == 1
    entry, generation = history[0]
    assert generation == 2
    assert entry.source == ("Welcome, traveler.",)
    assert entry.translation == "Chào mừng, lữ khách."
    assert entry.can_replay
    if output == OutputMode.TTS:
        assert published == ["speech", "history"]

    worker._ocr.extract_text_regions.return_value = []
    worker._process_frame("frame", 2)
    clock[0] += 2.0
    worker._process_frame("frame", 2)
    worker._ocr.extract_text_regions.return_value = dialogue
    worker._process_frame("frame", 2)
    clock[0] += 0.5
    worker._process_frame("frame", 2)
    assert len(history) == 2


def test_worker_never_adds_source_error_fallback_to_history(monkeypatch):
    clock = [5.0]
    monkeypatch.setattr("ludivoxa.gui.workers.time.monotonic", lambda: clock[0])
    worker = ProcessWorker(Config(source_language="English", output_mode=OutputMode.OVERLAY))
    worker._ocr = MagicMock()
    worker._ocr.extract_text_regions.return_value = [OCRResult("Welcome", {})]
    worker._translator = MagicMock()
    worker._translator.translate.side_effect = RuntimeError("Endpoint unavailable")
    record = MagicMock()
    worker.dialogue_translated.connect(record)
    worker._process_frame("frame", 2)
    clock[0] += 0.5
    worker._process_frame("frame", 2)
    record.assert_not_called()


@pytest.mark.parametrize("overlay", [OverlayMode.BANNER, OverlayMode.INPLACE])
def test_stale_overlay_keeps_history_and_skips_a_late_translation(monkeypatch, overlay):
    clock = [5.0]
    monkeypatch.setattr("ludivoxa.gui.workers.time.monotonic", lambda: clock[0])
    worker = ProcessWorker(Config(source_language="English", output_mode=OutputMode.OVERLAY, hide_stale_overlay=True))
    worker.set_mode(overlay)
    worker._running = True
    worker._ocr = MagicMock()
    worker._translator = MagicMock()
    old, new = (np.zeros((60, 170, 4), dtype=np.uint8) for _ in range(2))
    box = {"x": 8, "y": 12, "width": 130, "height": 26}
    cv2.putText(old, "Welcome.", (8, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255,) * 4, 2)
    cv2.putText(new, "Goodbye.", (8, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255,) * 4, 2)
    worker._ocr.extract_text_regions.side_effect = lambda frame: [
        OCRResult("Welcome." if frame is old else "Goodbye.", box)
    ]
    changes, history = [], []
    signal = worker.text_ready if overlay == OverlayMode.BANNER else worker.regions_ready
    signal.connect(lambda value, _: changes.append(value))
    worker.dialogue_translated.connect(lambda entry, _: history.append(entry))
    worker._translator.translate.return_value = "Chào mừng.", False
    worker._process_frame(old, 1)
    assert changes[-1]
    changes.clear()
    clock[0] += 0.5

    def slow_translate(*_args, **_kwargs):
        worker.submit_frame(new, 1)
        return "Chào mừng.", False

    worker._translator.translate.side_effect = slow_translate
    worker._process_frame(old, 1)
    assert len(changes) == 1 and not changes[0]
    assert [entry.translation for entry in history] == ["Chào mừng."]
    assert worker._ocr.extract_text_regions.call_count == 2

    def check_cleared_before_translation(*_args, **_kwargs):
        assert len(changes) == 2 and not changes[-1]
        return "Tạm biệt.", False

    worker._translator.translate.side_effect = check_cleared_before_translation
    worker._process_frame(*worker._frame_buffer.get(timeout=0))
    assert changes[-1]
    assert worker._ocr.extract_text_regions.call_count == 3


def test_retranslation_uses_saved_source_language_and_speaker_without_mutating_live_llm_context():
    config = Config()
    client = LLMTranslator(
        LLMSettings(model="local", system_prompt="Translate {source_language} to {target_language}."),
        source_language="English",
    )
    client._loaded = True
    client._chat = MagicMock(return_value="Alex: Bản dịch mới.")
    client._remember("Live dialogue", "Câu đang dịch", None)
    client._remember("こんにちは", "Bản dịch cũ.", ("Alex", "female"))
    worker = ProcessWorker(config)
    worker._translator = client
    entry = DialogueEntry(
        ("こんにちは",), "Bản dịch cũ.", "Japanese", "Vietnamese", ("Alex", "female"), ("", 7, 1.2, "")
    )
    done = []
    worker.dialogue_retranslated.connect(lambda original, newer: done.append((original, newer)))

    worker._retranslate_dialogue(entry)

    assert done == [(entry, replace(entry, translation="Bản dịch mới."))]
    messages = client._chat.call_args.args[0]
    assert messages[0]["content"].startswith("Translate Japanese to Vietnamese.")
    assert len(messages) == 2 and '"gender": "female"' in messages[1]["content"]
    assert list(client._history) == [
        ("Live dialogue", "Câu đang dịch"),
        ("こんにちは", "Bản dịch cũ.", ("Alex", "female")),
    ]
    assert client._cache.get("こんにちは", scope=("Alex", "female")) == "Bản dịch cũ."


def test_history_page_copy_replay_and_async_result_stay_on_the_right_row():
    app = QApplication.instance() or QApplication([])
    page = HistoryPage()
    first = DialogueEntry(("Hello",), "Xin chào", "English", "Vietnamese", None, ("model", 7, 1.0, ""))
    second = replace(first, source=("Goodbye",), translation="Tạm biệt")
    try:
        assert not page.replay.isEnabled()
        for entry in (first, second):
            page.append(entry)
        assert page.source.toPlainText() == "Goodbye"
        page.entries.setCurrentRow(0)
        replay = MagicMock()
        page.replay_requested.connect(replay)
        page.replay.click()
        replay.assert_called_once_with(first)
        old_clipboard = app.clipboard().text()
        try:
            page.copy.click()
            assert app.clipboard().text() == first.translation
        finally:
            app.clipboard().setText(old_clipboard)

        request = MagicMock()
        page.retranslation_requested.connect(request)
        page.retranslate.click()
        request.assert_called_once_with(first)
        page.entries.setCurrentRow(1)
        page.finish_retranslation(first, replace(first, translation="Chào bạn"))
        assert page.translation.toPlainText() == second.translation
        page.entries.setCurrentRow(0)
        assert page.translation.toPlainText() == "Chào bạn"
        for index in range(HISTORY_LIMIT):
            page.append(replace(second, translation=str(index)))
        assert page.entries.count() == HISTORY_LIMIT
    finally:
        page.close()
