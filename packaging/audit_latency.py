"""Audit the existing OCR -> translator -> speech path and Windows loopback TTFA.

Optional PyAudioWPatch is used only by this diagnostic, never by the app.
Inputs are complete, previously captured frames: native capture phase is
reported separately rather than being confused with measured audio latency.
"""

import argparse
import copy
import json
import socket
import sys
import time
import wave
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QTimer, Slot

from interpreter.config import Config, OutputMode
from interpreter.gui.main_window import PROCESS_INTERVAL_MS
from interpreter.gui.workers import ProcessWorker
from interpreter.llm_translate import LLMTranslator
from interpreter.ocr import create_ocr
from interpreter.speech import OfflineSynthesizer, PCMPlayer, SpeechController


def wav_pcm(data):
    with wave.open(BytesIO(data)) as stream:
        pcm = np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2").astype(np.float32) / 32768
        return pcm.reshape(-1, stream.getnchannels()).mean(axis=1), stream.getframerate()


def match_audio(recorded, reference, sample_rate):
    """Locate the first voiced reference segment using normalized correlation.

    Background game sound alone must not count as first TTS audio. Returns
    the recorded onset index, leading reference silence, and match confidence.
    """
    window = max(1, round(sample_rate * 0.005))
    energy = np.convolve(reference * reference, np.ones(window) / window, mode="same")
    active = np.flatnonzero(energy > max(float(energy.max()) * 0.0004, 1e-10))
    if not len(active):
        raise ValueError("Reference audio has no voiced samples")
    onset = int(active[0])

    def speech_band(samples):
        # Windows mixer resampling/enhancements can change high-frequency
        # phase. Match voiced speech in the same smooth 80-2000 Hz band on
        # both signals instead of treating unrelated game sound as onset.
        frequencies = np.fft.rfftfreq(len(samples), 1 / sample_rate)
        band = (1 - np.exp(-((frequencies / 80) ** 4))) * np.exp(-((frequencies / 2000) ** 8))
        return np.fft.irfft(np.fft.rfft(samples) * band, n=len(samples))

    reference_band = speech_band(reference)
    template = reference_band[onset : onset + round(sample_rate * 0.65)].astype(np.float64)
    template -= template.mean()
    signal = speech_band(np.asarray(recorded, dtype=np.float64))
    length = len(template)
    if len(signal) < length:
        raise ValueError("Loopback recording is too short")
    fft_size = 1 << (len(signal) + length - 2).bit_length()
    correlation = np.fft.irfft(np.fft.rfft(signal, fft_size) * np.conj(np.fft.rfft(template, fft_size)), fft_size)
    sums = np.concatenate(([0.0], np.cumsum(signal)))
    squares = np.concatenate(([0.0], np.cumsum(signal * signal)))
    variance = squares[length:] - squares[:-length] - (sums[length:] - sums[:-length]) ** 2 / length
    scores = correlation[: len(signal) - length + 1] / np.sqrt(np.maximum(variance * np.dot(template, template), 1e-20))
    index = int(np.argmax(scores))
    score = float(scores[index])
    if score < 0.4:
        raise ValueError(f"No reliable TTS match in loopback (correlation={score:.3f})")
    return index, onset / sample_rate, score


class Loopback:
    def __init__(self, tools_path):
        sys.path.insert(0, str(Path(tools_path).resolve()))
        import pyaudiowpatch as pa

        self.pa = pa
        self.audio = pa.PyAudio()
        self.device = self.audio.get_default_wasapi_loopback()
        self.rate = int(self.device["defaultSampleRate"])
        self.channels = self.device["maxInputChannels"]
        self.chunks = []
        self.statuses = []
        self.stream = None

    def start(self):
        def callback(data, frames, clock, status):
            observed = time.perf_counter()
            samples = np.frombuffer(data, dtype="<f4").reshape(-1, self.channels).mean(axis=1).copy()
            self.chunks.append((observed, samples))
            if status:
                self.statuses.append(status)
            return None, self.pa.paContinue

        self.stream = self.audio.open(
            format=self.pa.paFloat32,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=self.device["index"],
            frames_per_buffer=round(self.rate * 0.01),
            stream_callback=callback,
        )

    def stop(self):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        self.audio.terminate()

    def locate(self, wav, earliest):
        reference, rate = wav_pcm(wav)
        if rate != self.rate:
            reference = np.interp(
                np.arange(round(len(reference) * self.rate / rate)) * rate / self.rate,
                np.arange(len(reference)),
                reference,
            )
        chunks = [(clock, data) for clock, data in self.chunks if clock >= earliest]
        if not chunks:
            raise RuntimeError("No loopback samples")
        recorded = np.concatenate([data for _, data in chunks])
        try:
            index, silence, score = match_audio(recorded, reference, self.rate)
        except ValueError:
            np.savez_compressed(
                ".test-results/ttfa-loopback-mismatch.npz", recorded=recorded, reference=reference, rate=self.rate
            )
            raise
        ends = np.cumsum([len(data) for _, data in chunks])
        block = int(np.searchsorted(ends, index, side="right"))
        clock, data = chunks[block]
        before = 0 if block == 0 else int(ends[block - 1])
        estimate = clock - (len(data) - (index - before)) / self.rate
        return {
            "observed": clock,
            "sample_estimate": estimate,
            "reference_leading_silence_ms": silence * 1000,
            "correlation": score,
        }


