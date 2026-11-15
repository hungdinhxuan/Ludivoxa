"""Speech scheduling, cancellation, real optional offline synthesis and UI integration."""

import os
import socket
import threading
import time
import wave
from concurrent.futures import CancelledError
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from interpreter.config import Config, LLMSettings, OutputMode, OverlayMode, TranslationBackend
from interpreter.gui.main_window import MainWindow
from interpreter.gui.workers import ProcessWorker
from interpreter.ocr import OCRResult
from interpreter.speech import (
    OfflineSynthesizer,
    PCMPlayer,
    PiperSynthesizer,
    SpeechController,
    available_voices,
    clean_speech_text,
    voice_details,
)


class Player(QObject):
    finished = Signal()
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.play = MagicMock()
        self.stop = MagicMock()
        self.setSource = MagicMock()
        self.setAudioOutput = MagicMock()


@pytest.fixture(scope="module")
def qapp():
    # Native audio/COM objects must not outlive a repeatedly recreated Qt app.
    return QApplication.instance() or QApplication([])


@pytest.fixture
def speech(qapp):
    player = Player()
    synth = MagicMock()
    synth.synthesize.return_value = b"test audio"
    controller = SpeechController(synthesizer=synth, player=player)
    yield controller, synth, player, qapp
    controller.close()


