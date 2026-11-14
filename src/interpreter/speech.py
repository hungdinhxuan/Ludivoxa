"""Offline Vietnamese voices, background prefetch and buffered native PCM playback."""

import json
import re
import threading
import time
import unicodedata
import wave
from collections import OrderedDict
from concurrent.futures import CancelledError, ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import numpy as np
from PySide6.QtCore import QCoreApplication, QObject, Signal, Slot

from . import log

DEFAULT_VOICE_PATH = str(Path.home() / ".interpreter" / "voices" / "vi_VN-vais1000-medium.onnx")
logger = log.get_logger()


def clean_speech_text(text):
    """Keep spoken text and common punctuation; silence game/markup symbols.

    This applies only to speech, leaving OCR and the displayed translation
    intact. Nano's normalizer otherwise expands e.g. √ to "căn bậc hai" and
    decorative ~ to "khoảng". Replace removed symbols with spaces so adjacent
    words do not run together. Preserve accents, scripts, currency, percentages
    and basic numeric operators rather than limiting input to ASCII.
    """
    punctuation = ".,!?;:'\"-–—…()‘’“”。，！？；：、「」『』（）%+=/"
    result = []
    for character in unicodedata.normalize("NFC", text):
        category = unicodedata.category(character)
        variation_selector = "\ufe00" <= character <= "\ufe0f" or "\U000e0100" <= character <= "\U000e01ef"
        if not variation_selector and (category[0] in "LMN" or category == "Sc" or character in punctuation):
            result.append(character)
        else:
            result.append(" ")
    return " ".join("".join(result).split())


def voice_details(path):
    """Read local speaker labels without loading a voice."""
    if Path(path).is_dir():
        names = json.loads((Path(path) / "voices.json").read_text(encoding="utf-8"))["voices"]
        if not names or not all(isinstance(name, str) and name for name in names):
            raise ValueError("Invalid local VieNeu voices.json")
        return [(name, i) for i, name in enumerate(names)]
    data = json.loads(Path(f"{path}.json").read_text(encoding="utf-8"))
    speakers = sorted(
        ((str(name), int(index)) for name, index in data.get("speaker_id_map", {}).items()), key=lambda x: x[1]
    )
    return speakers or [(f"Speaker {i + 1}", i) for i in range(int(data.get("num_speakers", 1)))]


def available_voices(selected=""):
    directories = {Path("models/voices"), Path(DEFAULT_VOICE_PATH).parent}
    if selected:
        directories.add(Path(selected).expanduser().parent)
    paths = {p.resolve() for directory in directories for p in directory.glob("vi_*.onnx")}
    if selected and Path(selected).expanduser().is_file():
        paths.add(Path(selected).expanduser().resolve())
    voices = []
    for path in sorted(paths):
        try:
            voices.append((f"{path.stem} ({len(voice_details(path))} voices)", str(path)))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    nano_paths = {Path("models/VieNeu-TTS-v3-Nano")}
    if selected and Path(selected).expanduser().is_dir():
        nano_paths.add(Path(selected).expanduser())
    for path in sorted(nano_paths):
        if not (path / "text_encoder.onnx").is_file():
            continue
        try:
            voices.append((f"VieNeu Nano / voice cloning ({len(voice_details(path))} voices)", str(path.resolve())))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return voices


