"""HTTP API contracts, private inference routing and desktop speech priority."""

import json
import threading
import wave
from concurrent.futures import CancelledError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from unittest.mock import MagicMock

import pytest
import requests

from ludivoxa.config import Config, LLMSettings, TranslationBackend
from ludivoxa.local_api import LocalApi, validate_listener
from ludivoxa.speech import SpeechController, SpeechStream, _pcm_wav

TOKEN = "test_token_24_characters_only"
PCM = b"\x00\x20" * 120


@pytest.fixture
def upstream():
    calls, streaming = [], threading.Event()
    proceed = threading.Event()

    class Chat(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, payload, self.headers.get("Authorization")))
            dialogue = payload["messages"][-1]["content"]
            if dialogue == "wait":
                streaming.set()
                assert proceed.wait(5)
            if dialogue == "redirect":
                self.send_response(307)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/outside")
                self.end_headers()
                return
            message = {"content": "Xin chào"}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"message": message, "choices": [{"message": message}]}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Chat)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, streaming, proceed
    finally:
        proceed.set()
        server.shutdown()
        server.server_close()
        thread.join(1)


@pytest.fixture
def api_client(upstream):
    config = Config(
        translation_backend=TranslationBackend.LLM,
        llm=LLMSettings(base_url=upstream[0], model="test-model", api_key="upstream-key"),
        game_profiles={
            "Example": {"source_language": "Korean", "system_prompt": "Names stay intact.", "prompt_mode": "append"}
        },
        tts_model_path="configured.onnx",
    )
    audio_calls = []

    def render(text, settings, cancelled):
        audio_calls.append((text, settings))
        result = SpeechStream()
        for pcm in (PCM, PCM):
            result.put(_pcm_wav(pcm, 24000), cancelled)
        result.finish()
        return result

    server = LocalApi(config, render, port=0, token=TOKEN)
    client = requests.Session()
    client.trust_env = False
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    base = f"http://127.0.0.1:{server.server_port}/v1"

    def post(route, data):
        return client.post(base + route, json=data, timeout=5)

    try:
        yield config, client, base, post, audio_calls
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize("provider,path", [("ollama", "/api/chat"), ("openai", "/v1/chat/completions")])
def test_translation_context_speaker_and_profile(api_client, upstream, provider, path, monkeypatch):
    config, _, _, post, _ = api_client
    config.llm.provider = provider
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    first = post(
        "/translate",
        {
            "text": "안녕하세요",
            "game_profile": "Example",
            "speaker": {"name": "Mina", "gender": "female"},
        },
    )
    assert first.status_code == 200
    reply = first.json()
    session = reply["session_id"]
    assert (reply["text"], reply["source_language"], reply["target_language"]) == ("Xin chào", "Korean", "Vietnamese")
    assert len(upstream[1]) == 1  # No additional warm-up for each API session.
    route, payload, bearer = upstream[1][-1]
    assert route == path and bearer == "Bearer upstream-key"
    assert "from Korean into Vietnamese" in payload["messages"][0]["content"]
    assert "Names stay intact." in payload["messages"][0]["content"]
    assert json.loads(payload["messages"][-1]["content"]) == {
        "speaker": "Mina",
        "gender": "female",
        "dialogue": "안녕하세요",
    }
    second = post("/translate", {"text": "감사합니다", "session_id": session, "game_profile": "Example"})
    assert second.status_code == 200 and second.json()["session_id"] == session
    assert [message["role"] for message in upstream[1][-1][1]["messages"]] == ["system", "user", "assistant", "user"]
    isolated = post("/translate", {"text": "감사합니다", "game_profile": "Example"})
    assert isolated.json()["session_id"] != session
    assert len(upstream[1][-1][1]["messages"]) == 2
    changed = post("/translate", {"text": "감사합니다", "session_id": session, "target_language": "English"})
    assert changed.json()["target_language"] == "English"
    assert len(upstream[1][-1][1]["messages"]) == 2
    assert post("/translate", {"text": "hello", "session_id": "unknown"}).status_code == 404


@pytest.mark.parametrize(
    ("text", "source", "detected"),
    [
        ("Wait for me.", "Auto", "English"),
        ("Start また会いましょう。", "Auto", "Japanese"),
        ("Start 안녕하세요", "Auto", "Korean"),
        ("欢迎来到村庄。", "Chinese", "Chinese"),
    ],
)
def test_translation_scripts(api_client, text, source, detected):
    reply = api_client[3]("/translate", {"text": text, "source_language": source}).json()
    assert reply["source_language"] == detected and reply["text"] == "Xin chào"


def test_api_request_validation_and_no_cloud_forwarding(api_client, upstream):
    config, client, base, post, _ = api_client
    assert client.get(base + "/health", timeout=5).json()["status"] == "ok"
    assert client.get(base + "/health", headers={"Authorization": None}, timeout=5).status_code == 401
    assert client.get(base + "/health", headers={"Host": "wrong.example"}, timeout=5).status_code == 403
    assert client.get(base + "/health", headers={"Origin": "http://wrong.example"}, timeout=5).status_code == 403
    assert post("/translate", {"text": "hello", "base_url": upstream[0]}).status_code == 400
    assert post("/translate", {"text": "x" * 1201}).status_code == 400
    assert client.post(base + "/speech", data=b"{}", timeout=5).status_code == 415
    assert client.post(base + "/speech", json={"text": "x" * 17000}, timeout=5).status_code == 413
    config.llm.base_url = "https://8.8.8.8"
    reply = post("/translate", {"text": "hello"})
    assert reply.status_code == 503 and reply.json()["error"]["code"] == "local_endpoint_required"
    assert not upstream[1]
    config.llm.base_url = upstream[0]
    assert post("/translate", {"text": "redirect"}).status_code == 502
    assert len(upstream[1]) == 1


