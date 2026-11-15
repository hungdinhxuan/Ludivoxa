"""Negotiate speech formats on mono, stereo and surround output endpoints."""

import sys
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from interpreter.speech import PCMPlayer, SpeechStream, _output_format, _wav_bytes


class AudioError(Exception):
    pass


class AudioBackend:
    PortAudioError = AudioError

    def __init__(self, native_channels=8, supported=None, conversion=False):
        self.original = dict(
            index=3, name="Selected speaker", hostapi=0, max_output_channels=2, default_samplerate=48000
        )
        self.native = dict(self.original, index=12, hostapi=1, max_output_channels=native_channels)
        self.devices = [self.original, self.native]
        self.supported = supported if supported is not None else {3: {2}, 12: {native_channels}}
        self.conversion = conversion
        self.checks, self.streams = [], []

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
        self.check_output_settings(**{k: v for k, v in settings.items() if k != "latency"})
        stream = SimpleNamespace(
            settings=settings,
            writes=[],
            latency=0.07,
            start=lambda: None,
            stop=lambda: None,
            close=lambda: None,
        )

        def write(samples):
            assert samples.dtype == np.float32 and samples.shape[1] == settings["channels"]
            stream.writes.append(samples.copy())
            return False

        stream.write = write
        self.streams.append(stream)
        return stream


@pytest.fixture
def backend(monkeypatch):
    # Exercise automatic MME -> WASAPI selection on every test platform.
    monkeypatch.setattr("interpreter.speech.os", SimpleNamespace(name="nt"))
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