class PCMPlayer(QObject):
    """Native blocking audio writes on a dedicated thread, independent of Qt painting."""

    finished = Signal()
    error = Signal(str)
    _completed = Signal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="interpreter-audio")
        self._lock = threading.Lock()
        self._stream = None
        self._volume = 1.0
        self._generation = 0
        self._closed = False
        self.underruns = 0
        self.diagnostics = {}
        self._completed.connect(self._on_completed)
        app = QCoreApplication.instance()
        if app:
            app.aboutToQuit.connect(self.close)

    def setVolume(self, volume):
        self._volume = volume

    def play(self, wav_bytes):
        if self._closed:
            return
        self.stop()
        token = self._generation
        self._executor.submit(self._write_audio, token, wav_bytes, self._volume)

    def _write_audio(self, token, wav_bytes, volume):
        stream = None
        report = {}
        try:
            import sounddevice as sd

            device = sd.query_devices(kind="output")
            rate = int(device["default_samplerate"])
            channels = min(2, int(device["max_output_channels"]))
            with wave.open(BytesIO(wav_bytes)) as wav_file:
                if wav_file.getsampwidth() != 2:
                    raise ValueError("Piper audio must use 16-bit PCM.")
                samples = np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype="<i2")
                samples = samples.reshape(-1, wav_file.getnchannels()).mean(axis=1) / 32768.0
                source_rate = wav_file.getframerate()
            if rate != source_rate:
                size = round(len(samples) * rate / source_rate)
                samples = np.interp(np.arange(size) * source_rate / rate, np.arange(len(samples)), samples)
            pcm = np.ascontiguousarray(np.repeat(samples[:, None], channels, axis=1) * volume, dtype="float32")
            host = sd.query_hostapis(device["hostapi"])["name"]
            callback_mode = host in ("Windows WDM-KS", "ASIO")
            done = threading.Event()
            cursor = 0
            callback_underruns = 0

            def render(outdata, frames, time_info, status):
                nonlocal cursor, callback_underruns
                if status.output_underflow:
                    callback_underruns += 1
                count = min(frames, len(pcm) - cursor)
                outdata.fill(0)
                outdata[:count] = pcm[cursor : cursor + count]
                cursor += count
                if cursor == len(pcm):
                    raise sd.CallbackStop

            options = {"callback": render, "finished_callback": done.set} if callback_mode else {}
            stream = sd.OutputStream(samplerate=rate, channels=channels, dtype="float32", latency="high", **options)
            with self._lock:
                if token != self._generation or self._closed:
                    return
                self._stream = stream
                stream.start()
            start = time.monotonic()
            # Only this worker touches the native stream. Aborting from the UI
            # concurrently with a blocking write corrupts some Windows drivers.
            # Bounded writes keep explicit cancellation responsive without a
            # GUI timer or callback supplying audio.
            underrun = False
            if callback_mode:
                # WDM-KS/ASIO do not implement PortAudio's blocking API. Feed
                # the already generated PCM from their native audio callback;
                # completion still waits for the driver's drained-buffer signal.
                while not done.wait(0.05):
                    with self._lock:
                        cancelled = token != self._generation or self._closed
                    if cancelled:
                        stream.abort()
                        return
                    if time.monotonic() - start > len(pcm) / rate + 10:
                        stream.abort()
                        raise RuntimeError("Audio driver did not report playback completion.")
                if cursor != len(pcm):
                    raise RuntimeError("Audio driver stopped before all samples were delivered.")
                underrun = callback_underruns
            else:
                block = max(1, rate // 20)
                for offset in range(0, len(pcm), block):
                    with self._lock:
                        cancelled = token != self._generation or self._closed
                    if cancelled:
                        stream.abort()
                        return
                    underrun |= stream.write(pcm[offset : offset + block])
            stream.stop()  # Drain all queued samples before reporting completion.
            report = dict(
                sample_rate=rate,
                duration_seconds=len(pcm) / rate,
                processed_seconds=len(pcm) / rate,
                elapsed_seconds=time.monotonic() - start,
                underruns=int(underrun),
                host_api=host,
                callback_mode=callback_mode,
                buffer_active=False,
            )
        except Exception as exc:
            report = {"error": f"Audio playback failed: {exc}", "buffer_active": False}
        finally:
            with self._lock:
                if self._stream is stream:
                    self._stream = None
            if stream is not None:
                stream.close()
        if not self._closed:
            self._completed.emit(token, report)

    @Slot(int, object)
    def _on_completed(self, token, report):
        if token != self._generation or self._closed:
            return
        self.diagnostics = report
        if "error" in report:
            self.error.emit(report["error"])
            return
        self.underruns += report["underruns"]
        logger.debug("speech playback completed", **report)
        self.finished.emit()

    def stop(self):
        with self._lock:
            self._generation += 1
            # The writer notices within one 50 ms block plus device buffering.
            # Native stop/abort/close must all run on the writer's own thread.
        self.diagnostics["buffer_active"] = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.stop()
        self._executor.shutdown(wait=True, cancel_futures=True)


class PiperSynthesizer:
    """Two resident CPU voices and a bounded audio cache, used on one thread."""

    def __init__(self):
        self._voice = None
        self._voices = OrderedDict()
        self._audio_cache = OrderedDict()

    def prepare(self, model_path):
        path = Path(model_path).expanduser().resolve()
        config_path = Path(f"{path}.json")
        if not path.is_file() or not config_path.is_file():
            raise ValueError("Select a local Vietnamese Piper .onnx voice and matching .onnx.json file (see README).")
        key = str(path)
        if key not in self._voices:
            import onnxruntime
            from piper import PiperVoice
            from piper.config import PhonemeType, PiperConfig

            config = PiperConfig.from_dict(json.loads(config_path.read_text(encoding="utf-8")))
            if config.phoneme_type != PhonemeType.ESPEAK or config.espeak_voice not in ("vi", "vi-vn", "vi_VN"):
                raise ValueError("TTS requires a Vietnamese Piper voice.")
            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            options.add_session_config_entry("session.inter_op.allow_spinning", "0")
            self._voices[key] = PiperVoice(
                config=config,
                session=onnxruntime.InferenceSession(key, sess_options=options, providers=["CPUExecutionProvider"]),
            )
            if len(self._voices) > 2:
                self._voices.popitem(last=False)
        self._voices.move_to_end(key)
        self._voice = self._voices[key]
        return self._voice

    def synthesize(self, text, model_path, speaker_id=0, rate=1.0):
        voice = self.prepare(model_path)
        from piper.config import SynthesisConfig

        if not 0 <= speaker_id < voice.config.num_speakers:
            raise ValueError("Selected speaker does not exist in this voice model.")
        key = (str(Path(model_path).resolve()), speaker_id, rate, text)
        if key in self._audio_cache:
            self._audio_cache.move_to_end(key)
            return self._audio_cache[key]
        start = time.perf_counter()
        output = BytesIO()
        with wave.open(output, "wb") as wav_file:
            voice.synthesize_wav(
                text,
                wav_file,
                syn_config=SynthesisConfig(speaker_id=speaker_id, length_scale=1.0 / min(2.0, max(0.75, rate))),
            )
        data = output.getvalue()
        self._audio_cache[key] = data
        while len(self._audio_cache) > 16 or sum(map(len, self._audio_cache.values())) > 8 * 1024 * 1024:
            self._audio_cache.popitem(last=False)
        logger.debug("speech synthesized", tts_ms=int((time.perf_counter() - start) * 1000))
        return data


class VieneuSynthesizer:
    """Local ONNX voice cloning, with one resident CPU engine and enrolled reference."""

    MODEL_FILES = (
        "text_encoder.onnx",
        "duration_predictor.onnx",
        "vector_estimator.onnx",
        "codec_decoder.onnx",
        "config.json",
        "constants.npz",
        "voices.json",
    )
    CLONE_FILES = ("speaker_encoder.onnx", "codec_encoder.onnx", "reference_encoder.onnx")

    def __init__(self):
        self._engine = None
        self._path = None
        self._reference_key = None
        self._reference_voice = None
        self._audio_cache = OrderedDict()

    def prepare(self, model_path):
        path = Path(model_path).expanduser().resolve()
        if not all((path / name).is_file() for name in self.MODEL_FILES):
            raise ValueError(
                "Select a complete local VieNeu Nano model folder (see VOICE_CLONING.md). Models are never downloaded."
            )
        if self._engine is None or self._path != path:
            from vieneu import Vieneu

            self._engine = Vieneu(mode="v3nano", onnx_dir=str(path), threads=2, steps=8)
            self._path = path
            self._reference_key = self._reference_voice = None
            self._audio_cache.clear()
        return self._engine

    def synthesize(self, text, model_path, speaker_id=0, rate=1.0, reference_audio="", cancelled=None):
        engine = self.prepare(model_path)
        reference_key = None
        if reference_audio:
            reference = Path(reference_audio).expanduser().resolve()
            if not reference.is_file():
                raise ValueError(
                    "Reference audio does not exist. Select a local recording, or clear it to use a preset."
                )
            if not all((self._path / name).is_file() for name in self.CLONE_FILES):
                raise ValueError(
                    "Local voice-cloning graphs are missing. Provision the clone files; no download or fallback is used."
                )
            stat = reference.stat()
            reference_key = (str(reference), stat.st_mtime_ns, stat.st_size)
            if reference_key != self._reference_key:
                # Disable optional online denoiser discovery; all three encoder
                # graphs were checked locally above. Encode the reference once.
                speaker, style = engine.encode_reference(str(reference), denoise=False)
                self._reference_voice = {"speaker_emb": speaker, "style": style}
                self._reference_key = reference_key
            voice = self._reference_voice
        else:
            speakers = voice_details(self._path)
            if not 0 <= speaker_id < len(speakers):
                raise ValueError("Selected speaker does not exist in this voice model.")
            voice = speakers[speaker_id][0]
        key = (text, speaker_id, rate, reference_key)
        if key in self._audio_cache:
            self._audio_cache.move_to_end(key)
            return self._audio_cache[key]
        start = time.perf_counter()
        if cancelled is None:
            samples = np.asarray(engine.infer(text, voice=voice, speed=rate, sway=-1.0), dtype=np.float32)
        else:
            # Keep fully buffered playback, but stop obsolete synthesis between
            # Nano's existing chunks. A superseded paragraph must not occupy
            # the only speech worker until all of its chunks have completed.
            chunks = []
            iterator = iter(engine.infer_stream(text, voice=voice, speed=rate, sway=-1.0))
            while True:
                if cancelled():
                    raise CancelledError()
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                if cancelled():
                    raise CancelledError()
                chunk = np.asarray(chunk, dtype=np.float32)
                if chunk.ndim != 1 or not np.isfinite(chunk).all():
                    raise ValueError("Local voice synthesis returned invalid audio.")
                chunks.append(chunk)
            samples = np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)
        if samples.ndim != 1 or not samples.size or not np.isfinite(samples).all():
            raise ValueError("Local voice synthesis returned invalid audio.")
        output = BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(engine.sample_rate)
            wav_file.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        audio = output.getvalue()
        self._audio_cache[key] = audio
        while len(self._audio_cache) > 16 or sum(map(len, self._audio_cache.values())) > 8 * 1024 * 1024:
            self._audio_cache.popitem(last=False)
        logger.debug(
            "local cloned/preset speech synthesized",
            tts_ms=int((time.perf_counter() - start) * 1000),
            reference=bool(reference_audio),
            audio_seconds=len(samples) / engine.sample_rate,
        )
        return audio