def audit(config_path, tools_path, repeats, report_path=None, streaming=False, selected_box=False):
    app = QCoreApplication.instance() or QCoreApplication([])
    config_path = Path(config_path).resolve()
    root = Path(__file__).resolve().parents[1]
    config = Config.load(str(config_path))
    config.output_mode, config.tts_enabled = OutputMode.TTS, True
    for owner, key in (
        (config.paddle, "detection_model_dir"),
        (config.paddle, "recognition_model_dir"),
        (config, "tts_model_path"),
    ):
        path = Path(getattr(owner, key)).expanduser()
        setattr(owner, key, str(path if path.is_absolute() else config_path.parent / path))
    if config.tts_reference_audio:
        config.tts_reference_audio = str((config_path.parent / config.tts_reference_audio).resolve())
    medium = Image.new("RGB", (2560, 1343), "black")
    draw = ImageDraw.Draw(medium)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 40)
    for y, line in [
        (1050, "We should leave before the guards return."),
        (1110, "Bring the key and wait for me by the gate."),
    ]:
        draw.text((560, y), line, font=font, fill="white")
    rgb = np.asarray(medium)
    medium_frame = np.concatenate((rgb[:, :, ::-1], np.full((*rgb.shape[:2], 1), 255, dtype=np.uint8)), axis=2)
    cases = [
        ("short_game", np.load(root / ".test-results/fast-ocr-frame.npy")),
        ("medium_synthetic", medium_frame),
        ("long_game", np.load(root / ".test-results/ocr-latency-live.npy")),
    ]
    report = {
        "complete": False,
        "samples": [],
        "model": config.llm.model,
        "voice": config.tts_model_path,
        "speaker": config.tts_speaker_id,
        "rate": config.tts_rate,
        "capture_poll_ms": PROCESS_INTERVAL_MS,
        "native_capture_period_ms": 250,
        "input": "complete saved frame available at t0; game reveal/typewriter time excluded",
        "audio_measurement": "Windows WASAPI loopback with reference correlation; callback delivery time, not acoustic/microphone latency",
        "output_volume": 0.25,
        "nano_steps": 8,
        "speech_synthesis_mode": "sentence/audio streaming" if streaming else "complete buffered audio",
        "tts_ms_definition": "first playable chunk" if streaming else "complete waveform",
        "selected_box": selected_box,
        "playback_truncated_for_ttfa": True,
        "matching": "normalized waveform correlation in 80-2000 Hz speech band; 650 ms reference segment",
    }
    real_connect = socket.socket.connect
    addresses = []

    def local_only(sock, address):
        if address[0] not in ("127.0.0.1", "::1"):
            raise RuntimeError(f"Non-local audit connection: {address}")
        addresses.append(address)
        return real_connect(sock, address)

    socket.socket.connect = local_only
    ocr = create_ocr(config)
    voice = OfflineSynthesizer()
    try:
        start = time.perf_counter()
        ocr.load()
        report["ocr_initial_load_ms"] = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        voice.prepare(config.tts_model_path)
        report["voice_initial_load_ms"] = (time.perf_counter() - start) * 1000
        warmup = LLMTranslator(config.llm, source_language=config.source_language)
        start = time.perf_counter()
        warmup.load()
        report["resident_llm_warmup_ms"] = (time.perf_counter() - start) * 1000
        # Remove first-inference kernel/phonemizer costs from warm TTFA.
        voice.synthesize(
            "Xin chào. Bạn có thể giúp tôi không?", config.tts_model_path, config.tts_speaker_id, config.tts_rate
        )
        if selected_box:
            cropped = []
            for name, frame in cases:
                boxes = [r.bbox for r in ocr.extract_text_regions(frame) if r.bbox]
                if not boxes:
                    raise ValueError("No dialogue to crop for " + name)
                left = max(0, min(b["x"] for b in boxes) - 16)
                top = max(0, min(b["y"] for b in boxes) - 16)
                right = min(frame.shape[1], max(b["x"] + b["width"] for b in boxes) + 16)
                bottom = min(frame.shape[0], max(b["y"] + b["height"] for b in boxes) + 16)
                cropped.append((name, frame[top:bottom, left:right].copy()))
            cases = cropped

        def run_trial(name, frame, run):
            trace = {"case": name, "run": run + 1, "ocr": [], "poll_phase_ms": [25, 75, 125][run % 3]}
            ocr._last_frame = ocr._last_regions = None
            ocr._frame_cache.clear()
            voice._vieneu._audio_cache.clear()
            voice._piper._audio_cache.clear()
            translator = LLMTranslator(copy.deepcopy(config.llm), source_language=config.source_language)
            translator._loaded = True
            translator._history.extend(
                [
                    ("We need to leave before sunset.", "Chúng ta cần rời đi trước khi mặt trời lặn."),
                    ("Have you seen the key?", "Bạn có thấy chiếc chìa khóa không?"),
                    ("Wait here. I will be back soon.", "Đợi ở đây. Tôi sẽ sớm quay lại."),
                ]
            )

            class TimedOCR:
                def __getattr__(self, key):
                    return getattr(ocr, key)

                @property
                def confidence_threshold(self):
                    return ocr.confidence_threshold

                @confidence_threshold.setter
                def confidence_threshold(self, value):
                    ocr.confidence_threshold = value

                def extract_text_regions(self, image):
                    begin = time.perf_counter()
                    regions = ocr.extract_text_regions(image)
                    end = time.perf_counter()
                    trace["ocr"].append({"start": begin, "end": end})
                    trace.setdefault("source", " ".join(r.text for r in regions))
                    return regions

                def close(self):
                    pass

            class TimedTranslator:
                def preview_translation(self, text):
                    start = time.perf_counter()
                    result = translator.preview_translation(text)
                    trace["preview_llm_ms"] = (time.perf_counter() - start) * 1000
                    return result

                def translate(self, text):
                    trace["llm_start"] = time.perf_counter()
                    result = translator.translate(text)
                    trace["llm_end"] = time.perf_counter()
                    trace["translation"] = result[0]
                    trace["llm_cache_hit"] = result[1]
                    return result

                def translate_stream(self, text, callback):
                    trace["llm_start"] = time.perf_counter()

                    def sentence(value):
                        trace.setdefault("first_sentence", time.perf_counter())
                        callback(value)

                    result = translator.translate_stream(text, sentence)
                    trace["llm_end"] = time.perf_counter()
                    trace["translation"], trace["llm_cache_hit"] = result
                    return result

            class TimedVoice(OfflineSynthesizer):
                def __init__(self):
                    pass  # Delegate to the already resident voice below.

                def synthesize(self, *args, **kwargs):
                    trace["tts_start"] = time.perf_counter()
                    wav = voice.synthesize(*args, **kwargs)
                    trace["tts_end"] = time.perf_counter()
                    trace["wav"] = wav
                    return wav

                def iter_audio(self, *args, **kwargs):
                    trace.setdefault("tts_start", time.perf_counter())
                    for wav in voice.iter_audio(*args, **kwargs):
                        trace.setdefault("tts_first_chunk", time.perf_counter())
                        trace.setdefault("wav_chunks", []).append(wav)
                        yield wav
                    trace["tts_end"] = time.perf_counter()

            loop = QEventLoop()
            feed = QTimer()
            heartbeat = QTimer()
            ticks = []
            player = PCMPlayer()
            player.setVolume(0.25)
            controller = SpeechController(synthesizer=TimedVoice(), player=player)
            original_play = player.play

            def play(wav):
                trace["play_request"] = time.perf_counter()
                original_play(wav)
                QTimer.singleShot(2200, loop.quit)

            player.play = play
            errors = []
            player.error.connect(lambda error: (errors.append(error), loop.quit()))
            worker = ProcessWorker(copy.deepcopy(config))
            worker.stream_speech = streaming
            worker._ocr = TimedOCR()
            worker._translator = TimedTranslator()
            worker._load_ocr = worker._load_translator = lambda: None

            class SpeechReceiver(QObject):
                @Slot(object, int)
                def receive(self, text, generation):
                    controller.submit(
                        text,
                        config.tts_model_path,
                        config.tts_speaker_id,
                        config.tts_rate,
                        config.tts_reference_audio,
                    )

            receiver = SpeechReceiver()
            worker.speech_ready.connect(receiver.receive)
            if streaming:
                worker.speech_stream_ready.connect(receiver.receive)
            feed.timeout.connect(lambda: worker.submit_frame(frame, 1))
            heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
            recorder = Loopback(tools_path)
            recorder.start()
            report.setdefault("loopback_device", recorder.device)
            trace["t0"] = time.perf_counter()
            QTimer.singleShot(
                trace["poll_phase_ms"], lambda: (worker.submit_frame(frame, 1), feed.start(PROCESS_INTERVAL_MS))
            )
            heartbeat.start(10)
            deadline = QTimer()
            deadline.setSingleShot(True)
            deadline.timeout.connect(loop.quit)
            deadline.start(45000)
            worker.start(config.ocr_confidence)
            loop.exec()
            feed.stop()
            heartbeat.stop()
            deadline.stop()
            worker.stop()
            controller.close()
            controller._executor.shutdown(wait=True, cancel_futures=True)
            recorder.stop()
            if streaming and trace.get("wav_chunks"):
                chunks = trace.pop("wav_chunks")
                pcm_chunks = [wav_pcm(chunk) for chunk in chunks]
                data = BytesIO()
                with wave.open(data, "wb") as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(pcm_chunks[0][1])
                    output.writeframes((np.concatenate([p[0] for p in pcm_chunks]) * 32768).astype("<i2").tobytes())
                trace["wav"] = data.getvalue()
            if errors or "wav" not in trace:
                raise RuntimeError(f"Audit failed: {errors}")
            pcm, rate = wav_pcm(trace["wav"])
            trace["audio_duration_seconds"] = len(pcm) / rate
            located = recorder.locate(trace.pop("wav"), trace["play_request"] - 0.05)
            first = trace["ocr"][0]
            trace["first_poll_and_dispatch_ms"] = (first["start"] - trace["t0"]) * 1000
            trace["first_ocr_ms"] = (first["end"] - first["start"]) * 1000
            trace["settle_and_repeat_observation_ms"] = (trace["llm_start"] - first["end"]) * 1000
            trace["llm_ms"] = (trace["llm_end"] - trace["llm_start"]) * 1000
            trace["llm_to_tts_dispatch_ms"] = (trace["tts_start"] - trace["llm_end"]) * 1000
            tts_ready = trace["tts_first_chunk"] if streaming else trace["tts_end"]
            trace["tts_ms"] = (tts_ready - trace["tts_start"]) * 1000
            trace["tts_to_play_dispatch_ms"] = (trace["play_request"] - tts_ready) * 1000
            trace["frame_to_play_request_ms"] = (trace["play_request"] - trace["t0"]) * 1000
            trace["play_request_to_observed_audio_ms"] = (located["observed"] - trace["play_request"]) * 1000
            trace["ttfa_loopback_observed_ms"] = (located["observed"] - trace["t0"]) * 1000
            trace["ttfa_loopback_sample_estimate_ms"] = (located["sample_estimate"] - trace["t0"]) * 1000
            trace["visible_text_ttfa_mean_phase_estimate_ms"] = trace["ttfa_loopback_observed_ms"] + 125
            trace["audio_match_correlation"] = located["correlation"]
            trace["reference_leading_silence_ms"] = located["reference_leading_silence_ms"]
            trace["loopback_status_flags"] = recorder.statuses
            trace["max_qt_pause_ms"] = float(max(np.diff(ticks)) * 1000) if len(ticks) > 1 else None
            return trace

        for name, frame in cases:
            for run in range(repeats):
                trace = run_trial(name, frame, run)
                report["samples"].append(trace)
                if report_path:
                    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(
                    json.dumps(
                        {
                            k: trace[k]
                            for k in (
                                "case",
                                "run",
                                "first_ocr_ms",
                                "llm_ms",
                                "tts_ms",
                                "ttfa_loopback_observed_ms",
                                "audio_match_correlation",
                            )
                        }
                    ),
                    flush=True,
                )
                app.processEvents()
        report["network_destinations"] = addresses
        # Probe the same playback parameters after the measurements so device
        # initialization here cannot shorten the first measured playback.
        import sounddevice as sd

        output = sd.query_devices(kind="output")
        probe = sd.OutputStream(
            samplerate=int(output["default_samplerate"]),
            channels=min(2, int(output["max_output_channels"])),
            dtype="float32",
            latency="high",
        )
        report["output_device"] = output
        report["reported_output_stream_latency_ms"] = probe.latency * 1000
        probe.close()
        report["context_lines"] = config.llm.context_lines
        report["history"] = "three seeded neutral English/Vietnamese context pairs"
        report["complete"] = True
        return report
    finally:
        ocr.close()
        socket.socket.connect = real_connect


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="dist/Interpreter/config.yml")
    parser.add_argument("--tools", default=".test-results/audit-tools")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--report", default=".test-results/current-ttfa-audit.json")
    parser.add_argument("--streaming", action="store_true")
    parser.add_argument("--selected-box", action="store_true")
    parser.add_argument(
        "--managed-runtime", action="store_true", help="Lease this bundle's private Ollama for the audit"
    )
    args = parser.parse_args()
    lease = None
    try:
        if args.managed_runtime:
            from local_runtime import ensure_bundled_ollama

            lease = ensure_bundled_ollama(Path(args.config).resolve().parent)
        result = audit(args.config, args.tools, args.repeats, args.report, args.streaming, args.selected_box)
        Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if lease is not None:
            lease.close()
