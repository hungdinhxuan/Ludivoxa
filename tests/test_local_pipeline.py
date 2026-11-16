"""Offline integration contracts: real HTTP, Paddle adapter, worker and overlay signals."""

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
import requests
from PySide6.QtWidgets import QApplication

from interpreter.config import Config, LLMSettings, OCRBackend, OverlayMode, PaddleSettings, TranslationBackend
from interpreter.gui.workers import ProcessWorker
from interpreter.language_detection import detect_language
from interpreter.languages import SAMPLE_TEXT, contains_script
from interpreter.llm_translate import LLMTranslationError, LLMTranslator
from interpreter.models import ModelLoadError
from interpreter.paddle_ocr import PaddleOCR

LANGUAGES = ["English", "Japanese"]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def endpoint(monkeypatch):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "message": {"content": "Xin chào"},
                        "choices": [{"message": {"content": "Xin chào"}}],
                    }
                ).encode()
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    original_connect = socket.socket.connect

    def local_only(sock, address):
        assert address[0] == "127.0.0.1", f"Unexpected network destination: {address}"
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)
    # A configured environment proxy must not receive OCR text or API credentials.
    monkeypatch.setenv("HTTP_PROXY", "http://cloud.invalid:9999")
    monkeypatch.setenv("ALL_PROXY", "http://cloud.invalid:9999")
    yield f"http://127.0.0.1:{server.server_port}", calls
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("language", LANGUAGES)
def test_detection_and_validation(language):
    text = SAMPLE_TEXT[language]
    assert contains_script(text, language)
    assert contains_script(text, "Auto")
    assert detect_language(text) == language


def test_mixed_scripts_and_noise():
    assert detect_language("Start こんにちは") == "Japanese"
    assert detect_language("勇者") == "Japanese"
    assert not contains_script("12345 !!!", "Auto")


@pytest.mark.parametrize("provider", ["ollama", "openai"])
@pytest.mark.parametrize("mode", [OverlayMode.BANNER, OverlayMode.INPLACE])
@pytest.mark.parametrize("language", ["English", "Japanese"])
def test_capture_ocr_http_translation_overlay(endpoint, qapp, provider, mode, language):
    base, calls = endpoint
    settings = LLMSettings(provider=provider, base_url=base, model="local-test")
    config = Config(
        ocr_backend=OCRBackend.PADDLE, translation_backend=TranslationBackend.LLM, source_language="Auto", llm=settings
    )
    worker = ProcessWorker(config)
    worker.set_mode(mode)
    # This contract uses a synthetic native predictor; real spawn/IPC behavior
    # is verified separately with local models in test_paddle_inference.
    worker._ocr = PaddleOCR(config.paddle)
    text = SAMPLE_TEXT[language] + " HP 100"
    worker._ocr._model = SimpleNamespace(
        predict=lambda *args, **kwargs: [
            {
                "rec_texts": [text, "###"],
                "rec_scores": [0.99, 0.99],
                "rec_polys": [[[1, 2], [90, 2], [90, 20], [1, 20]]] * 2,
            }
        ]
    )
    worker._translator = LLMTranslator(settings, source_language="Auto")
    results = []
    worker.text_ready.connect(lambda text, generation: results.append(text))
    worker.regions_ready.connect(lambda regions, generation: results.append(regions))
    start = time.perf_counter()
    worker._process_frame(np.zeros((32, 100, 4), dtype=np.uint8))
    elapsed = (time.perf_counter() - start) * 1000
    expected_path = "/api/chat" if provider == "ollama" else "/v1/chat/completions"
    assert len(calls) == 2  # warmup + translation; no cloud fallback or noise translation
    assert all(path == expected_path for path, _ in calls)
    prompt = calls[-1][1]["messages"][0]["content"]
    assert f"from {language} into Vietnamese" in prompt
    assert calls[-1][1]["messages"][-1]["content"].startswith(SAMPLE_TEXT[language])
    if mode == OverlayMode.BANNER:
        assert results == ["Xin chào"]
    else:
        assert results[0][0][0] == "Xin chào"
        assert results[0][0][1] == {"x": 1, "y": 2, "width": 89, "height": 18}
    worker._process_frame(np.zeros((32, 100, 4), dtype=np.uint8))
    assert len(calls) == 2  # repeat frame is cached
    print(f"{provider} {language} {mode.value}: {elapsed:.1f} ms (synthetic OCR and HTTP fixture)")


