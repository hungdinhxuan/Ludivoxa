"""Voice cloning through a loopback llama.cpp speech server."""

import base64
from concurrent.futures import CancelledError
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from urllib.parse import urlsplit

import requests

_MAX_REFERENCE_BYTES = 8 * 1024 * 1024
_MAX_AUDIO_BYTES = 8 * 1024 * 1024
_LANGUAGES = {
    "english": ("en", "Hello. This is your cloned voice."),
    "japanese": ("ja", "こんにちは。これはクローン音声のテストです。"),
    "korean": ("ko", "안녕하세요. 복제한 목소리를 시험하고 있습니다."),
    "simplified chinese": ("zh", "你好，这是克隆语音测试。"),
    "traditional chinese": ("zh", "你好，這是複製語音測試。"),
}


def clone_language(target):
    return _LANGUAGES.get(target.strip().casefold())


def local_clone_url(value):
    """Validate the dedicated loopback endpoint without resolving DNS."""
    try:
        url = urlsplit(value.strip())
        if (
            url.scheme == "http"
            and url.hostname in ("127.0.0.1", "::1")
            and url.port
            and url.path in ("", "/")
            and not (url.username is not None or url.query or url.fragment)
        ):
            return f"http://{url.netloc}"
    except ValueError:
        pass
    return ""


@dataclass(frozen=True)
class LocalCloneVoice:
    endpoint: str
    language: str
    sample: str


class LocalCloneSynthesizer:
    """Receive bounded PCM frames and keep the selected reference in memory."""

    def __init__(self):
        self._reference_key = self._reference = None

    @staticmethod
    def _endpoint(voice):
        url = local_clone_url(voice.endpoint)
        if not url:
            raise ValueError("Use a local voice server such as http://127.0.0.1:17867.")
        return url

    @staticmethod
    def _client():
        session = requests.Session()
        session.trust_env = False
        return session

    def prepare(self, voice):
        endpoint = self._endpoint(voice)
        try:
            with self._client() as session, session.get(
                endpoint + "/health", stream=True, timeout=(2, 2), allow_redirects=False
            ) as response:
                if response.status_code != 200:
                    raise ValueError("The local voice server is still loading or is unavailable.")
        except requests.RequestException as exc:
            raise ValueError("Start the local Qwen3-TTS server to use this voice.") from exc

    def _reference_b64(self, reference_audio):
        if not reference_audio:
            raise ValueError("Choose a local WAV or MP3 recording to clone.")
        path = Path(reference_audio).expanduser().resolve()
        if path.suffix.lower() not in (".wav", ".mp3") or not path.is_file():
            raise ValueError("Choose an existing WAV or MP3 recording to clone.")
        stat = path.stat()
        if not 0 < stat.st_size <= _MAX_REFERENCE_BYTES:
            raise ValueError("Choose a short voice recording under 8 MB.")
        key = (path, stat.st_mtime_ns, stat.st_size)
        if key != self._reference_key:
            with path.open("rb") as stream:
                data = stream.read(_MAX_REFERENCE_BYTES + 1)
            if not data or len(data) > _MAX_REFERENCE_BYTES:
                raise ValueError("Choose a short voice recording under 8 MB.")
            self._reference_key = key
            self._reference = base64.b64encode(data).decode("ascii")
        return self._reference

    def iter_pcm(self, voice, text, reference_audio, cancelled):
        endpoint = self._endpoint(voice)
        if cancelled():
            raise CancelledError()
        body = {
            "input": text,
            "lang": voice.language,
            "speaker_ref_b64": self._reference_b64(reference_audio),
            "response_format": "pcm16",
            "stream": True,
        }
        try:
            with self._client() as session, session.post(
                endpoint + "/tts", json=body, stream=True, timeout=(2, 15), allow_redirects=False
            ) as response:
                if response.status_code != 200:
                    raise ValueError(f"Local voice server returned HTTP {response.status_code}.")
                content_type = Message()
                content_type["content-type"] = response.headers.get("Content-Type", "")
                rate = content_type.get_param("rate", "")
                if (
                    content_type.get_content_type() != "audio/pcm"
                    or not rate.isdecimal()
                    or not 8000 <= int(rate) <= 96000
                    or content_type.get_param("bits") != "16"
                    or content_type.get_param("encoding") != "signed-int"
                    or content_type.get_param("channels", "1") != "1"
                ):
                    raise ValueError("The local voice server returned an unsupported audio format.")
                sample_rate = int(rate)
                total = 0
                pending = b""
                for chunk in response.iter_content(chunk_size=4096):
                    if cancelled():
                        raise CancelledError()
                    total += len(chunk)
                    if total > _MAX_AUDIO_BYTES:
                        raise ValueError("The local voice server exceeded the speech size limit.")
                    data = pending + chunk
                    complete = len(data) - len(data) % 2
                    pending = data[complete:]
                    if complete:
                        yield data[:complete], sample_rate
                if total == 0 or pending:
                    raise ValueError("The local voice server returned incomplete audio.")
        except requests.RequestException as exc:
            raise ValueError("Unable to read speech from the local Qwen3-TTS server.") from exc
