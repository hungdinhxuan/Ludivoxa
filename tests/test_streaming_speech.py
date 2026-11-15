"""Sentence streaming, bounded audio hand-offs and stale-session cancellation."""

import json
import os
import threading
import time
from concurrent.futures import CancelledError
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
import requests
from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from interpreter.config import Config, LLMSettings, OutputMode
from interpreter.gui.workers import ProcessWorker
from interpreter.llm_translate import LLMTranslationError, LLMTranslator
from interpreter.ocr import OCRResult
from interpreter.speech import PCMPlayer, SpeechController, SpeechStream, _output_device, _wav_bytes


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def until(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
        time.sleep(0.002)
    assert predicate()


def translator(provider="ollama", packets=None):
    client = LLMTranslator(LLMSettings(provider=provider, model="local", base_url="http://192.168.1.50:8000"))
    client._loaded = True
    response = MagicMock(spec=requests.Response)
    response.status_code = 200
    response.__enter__.return_value = response
    response.iter_lines.return_value = iter(packets or [])
    client._session.post = MagicMock(return_value=response)
    return client, response


def packet(text="", done=False, provider="ollama"):
    body = (
        {"message": {"content": text}, "done": done}
        if provider == "ollama"
        else {"choices": [{"delta": {"content": text}, "finish_reason": "stop" if done else None}]}
    )
    return (("data: " if provider == "openai" else "") + json.dumps(body)).encode()


@pytest.mark.parametrize("provider", ["ollama", "openai"])
def test_sentence_arrives_before_final_response_and_history_commits_once(provider):
    received = []
    client, response = translator(provider)

    def packets():
        yield packet("Xin chào. ", provider=provider)
        assert received == ["Xin chào."]
        assert not client._history and client._cache.get("Hello, traveller") is None
        yield packet("Hãy đi cùng tôi.", True, provider)

    response.iter_lines.return_value = packets()
    result, hit = client.translate_stream("Hello, traveller", received.append)
    assert not hit and result == "Xin chào. Hãy đi cùng tôi."
    assert received == ["Xin chào.", "Hãy đi cùng tôi."]
    assert list(client._history) == [("Hello, traveller", result)]
    client.translate_stream("Hello, traveller", received.append)
    assert client._session.post.call_count == 1
    assert len(client._history) == 1
    kwargs = client._session.post.call_args.kwargs
    assert kwargs["stream"] and kwargs["json"]["stream"] and kwargs["allow_redirects"] is False
    assert client._session.trust_env is False
    response.__exit__.assert_called()


@pytest.mark.parametrize(
    "lines",
    [
        [packet("Xin chào.")],
        [b"not json"],
        [b'{"error":"offline"}'],
    ],
)
def test_broken_stream_never_commits_context_or_retries_elsewhere(lines):
    client, _ = translator(packets=lines)
    with pytest.raises(LLMTranslationError):
        client.translate_stream("Hello", lambda _: None)
    assert not client._history and client._cache.get("Hello") is None
    assert client._session.post.call_count == 1


def test_reasoning_and_utf8_are_not_read_and_cancellation_closes_response():
    client, response = translator(
        packets=[packet("<thi"), packet("nk>Secret. "), packet("</think>Xin chào. "), packet("Tạm biệt.", True)]
    )
    spoken = []
    result, _ = client.translate_stream("Hello", spoken.append)
    assert spoken == ["Xin chào.", "Tạm biệt."] and "Secret" not in result
    client, response = translator(packets=[packet("Xin chào. "), packet("Tạm biệt.", True)])

    def cancel(_):
        raise CancelledError()

    with pytest.raises(CancelledError):
        client.translate_stream("Hello", cancel)
    assert not client._history
    response.__exit__.assert_called()


def test_bounded_audio_queue_unblocks_on_cancellation():
    stream = SpeechStream(capacity=2)
    stream.put(b"first")
    stream.put(b"second")
    cancelled = threading.Event()

    def blocked_producer():
        try:
            stream.put(b"third")
        except CancelledError:
            cancelled.set()

    thread = threading.Thread(target=blocked_producer)
    thread.start()
    assert not cancelled.wait(0.05)
    stream.cancel()
    assert cancelled.wait(0.5)
    thread.join(1)
    assert not thread.is_alive()


class Player(QObject):
    finished = Signal()
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.play = MagicMock()
        self.stop = MagicMock()


def test_playback_starts_while_translation_is_still_open_and_preserves_order(qapp):
    source = SpeechStream(key="hello")
    source.put("Xin chào.")
    synth = MagicMock()
    synth.iter_audio.side_effect = lambda text, *_args, **_kwargs: iter([text.encode()])
    player = Player()
    controller = SpeechController(synthesizer=synth, player=player)
    try:
        controller.submit(source, "voice.onnx")
        until(lambda: player.play.called)
        assert controller._working and not source._done.is_set()
        audio = player.play.call_args.args[0]
        chunks = iter(audio)
        assert next(chunks) == "Xin chào.".encode()
        source.put("Đi cùng tôi.")
        source.finish()
        assert list(chunks) == ["Đi cùng tôi.".encode()]
        until(lambda: not controller._working)
        assert player.play.call_count == 1  # One native stream for the utterance.
    finally:
        controller.close()


def test_new_dialogue_does_not_cancel_current_stream_and_only_latest_waits(qapp):
    synth = MagicMock()
    synth.iter_audio.side_effect = lambda text, *_args, **_kwargs: iter([text.encode()])
    player = Player()
    controller = SpeechController(synthesizer=synth, player=player)
    first = SpeechStream(key="first")
    first.put("Một.")
    try:
        controller.submit(first, "voice.onnx")
        until(lambda: player.play.called)
        output = iter(player.play.call_args.args[0])
        assert next(output) == "Một.".encode()
        old = SpeechStream(key="old")
        old.put("Cũ.")
        old.finish()
        controller.submit(old, "voice.onnx")
        newest = SpeechStream(key="new")
        newest.put("Mới.")
        newest.finish()
        controller.submit(newest, "voice.onnx")
        assert old._cancelled.is_set() and not first._cancelled.is_set()
        first.put("Hai.")
        first.finish()
        assert list(output) == [b"Hai."]
        until(lambda: controller._ready is not None)
        player.finished.emit()
        until(lambda: player.play.call_count == 2)
        assert list(player.play.call_args.args[0]) == ["Mới.".encode()]
        assert [call.args[0] for call in synth.iter_audio.call_args_list] == ["Một.", "Hai.", "Mới."]
    finally:
        controller.close()


def test_stopping_open_translation_releases_speech_worker(qapp):
    source = SpeechStream(key="open")
    source.put("Xin chào.")
    synth = MagicMock()
    synth.iter_audio.return_value = iter([b"audio"])
    player = Player()
    controller = SpeechController(synthesizer=synth, player=player)
    try:
        controller.submit(source, "voice.onnx")
        until(lambda: player.play.called)
        controller.stop()
        until(lambda: not controller._working)
        assert source._cancelled.is_set()
        assert controller._ready is None
    finally:
        controller.close()


def test_real_translator_worker_streams_stable_source_once(qapp, monkeypatch):
    config = Config(source_language="English", output_mode=OutputMode.TTS)
    worker = ProcessWorker(config)
    worker._ocr = MagicMock()
    worker._ocr.extract_text_regions.return_value = [OCRResult("Welcome brave hero", None)]
    client, _ = translator(packets=[packet("Xin chào. "), packet("Anh hùng!", True)])
    worker._translator = client
    streams, legacy = [], []
    worker.speech_stream_ready.connect(lambda stream, generation: streams.append((stream, generation)))
    worker.speech_ready.connect(lambda *args: legacy.append(args))
    clock = [10.0]
    monkeypatch.setattr("interpreter.gui.workers.time.monotonic", lambda: clock[0])
    frame = np.zeros((20, 80, 4), dtype=np.uint8)
    worker._process_frame(frame, 7)
    assert not streams
    clock[0] += 0.4
    worker._process_frame(frame, 7)
    worker._process_frame(frame, 7)
    assert len(streams) == 1 and not legacy
    assert streams[0][1] == 7 and list(streams[0][0]) == ["Xin chào.", "Anh hùng!"]
    assert client._session.post.call_count == 1


def test_speculative_translation_is_not_context_until_source_is_confirmed():
    client, _ = translator(packets=[packet("Bản mới.", True)])
    client._chat = MagicMock(return_value="Chưa xác nhận.")
    client.preview_translation("Incomplete dialogue?")
    assert not client._history and client._cache.get("Incomplete dialogue?") is None
    spoken = []
    client.translate_stream("A completely different final line", spoken.append)
    assert spoken == ["Bản mới."]
    assert list(client._history) == [("A completely different final line", "Bản mới.")]
    assert client._cache.get("Incomplete dialogue?") is None


def test_preview_is_invalidated_by_context_changes():
    client, _ = translator(packets=[packet("Mới.", True)])
    client._chat = MagicMock(return_value="Cũ.")
    client.preview_translation("Hello")
    client._history.append(("Another line", "Khác."))
    spoken = []
    client.translate_stream("Hello", spoken.append)
    assert spoken == ["Mới."] and client._session.post.call_count == 1


def test_preview_reuses_settle_wait_without_reading_or_committing_early(qapp, monkeypatch):
    worker = ProcessWorker(Config(source_language="English", output_mode=OutputMode.TTS))
    worker._ocr = MagicMock()
    worker._ocr.extract_text_regions.return_value = [OCRResult("Would you help me?", None)]
    client, _ = translator()
    client._chat = MagicMock(return_value="Bạn giúp tôi nhé?")
    worker._translator = client
    spoken = []
    worker.speech_stream_ready.connect(lambda stream, _: spoken.append(stream))
    clock = [10.0]
    monkeypatch.setattr("interpreter.gui.workers.time.monotonic", lambda: clock[0])
    frame = np.zeros((20, 80, 4), dtype=np.uint8)
    worker._process_frame(frame)
    client._chat.assert_not_called()
    clock[0] += 0.15
    worker._process_frame(frame)
    assert client._chat.call_count == 1 and not spoken and not client._history
    clock[0] += 0.15
    worker._process_frame(frame)
    assert client._chat.call_count == 1 and not spoken
    clock[0] += 0.1
    worker._process_frame(frame)
    assert len(spoken) == 1 and list(spoken[0]) == ["Bạn giúp tôi nhé?"]
    assert len(client._history) == 1
    client._session.post.assert_not_called()


def test_stale_gui_signal_cancels_the_stream_instead_of_leaving_a_blocked_producer(qapp):
    from PySide6.QtWidgets import QMainWindow

    from interpreter.gui.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._capturing, window._capture_generation = True, 2
    source = SpeechStream(key="old session")
    window._on_speech_ready(source, 1)
    assert source._cancelled.is_set()


def test_native_interface_selection_keeps_the_same_endpoint_and_rejects_ambiguity(monkeypatch):
    monkeypatch.setattr("interpreter.speech.os", SimpleNamespace(name="nt"))
    current = {"name": "Speakers (High Definition", "index": 3, "hostapi": 0, "max_output_channels": 2}
    native = {"name": "Speakers (High Definition Audio Device)", "index": 12, "hostapi": 1, "max_output_channels": 2}
    devices = [current, native]
    sd = SimpleNamespace(
        query_devices=lambda **kwargs: current if kwargs else devices,
        query_hostapis=lambda i: {"name": ("MME", "Windows WASAPI")[i]},
    )
    assert _output_device(sd) == native
    devices.append({**native, "index": 13})
    assert _output_device(sd) == current
    devices[:] = [current, {**native, "name": "Headphones"}]
    assert _output_device(sd) == current


@pytest.mark.parametrize("provider", ["ollama", "openai"])
def test_output_limit_is_reported_without_caching_a_truncated_sentence(provider):
    body = (
        {"message": {"content": "Chưa xong"}, "done": True, "done_reason": "length"}
        if provider == "ollama"
        else {"choices": [{"delta": {"content": "Chưa xong"}, "finish_reason": "length"}]}
    )
    line = (("data: " if provider == "openai" else "") + json.dumps(body)).encode()
    client, _ = translator(provider, [line])
    with pytest.raises(LLMTranslationError, match="output limit"):
        client.translate_stream("A long paragraph", lambda _: None)
    assert not client._history and client._cache.get("A long paragraph") is None


def test_one_packet_with_several_sentences_and_abbreviation_preserves_text():
    client, _ = translator(packets=[packet("Hỏi Dr. Smith nhé. Đợi tôi. "), packet("Tạm biệt.", True)])
    spoken = []
    client.translate_stream("Ask the doctor", spoken.append)
    assert spoken == ["Hỏi Dr. Smith nhé.", "Đợi tôi.", "Tạm biệt."]


@pytest.mark.skipif(not os.environ.get("INTERPRETER_TEST_VOICE"), reason="Real audio device required")
def test_one_native_audio_stream_drains_all_chunks_during_ui_stalls(qapp):
    import sounddevice as sd

    sd.query_devices(kind="output")
    source = SpeechStream(capacity=2)
    audio = _wav_bytes(np.zeros(6000, dtype=np.float32), 24000)

    def produce():
        for _ in range(12):
            source.put(audio)
            time.sleep(0.02)
        source.finish()

    producer = threading.Thread(target=produce, daemon=True)
    player = PCMPlayer()
    player.setVolume(0)
    errors = []
    loop = QEventLoop()
    player.finished.connect(loop.quit)
    player.error.connect(lambda error: (errors.append(error), loop.quit()))
    blocker = QTimer()
    blocker.timeout.connect(lambda: time.sleep(0.35))
    blocker.start(400)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(8000)
    try:
        producer.start()
        player.play(source)
        loop.exec()
        assert not errors
        assert player.diagnostics["streaming"]
        assert player.diagnostics["processed_seconds"] == 3.0
        assert player.underruns == 0
        assert player.diagnostics["elapsed_seconds"] < 3.75
    finally:
        blocker.stop()
        timer.stop()
        player.close()
        producer.join(1)
    assert not producer.is_alive()
