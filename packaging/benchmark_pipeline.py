"""Measure local OCR, LLM, voice generation and the real warm speech pipeline."""

import argparse
import json
import platform
import socket
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QTimer, Slot

from ludivoxa.config import Config, OutputMode
from ludivoxa.gui.main_window import PROCESS_INTERVAL_MS
from ludivoxa.gui.workers import ProcessWorker
from ludivoxa.llm_translate import LLMTranslator
from ludivoxa.ocr import create_ocr
from ludivoxa.speech import OfflineSynthesizer, PCMPlayer, SpeechController


def summary(values):
    return {
        "samples": len(values),
        "median_ms": float(np.median(values)),
        "p95_ms": float(np.percentile(values, 95)),
        "max_ms": max(values),
    }


def run(config_path, frame_path):
    app = QCoreApplication.instance() or QCoreApplication([])
    config_path = Path(config_path).resolve()
    config = Config.load(str(config_path))
    for owner, key in (
        (config.paddle, "detection_model_dir"),
        (config.paddle, "recognition_model_dir"),
        (config, "tts_model_path"),
    ):
        path = Path(getattr(owner, key)).expanduser()
        setattr(owner, key, str(path if path.is_absolute() else config_path.parent / path))
    frame = np.load(frame_path)
    destinations = []
    original_connect = socket.socket.connect

    def local_only(sock, address):
        if address[0] not in ("127.0.0.1", "::1"):
            raise RuntimeError(f"Non-local benchmark connection: {address}")
        destinations.append(address)
        return original_connect(sock, address)

    socket.socket.connect = local_only
    report = {
        "platform": platform.platform(),
        "model": config.llm.model,
        "frame": str(Path(frame_path).resolve()),
        "frame_shape": list(frame.shape),
        "input_kind": "previously captured game frame, not live game interaction",
    }
    ocr = create_ocr(config)
    controller = worker = None
    try:
        start = time.perf_counter()
        ocr.load()
        report["ocr_load_ms"] = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        regions = ocr.extract_text_regions(frame)
        report["first_full_ocr_ms"] = (time.perf_counter() - start) * 1000
        expected = " ".join(r.text for r in regions)
        if not regions or not regions[0].bbox:
            raise RuntimeError("Benchmark frame has no recognized text box")
        report["source_text"] = expected
        full = []
        for i in range(8):
            ocr.confidence_threshold = config.ocr_confidence + (i + 1) * 0.000001
            start = time.perf_counter()
            result = ocr.extract_text_regions(frame)
            full.append((time.perf_counter() - start) * 1000)
            assert " ".join(r.text for r in result) == expected
        report["full_ocr_uncached"] = summary(full)
        ocr.confidence_threshold = config.ocr_confidence
        regions = ocr.extract_text_regions(frame)
        box = regions[0].bbox
        changed = []
        for i in range(24):
            image = frame.copy()
            image[min(frame.shape[0] - 1, box["y"] + box["height"] + 8), min(frame.shape[1] - 1, box["x"] + i), 0] ^= 1
            start = time.perf_counter()
            result = ocr.extract_text_regions(image)
            changed.append((time.perf_counter() - start) * 1000)
            assert " ".join(r.text for r in result) == expected
        report["changed_region_ocr"] = summary(changed)
        cached = []
        for _ in range(20):
            start = time.perf_counter()
            ocr.extract_text_regions(image)
            cached.append((time.perf_counter() - start) * 1000)
        report["identical_frame_cache"] = summary(cached)

        translator = LLMTranslator(config.llm, source_language="Auto")
        start = time.perf_counter()
        translator.load()
        report["llm_warmup_ms"] = (time.perf_counter() - start) * 1000
        sources = [
            "Welcome, brave hero!",
            "Where did you hide the key?",
            "We need to leave before sunset.",
            "Please open the door.",
            "こんにちは世界",
            "今日はどこに行くの？",
            "鍵はどこですか？",
            "一緒に帰りましょう。",
        ]
        translations = []
        for source in sources:
            start = time.perf_counter()
            text, hit = translator.translate(source)
            assert text and not hit
            translations.append({"source": source, "translation": text, "ms": (time.perf_counter() - start) * 1000})
        report["llm_uncached"] = summary([r["ms"] for r in translations])
        report["translations"] = translations

        voice = OfflineSynthesizer()
        start = time.perf_counter()
        voice.prepare(config.tts_model_path)
        report["voice_load_ms"] = (time.perf_counter() - start) * 1000
        samples = []
        for translated in translations[:4]:
            start = time.perf_counter()
            voice.synthesize(translated["translation"], config.tts_model_path, config.tts_speaker_id, config.tts_rate)
            samples.append((time.perf_counter() - start) * 1000)
        report["voice_uncached"] = summary(samples)

        config.output_mode, config.tts_enabled = OutputMode.TTS, True
        loop = QEventLoop()
        pipeline = {"speech_events": 0}
        ticks = []

        class TimedPlayer(PCMPlayer):
            def play(self, audio):
                pipeline["frame_to_play_request_ms"] = (time.perf_counter() - pipeline["started"]) * 1000
                super().play(audio)

        player = TimedPlayer()
        player.setVolume(0)
        controller = SpeechController(synthesizer=voice, player=player)
        errors = []
        player.error.connect(lambda error: (errors.append(error), loop.quit()))
        player.finished.connect(loop.quit)

        class Recorder(QObject):
            @Slot()
            def ready(self):
                pipeline["started"] = time.perf_counter()
                worker.submit_frame(frame, 1)
                feed.start(PROCESS_INTERVAL_MS)
                heartbeat.start(10)

            @Slot(str, int)
            def speech(self, text, generation):
                pipeline["speech_events"] += 1
                controller.submit(text, config.tts_model_path, config.tts_speaker_id, config.tts_rate)

        recorder = Recorder()
        worker = ProcessWorker(config)
        worker._ocr, worker._translator = ocr, translator
        worker._load_ocr = lambda: None  # Measure a warm pipeline with the engines above.
        worker._load_translator = lambda: None
        worker.models_ready.connect(recorder.ready)
        worker.speech_ready.connect(recorder.speech)
        feed, heartbeat = QTimer(), QTimer()
        feed.timeout.connect(lambda: worker.submit_frame(frame, 1))
        heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        deadline.start(30000)
        worker.start(config.ocr_confidence)
        loop.exec()
        feed.stop()
        heartbeat.stop()
        deadline.stop()
        if errors or not player.diagnostics.get("processed_seconds"):
            raise RuntimeError(f"Pipeline playback failed: {errors}")
        assert pipeline["speech_events"] == 1
        pipeline.pop("started", None)
        pipeline["ui_max_pause_ms"] = float(max(np.diff(ticks)) * 1000) if len(ticks) > 1 else None
        pipeline["native_playback"] = player.diagnostics
        pipeline["capture_poll_ms"] = PROCESS_INTERVAL_MS
        pipeline["includes_source_settling"] = True
        report["warm_pipeline"] = pipeline
        report["network_destinations"] = destinations
        return report
    finally:
        if worker:
            worker.stop()
        if controller:
            controller.close()
        ocr.close()
        socket.socket.connect = original_connect
        app.processEvents()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="dist/Ludivoxa/config.yml")
    parser.add_argument("--frame", required=True)
    parser.add_argument("--report", default=".test-results/setup-speed.json")
    args = parser.parse_args()
    data = run(args.config, args.frame)
    Path(args.report).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
