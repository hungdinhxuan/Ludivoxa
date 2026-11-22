"""Opt-in, private HTTP access to the desktop app's translation and speech engines."""

import json
import re
import secrets
import socket
import threading
import time
import wave
from collections import OrderedDict
from concurrent.futures import CancelledError
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from ipaddress import IPv4Address, ip_address, ip_network
from urllib.parse import urlsplit, urlunsplit

from . import __version__, log
from .config import CHARACTER_GENDERS, TranslationBackend
from .language_detection import detect_language
from .languages import SOURCE_LANGUAGES, target_language_or_default
from .llm_translate import LLMTranslationError, LLMTranslator, normalize_base_url
from .models import ModelLoadError
from .speech import clean_speech_text

DEFAULT_PORT = 8765
_PRIVATE_NETWORKS = tuple(map(ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
_MAX_BODY = 16384
_MAX_AUDIO = 8 * 1024 * 1024
_MAX_SESSIONS = 32
_ROUTES = {"/v1/translate": "translate", "/v1/speech": "speech"}
logger = log.get_logger()


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def validate_listener(host, port, token):
    try:
        address = IPv4Address(host)
    except ValueError:
        raise ValueError("API host must be 127.0.0.1 or a private IPv4 address.") from None
    if host != "127.0.0.1" and not any(address in network for network in _PRIVATE_NETWORKS):
        raise ValueError("API host must be 127.0.0.1 or a private IPv4 address.")
    if not 0 <= port <= 65535:
        raise ValueError("API port must be between 0 and 65535.")
    if (token or host != "127.0.0.1") and not re.fullmatch(r"[A-Za-z0-9._~-]{24,128}", token):
        raise ValueError("Set LUDIVOXA_API_TOKEN to a random 24–128 character URL-safe token.")


def _local_upstream(settings):
    """Pin localhost to loopback and reject public destinations for API requests."""
    url = normalize_base_url(settings.provider, settings.base_url)
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        address = ip_address("127.0.0.1" if hostname == "localhost" else hostname)
        port = parsed.port
        local = address.is_loopback or any(address in network for network in _PRIVATE_NETWORKS)
    except (TypeError, ValueError):
        local = False
    if (
        not local
        or parsed.scheme not in ("http", "https")
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
        or "\\" in url
        or any(char.isspace() for char in url)
    ):
        raise ApiError(503, "local_endpoint_required", "Configure a loopback or private LAN LLM address in Engines.")
    netloc = f"[{address}]" if address.version == 6 else str(address)
    if port is not None:
        netloc += f":{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _fields(data, *allowed):
    if data.keys() - set(allowed):
        raise ApiError(400, "invalid_request", "The request contains an unknown field.")


def _string(data, key, limit, default=None):
    if key not in data and default is not None:
        return default
    value = data.get(key)
    if isinstance(value, str) and 0 < len(value.strip()) <= limit:
        try:
            value.encode("utf-8")
            return value.strip()
        except UnicodeEncodeError:
            pass
    raise ApiError(400, "invalid_request", f"{key} must be a nonempty string of up to {limit} characters.")


class _Engines:
    def __init__(self, config, render_speech):
        self.config = config
        self.render_speech = render_speech
        self.sessions = OrderedDict()

    def translate(self, data):
        _fields(data, "text", "source_language", "target_language", "game_profile", "session_id", "speaker")
        text = _string(data, "text", 1200)
        title = _string(data, "game_profile", 256, "")
        profile = self.config.game_profiles.get(title)
        if title and profile is None:
            raise ApiError(404, "profile_not_found", "No game profile matches that title.")
        source = _string(data, "source_language", 32, (profile.source_language if profile else "") or "Auto")
        source = next((name for name in SOURCE_LANGUAGES if name.casefold() == source.casefold()), "")
        if not source:
            raise ApiError(400, "invalid_request", "Use a supported source language name or Auto.")
        speaker = None
        if "speaker" in data:
            info = data["speaker"]
            if not isinstance(info, dict):
                raise ApiError(400, "invalid_request", "speaker must be an object.")
            _fields(info, "name", "gender")
            name = _string(info, "name", 80)
            gender = _string(info, "gender", 16, "unknown").casefold()
            if gender not in CHARACTER_GENDERS:
                raise ApiError(400, "invalid_request", "Use male, female, nonbinary or unknown for speaker gender.")
            speaker = (name, gender)

        session_id = _string(data, "session_id", 32, "")
        previous = self.sessions.get(session_id)
        if session_id and previous is None:
            raise ApiError(404, "session_not_found", "Omit session_id to start a new session.")
        llm = self.config.llm
        target = _string(data, "target_language", 64, target_language_or_default(llm.target_language))
        if self.config.translation_backend != TranslationBackend.LLM or not llm.model:
            raise ApiError(503, "translation_unconfigured", "Choose an LLM provider and model in Engines.")
        settings = replace(
            llm, base_url=_local_upstream(llm), target_language=target, context_lines=min(llm.context_lines, 12)
        )
        prompt = profile.system_prompt if profile else ""
        mode = profile.prompt_mode if profile else "append"
        scope = (settings, source, title, prompt, mode)
        fresh = previous is None or previous[0] != scope
        if fresh:
            translator = LLMTranslator(settings, cache_size=16, similarity_threshold=1.0, source_language=source)
            translator.set_capture_profile(title, source, prompt, prompt_mode=mode)
        else:
            translator = previous[1]
        try:
            translated, cached = translator.translate(text, speaker=speaker, warmup=False)
            if not translated:
                raise ApiError(502, "empty_translation", "The configured LLM returned no translation.")
        except Exception:
            if fresh:
                translator.close()
            raise
        if previous is not None and fresh:
            previous[1].close()
        session_id = session_id or secrets.token_hex(16)
        self.sessions[session_id] = (scope, translator)
        self.sessions.move_to_end(session_id)
        if len(self.sessions) > _MAX_SESSIONS:
            self.sessions.popitem(last=False)[1][1].close()
        return {
            "text": translated,
            "source_language": detect_language(text) if source == "Auto" else source,
            "target_language": target,
            "session_id": session_id,
            "cached": cached,
        }

    def speech(self, data, cancelled):
        _fields(data, "text", "stream", "speaker_id")
        text = clean_speech_text(_string(data, "text", 1200))
        if not any(char.isalnum() for char in text):
            raise ApiError(400, "invalid_request", "Enter text that can be spoken.")
        stream = data.get("stream", False)
        if type(stream) is not bool:
            raise ApiError(400, "invalid_request", "stream must be true or false.")
        if not self.config.target_supports_speech:
            raise ApiError(503, "speech_unconfigured", "Configure a local voice for the target language first.")
        model, speaker_id, rate, reference = self.config.speech_voice_settings
        if "speaker_id" in data:
            speaker_id = data["speaker_id"]
            if type(speaker_id) is not int or not 0 <= speaker_id < 1024 or reference:
                raise ApiError(
                    400, "invalid_request", "Use a valid preset speaker ID with no clone reference selected."
                )
        audio = self.render_speech(text, (model, speaker_id, rate, reference), cancelled)
        if audio is None:
            raise ApiError(503, "engine_busy", "Desktop speech is active. Retry when it is idle.")
        return audio, stream


def _pcm_chunks(audio):
    rate, total = 0, 0
    for chunk in audio:
        with wave.open(BytesIO(chunk)) as source:
            sample_rate = source.getframerate()
            if source.getsampwidth() != 2 or source.getnchannels() != 1 or (rate and rate != sample_rate):
                raise ValueError("Unsupported voice audio format.")
            rate = sample_rate
            pcm = source.readframes(source.getnframes())
        if pcm:
            total += len(pcm)
            if total > _MAX_AUDIO:
                raise ValueError("Audio exceeds the response limit.")
            yield rate, pcm


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        pass  # Never record request URLs, credentials or dialogue.

    def _headers(self, status, content_type, length=None):
        self.close_connection = True
        self.send_response(status)
        self.send_header("Connection", "close")
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 401:
            self.send_header("WWW-Authenticate", "Bearer")
        if status == 503:
            self.send_header("Retry-After", "1")
        if length is None:
            self.send_header("Transfer-Encoding", "chunked")
        else:
            self.send_header("Content-Length", str(length))
        self.response_started = True
        self.end_headers()

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _auth(self):
        if self.headers.get_all("Host") not in self.server.host_headers or "Origin" in self.headers:
            raise ApiError(403, "forbidden", "Use a direct HTTP client and the configured API address.")
        supplied = self.headers.get("Authorization", "").encode("latin-1")
        if self.server.authorization and not secrets.compare_digest(supplied, self.server.authorization):
            raise ApiError(401, "unauthorized", "Supply the API bearer token.")

    def handle_expect_100(self):
        self._json(417, {"error": {"code": "expectation_failed", "message": "Send the JSON body directly."}})
        return False

    def _read_json(self):
        sizes = self.headers.get_all("Content-Length") or []
        if len(sizes) != 1 or not re.fullmatch(r"[0-9]{1,9}", sizes[0]) or "Transfer-Encoding" in self.headers:
            raise ApiError(400, "invalid_request", "Supply exactly one valid Content-Length header.")
        if self.headers.get_content_type() != "application/json":
            raise ApiError(415, "unsupported_media_type", "Use application/json.")
        size = int(sizes[0])
        if size > _MAX_BODY:
            raise ApiError(413, "request_too_large", "The JSON body exceeds 16 KiB.")
        try:
            raw = self.rfile.read(size)
            data = json.loads(raw.decode("utf-8")) if len(raw) == size else None
        except (ValueError, RecursionError):
            data = None
        if not isinstance(data, dict):
            raise ApiError(400, "invalid_request", "Send a UTF-8 JSON object.")
        return data

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        self.response_started = False
        try:
            try:
                self._auth()
                if method == "GET" and self.path == "/v1/health":
                    self._json(200, {"status": "ok", "version": __version__})
                    return
                route = _ROUTES.get(self.path) if method == "POST" else None
                if route is None:
                    raise ApiError(404, "not_found", "Unknown API route.")
                data = self._read_json()
                gate = self.server.inference[route]
                if not gate.acquire(blocking=False):
                    raise ApiError(503, "engine_busy", "An API request is in progress. Retry shortly.")
                try:
                    if self.server.stopping.is_set():
                        raise CancelledError()
                    if self.server.capture_active():
                        raise ApiError(503, "capture_active", "Stop desktop capture before calling this endpoint.")
                    if route == "translate":
                        self._json(200, self.server.engines.translate(data))
                    else:
                        self._send_audio(data)
                finally:
                    gate.release()
            except CancelledError:
                raise ApiError(503, "engine_busy", "Speech was interrupted by the desktop or app shutdown.") from None
            except (LLMTranslationError, ModelLoadError):
                raise ApiError(
                    502, "translation_failed", "The configured LLM could not complete the translation."
                ) from None
            except (ValueError, wave.Error, EOFError):
                raise ApiError(
                    502, "engine_error", "The configured engine could not produce a valid response."
                ) from None
        except ApiError as exc:
            if not self.response_started:
                try:
                    self._json(exc.status, {"error": {"code": exc.code, "message": str(exc)}})
                except OSError:
                    pass
        except OSError:
            pass  # Client disconnected, timed out, or the app closed the connection.
        except Exception as exc:
            logger.warning("local API request failed", error_type=type(exc).__name__)
            if not self.response_started:
                try:
                    self._json(500, {"error": {"code": "engine_error", "message": "The configured engine failed."}})
                except OSError:
                    pass

    def _send_audio(self, data):
        deadline = time.monotonic() + 60
        audio, stream = self.server.engines.speech(
            data, lambda: self.server.stopping.is_set() or self.server.capture_active() or time.monotonic() > deadline
        )
        try:
            frames = _pcm_chunks(audio)
            first = next(frames, None)
            if first is None:
                raise ApiError(502, "empty_audio", "The configured voice returned no audio.")
            rate, pcm = first
            if stream:
                self._headers(200, f"audio/pcm; rate={rate}; bits=16; encoding=signed-int; channels=1")
                self._write_chunk(pcm)
                for _, pcm in frames:
                    self._write_chunk(pcm)
                self.wfile.write(b"0\r\n\r\n")
            else:
                with BytesIO() as buffer:
                    with wave.open(buffer, "wb") as wav:
                        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                        wav.writeframesraw(pcm)
                        for _, pcm in frames:
                            wav.writeframesraw(pcm)
                    self._headers(200, "audio/wav", buffer.tell())
                    self.wfile.write(buffer.getbuffer())
        finally:
            audio.cancel()

    def _write_chunk(self, pcm):
        self.wfile.write(f"{len(pcm):x}\r\n".encode("ascii") + pcm + b"\r\n")


class LocalApi(ThreadingHTTPServer):
    """Bounded server. Each inference engine admits one API call at a time."""

    def __init__(self, config, render_speech, host="127.0.0.1", port=DEFAULT_PORT, token="", *, capture_active=None):
        validate_listener(host, port, token)
        self.engines = _Engines(config, render_speech)
        self.capture_active = capture_active or (lambda: False)
        super().__init__((host, port), _Handler)
        names = (host, "localhost") if host == "127.0.0.1" else (host,)
        self.host_headers = [[f"{name}:{self.server_port}"] for name in names]
        if self.server_port == 80:
            self.host_headers.extend([name] for name in names)
        self.authorization = f"Bearer {token}".encode("ascii") if token else b""
        self.inference = {route: threading.Lock() for route in _ROUTES.values()}
        self.stopping = threading.Event()
        self._lock = threading.Lock()
        self._connections = set()
        self._thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()

    def process_request(self, request, client_address):
        with self._lock:
            if len(self._connections) >= 8 or self.stopping.is_set():
                self.shutdown_request(request)
                return
            self._connections.add(request)
        request.settimeout(5)
        try:
            super().process_request(request, client_address)
        except Exception:
            with self._lock:
                self._connections.discard(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._lock:
                self._connections.discard(request)

    def close(self):
        self.stopping.set()
        self.shutdown()
        self.server_close()
        with self._lock:
            for connection in self._connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
