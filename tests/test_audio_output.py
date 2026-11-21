"""Negotiate speech formats on mono, stereo and surround output endpoints."""

import sys
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from ludivoxa.speech import PCMPlayer, SpeechStream, _output_format, _wav_bytes


class AudioError(Exception):
    pass


class AudioBackend:
    PortAudioError = AudioError
    CallbackStop = type("CallbackStop", (Exception,), {})

    def __init__(self, native_channels=8, supported=None, conversion=False):
        self.original = dict(
            index=3, name="Selected speaker", hostapi=0, max_output_channels=2, default_samplerate=48000
        )
        self.native = dict(self.original, index=12, hostapi=1, max_output_channels=native_channels)
        self.devices = [self.original, self.native]
        self.supported = supported if supported is not None else {3: {2}, 12: {native_channels}}
        self.conversion = conversion
        self.checks, self.streams = [], []
        self._initialized = 1
        self.initializations = self.terminations = 0
        self.pending_devices = None

    def _terminate(self):
        assert all(getattr(stream, "closed", True) for stream in self.streams)
        self.terminations += 1
        self._initialized = 0

    def _initialize(self):
        self.initializations += 1
        self._initialized = 1
        if self.pending_devices is not None:
            self.original, self.native = self.pending_devices
            self.devices = [self.original, self.native]
            self.pending_devices = None

    def query_devices(self, **kwargs):
        return self.original if kwargs else self.devices

    def query_hostapis(self, index):
        return {"name": ("MME", "Windows WASAPI")[index]}

    def WasapiSettings(self, **kwargs):
        return SimpleNamespace(**kwargs)

    def check_output_settings(self, **settings):
        self.checks.append(settings)
        if settings["channels"] in self.supported.get(settings["device"], set()):
            return
        extra = settings.get("extra_settings")
        if self.conversion and extra and extra.auto_convert and settings["channels"] == 2:
            return
        raise AudioError("Invalid number of channels", -9998)

    def OutputStream(self, **settings):
        self.check_output_settings(
            **{k: v for k, v in settings.items() if k not in ("latency", "callback", "finished_callback")}
        )
        stream = SimpleNamespace(
            settings=settings,
            writes=[],
            closed=False,
            latency=0.07,
            start=lambda: None,
            stop=lambda: None,
            close=lambda: None,
        )
        stream.close = lambda: setattr(stream, "closed", True)

        def write(samples):
            assert samples.dtype == np.float32 and samples.shape[1] == settings["channels"]
            stream.writes.append(samples.copy())
            return False

        stream.write = write
        if "callback" in settings:
            import threading
            import time

            def run():
                try:
                    while True:
                        samples = np.empty((480, settings["channels"]), dtype=np.float32)
                        try:
                            settings["callback"](samples, 480, None, SimpleNamespace(output_underflow=False))
                        except self.CallbackStop:
                            stream.writes.append(samples.copy())
                            break
                        stream.writes.append(samples.copy())
                        time.sleep(480 / settings["samplerate"])
                finally:
                    settings["finished_callback"]()

            callback_thread = threading.Thread(target=run, daemon=True)
            stream.start = callback_thread.start
            stream.stop = lambda: callback_thread.join(2)

            def close():
                callback_thread.join(2)
                stream.closed = True

            stream.close = close
        self.streams.append(stream)
        return stream


@pytest.fixture
def backend(monkeypatch):
    # Exercise automatic MME -> WASAPI selection on every test platform.
    monkeypatch.setattr("ludivoxa.speech.os", SimpleNamespace(name="nt"))
    device = AudioBackend()
    monkeypatch.setitem(sys.modules, "sounddevice", device)
    return device


@pytest.mark.parametrize("channels", [6, 8])
@pytest.mark.parametrize("streaming", [False, True])
def test_native_surround_format_plays_buffered_and_streamed_speech(backend, channels, streaming):
    app = QApplication.instance() or QApplication([])
    backend.native["max_output_channels"] = channels
    backend.supported[12] = {channels}
    wav = _wav_bytes(np.full(4800, 0.5, dtype=np.float32), 48000)
    if streaming:
        source = SpeechStream()
        source.put(wav)
        source.put(wav)
        source.finish()
    else:
        source = wav
    player = PCMPlayer()
    errors = []
    player.error.connect(errors.append)
    try:
        player._write_audio(player._generation, source, 0.5)
        assert not errors
        assert player.diagnostics["output_channels"] == channels
        assert player.diagnostics["output_device"] == 12
        assert player.diagnostics["streaming"] == streaming
        assert player.diagnostics["speech_volume"] == 0.5
        assert player.diagnostics["audio_peak"] == pytest.approx(0.25, abs=2e-5)
        assert player.diagnostics["audio_rms"] == pytest.approx(0.25, abs=2e-5)
        assert player.diagnostics["output_device_name"] == "Selected speaker"
        pcm = np.concatenate(backend.streams[0].writes)
        assert len(pcm) == (9600 if streaming else 4800)
        np.testing.assert_allclose(pcm[:, :2], 0.25, atol=2e-5)
        assert not np.any(pcm[:, 2:])  # No speech sent to surround/LFE.
        assert backend.checks[0]["channels"] == 2
        assert backend.checks[1]["channels"] == channels
    finally:
        player.close()
    assert app is not None


