"""Local model validation, reference enrollment/caching, and optional real cloning."""

import json
import os
import socket
import sys
import time
import wave
from concurrent.futures import CancelledError
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from ludivoxa.config import Config
from ludivoxa.speech import VieneuSynthesizer, clean_speech_text, voice_details


@pytest.fixture
def nano(tmp_path, monkeypatch):
    for name in VieneuSynthesizer.MODEL_FILES + VieneuSynthesizer.CLONE_FILES:
        (tmp_path / name).touch()
    (tmp_path / "voices.json").write_text(json.dumps({"voices": ["Minh Quân", "Mai Anh"]}), encoding="utf-8")
    engine = MagicMock(sample_rate=24000)
    engine.infer.return_value = np.zeros(24000, dtype=np.float32)
    engine.encode_reference.return_value = (np.ones(192), np.ones((50, 256)))
    factory = MagicMock(return_value=engine)
    monkeypatch.setitem(sys.modules, "vieneu", SimpleNamespace(Vieneu=factory))
    return tmp_path, VieneuSynthesizer(), engine, factory


def test_model_files_are_required_before_sdk_can_download(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "vieneu", None)
    with pytest.raises(ValueError, match="never downloaded"):
        VieneuSynthesizer().prepare(tmp_path)


def test_nano_presets_use_cpu_and_cache_complete_wav(nano):
    path, synth, engine, factory = nano
    assert voice_details(path) == [("Minh Quân", 0), ("Mai Anh", 1)]
    audio = synth.synthesize("Xin chào", path, 1, 1.25)
    assert synth.synthesize("Xin chào", path, 1, 1.25) == audio
    factory.assert_called_once_with(mode="v3nano", onnx_dir=str(path), threads=2, steps=8)
    engine.infer.assert_called_once_with("Xin chào", voice="Mai Anh", speed=1.25, sway=-1.0)
    engine.encode_reference.assert_not_called()
    with wave.open(BytesIO(audio)) as wav_file:
        assert wav_file.getframerate() == 24000 and wav_file.getnframes() == 24000


def test_reference_is_enrolled_once_and_changed_file_invalidates_cache(nano):
    path, synth, engine, _ = nano
    reference = path / "reference.wav"
    reference.write_bytes(b"first reference")
    synth.synthesize("Xin chào", path, reference_audio=str(reference))
    synth.synthesize("Tạm biệt", path, reference_audio=str(reference))
    engine.encode_reference.assert_called_once_with(str(reference), denoise=False)
    assert isinstance(engine.infer.call_args.kwargs["voice"], dict)
    reference.write_bytes(b"a different recording")
    synth.synthesize("Xin chào", path, reference_audio=str(reference))
    assert engine.encode_reference.call_count == 2
    assert engine.infer.call_count == 3


def test_missing_reference_or_clone_graphs_does_not_use_a_preset(nano):
    path, synth, engine, _ = nano
    with pytest.raises(ValueError, match="does not exist"):
        synth.synthesize("Xin chào", path, reference_audio=str(path / "missing.wav"))
    reference = path / "ref.wav"
    reference.touch()
    (path / "speaker_encoder.onnx").unlink()
    with pytest.raises(ValueError, match="no download or fallback"):
        synth.synthesize("Xin chào", path, reference_audio=str(reference))
    engine.infer.assert_not_called()


def test_invalid_audio_or_speaker_is_reported(nano):
    path, synth, engine, _ = nano
    with pytest.raises(ValueError, match="speaker"):
        synth.synthesize("Xin chào", path, 9)
    engine.infer.return_value = np.array([float("nan")])
    with pytest.raises(ValueError, match="invalid audio"):
        synth.synthesize("Xin chào", path)


def test_reference_configuration_is_optional_and_roundtrips(tmp_path):
    path = tmp_path / "config.yml"
    Config(tts_reference_audio="C:/voices/giọng mẫu.wav").save(str(path))
    assert Config.load(str(path)).tts_reference_audio == "C:/voices/giọng mẫu.wav"
    assert Config().tts_reference_audio == ""


def test_superseded_paragraph_stops_between_chunks_without_caching_partial_audio(nano):
    path, synth, engine, _ = nano
    cancelled = [False]
    generated = []

    def chunks(*args, **kwargs):
        generated.append(1)
        cancelled[0] = True
        yield np.ones(2400, dtype=np.float32)
        generated.append(2)
        yield np.ones(2400, dtype=np.float32)

    engine.infer_stream.side_effect = chunks
    with pytest.raises(CancelledError):
        synth.synthesize("A superseded paragraph", path, cancelled=lambda: cancelled[0])
    assert generated == [1]
    assert not synth._audio_cache


def test_cancellable_synthesis_buffers_all_chunks_before_playback(nano):
    path, synth, engine, _ = nano
    engine.infer_stream.return_value = iter([np.ones(2400), np.zeros(100), np.ones(2400)])
    audio = synth.synthesize("Xin chào", path, cancelled=lambda: False)
    with wave.open(BytesIO(audio)) as wav_file:
        assert wav_file.getnframes() == 4900
    assert synth.synthesize("Xin chào", path, cancelled=lambda: False) == audio
    engine.infer_stream.assert_called_once()


def test_nano_normalizer_does_not_expand_filtered_game_markers(monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError("Network request during text normalization")

    monkeypatch.setattr(socket.socket, "connect", forbid)
    normalize = pytest.importorskip("vieneu_utils.phonemize_text").normalize_to_chunks_v3_with_gaps
    text = "Xin chào. √ ▼ ~"
    # Reproduce the unwanted words with the actual optional SDK normalizer,
    # then verify that the text we send it no longer triggers those readings.
    before = " ".join(normalize(text)[0])
    assert "căn bậc hai" in before and "khoảng" in before
    after = " ".join(normalize(clean_speech_text(text))[0])
    assert after == "xin chào."


@pytest.mark.skipif(not os.environ.get("LUDIVOXA_TEST_CLONE_MODEL"), reason="Local Nano graphs required")
def test_real_offline_preset_and_reference_cloning(monkeypatch, tmp_path):
    def forbid(*args, **kwargs):
        raise AssertionError("Network request during local voice cloning")

    monkeypatch.setattr(socket.socket, "connect", forbid)
    model = Path(os.environ["LUDIVOXA_TEST_CLONE_MODEL"])
    synth = VieneuSynthesizer()
    start = time.perf_counter()
    audio = synth.synthesize("Xin chào. Chào mừng bạn đến với ngôi làng.", model)
    reference = tmp_path / "synthetic-reference.wav"
    reference.write_bytes(audio)
    cloned = synth.synthesize("Bạn có thể giúp tôi tìm một chai nước không?", model, reference_audio=str(reference))
    for data in (audio, cloned):
        with wave.open(BytesIO(data)) as wav_file:
            assert wav_file.getframerate() == 24000
            assert wav_file.getnframes() > 24000
    assert synth._reference_voice is not None
    print(f"Real preset + synthetic-reference clone including load: {(time.perf_counter() - start) * 1000:.1f}ms")