def test_translate_to_speech_wav_and_pcm(api_client):
    _, _, _, post, audio_calls = api_client
    translated = post("/translate", {"text": "Hello"}).json()["text"]
    reply = post("/speech", {"text": translated + " √", "speaker_id": 1})
    assert reply.status_code == 200 and reply.headers["Content-Type"] == "audio/wav"
    with wave.open(BytesIO(reply.content), "rb") as wav:
        assert (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) == (24000, 2, 1)
        assert wav.readframes(wav.getnframes()) == PCM * 2
    assert audio_calls == [(translated, ("configured.onnx", 1, 1.0, ""))]
    reply = post("/speech", {"text": translated, "stream": True})
    assert reply.headers["Transfer-Encoding"] == "chunked"
    assert reply.headers["Content-Type"].startswith("audio/pcm; rate=24000") and reply.content == PCM * 2
    assert post("/speech", {"text": "√ !!!"}).status_code == 400
    assert post("/speech", {"text": "hello", "speaker_id": True}).status_code == 400


def test_busy_translation_does_not_block_health_or_speech(api_client, upstream):
    _, client, base, post, _ = api_client
    _, _, started, proceed = upstream
    results = []
    thread = threading.Thread(target=lambda: results.append(post("/translate", {"text": "wait"})))
    thread.start()
    try:
        assert started.wait(2)
        assert client.get(base + "/health", timeout=2).status_code == 200
        reply = post("/translate", {"text": "queued"})
        assert reply.status_code == 503 and reply.json()["error"]["code"] == "engine_busy"
        assert post("/speech", {"text": "Xin chào"}).status_code == 200
    finally:
        proceed.set()
        thread.join(5)
    assert results[0].status_code == 200


def test_speech_yields_to_desktop_without_playing_api_audio():
    started, proceed, desktop_synthesized = (threading.Event() for _ in range(3))

    class Voice:
        def iter_audio(self, text, *args, cancelled, **kwargs):
            started.set()
            assert proceed.wait(3)
            assert cancelled()
            yield _pcm_wav(PCM, 24000)

        def synthesize(self, *args, **kwargs):
            desktop_synthesized.set()
            return _pcm_wav(PCM, 24000)

    player = MagicMock()
    controller = SpeechController(synthesizer=Voice(), player=player)
    try:
        audio = controller.render_for_api("Xin chào", ("voice.onnx", 0, 1.0, ""), lambda: False)
        assert started.wait(3)
        controller.submit("Đến lượt desktop", "voice.onnx")
        assert controller.render_for_api("Another request", ("voice.onnx", 0, 1.0, ""), lambda: False) is None
        proceed.set()
        with pytest.raises(CancelledError):
            next(iter(audio))
        assert desktop_synthesized.wait(3)
        player.play.assert_not_called()  # No Qt event pump, so only API could have called playback directly.
    finally:
        proceed.set()
        controller.close()


def test_lan_listener_requires_explicit_private_address_and_token():
    validate_listener("192.168.1.50", 8765, TOKEN)
    with pytest.raises(ValueError, match="TOKEN"):
        validate_listener("192.168.1.50", 8765, "")
    with pytest.raises(ValueError, match="private IPv4"):
        validate_listener("0.0.0.0", 8765, TOKEN)
    validate_listener("127.0.0.1", 8765, "")


def test_capture_defers_api_inference_without_dropping_health(upstream):
    capture = threading.Event()
    capture.set()
    config = Config(
        translation_backend=TranslationBackend.LLM, llm=LLMSettings(base_url=upstream[0], model="test-model")
    )
    synthesize = MagicMock()
    server = LocalApi(config, synthesize, port=0, capture_active=capture.is_set)
    try:
        with requests.Session() as client:
            client.trust_env = False
            url = f"http://127.0.0.1:{server.server_port}/v1"
            assert client.get(url + "/health", timeout=2).status_code == 200
            for endpoint in ("translate", "speech"):
                blocked = client.post(f"{url}/{endpoint}", json={"text": "Hello"}, timeout=2)
                assert blocked.status_code == 503 and blocked.json()["error"]["code"] == "capture_active"
            assert not upstream[1]
            synthesize.assert_not_called()
            capture.clear()
            assert client.post(url + "/translate", json={"text": "Hello"}, timeout=2).status_code == 200
    finally:
        server.close()


def test_shutdown_closes_listener():
    server = LocalApi(Config(), lambda *args: None, port=0)
    client = requests.Session()
    client.trust_env = False
    url = f"http://127.0.0.1:{server.server_port}/v1/health"
    assert client.get(url, timeout=2).status_code == 200
    server.close()
    with pytest.raises(requests.ConnectionError):
        client.get(url, timeout=2)
    client.close()