def test_wasapi_shared_conversion_keeps_stereo_when_supported(backend):
    backend.conversion = True
    device, settings = _output_format(backend)
    assert device["index"] == 12 and settings["channels"] == 2
    assert settings["extra_settings"].auto_convert
    assert not settings["extra_settings"].exclusive
    assert len(backend.checks) == 1


def test_next_utterance_requeries_endpoint_after_remote_audio_device_changes(backend):
    app = QApplication.instance() or QApplication([])
    player = PCMPlayer()
    wav = _wav_bytes(np.full(4800, 0.5, dtype=np.float32), 48000)
    try:
        player._write_audio(player._generation, wav, 1.0)
        assert player.diagnostics["output_device"] == 12
        backend.original["name"] = "Remote Audio"
        backend.native.update(name="Remote Audio", index=18)
        backend.supported[18] = backend.supported.pop(12)
        player._write_audio(player._generation, wav, 1.0)
        assert player.diagnostics["output_device"] == 18
        assert player.diagnostics["output_device_name"] == "Remote Audio"
        assert len(backend.streams) == 2
    finally:
        player.close()
    assert app is not None


@pytest.mark.parametrize("streaming", [False, True])
def test_rdp_and_console_changes_refresh_cached_portaudio_default_between_utterances(backend, streaming):
    app = QApplication.instance() or QApplication([])
    player = PCMPlayer()
    wav = _wav_bytes(np.full(4800, 0.5, dtype=np.float32), 48000)

    def play():
        source = wav
        if streaming:
            source = SpeechStream()
            source.put(wav)
            source.finish()
        player._write_audio(player._generation, source, 1.0)

    try:
        play()
        assert backend.initializations == 1
        for name, index in [("Remote Audio", 18), ("Selected speaker", 12), ("Remote Audio", 20)]:
            original = dict(backend.original, name=name, index=index - 1)
            native = dict(backend.native, name=name, index=index)
            backend.supported[index] = {8}
            backend.pending_devices = original, native
            stale_default = backend.query_devices(kind="output")
            count = backend.initializations
            player._media_devices.audioOutputsChanged.emit()
            player._media_devices.audioOutputsChanged.emit()
            assert backend.query_devices(kind="output") is stale_default
            assert backend.initializations == count  # No native calls on the GUI thread.
            play()
            assert backend.initializations == count + 1
            assert player.diagnostics["output_device"] == index
            assert player.diagnostics["output_device_name"] == name
            play()
            assert backend.initializations == count + 1  # No per-line reinitialization.
    finally:
        player.close()
    assert app is not None


def test_output_change_during_playback_does_not_reinitialize_or_cut_the_current_stream(backend):
    app = QApplication.instance() or QApplication([])
    player = PCMPlayer()
    output = backend.OutputStream
    changed = []

    def opening(**settings):
        stream = output(**settings)
        start = stream.start

        def starting():
            if not changed:
                changed.append(True)
                backend.pending_devices = (
                    dict(backend.original, name="Remote Audio"),
                    dict(backend.native, name="Remote Audio", index=18),
                )
                backend.supported[18] = {8}
                player._media_devices.audioOutputsChanged.emit()
                assert backend.initializations == 1 and not stream.closed
            return start()

        stream.start = starting
        return stream

    backend.OutputStream = opening
    wav = _wav_bytes(np.full(4800, 0.5, dtype=np.float32), 48000)
    try:
        player._write_audio(player._generation, wav, 1.0)
        assert len(np.concatenate(backend.streams[0].writes)) == 4800
        assert backend.streams[0].closed and backend.initializations == 1
        player._write_audio(player._generation, wav, 1.0)
        assert player.diagnostics["output_device_name"] == "Remote Audio"
        assert backend.initializations == 2
    finally:
        player.close()
    assert app is not None