@pytest.mark.parametrize("provider", ["ollama", "openai"])
def test_first_read_ocr_jitter_reaches_http_and_audio_once(endpoint, qapp, monkeypatch, provider):
    from PySide6.QtCore import QObject, Signal

    from interpreter.config import OutputMode
    from interpreter.gui import workers
    from interpreter.ocr import OCRResult
    from interpreter.speech import SpeechController, _wav_bytes

    base, calls = endpoint
    config = Config(
        source_language="English",
        output_mode=OutputMode.TTS,
        llm=LLMSettings(provider=provider, base_url=base, model="local-test"),
    )
    worker = ProcessWorker(config)
    worker.stream_speech = False
    worker._translator = LLMTranslator(config.llm, source_language="English")
    worker._translator._loaded = True
    worker._ocr = MagicMock()
    pixels = np.zeros((70, 280, 4), dtype=np.uint8)
    pixels[20:40, 20:240, :3] = 230
    box = dict(x=20, y=20, width=220, height=20)
    clock = [0.0]
    monkeypatch.setattr(workers, "time", SimpleNamespace(monotonic=lambda: clock[0], perf_counter=time.perf_counter))

    class Player(QObject):
        finished = Signal()
        error = Signal(str)

        def __init__(self):
            super().__init__()
            self.play = MagicMock()
            self.stop = MagicMock()

    player = Player()
    voice = MagicMock()
    voice.synthesize.return_value = _wav_bytes(np.full(4800, 0.1, dtype=np.float32), 48000)
    controller = SpeechController(synthesizer=voice, player=player)
    worker.speech_ready.connect(lambda text, _: controller.submit(text, "test-voice.onnx"))
    try:
        for i in range(25):
            text = "Wait by the gate." if i % 2 == 0 else "Wait by the gate.V"
            worker._ocr.extract_text_regions.return_value = [OCRResult(text, box)]
            clock[0] = i * 0.15
            worker._process_frame(pixels, 1)
            qapp.processEvents()
        deadline = time.monotonic() + 3
        while player.play.call_count == 0 and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.002)
        assert len(calls) == 1
        assert calls[0][1]["messages"][-1]["content"].endswith("Wait by the gate.")
        voice.synthesize.assert_called_once()
        assert voice.synthesize.call_args.args[0] == "Xin chào"
        player.play.assert_called_once()
        assert len(player.play.call_args.args[0]) > 44  # Actual nonempty WAV handed to playback.
    finally:
        controller.close()
        worker._translator._session.close()


def test_auto_context_mixed_text_and_manual_override(endpoint):
    base, calls = endpoint
    translator = LLMTranslator(LLMSettings(base_url=base, model="test"), source_language="Auto")
    translator.translate(SAMPLE_TEXT["English"])
    mixed = "こんにちは Hello 세계"
    translator.translate(mixed)
    messages = calls[-1][1]["messages"]
    assert messages[-1]["content"] == mixed
    assert messages[-2] == {"role": "assistant", "content": "Xin chào"}
    assert "Japanese" in messages[0]["content"]
    manual = LLMTranslator(LLMSettings(base_url=base, model="test"), source_language="French")
    manual.translate("Menu")
    assert "from French" in calls[-1][1]["messages"][0]["content"]


@pytest.mark.parametrize("provider,path", [("ollama", "/api/chat"), ("openai", "/v1/chat/completions")])
def test_lan_route_and_failure_has_no_fallback(provider, path):
    translator = LLMTranslator(LLMSettings(provider=provider, base_url="http://192.168.1.50:8000", model="local"))
    translator._loaded = True
    translator._session.post = MagicMock(side_effect=requests.ConnectionError("offline"))
    with pytest.raises(LLMTranslationError):
        translator.translate("こんにちは")
    assert translator._session.post.call_count == 1
    assert translator._session.post.call_args.args[0] == f"http://192.168.1.50:8000{path}"
    assert translator._session.trust_env is False
    assert translator._session.post.call_args.kwargs["allow_redirects"] is False


def test_redirects_are_not_followed():
    translator = LLMTranslator(LLMSettings(model="test"))
    translator._loaded = True
    translator._session.post = MagicMock(return_value=SimpleNamespace(status_code=302))
    with pytest.raises(LLMTranslationError, match="redirect"):
        translator.translate("こんにちは")
    assert translator._session.post.call_count == 1


