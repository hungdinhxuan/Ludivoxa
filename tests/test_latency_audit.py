"""Validate the diagnostic's time calculation without recording or playing audio."""

import importlib.util
import wave
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture(scope="module")
def audit_helper():
    path = Path(__file__).parents[1] / "packaging/audit_latency.py"
    spec = importlib.util.spec_from_file_location("latency_audit_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_audio():
    rng = np.random.default_rng(7)
    rate = 8000
    reference = np.concatenate((np.zeros(800), rng.normal(0, 0.1, 6000)))
    reference = (reference * 32767).astype("<i2").astype(np.float32) / 32768
    recording = rng.normal(0, 0.001, 20000)
    recording[4000 : 4000 + len(reference)] += reference * 0.25
    return reference, recording, rate


def test_reference_match_recovers_known_delay_despite_background_noise(audit_helper):
    reference, recording, rate = sample_audio()
    index, leading, score = audit_helper.match_audio(recording, reference, rate)
    assert abs(index - (4000 + round(leading * rate))) <= 2
    assert score > 0.9


def test_background_sound_and_silent_reference_are_not_counted_as_tts(audit_helper):
    reference, _, rate = sample_audio()
    noise = np.random.default_rng(90).normal(0, 0.1, 20000)
    with pytest.raises(ValueError, match="No reliable TTS"):
        audit_helper.match_audio(noise, reference, rate)
    with pytest.raises(ValueError, match="no voiced"):
        audit_helper.match_audio(noise, np.zeros(8000), rate)


def test_loopback_onset_maps_to_observation_clock_instead_of_sample_count_only(audit_helper):
    reference, recording, rate = sample_audio()
    data = BytesIO()
    with wave.open(data, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes((reference * 32768).astype("<i2").tobytes())
    loopback = audit_helper.Loopback.__new__(audit_helper.Loopback)
    loopback.rate = rate
    loopback.chunks = [
        (100 + (offset + 800) / rate, recording[offset : offset + 800]) for offset in range(0, 20000, 800)
    ]
    result = loopback.locate(data.getvalue(), earliest=0)
    assert result["observed"] == pytest.approx(100.6)
    assert 100.59 < result["sample_estimate"] < result["observed"]