def test_failed_device_refresh_never_plays_stale_audio_and_can_retry(backend):
    app = QApplication.instance() or QApplication([])
    player = PCMPlayer()
    initialize = backend._initialize
    backend._initialize = lambda: (_ for _ in ()).throw(AudioError("Device refresh failed"))
    source = SpeechStream()
    wav = _wav_bytes(np.zeros(4800, dtype=np.float32), 48000)
    source.put(wav)
    source.finish()
    try:
        player._write_audio(player._generation, source, 1.0)
        assert "Device refresh failed" in player.diagnostics["error"]
        assert not backend.streams and player._applied_output_revision == -1
        assert list(source) == [wav]
        backend._initialize = initialize
        player._write_audio(player._generation, wav, 1.0)
        assert len(backend.streams) == 1 and backend.terminations == 1
        assert "error" not in player.diagnostics
    finally:
        player.close()
    assert app is not None


def test_device_refresh_and_stream_cleanup_run_on_the_audio_writer(backend):
    import threading
    import time

    app = QApplication.instance() or QApplication([])
    player = PCMPlayer()
    threads = []
    initialize = backend._initialize

    def refreshing():
        threads.append(threading.get_ident())
        initialize()

    backend._initialize = refreshing
    finished = []
    player.finished.connect(lambda: finished.append(True))
    wav = _wav_bytes(np.zeros(4800, dtype=np.float32), 48000)
    try:
        player._media_devices.audioOutputsChanged.emit()
        assert not threads
        player.play(wav)
        deadline = time.monotonic() + 2
        while not finished and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.002)
        assert finished and threads and threads[0] != threading.get_ident()
        assert backend.streams[0].closed
    finally:
        player.close()


def test_other_players_cannot_refresh_portaudio_until_an_open_stream_closes(backend):
    import threading
    import time

    app = QApplication.instance() or QApplication([])
    first, second = PCMPlayer(), PCMPlayer()
    opened, release = threading.Event(), threading.Event()
    output = backend.OutputStream

    def opening(**settings):
        if not backend.streams:
            render = settings["callback"]

            def rendering(*args):
                opened.set()
                assert release.wait(2)
                return render(*args)

            settings["callback"] = rendering
        stream = output(**settings)
        return stream

    backend.OutputStream = opening
    wav = _wav_bytes(np.zeros(4800, dtype=np.float32), 48000)
    try:
        first.play(wav)
        assert opened.wait(2)
        second.play(wav)
        time.sleep(0.02)
        assert backend.initializations == 1
        release.set()
        deadline = time.monotonic() + 2
        while (len(backend.streams) < 2 or not backend.streams[-1].closed) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.002)
        assert len(backend.streams) == backend.initializations == 2
        assert all(stream.closed for stream in backend.streams)
    finally:
        release.set()
        first.close()
        second.close()


def test_wasapi_callback_preserves_pcm_across_unequal_stream_chunks(backend):
    app = QApplication.instance() or QApplication([])
    backend.native["max_output_channels"] = 2
    backend.supported[12] = {2}
    source = SpeechStream()
    expected = []
    for count, level in [(777, 0.1), (2000, 0.2), (521, 0.3)]:
        expected.extend([level] * count)
        source.put(_wav_bytes(np.full(count, level, dtype=np.float32), 48000))
    source.finish()
    player = PCMPlayer()
    try:
        player._write_audio(player._generation, source, 1.0)
        assert player.diagnostics["callback_mode"] and player.diagnostics["streaming"]
        assert player.diagnostics["processed_seconds"] == pytest.approx(len(expected) / 48000)
        pcm = np.concatenate(backend.streams[0].writes)
        np.testing.assert_allclose(pcm[: len(expected), 0], expected, atol=2 / 32768)
        assert not np.any(pcm[len(expected) :])
    finally:
        player.close()
    assert app is not None


def test_stalled_wasapi_callback_can_be_cancelled_without_blocking_write(backend):
    import time

    app = QApplication.instance() or QApplication([])
    streams = []

    def output(**settings):
        assert callable(settings["callback"])
        stream = SimpleNamespace(start=lambda: None, close=lambda: None, closed=False, latency=0.07)
        stream.close = lambda: setattr(stream, "closed", True)
        streams.append(stream)
        return stream  # Simulate a disconnected RDP driver that never requests samples.

    backend.OutputStream = output
    source = SpeechStream()
    for _ in range(10):
        source.put(_wav_bytes(np.zeros(4800, dtype=np.float32), 48000))
    source.finish()
    player = PCMPlayer()
    try:
        player.play(source)
        deadline = time.monotonic() + 2
        while not streams and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.002)
        assert streams
        time.sleep(0.05)
        assert source._queue.qsize() >= 6  # Bounded decoded PCM, rather than consuming all audio.
        start = time.monotonic()
        player.close()
        assert time.monotonic() - start < 0.5
        assert streams[0].closed
    finally:
        player.close()