def test_local_model_validation_precedes_import(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "paddleocr", None)
    with pytest.raises(ModelLoadError, match="never downloaded"):
        PaddleOCR(PaddleSettings(detection_model_dir=str(tmp_path))).load()


def test_paddle_load_settings_and_output(tmp_path, monkeypatch):
    for name in ("inference.yml", "inference.json", "inference.pdiparams"):
        (tmp_path / name).touch()
    (tmp_path / "inference.yml").write_text("Global:\n  model_name: test_model\n", encoding="utf-8")
    pipeline = MagicMock()
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=pipeline))
    settings = PaddleSettings(detection_model_dir=str(tmp_path), recognition_model_dir=str(tmp_path))
    ocr = PaddleOCR(settings)
    ocr.load()
    ocr.load()
    assert pipeline.call_count == 1
    kwargs = pipeline.call_args.kwargs
    assert kwargs["cpu_threads"] == 2 and kwargs["device"] == "cpu"
    assert not kwargs["use_textline_orientation"] and not kwargs["use_doc_unwarping"]
    pipeline.return_value.predict.return_value = [
        {
            "rec_texts": ["Hello", "low", "", "invalid"],
            "rec_scores": [0.99, 0.1, 0.9, 0.9],
            "rec_polys": [[[-2, 0], [50, 0], [50, 20], [-2, 20]]] * 3 + [[[float("nan"), 0]] * 4],
        }
    ]
    frame = np.full((24, 60, 4), [11, 22, 33, 255], dtype=np.uint8)
    regions = ocr.extract_text_regions(frame)
    assert len(regions) == 1 and regions[0].text == "Hello"
    assert regions[0].bbox == {"x": 0, "y": 0, "width": 50, "height": 20}
    assert list(pipeline.return_value.predict.call_args.args[0][0, 0]) == [11, 22, 33]


def test_config_compatibility_and_paddle_roundtrip(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("llm:\n  target_language: French\n", encoding="utf-8")
    config = Config.load(str(path))
    assert config.llm.target_language == "French"
    assert config.ocr_backend == OCRBackend.MEIKI and config.source_language == "Japanese"
    config.ocr_backend = OCRBackend.PADDLE
    config.source_language = "Auto"
    config.paddle = PaddleSettings("C:/models/det", "C:/models/rec")
    config.save(str(path))
    loaded = Config.load(str(path))
    assert loaded.paddle == config.paddle and loaded.source_language == "Auto"
    assert LLMSettings().target_language == "Vietnamese"
    assert PaddleSettings.from_dict({"cpu_threads": -1, "max_side": "bad"}) == PaddleSettings()


def test_local_onnx_exports_use_existing_paddle_pipeline_with_cpu_limits(tmp_path, monkeypatch):
    for name in ("det", "rec"):
        path = tmp_path / name
        path.mkdir()
        (path / "inference.yml").write_text(f"Global:\n  model_name: {name}\n", encoding="utf-8")
        (path / "inference.onnx").touch()
    pipeline = MagicMock()
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=pipeline))
    settings = PaddleSettings(str(tmp_path / "det"), str(tmp_path / "rec"))
    ocr = PaddleOCR(settings)
    ocr.load()
    kwargs = pipeline.call_args.kwargs
    assert kwargs["engine"] == "onnxruntime"
    assert kwargs["engine_config"]["providers"] == ["CPUExecutionProvider"]
    assert kwargs["engine_config"]["intra_op_num_threads"] == 2
    assert kwargs["engine_config"]["inter_op_num_threads"] == 1
    assert kwargs["text_recognition_batch_size"] == 1
    # An inference error must not change runtime or download other weights.
    pipeline.return_value.predict.side_effect = RuntimeError("invalid graph")
    with pytest.raises(RuntimeError, match="invalid graph"):
        ocr.extract_text_regions(np.zeros((24, 60, 4), dtype=np.uint8))
    assert pipeline.call_count == 1
    (tmp_path / "rec" / "inference.onnx").unlink()
    with pytest.raises(ModelLoadError, match="never downloaded"):
        PaddleOCR(settings).load()
    assert pipeline.call_count == 1
