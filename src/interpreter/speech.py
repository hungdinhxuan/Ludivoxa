"""Offline Vietnamese voices, background prefetch and buffered native PCM playback."""

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
import wave
from collections import OrderedDict
from concurrent.futures import CancelledError, ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from queue import Empty, Full, Queue

import numpy as np
from PySide6.QtCore import QCoreApplication, QObject, Signal, Slot
from PySide6.QtMultimedia import QMediaDevices

from . import log

DEFAULT_VOICE_PATH = str(Path.home() / ".interpreter" / "voices" / "vi_VN-vais1000-medium.onnx")
logger = log.get_logger()
# PortAudio's device snapshot is process-wide. Never rebuild it while another
# PCMPlayer owns a native stream, including a cancelled writer still closing.
_AUDIO_DEVICE_LOCK = threading.Lock()


class SpeechStream:
    """Bounded hand-off between translation, synthesis and native playback."""

    def __init__(
        self,
        key=None,
        capacity=32,
        source=None,
        speaker_id=None,
        speaker_name="",
        reference_audio=None,
        voice_settings=None,
    ):
        self.key, self.source = key, source
        # Optional per-utterance binding; automatic routing never edits the
        # global voice setting or a line that is already being synthesized.
        self.speaker_id, self.speaker_name, self.reference_audio = speaker_id, speaker_name, reference_audio
        self.voice_settings = voice_settings
        self._queue = Queue(maxsize=capacity)
        self._done, self._cancelled = threading.Event(), threading.Event()
        self._error = None
        self.translation_text = ""  # Populated only on generated audio streams.

    def put(self, value, cancelled=lambda: False):
        while not self._cancelled.is_set() and not cancelled():
            try:
                self._queue.put(value, timeout=0.05)
                return
            except Full:
                continue
        raise CancelledError()

    def finish(self, error=None):
        if self._done.is_set():
            return
        self._error = error
        self._done.set()

    def cancel(self):
        self._cancelled.set()
        if self.source is not None:
            self.source.cancel()

    def __iter__(self):
        while not self._cancelled.is_set():
            try:
                yield self._queue.get(timeout=0.05)
            except Empty:
                if self._done.is_set():
                    if self._error:
                        raise self._error from None
                    return
        raise CancelledError()


def _output_device(sd):
    """Use WASAPI only when it uniquely identifies the same selected endpoint."""
    device = sd.query_devices(kind="output")
    host = sd.query_hostapis(device["hostapi"])["name"]
    if os.name == "nt" and host == "MME":
        devices = sd.query_devices()
        matches = [
            d
            for d in devices
            if d["max_output_channels"] > 0
            and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI"
            and d["name"].startswith(device["name"])
        ]
        if len(matches) == 1:
            return matches[0]
    return device


def _speech_text_key(text):
    return " ".join(re.findall(r"\w+", text.casefold()))


def _output_format(sd):
    """Validate the selected endpoint's format before consuming speech audio."""
    preferred = _output_device(sd)
    original = sd.query_devices(kind="output")
    devices = [preferred]
    if preferred["index"] != original["index"]:
        devices.append(original)  # Only fall back to the user's original output.
    last_error = None
    for device in devices:
        maximum = int(device["max_output_channels"])
        if maximum < 1:
            continue
        settings = dict(device=device["index"], samplerate=int(device["default_samplerate"]), dtype="float32")
        if sd.query_hostapis(device["hostapi"])["name"] == "Windows WASAPI":
            settings["extra_settings"] = sd.WasapiSettings(exclusive=False, auto_convert=True)
        # Some surround/virtual endpoints require their native channel count;
        # max_output_channels is not a guarantee that stereo is supported.
        for channels in dict.fromkeys((min(2, maximum), maximum, 1)):
            try:
                sd.check_output_settings(channels=channels, **settings)
            except sd.PortAudioError as exc:
                last_error = exc
                continue
            return device, dict(settings, channels=channels)
    raise ValueError(f"Selected audio output has no supported speech format. {last_error or 'No output channels.'}")