def test_stalled_wasapi_reports_failure_and_invalidates_cached_endpoint(backend, monkeypatch):
    app = QApplication.instance() or QApplication([])
    clock = [0.0]

    def tick():
        clock[0] += 1
        return clock[0]

    monkeypatch.setattr("ludivoxa.speech.time", SimpleNamespace(monotonic=tick))
    backend.OutputStream = lambda **settings: SimpleNamespace(start=lambda: None, close=lambda: None, latency=0.07)
    source = SpeechStream()
    for _ in range(6):
        source.put(_wav_bytes(np.zeros(4800, dtype=np.float32), 48000))
    source.finish()
    player = PCMPlayer()
    try:
        player._write_audio(player._generation, source, 1.0)
        assert "stopped accepting samples" in player.diagnostics["error"]
        assert player._applied_output_revision == -1
    finally:
        player.close()
    assert app is not None


@pytest.mark.parametrize("host", ["Windows WDM-KS", "ASIO"])
def test_callback_hosts_drain_every_stream_chunk_without_blocking_writes(backend, monkeypatch, host):
    app = QApplication.instance() or QApplication([])
    backend.devices = [backend.original]
    backend.supported[3] = {2}
    monkeypatch.setattr(backend, "query_hostapis", lambda index: {"name": host})
    rendered = []

    class CallbackStop(Exception):
        pass

    def output_stream(**settings):
        def start():
            while True:
                out = np.empty((128, settings["channels"]), dtype=np.float32)
                try:
                    settings["callback"](out, 128, None, SimpleNamespace(output_underflow=False))
                except CallbackStop:
                    rendered.append(out.copy())
                    break
                rendered.append(out.copy())
            settings["finished_callback"]()

        return SimpleNamespace(start=start, stop=lambda: None, close=lambda: None, latency=0.06)

    monkeypatch.setattr(backend, "CallbackStop", CallbackStop, raising=False)
    monkeypatch.setattr(backend, "OutputStream", output_stream)
    source = SpeechStream()
    for level in (0.1, 0.2, 0.3):
        source.put(_wav_bytes(np.full(4800, level, dtype=np.float32), 48000))
    source.finish()
    player = PCMPlayer()
    try:
        player._write_audio(player._generation, source, 1.0)
        assert player.diagnostics["host_api"] == host
        assert player.diagnostics["callback_mode"] and not player.diagnostics["streaming"]
        assert player.diagnostics["processed_seconds"] == pytest.approx(0.3)
        assert player.diagnostics["underruns"] == 0
        pcm = np.concatenate(rendered)
        for index, level in enumerate((0.1, 0.2, 0.3)):
            np.testing.assert_allclose(pcm[index * 4800 : (index + 1) * 4800], level, atol=2 / 32768)
    finally:
        player.close()
    assert app is not None


def test_unsupported_auto_selected_wasapi_returns_to_original_endpoint(backend):
    backend.supported[12] = set()
    backend.devices.append(dict(backend.native, index=15, name="Different speaker"))
    device, settings = _output_format(backend)
    assert device is backend.original
    assert settings["device"] == 3 and "extra_settings" not in settings
    assert [s["device"] for s in backend.checks] == [12, 12, 12, 3]


@pytest.mark.parametrize("maximum", [1, 2])
def test_mono_supported_even_when_reported_maximum_is_stereo(backend, maximum):
    backend.original["max_output_channels"] = maximum
    backend.devices = [backend.original]
    backend.supported[3] = {1}
    _, settings = _output_format(backend)
    assert settings["channels"] == 1 and "extra_settings" not in settings
    assert len(backend.checks) == maximum


def test_no_supported_output_format_does_not_consume_stream(backend):
    app = QApplication.instance() or QApplication([])
    backend.supported = {}
    source = SpeechStream()
    source.put(_wav_bytes(np.zeros(480), 48000))
    source.finish()
    player = PCMPlayer()
    try:
        player._write_audio(player._generation, source, 1.0)
        assert "no supported speech format" in player.diagnostics["error"]
        assert not backend.streams
        assert len(list(source)) == 1
    finally:
        player.close()
    assert app is not None


def test_zero_output_channels_is_reported_before_opening_stream(backend):
    backend.original["max_output_channels"] = 0
    backend.devices = [backend.original]
    with pytest.raises(ValueError, match="No output channels"):
        _output_format(backend)
    assert not backend.checks and not backend.streams