def until(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
        time.sleep(0.002)  # Let background cold imports acquire the GIL, as app.exec() does.
    assert predicate()


def test_reads_new_text_once_and_keeps_only_latest_pending(speech):
    controller, synth, player, _ = speech
    synth.synthesize.side_effect = lambda text, *_: text.encode("utf-8")
    controller.submit("Xin chào", "voice.onnx")
    until(lambda: player.play.call_count == 1)
    controller.submit(" Xin  chào ", "voice.onnx")
    controller.submit("Dòng cũ", "voice.onnx")
    controller.submit("Dòng mới nhất", "voice.onnx")
    until(lambda: not controller._working and controller._ready is not None)
    assert player.play.call_count == 1  # Prefetch doesn't interrupt current playback.
    player.finished.emit()
    until(lambda: player.play.call_count == 2)
    assert synth.synthesize.call_args.args[0] == "Dòng mới nhất"
    assert player.play.call_args.args[0] == "Dòng mới nhất".encode()
    assert 2 <= synth.synthesize.call_count <= 3


def test_background_synthesis_and_stale_completion_after_stop(speech):
    controller, synth, player, _ = speech
    release = threading.Event()
    main_thread = threading.get_ident()
    threads = []

    def slow(*args):
        threads.append(threading.get_ident())
        release.wait(2)
        return b"audio"

    synth.synthesize.side_effect = slow
    controller.submit("Xin chào", "voice.onnx")
    until(lambda: bool(threads))
    assert threads[0] != main_thread
    controller.stop(reset=True)
    release.set()
    until(lambda: not controller._working)
    player.play.assert_not_called()
    controller.submit("Xin chào", "voice.onnx")
    until(lambda: player.play.call_count == 1)


def test_ocr_reversion_to_playing_translation_does_not_queue_it_again(speech):
    controller, _synth, player, _ = speech
    controller.submit("The current dialogue", "voice.onnx")
    until(lambda: player.play.call_count == 1)
    controller.submit("An OCR variant", "voice.onnx")
    until(lambda: controller._ready is not None)
    controller.submit("The current dialogue", "voice.onnx")
    player.finished.emit()
    QTest.qWait(30)
    assert player.play.call_count == 1
    assert controller._ready is None and controller._pending is None


def test_new_request_cooperatively_cancels_obsolete_offline_synthesis(qapp):
    started = threading.Event()
    cancelled_old = threading.Event()

    class CancellableVoice(OfflineSynthesizer):
        def __init__(self):
            pass

        def synthesize(self, text, *args, cancelled, **kwargs):
            if text == "Obsolete paragraph":
                started.set()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if cancelled():
                        cancelled_old.set()
                        raise CancelledError()
                    time.sleep(0.005)
                raise AssertionError("Obsolete work was not cancelled")
            return text.encode()

    player = Player()
    controller = SpeechController(synthesizer=CancellableVoice(), player=player)
    try:
        controller.submit("Obsolete paragraph", "voice")
        until(started.is_set)
        controller.submit("Latest dialogue", "voice")
        until(lambda: player.play.call_count == 1)
        assert cancelled_old.is_set()
        assert player.play.call_args.args[0] == b"Latest dialogue"
    finally:
        controller.close()


def test_disabled_empty_and_closed_speech_are_ignored(speech):
    controller, synth, _, _ = speech
    controller.submit(" ", "voice.onnx")
    controller.set_enabled(False)
    controller.submit("Xin chào", "voice.onnx")
    controller.close()
    controller.submit("Xin chào", "voice.onnx")
    synth.synthesize.assert_not_called()


def test_missing_voice_shows_error_without_breaking_translation(speech):
    controller, synth, player, _ = speech
    synth.synthesize.side_effect = ValueError("Missing local voice")
    messages = []
    controller.status_changed.connect(messages.append)
    controller.submit("Xin chào", "missing.onnx")
    until(lambda: "Missing local voice" in messages)
    player.play.assert_not_called()


def test_synthesizer_requires_local_files(tmp_path):
    with pytest.raises(ValueError, match="local Vietnamese"):
        PiperSynthesizer().synthesize("Xin chào", str(tmp_path / "missing.onnx"))


@pytest.mark.parametrize("mode", [OverlayMode.BANNER, OverlayMode.INPLACE])
def test_worker_speech_is_only_emitted_for_successful_translation(mode, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("interpreter.gui.workers.time.monotonic", lambda: clock[0])
    worker = ProcessWorker(Config())
    worker.set_mode(mode)
    worker._ocr = MagicMock()
    worker._ocr.extract_text_regions.return_value = [OCRResult("こんにちは")]
    worker._translator = MagicMock()
    worker._translator.translate.return_value = ("Xin chào", False)
    texts = []
    worker.speech_ready.connect(lambda text, generation: texts.append((text, generation)))
    worker._process_frame("frame", 5)
    assert texts == []
    clock[0] = 0.4
    worker._process_frame("frame", 5)
    assert texts == [("Xin chào", 5)]
    worker._translator.translate.side_effect = RuntimeError("offline")
    worker._process_frame("frame", 5)
    assert len(texts) == 1


def test_ui_speech_respects_capture_generation_and_target_but_continues_when_overlay_hidden(speech):
    _, _, _, _app = speech
    win = MainWindow.__new__(MainWindow)
    from PySide6.QtWidgets import QMainWindow

    QMainWindow.__init__(win)
    win._config = Config(translation_backend=TranslationBackend.LLM, llm=LLMSettings(model="m"))
    win._speech = MagicMock()
    win._capturing = True
    win._capture_generation = 2
    win._paused = False
    win._on_speech_ready("Xin chào", 1)
    win._speech.submit.assert_not_called()
    win._on_speech_ready("Xin chào", 2)
    win._speech.submit.assert_called_once()
    win._paused = True
    win._on_speech_ready("Dòng mới", 2)
    assert win._speech.submit.call_count == 2
    win._paused = False
    win._config.llm.target_language = "English"
    win._on_speech_ready("Hello", 2)
    assert win._speech.submit.call_count == 2


def test_tts_config_roundtrip(tmp_path):
    config = Config(tts_enabled=False, tts_model_path="C:/voices/vi.onnx")
    path = str(tmp_path / "config.yml")
    config.save(path)
    loaded = Config.load(path)
    assert not loaded.tts_enabled and loaded.tts_model_path == config.tts_model_path


@pytest.mark.parametrize("mode", list(OutputMode))
def test_output_modes_gate_overlay_and_tts(speech, mode):
    from PySide6.QtWidgets import QMainWindow

    win = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(win)
    win._config = Config(output_mode=mode, translation_backend=TranslationBackend.LLM)
    win._speech = MagicMock()
    win._banner_overlay = MagicMock()
    win._inplace_overlay = MagicMock()
    win._capture = None
    win._mode = OverlayMode.BANNER
    win._capturing, win._paused, win._capture_generation = True, False, 1
    win._show_overlay()
    win._on_text_ready("Xin chào", 1)
    win._on_regions_ready([("Xin chào", None)], 1)
    win._on_speech_ready("Xin chào", 1)
    assert win._banner_overlay.set_text.called == (mode != OutputMode.TTS)
    assert win._inplace_overlay.set_regions.called == (mode != OutputMode.TTS)
    assert win._speech.submit.called == (mode != OutputMode.OVERLAY)
    if mode == OutputMode.TTS:
        win._banner_overlay.show.assert_not_called()
        win._inplace_overlay.show.assert_not_called()


def test_output_and_voice_config_migrate_and_roundtrip(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("tts_enabled: false\n", encoding="utf-8")
    assert Config.load(str(path)).output_mode == OutputMode.OVERLAY
    config = Config(output_mode="tts", tts_speaker_id=42, tts_rate=1.25)
    config.save(str(path))
    loaded = Config.load(str(path))
    assert loaded.output_mode == OutputMode.TTS and loaded.tts_enabled
    assert (loaded.tts_speaker_id, loaded.tts_rate) == (42, 1.25)


def test_voice_catalog_and_ui_speaker_selection(speech, tmp_path):
    import json

    from PySide6.QtWidgets import QGroupBox, QMainWindow, QWidget

    model = tmp_path / "vi_VN-vivos-x_low.onnx"
    model.touch()
    Path = type(model)
    Path(str(model) + ".json").write_text(json.dumps({"num_speakers": 65}), encoding="utf-8")
    assert len(voice_details(str(model))) == 65
    assert len(available_voices(str(model))) >= 1
    win = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(win)
    win._config = Config(tts_model_path=str(model))
    win._config.save = MagicMock()
    win._speech = MagicMock()
    win._mode_row_widget = QWidget()
    win._capturing, win._paused = False, False
    page = win._build_play_page(QGroupBox())
    assert win._tts_speaker_combo.count() == 65
    win._tts_speaker_combo.setCurrentIndex(42)
    win._tts_rate_spin.setValue(1.25)
    assert (win._config.tts_speaker_id, win._config.tts_rate) == (42, 1.25)
    win._output_combo.setCurrentIndex(win._output_combo.findData("overlay"))
    assert not win._config.tts_enabled
    win._speech.set_enabled.assert_called_with(False)
    page.close()


def test_speech_prefetch_is_ready_before_current_playback_ends(speech):
    controller, synth, player, _ = speech
    controller.submit("Câu thứ nhất", "voice.onnx")
    until(lambda: player.play.call_count == 1)
    controller.submit("Câu thứ hai", "voice.onnx", 3, 1.25)
    until(lambda: controller._ready is not None)
    assert player.play.call_count == 1
    assert synth.synthesize.call_args.args[2:] == (3, 1.25)
    player.finished.emit()
    assert player.play.call_count == 2  # Already synthesized, no extra worker round trip.


def test_punctuation_changes_do_not_restart_speech(speech):
    controller, synth, player, _ = speech
    controller.submit("Xin chào!", "voice.onnx")
    until(lambda: player.play.call_count == 1)
    controller.submit("Xin chào...", "voice.onnx")
    assert synth.synthesize.call_count == 1


def test_completed_playback_is_not_requeued_by_stable_ocr_variant(speech):
    import numpy as np

    controller, synth, player, _ = speech
    worker = ProcessWorker(Config(source_language="English", output_mode=OutputMode.TTS))
    worker._ocr = MagicMock()
    worker._translator = MagicMock()
    worker._translator.translate.side_effect = lambda text: (f"VI: {text}", False)
    worker.speech_ready.connect(lambda text, generation: controller.submit(text, "voice.onnx"))
    image = np.zeros((80, 240, 4), dtype=np.uint8)
    image[20:40, 20:180, :3] = 255
    box = {"x": 20, "y": 20, "width": 160, "height": 20}
    worker._ocr.extract_text_regions.return_value = [OCRResult("Welcome brave hero", box)]
    worker._process_frame(image, 1)
    time.sleep(0.4)
    worker._process_frame(image, 1)
    until(lambda: player.play.call_count == 1)
    player.finished.emit()
    worker._ocr.extract_text_regions.return_value = [OCRResult("Welcome brave her0", box)]
    worker._process_frame(image, 1)
    time.sleep(0.4)
    worker._process_frame(image, 1)
    assert synth.synthesize.call_count == player.play.call_count == 1
    worker._translator.translate.assert_called_once()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Xin chào. √ ▼ ✓", "Xin chào."),
        ("★Xin chào★♥→Bạn ổn chứ? 😊", "Xin chào Bạn ổn chứ?"),
        ("**Đợi tôi!** ~ `Tiếp tục` | ^", "Đợi tôi! Tiếp tục"),
        ("Tôi có 100₫, giảm 20%. 2+2=4.", "Tôi có 100₫, giảm 20%. 2+2=4."),
        ("“Đừng đi…” — cô ấy nói. (Đợi đã!)", "“Đừng đi…” — cô ấy nói. (Đợi đã!)"),
        ("Tiếng Việt: a\u0306\u0301, o\u031b\u0300.", "Tiếng Việt: ắ, ờ."),
        ("日本語、English! 한국어。", "日本語、English! 한국어。"),
        ("Căn bậc hai của 9 là 3.", "Căn bậc hai của 9 là 3."),
        ("Xin\u200bchào\x00bạn\ufe0f", "Xin chào bạn"),
        ("√ ▼ ▷ ☑️ 🔊 ~", ""),
    ],
)
def test_speech_filter_removes_decorations_and_preserves_readable_text(text, expected):
    assert clean_speech_text(text) == expected


def test_only_clean_text_reaches_synthesis_and_decorations_do_not_repeat_it(speech):
    controller, synth, player, _ = speech
    controller.submit("√ ▼ ♥", "voice.onnx")
    synth.synthesize.assert_not_called()
    controller.submit("Xin chào. √ ▼", "voice.onnx")
    until(lambda: player.play.call_count == 1)
    assert synth.synthesize.call_args.args[0] == "Xin chào."
    player.finished.emit()
    controller.submit("Xin chào. ✓", "voice.onnx")
    assert synth.synthesize.call_count == 1


def test_preview_works_without_enabling_automatic_speech(speech):
    controller, _, player, _ = speech
    controller.set_enabled(False)
    controller.preview("voice.onnx", 0, 1.0)
    until(lambda: player.play.call_count == 1)
    assert not controller._enabled


def test_stopping_discards_prefetched_audio(speech):
    controller, _, player, _ = speech
    controller.submit("Câu đầu", "voice.onnx")
    until(lambda: player.play.call_count == 1)
    controller.submit("Câu tiếp", "voice.onnx")
    until(lambda: controller._ready is not None)
    controller.stop()
    player.finished.emit()
    assert player.play.call_count == 1 and not controller._busy


def test_voice_preparation_runs_once_in_background(speech):
    controller, synth, _, _ = speech
    caller = threading.get_ident()
    threads = []
    synth.prepare.side_effect = lambda *_: threads.append(threading.get_ident())
    controller.prepare("voice.onnx")
    controller.prepare("voice.onnx")
    until(lambda: bool(threads))
    assert threads == [threads[0]] and threads[0] != caller


def test_hide_overlay_does_not_cancel_speech(speech):
    from PySide6.QtWidgets import QMainWindow

    win = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(win)
    win._config = Config()
    win._capturing, win._paused = True, False
    win._speech = MagicMock()
    win._pause_btn = MagicMock()
    win._banner_overlay, win._inplace_overlay = MagicMock(), MagicMock()
    win._show_overlay = MagicMock()
    win._toggle_pause()
    assert win._paused
    win._speech.stop.assert_not_called()
    win._toggle_pause()
    assert not win._paused
    win._speech.stop.assert_not_called()
    assert Config.DEFAULT_HOTKEYS["toggle_overlay"] == "f8"


@pytest.mark.skipif(not os.environ.get("INTERPRETER_TEST_VOICE"), reason="Real audio device required")
def test_pcm_playback_continues_through_long_ui_stalls(speech):
    import numpy as np
    import sounddevice as sd

    try:
        sd.query_devices(kind="output")
    except sd.PortAudioError:
        pytest.skip("No native output device")
    data = BytesIO()
    with wave.open(data, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(np.zeros(22050 * 4, dtype="<i2").tobytes())
    player = PCMPlayer()
    player.setVolume(0)
    loop = QEventLoop()
    errors = []
    player.finished.connect(loop.quit)
    player.error.connect(lambda message: (errors.append(message), loop.quit()))
    blocker = QTimer()
    blocker.timeout.connect(lambda: time.sleep(0.6))
    blocker.start(450)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(10000)
    try:
        player.play(data.getvalue())
        loop.exec()
        assert not errors
        assert player.diagnostics.get("processed_seconds") == 4.0
        assert player.diagnostics["elapsed_seconds"] < 4.75
        assert player.underruns == 0
    finally:
        blocker.stop()
        timeout.stop()
        player.close()


@pytest.mark.skipif(not os.environ.get("INTERPRETER_TEST_VOICE"), reason="Real audio device required")
def test_explicit_stop_ignores_stale_native_completion(speech):
    import numpy as np

    audio = BytesIO()
    with wave.open(audio, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(np.zeros(22050 * 4, dtype="<i2").tobytes())
    player = PCMPlayer()
    player.setVolume(0)
    completions = []
    player.finished.connect(lambda: completions.append(True))
    try:
        player.play(audio.getvalue())
        until(lambda: player._stream is not None)
        player.stop()
        for _ in range(20):
            QTest.qWait(10)
            time.sleep(0.002)
        assert not completions
    finally:
        player.close()


def test_tts_only_batches_inplace_regions_into_one_request(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("interpreter.gui.workers.time.monotonic", lambda: clock[0])
    worker = ProcessWorker(Config(output_mode="tts", source_language="English"))
    worker.set_mode(OverlayMode.INPLACE)
    worker._ocr = MagicMock()
    worker._ocr.extract_text_regions.return_value = [OCRResult("Hello"), OCRResult("world")]
    worker._translator = MagicMock()
    worker._translator.translate.return_value = ("Xin chào", False)
    worker._process_frame("frame")
    worker._translator.translate.assert_not_called()
    clock[0] = 0.4
    worker._process_frame("frame")
    worker._translator.translate.assert_called_once_with("Hello world")


@pytest.mark.skipif(
    not os.environ.get("INTERPRETER_TEST_VOICE"), reason="Set INTERPRETER_TEST_VOICE for real Piper test"
)
def test_real_vietnamese_synthesis_is_offline(monkeypatch, speech):
    def forbid(*args, **kwargs):
        raise AssertionError("Network access attempted during speech synthesis")

    monkeypatch.setattr(socket.socket, "connect", forbid)
    synthesizer = PiperSynthesizer()
    start = time.perf_counter()
    data = synthesizer.synthesize("Xin chào. Chào mừng bạn đến với ngôi làng.", os.environ["INTERPRETER_TEST_VOICE"])
    elapsed = (time.perf_counter() - start) * 1000
    with wave.open(BytesIO(data)) as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getnframes() > wav_file.getframerate()
        duration = wav_file.getnframes() / wav_file.getframerate()
    assert synthesizer._voice.session.get_providers() == ["CPUExecutionProvider"]
    assert synthesizer._voice.session.get_session_options().intra_op_num_threads == 2
    print(f"Vietnamese TTS: {elapsed:.1f}ms including load, {duration:.2f}s audio, {len(data)} bytes")
    if QMediaDevices.audioOutputs():
        # Verify real PCM playback completion silently.
        controller = SpeechController(synthesizer=MagicMock(synthesize=MagicMock(return_value=data)))
        controller._player.setVolume(0)
        messages = []
        controller.status_changed.connect(messages.append)
        try:
            controller.submit("Xin chào", "unused.onnx")
            until(lambda: "Ready" in messages)
            assert controller._player.underruns == 0
            import sounddevice as sd

            assert controller._player.diagnostics["sample_rate"] == int(
                sd.query_devices(controller._player.diagnostics["output_device"])["default_samplerate"]
            )
            messages.clear()
            controller.submit("Dòng tiếp theo", "unused.onnx")
            until(lambda: "Ready" in messages)
            assert controller._player.underruns == 0
            controller.stop()
            until(lambda: not controller._player.diagnostics.get("buffer_active"))
        finally:
            controller.close()


@pytest.mark.skipif(not os.environ.get("INTERPRETER_TEST_VOICE"), reason="Local voice fixtures required")
def test_real_speaker_and_speed_changes():
    from pathlib import Path

    root = Path(os.environ["INTERPRETER_TEST_VOICE"]).parent
    model = root / "vi_VN-vivos-x_low.onnx"
    if not model.is_file():
        pytest.skip("VIVOS fixture is not provisioned")
    synthesizer = PiperSynthesizer()
    audio = synthesizer.synthesize("Xin chào, chúc bạn một ngày tốt lành.", str(model), 0)
    other = synthesizer.synthesize("Xin chào, chúc bạn một ngày tốt lành.", str(model), 64)
    fast = synthesizer.synthesize("Xin chào, chúc bạn một ngày tốt lành.", str(model), 0, 1.5)
    assert audio != other
    with wave.open(BytesIO(audio)) as normal, wave.open(BytesIO(fast)) as faster:
        assert faster.getnframes() < normal.getnframes()
    assert audio == synthesizer.synthesize("Xin chào, chúc bạn một ngày tốt lành.", str(model), 0)
    with pytest.raises(ValueError, match="speaker"):
        synthesizer.synthesize("Xin chào", str(model), 65)