class OfflineSynthesizer:
    """Dispatch local model files to Piper and local Nano folders to VieNeu."""

    def __init__(self):
        self._piper = PiperSynthesizer()
        self._vieneu = VieneuSynthesizer()

    def prepare(self, model_path):
        return (self._vieneu if Path(model_path).expanduser().is_dir() else self._piper).prepare(model_path)

    def synthesize(self, text, model_path, speaker_id=0, rate=1.0, reference_audio="", cancelled=None):
        if Path(model_path).expanduser().is_dir():
            return self._vieneu.synthesize(text, model_path, speaker_id, rate, reference_audio, cancelled)
        return self._piper.synthesize(text, model_path, speaker_id, rate)


class SpeechController(QObject):
    """Prefetch the latest translation while playing; don't interrupt a spoken line."""

    status_changed = Signal(str)
    _synthesized = Signal(int, int, object)
    _voice_ready = Signal(int, str)

    def __init__(self, parent=None, synthesizer=None, player=None):
        super().__init__(parent)
        self._synthesizer = synthesizer or OfflineSynthesizer()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="interpreter-speech")
        self._player = player or PCMPlayer(self)
        self._player.finished.connect(self._on_finished)
        self._player.error.connect(self._playback_error)
        self._synthesized.connect(self._on_synthesized)
        self._voice_ready.connect(self._on_voice_ready)
        self._enabled = True
        self._closed = self._working = self._playing = False
        self._generation = self._request = 0
        self._last_text = ""
        self._playing_text = ""
        self._pending = self._ready = None
        self._warm_key = None

    @property
    def _busy(self):
        return bool(self._working or self._playing or self._pending or self._ready)

    def prepare(self, model_path):
        key = model_path or DEFAULT_VOICE_PATH
        if self._closed or not self._enabled or key == self._warm_key:
            return
        self._warm_key = key
        generation = self._generation
        self.status_changed.emit("Loading local voice...")
        future = self._executor.submit(self._synthesizer.prepare, key)

        def done(result):
            try:
                result.result()
                status = "Voice ready"
            except Exception as exc:
                status = str(exc)
            if not self._closed:
                self._voice_ready.emit(generation, status)

        future.add_done_callback(done)

    def _on_voice_ready(self, generation, status):
        if generation == self._generation and not self._closed and not self._busy:
            self.status_changed.emit(status)

    def submit(self, text, model_path, speaker_id=0, rate=1.0, reference_audio=""):
        text = clean_speech_text(text)
        normalized = " ".join(re.findall(r"\w+", text.casefold()))
        if self._closed or not self._enabled or not normalized or normalized == self._last_text:
            return
        if self._playing and normalized == self._playing_text:
            # OCR can oscillate A -> B -> A while A is still being read. Keep
            # the current audio and discard the obsolete variant/prefetch.
            self._last_text = normalized
            self._request += 1
            self._pending = self._ready = None
            return
        self._last_text = normalized
        self._request += 1
        self._ready = None
        self._pending = (self._request, text, model_path or DEFAULT_VOICE_PATH, speaker_id, rate, reference_audio)
        self._start_pending()

    def preview(self, model_path, speaker_id=0, rate=1.0, reference_audio=""):
        self.stop(reset=True)
        enabled, self._enabled = self._enabled, True
        self.submit("Xin chào. Bạn đang nghe thử giọng đọc tiếng Việt.", model_path, speaker_id, rate, reference_audio)
        self._enabled = enabled

    def _start_pending(self):
        if self._working or not self._pending or self._closed:
            return
        request, text, model_path, speaker_id, rate, reference_audio = self._pending
        self._pending = None
        self._working = True
        generation = self._generation
        if not self._playing:
            self.status_changed.emit("Preparing speech...")
        options = {"reference_audio": reference_audio} if reference_audio else {}
        if isinstance(self._synthesizer, OfflineSynthesizer):
            options["cancelled"] = lambda: (self._closed or generation != self._generation or request != self._request)
        future = self._executor.submit(self._synthesizer.synthesize, text, model_path, speaker_id, rate, **options)

        def done(result):
            try:
                value = result.result()
            except Exception as exc:
                value = str(exc)
            if not self._closed:
                self._synthesized.emit(generation, request, value)

        future.add_done_callback(done)

    def _on_synthesized(self, generation, request, result):
        self._working = False
        if generation != self._generation or request != self._request or self._closed:
            self._start_pending()
            return
        if isinstance(result, str):
            self.status_changed.emit(result)
        elif self._playing:
            self._ready = (result, self._last_text)
        else:
            self._play(result, self._last_text)
        self._start_pending()

    def _play(self, audio, text_key):
        self._playing = True
        self._playing_text = text_key
        self.status_changed.emit("Reading Vietnamese translation")
        self._player.play(audio)

    def _on_finished(self):
        self._playing = False
        self._playing_text = ""
        if self._ready is not None:
            (audio, text_key), self._ready = self._ready, None
            self._play(audio, text_key)
        elif not self._busy:
            self.status_changed.emit("Ready")

    def _playback_error(self, message):
        self._playing = False
        self._playing_text = ""
        self._pending = self._ready = None
        self._generation += 1
        self.status_changed.emit(message)

    def set_enabled(self, enabled):
        self._enabled = enabled
        if not enabled:
            self.stop()

    def stop(self, reset=False, reason="user"):
        logger.debug("speech stopped", reason=reason)
        self._generation += 1
        self._pending = self._ready = None
        self._playing = False
        self._playing_text = ""
        self._player.stop()
        if reset:
            self._last_text = ""

    def close(self):
        self._closed = True
        self.stop(reason="shutdown")
        self._executor.shutdown(wait=False, cancel_futures=True)
        close = getattr(self._player, "close", None)
        if callable(close):
            close()