def _wav_bytes(samples, sample_rate):
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim != 1 or not samples.size or not np.isfinite(samples).all():
        raise ValueError("Local voice synthesis returned invalid audio.")
    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return output.getvalue()


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
        self._output_revision = 0
        self._applied_output_revision = -1
        if os.name == "nt":
            self._media_devices = QMediaDevices(self)
            self._media_devices.audioOutputsChanged.connect(self._on_audio_outputs_changed)
        self._completed.connect(self._on_completed)
        app = QCoreApplication.instance()
        if app:
            app.aboutToQuit.connect(self.close)

    def setVolume(self, volume):
        self._volume = volume

    @Slot()
    def _on_audio_outputs_changed(self):
        # Qt observes the live Windows default (including Remote Audio), while
        # PortAudio keeps the list/default from its last initialization. Mark
        # it here; all native work remains on the audio writer's thread.
        with self._lock:
            self._output_revision += 1

    def play(self, wav_bytes):
        if self._closed:
            return
        self.stop()
        self._audio = wav_bytes
        token = self._generation
        self._executor.submit(self._write_audio, token, wav_bytes, self._volume)

    def _write_audio(self, token, wav_bytes, volume):
        stream = None
        report = {}
        _AUDIO_DEVICE_LOCK.acquire()
        try:
            with self._lock:
                if token != self._generation or self._closed:
                    return
                revision = self._output_revision
            import sounddevice as sd

            if os.name == "nt" and self._applied_output_revision != revision:
                # Refresh once at startup and after a device/default change,
                # before opening/consuming audio. Steady-state speech does not
                # pay for repeated enumeration. A failed refresh must not play
                # through the stale physical speaker during an RDP session.
                if sd._initialized:
                    sd._terminate()
                sd._initialize()
                self._applied_output_revision = revision
            device, output_format = _output_format(sd)
            rate = output_format["samplerate"]
            channels = output_format["channels"]
            host = sd.query_hostapis(device["hostapi"])["name"]
            buffered_callback = host in ("Windows WDM-KS", "ASIO")
            callback_mode = buffered_callback or host == "Windows WASAPI"
            audio_peak, audio_energy, audio_samples = 0.0, 0.0, 0

            def decoded():
                nonlocal audio_peak, audio_energy, audio_samples
                chunks = iter(wav_bytes) if isinstance(wav_bytes, SpeechStream) else iter([wav_bytes])
                for chunk in chunks:
                    with wave.open(BytesIO(chunk)) as wav_file:
                        if wav_file.getsampwidth() != 2:
                            raise ValueError("Speech audio must use 16-bit PCM.")
                        samples = np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype="<i2")
                        samples = samples.reshape(-1, wav_file.getnchannels()).mean(axis=1) / 32768.0
                        source_rate = wav_file.getframerate()
                    if rate != source_rate:
                        size = round(len(samples) * rate / source_rate)
                        samples = np.interp(np.arange(size) * source_rate / rate, np.arange(len(samples)), samples)
                    audio_peak = max(audio_peak, float(np.max(np.abs(samples))) * abs(volume))
                    audio_energy += float(np.sum(samples * samples)) * volume * volume
                    audio_samples += len(samples)
                    pcm = np.zeros((len(samples), channels), dtype="float32")
                    # Mono speech goes to the front L/R channels. Do not send
                    # a full-range voice signal to surround or LFE channels.
                    pcm[:, : min(2, channels)] = (samples * volume)[:, None]
                    yield pcm

            chunks = iter(decoded())
            if buffered_callback:
                # These hosts cannot use blocking writes. Preserve their native
                # callback path with a fully buffered utterance.
                pcm = np.concatenate(list(chunks))
                chunks = iter(())
            else:
                pcm = next(chunks)
            done = threading.Event()
            cursor = 0
            callback_underruns = 0
            delivered_samples = 0
            queued_pcm = Queue(maxsize=2)
            production_done = threading.Event()
            if buffered_callback or not isinstance(wav_bytes, SpeechStream):
                production_done.set()
            last_callback = time.monotonic()

            def render(outdata, frames, time_info, status):
                nonlocal cursor, callback_underruns, pcm, delivered_samples, last_callback
                last_callback = time.monotonic()
                if status.output_underflow:
                    callback_underruns += 1
                outdata.fill(0)
                offset = 0
                while offset < frames:
                    if cursor == len(pcm):
                        try:
                            pcm = queued_pcm.get_nowait()
                            cursor = 0
                        except Empty:
                            if production_done.is_set():
                                raise sd.CallbackStop from None
                            return  # The real-time callback must never wait for synthesis.
                    count = min(frames - offset, len(pcm) - cursor)
                    outdata[offset : offset + count] = pcm[cursor : cursor + count]
                    cursor += count
                    offset += count
                    delivered_samples += count
                if cursor == len(pcm) and production_done.is_set() and queued_pcm.empty():
                    raise sd.CallbackStop

            options = {"callback": render, "finished_callback": done.set} if callback_mode else {}
            stream = sd.OutputStream(**output_format, latency=0.06, **options)
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
            total_samples = len(pcm)
            if callback_mode:

                def check_output():
                    with self._lock:
                        if token != self._generation or self._closed:
                            raise CancelledError()
                    if done.is_set() and not production_done.is_set():
                        raise RuntimeError("Audio output stopped while speech was being produced.")
                    if time.monotonic() - last_callback > 2:
                        raise RuntimeError("Audio output stopped accepting samples. Reconnect the audio device.")

                # WASAPI blocking writes can hang indefinitely after an RDP
                # endpoint stops. Feed bounded PCM from this writer and let the
                # native callback drain it; cancellation never waits on write().
                for next_pcm in chunks:
                    while True:
                        check_output()
                        try:
                            queued_pcm.put(next_pcm, timeout=0.05)
                            total_samples += len(next_pcm)
                            break
                        except Full:
                            continue
                production_done.set()
                while not done.wait(0.05):
                    check_output()
                    if time.monotonic() - start > total_samples / rate + 10:
                        raise RuntimeError("Audio driver did not report playback completion.")
                if delivered_samples != total_samples:
                    raise RuntimeError("Audio driver stopped before all samples were delivered.")
                underrun = callback_underruns
            else:
                block = max(1, rate // 50)
                while True:
                    for offset in range(0, len(pcm), block):
                        with self._lock:
                            cancelled = token != self._generation or self._closed
                        if cancelled:
                            stream.abort()
                            return
                        underrun |= stream.write(pcm[offset : offset + block])
                    try:
                        pcm = next(chunks)
                        total_samples += len(pcm)
                    except StopIteration:
                        break
            stream.stop()  # Drain all queued samples before reporting completion.
            report = dict(
                sample_rate=rate,
                duration_seconds=total_samples / rate,
                processed_seconds=total_samples / rate,
                elapsed_seconds=time.monotonic() - start,
                underruns=int(underrun),
                host_api=host,
                callback_mode=callback_mode,
                streaming=isinstance(wav_bytes, SpeechStream) and not buffered_callback,
                requested_latency_ms=60,
                output_device=device["index"],
                output_device_name=device["name"],
                output_channels=channels,
                speech_volume=volume,
                audio_peak=audio_peak,
                audio_rms=(audio_energy / max(1, audio_samples)) ** 0.5,
                device_latency_ms=stream.latency * 1000,
                buffer_active=False,
            )
        except CancelledError:
            return
        except Exception as exc:
            report = {"error": f"Audio playback failed: {exc}", "buffer_active": False}
            self._applied_output_revision = -1  # Retry with a fresh endpoint after a driver failure.
            logger.error("speech playback failed", error=str(exc))
        finally:
            with self._lock:
                if self._stream is stream:
                    self._stream = None
            try:
                if stream is not None:
                    stream.close()
            finally:
                _AUDIO_DEVICE_LOCK.release()
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
            audio = getattr(self, "_audio", None)
            if isinstance(audio, SpeechStream):
                audio.cancel()
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

    def _voice_settings(self, model_path, speaker_id, reference_audio):
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
        return engine, voice, reference_key

    def iter_audio(
        self, text, model_path, speaker_id=0, rate=1.0, reference_audio="", cancelled=lambda: False, chunk_chars=48
    ):
        engine, voice, reference_key = self._voice_settings(model_path, speaker_id, reference_audio)
        key = (text, speaker_id, rate, reference_key)
        if key in self._audio_cache:
            yield self._audio_cache[key]
            return
        cached = []
        for samples in engine.infer_stream(text, voice=voice, speed=rate, sway=-1.0, max_chars=chunk_chars):
            if cancelled():
                raise CancelledError()
            audio = _wav_bytes(samples, engine.sample_rate)
            cached.append(np.asarray(samples, dtype=np.float32))
            yield audio
        if not cached:
            raise ValueError("Local voice synthesis returned no audio.")
        self._audio_cache[key] = _wav_bytes(np.concatenate(cached), engine.sample_rate)
        while len(self._audio_cache) > 16 or sum(map(len, self._audio_cache.values())) > 8 * 1024 * 1024:
            self._audio_cache.popitem(last=False)

    def synthesize(self, text, model_path, speaker_id=0, rate=1.0, reference_audio="", cancelled=None):
        engine, voice, reference_key = self._voice_settings(model_path, speaker_id, reference_audio)
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

    def iter_audio(
        self, text, model_path, speaker_id=0, rate=1.0, reference_audio="", cancelled=lambda: False, chunk_chars=48
    ):
        if Path(model_path).expanduser().is_dir():
            yield from self._vieneu.iter_audio(
                text, model_path, speaker_id, rate, reference_audio, cancelled, chunk_chars
            )
        else:
            yield self._piper.synthesize(text, model_path, speaker_id, rate)


class SpeechController(QObject):
    """Prefetch the latest translation while playing; don't interrupt a spoken line."""

    status_changed = Signal(str)
    _synthesized = Signal(int, int, object)
    _voice_ready = Signal(int, str)
    _stream_ready = Signal(int, int, object)

    def __init__(self, parent=None, synthesizer=None, player=None):
        super().__init__(parent)
        self._synthesizer = synthesizer or OfflineSynthesizer()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="interpreter-speech")
        self._player = player or PCMPlayer(self)
        self._player.finished.connect(self._on_finished)
        self._player.error.connect(self._playback_error)
        self._synthesized.connect(self._on_synthesized)
        self._voice_ready.connect(self._on_voice_ready)
        self._stream_ready.connect(self._on_stream_ready)
        self._enabled = True
        self._closed = self._working = self._playing = False
        self._generation = self._request = 0
        self._last_text = ""
        self._playing_text = ""
        self._pending = self._ready = None
        self._warm_key = None
        self._playing_audio = self._input_stream = None
        self._last_spoken_translation = None
        self._last_character_voice = None

    @staticmethod
    def _character_voice(stream):
        if not isinstance(stream, SpeechStream) or stream.speaker_id is None:
            return None
        return stream.speaker_name.casefold(), stream.speaker_id, stream.reference_audio

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
        if isinstance(text, SpeechStream):
            normalized = ("source", text.key)
            if self._character_voice(text) is not None:
                normalized += (self._character_voice(text),)
        else:
            text = clean_speech_text(text)
            normalized = _speech_text_key(text)
        if self._closed or not self._enabled or not normalized or normalized == self._last_text:
            if isinstance(text, SpeechStream):
                text.cancel()
            return
        previous = self._last_spoken_translation
        previous_key = _speech_text_key(previous.translation_text) if isinstance(previous, SpeechStream) else previous
        if self._character_voice(text) != self._last_character_voice:
            previous_key = None
        if (self._playing and normalized == self._playing_text) or (
            not isinstance(text, SpeechStream) and normalized == previous_key
        ):
            # OCR can oscillate A -> B -> A. Compare translated text too when
            # switching between streamed and buffered output paths.
            self._last_text = normalized
            self._request += 1
            if isinstance(text, SpeechStream):
                text.cancel()
            self._cancel_waiting_streams()
            self._pending = self._ready = None
            return
        self._last_text = normalized
        self._request += 1
        self._cancel_waiting_streams()
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
        if isinstance(text, SpeechStream):
            self._input_stream = text
            future = self._executor.submit(
                self._produce_stream, generation, request, text, model_path, speaker_id, rate, reference_audio
            )
        else:
            future = self._executor.submit(self._synthesizer.synthesize, text, model_path, speaker_id, rate, **options)

        def done(result):
            try:
                value = result.result()
            except Exception as exc:
                value = str(exc)
            if not self._closed:
                self._synthesized.emit(generation, request, value)

        future.add_done_callback(done)

    def _cancel_waiting_streams(self):
        if self._pending and isinstance(self._pending[1], SpeechStream):
            self._pending[1].cancel()
        if self._ready and isinstance(self._ready[0], SpeechStream):
            self._ready[0].cancel()
        current_source = self._playing_audio.source if isinstance(self._playing_audio, SpeechStream) else None
        if self._input_stream is not None and self._input_stream is not current_source:
            self._input_stream.cancel()

    def _produce_stream(self, generation, request, source, model_path, speaker_id, rate, reference_audio):
        audio = SpeechStream(capacity=2, source=source)
        first = True
        previous = self._last_spoken_translation
        previous_key = _speech_text_key(previous.translation_text) if isinstance(previous, SpeechStream) else previous
        if self._character_voice(source) != self._last_character_voice:
            previous_key = None

        def cancelled():
            return (
                self._closed
                or generation != self._generation
                or (request != self._request and audio is not self._playing_audio)
            )

        def translated_parts():
            # Restore the translated-text guard used by buffered speech. OCR
            # keys may differ while their Vietnamese translations are equal.
            # Hold only a prefix matching the last played translation; release
            # the full new text immediately when it diverges. Packet/sentence
            # boundaries must not affect the comparison.
            comparing = previous_key
            pending = []
            for text in source:
                if cancelled():
                    raise CancelledError()
                text = clean_speech_text(text)
                if not _speech_text_key(text):
                    continue
                if comparing:
                    pending.append(text)
                    key = _speech_text_key(" ".join(pending))
                    if key == comparing or comparing.startswith(key + " "):
                        continue
                    text = " ".join(pending)
                    pending.clear()
                    comparing = None
                yield text
            if pending:
                text = " ".join(pending)
                if _speech_text_key(text) != comparing:
                    yield text  # A shorter, different translation remains valid.
                else:
                    logger.debug("speech repeat suppressed", reason="same translated utterance", request=request)

        try:
            for text in translated_parts():
                audio.translation_text = " ".join(filter(None, (audio.translation_text, text)))
                for chunk in self._synthesizer.iter_audio(
                    text,
                    model_path,
                    speaker_id,
                    rate,
                    reference_audio=reference_audio,
                    cancelled=cancelled,
                    chunk_chars=40 if first else 80,
                ):
                    audio.put(chunk, cancelled)
                    if first:
                        first = False
                        self._stream_ready.emit(generation, request, audio)
            audio.finish()
            logger.info(
                "speech translation processed",
                source_id=hashlib.sha256(str(source.key).encode("utf-8")).hexdigest()[:12],
                translation_id=hashlib.sha256(_speech_text_key(audio.translation_text).encode("utf-8")).hexdigest()[
                    :12
                ],
                generated_audio=not first,
                request=request,
            )
        except Exception as exc:
            audio.finish(exc)
            if first:
                raise
        finally:
            if cancelled():
                source.cancel()
        return audio

    def _on_stream_ready(self, generation, request, audio):
        if generation != self._generation or request != self._request or self._closed:
            audio.cancel()
        elif self._playing:
            self._ready = (audio, self._last_text)
        else:
            self._play(audio, self._last_text)

    def _on_synthesized(self, generation, request, result):
        self._working = False
        if generation != self._generation or request != self._request or self._closed:
            self._start_pending()
            return
        if isinstance(result, str):
            self.status_changed.emit(result)
        elif isinstance(result, SpeechStream):
            if not self._busy:
                self.status_changed.emit("Ready")
        elif self._playing:
            self._ready = (result, self._last_text)
        else:
            self._play(result, self._last_text)
        self._start_pending()

    def _play(self, audio, text_key):
        self._playing = True
        self._playing_audio = audio
        self._playing_text = text_key
        self._last_spoken_translation = audio if isinstance(audio, SpeechStream) else text_key
        self._last_character_voice = self._character_voice(audio.source) if isinstance(audio, SpeechStream) else None
        logger.info(
            "speech playback started",
            request=self._request,
            streaming=isinstance(audio, SpeechStream),
            preset_id=audio.source.speaker_id if isinstance(audio, SpeechStream) and audio.source else None,
            source_id=hashlib.sha256(
                str(audio.source.key if isinstance(audio, SpeechStream) and audio.source else text_key).encode("utf-8")
            ).hexdigest()[:12],
        )
        self.status_changed.emit("Reading Vietnamese translation")
        self._player.play(audio)

    def _on_finished(self):
        self._playing = False
        self._playing_audio = None
        self._playing_text = ""
        if self._ready is not None:
            (audio, text_key), self._ready = self._ready, None
            self._play(audio, text_key)
        elif not self._busy:
            self.status_changed.emit("Ready")

    def _playback_error(self, message):
        self._playing = False
        self._playing_text = ""
        if isinstance(self._playing_audio, SpeechStream):
            self._playing_audio.cancel()
        self._playing_audio = None
        self._last_spoken_translation = None
        self._last_character_voice = None
        self._cancel_waiting_streams()
        self._pending = self._ready = None
        self._generation += 1
        self.status_changed.emit(message)

    def set_enabled(self, enabled):
        self._enabled = enabled
        if not enabled:
            self.stop()

    def reset_repeat_guard(self):
        """Allow an identical line in a new scene without interrupting speech."""
        self._last_text = ""
        self._last_spoken_translation = None
        self._last_character_voice = None
        self._playing_text = ""

    def stop(self, reset=False, reason="user"):
        logger.debug("speech stopped", reason=reason)
        self._generation += 1
        self._cancel_waiting_streams()
        if isinstance(self._playing_audio, SpeechStream):
            self._playing_audio.cancel()
        self._playing_audio = None
        self._pending = self._ready = None
        self._playing = False
        self._playing_text = ""
        self._player.stop()
        if reset:
            self._last_text = ""
            self._last_spoken_translation = None
            self._last_character_voice = None

    def close(self):
        self._closed = True
        self.stop(reason="shutdown")
        self._executor.shutdown(wait=False, cancel_futures=True)
        close = getattr(self._player, "close", None)
        if callable(close):
            close()
