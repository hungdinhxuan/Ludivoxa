"""Opt-in executable diagnostics, using real local OCR/translation/speech models."""

import copy
import json
import socket
import sys
import threading
import time
import traceback
import wave
from io import BytesIO
from pathlib import Path


def run_smoke(report_path):
    report = {"ok": False}
    ocr = None
    heartbeat = None
    heartbeat_stop = threading.Event()
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        from PySide6.QtWidgets import QApplication

        from ludivoxa import __version__
        from ludivoxa.config import CharacterVoices, Config, OutputMode
        from ludivoxa.gui.workers import ProcessWorker
        from ludivoxa.languages import TARGET_LANGUAGE_PRESETS, contains_kana, contains_script
        from ludivoxa.llm_translate import LLMTranslator
        from ludivoxa.ocr import create_ocr
        from ludivoxa.paddle_ocr import IsolatedPaddleOCR
        from ludivoxa.speech import (
            OfflineSynthesizer,
            PCMPlayer,
            SpeechController,
            SpeechStream,
            available_voices,
            voice_details,
        )

        report["frozen"] = bool(getattr(sys, "frozen", False))
        report["executable"] = sys.executable
        app = QApplication.instance() or QApplication([])
        real_connect = socket.socket.connect
        destinations = []

        def local_only(sock, address):
            if address[0] not in ("127.0.0.1", "::1"):
                raise AssertionError(f"Unexpected inference destination: {address}")
            destinations.append(address)
            return real_connect(sock, address)

        socket.socket.connect = local_only
        config = Config.load("config.yml")
        ocr = create_ocr(config)
        start = time.perf_counter()
        ocr.load()
        report["ocr_load_ms"] = (time.perf_counter() - start) * 1000
        report["ocr_isolated"] = isinstance(ocr, IsolatedPaddleOCR)
        ticks = [time.perf_counter()]

        def tick():
            while not heartbeat_stop.wait(0.01):
                ticks.append(time.perf_counter())

        heartbeat = threading.Thread(target=tick)
        heartbeat.start()
        recognized = []
        for text, font in (("Welcome brave hero", "arial.ttf"), ("こんにちは世界", "msgothic.ttc")):
            if font == "msgothic.ttc":
                ocr.close()
                japanese_config = copy.deepcopy(config)
                suffix = "_onnx" if Path(config.paddle.detection_model_dir).name.endswith("_onnx") else ""
                japanese_config.paddle.recognition_model_dir = f"models/PP-OCRv5_mobile_rec{suffix}"
                japanese_config.source_language = "Japanese"
                ocr = create_ocr(japanese_config)
                ocr.load()
            image = Image.new("RGB", (900, 100), "white")
            ImageDraw.Draw(image).text(
                (15, 10), text, fill="black", font=ImageFont.truetype(str(Path("C:/Windows/Fonts") / font), 36)
            )
            frame = np.concatenate((np.asarray(image)[:, :, ::-1], np.full((100, 900, 1), 255, dtype=np.uint8)), axis=2)
            regions = ocr.extract_text_regions(frame)
            result = " ".join(region.text for region in regions)
            if result != text:
                raise AssertionError(f"OCR mismatch: {result!r} != {text!r}")
            recognized.append(result)
        report["ocr"] = recognized
        heartbeat_stop.set()
        heartbeat.join()
        report["ocr_parent_max_pause_ms"] = max(np.diff(ticks)) * 1000
        if report["ocr_parent_max_pause_ms"] > 200:
            raise AssertionError("OCR blocked the parent's Python threads")
        translator = LLMTranslator(config.llm, source_language="Auto")
        translator.load()
        translations = []
        for text in ("Damn! This storm is intense. I hope I can make it home in one piece.", "こんにちは世界"):
            start = time.perf_counter()
            result, cached = translator.translate(text)
            if not result or "<think>" in result or "let's tackle" in result.lower():
                raise AssertionError(f"Invalid translation: {result!r}")
            translations.append(
                {"source": text, "translation": result, "ms": (time.perf_counter() - start) * 1000, "cached": cached}
            )
        report["translations"] = translations
        cjk_translations = []
        for language, samples in (
            ("Chinese", (("欢迎来到村庄", "msyh.ttc"), ("歡迎來到村莊", "msjh.ttc"))),
            ("Korean", (("안녕하세요 용사님", "malgun.ttf"),)),
        ):
            language_config = copy.deepcopy(config)
            language_config.source_language = language
            language_config.output_mode = OutputMode.OVERLAY
            worker = ProcessWorker(language_config)
            worker._load_ocr()
            worker._load_translator()
            try:
                if worker.has_failed_models():
                    raise AssertionError(worker.get_failure_message())
                recognized_cjk, overlay = [], []
                worker.ocr_results_ready.connect(
                    lambda regions, _, results=recognized_cjk: results.extend(r.text for r in regions)
                )
                worker.text_ready.connect(lambda text, _, results=overlay: results.append(text))
                for text, font_name in samples:
                    recognized_cjk.clear()
                    overlay.clear()
                    image = Image.new("RGBA", (640, 100), "white")
                    font = ImageFont.truetype(str(Path("C:/Windows/Fonts") / font_name), 36)
                    ImageDraw.Draw(image).text((15, 10), text, fill="black", font=font)
                    frame = np.asarray(image)[:, :, [2, 1, 0, 3]].copy()
                    start = time.perf_counter()
                    worker._process_frame(frame)
                    elapsed = (time.perf_counter() - start) * 1000
                    if recognized_cjk != [text] or not overlay or not overlay[-1] or overlay[-1] == text:
                        raise AssertionError(f"Failed {language} OCR to overlay: {recognized_cjk!r}, {overlay!r}")
                    cjk_translations.append(
                        {"language": language, "source": text, "translation": overlay[-1], "ms": elapsed}
                    )
            finally:
                worker._discard_ocr()
        report["cjk_translations"] = cjk_translations
        target_translations = []
        for target in TARGET_LANGUAGE_PRESETS[1:]:
            language_config = copy.deepcopy(config)
            language_config.llm.target_language = target
            language_config.output_mode = OutputMode.TTS
            language_config.tts_enabled = True
            if target == "English":
                source, text, font_name = "Japanese", "日が暮れる前に村へ戻ってください。", "msgothic.ttc"
            else:
                source, text, font_name = "English", "Please return to the village after sunset.", "arial.ttf"
            language_config.source_language = source
            worker = ProcessWorker(language_config)
            worker._ocr = ocr  # The already-loaded Japanese recognizer reads English as well.
            worker._load_translator()
            if worker.has_failed_models():
                raise AssertionError(worker.get_failure_message())
            try:
                overlays, speech = [], []
                worker.text_ready.connect(lambda value, _, results=overlays: results.append(value))
                worker.speech_ready.connect(lambda value, _, results=speech: results.append(value))
                worker.speech_stream_ready.connect(lambda value, _, results=speech: results.append(value))
                image = Image.new("RGBA", (900, 100), "white")
                font = ImageFont.truetype(str(Path("C:/Windows/Fonts") / font_name), 36)
                ImageDraw.Draw(image).text((15, 10), text, fill="black", font=font)
                frame = np.asarray(image)[:, :, [2, 1, 0, 3]].copy()
                start = time.perf_counter()
                worker._process_frame(frame)
                elapsed = (time.perf_counter() - start) * 1000
                result = overlays[0] if len(overlays) == 1 else ""
                script = "Chinese" if target.endswith("Chinese") else target
                if speech or result == text or not contains_script(result, script):
                    raise AssertionError(f"Failed {source} to {target} overlay: {overlays!r}, speech={bool(speech)}")
                if target == "Japanese" and not contains_kana(result):
                    raise AssertionError(f"Expected kana in Japanese output: {result!r}")
                if target == "Simplified Chinese" and not any(char in result for char in "请后庄归"):
                    raise AssertionError(f"Expected Simplified Chinese output: {result!r}")
                if target == "Traditional Chinese" and not any(char in result for char in "請後莊歸"):
                    raise AssertionError(f"Expected Traditional Chinese output: {result!r}")
                target_translations.append(
                    {"source_language": source, "target_language": target, "translation": result, "ms": elapsed}
                )
            finally:
                worker._translator._session.close()
        report["target_translations"] = target_translations
        start = time.perf_counter()
        synthesizer = OfflineSynthesizer()
        audio = synthesizer.synthesize(
            translations[0]["translation"], config.tts_model_path, config.tts_speaker_id, config.tts_rate
        )
        report["tts_load_and_synthesis_ms"] = (time.perf_counter() - start) * 1000
        with wave.open(BytesIO(audio)) as wav_file:
            report["audio_seconds"] = wav_file.getnframes() / wav_file.getframerate()
            if wav_file.getnchannels() != 1 or wav_file.getnframes() < wav_file.getframerate():
                raise AssertionError("Invalid synthesized audio")
        from PySide6.QtCore import QEventLoop, QTimer

        player = PCMPlayer()
        player.setVolume(0)
        loop = QEventLoop()
        errors = []
        player.finished.connect(loop.quit)
        player.error.connect(lambda message: (errors.append(message), loop.quit()))
        blocker = QTimer()
        blocker.timeout.connect(lambda: time.sleep(0.6))
        blocker.start(450)
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        timeout.start(round((report["audio_seconds"] + 6) * 1000))
        try:
            player.play(audio)
            loop.exec()
            if errors or not player.diagnostics.get("processed_seconds"):
                raise AssertionError(f"Native playback failed: {errors}")
            report["playback_under_ui_stall"] = player.diagnostics
            if player.diagnostics["elapsed_seconds"] > report["audio_seconds"] + 0.8:
                raise AssertionError("Audio stalled with the UI")
        finally:
            blocker.stop()
            timeout.stop()
            player.close()
        reference = report_path.parent / "synthetic-clone-reference.wav"
        reference.write_bytes(audio)
        start = time.perf_counter()
        cloned = synthesizer.synthesize(
            "Xin chào. Bạn có thể giúp tôi một việc không?", "models/VieNeu-TTS-v3-Nano", reference_audio=str(reference)
        )
        with wave.open(BytesIO(cloned)) as wav_file:
            report["cloned_audio_seconds"] = wav_file.getnframes() / wav_file.getframerate()
            if wav_file.getframerate() != 24000 or report["cloned_audio_seconds"] < 1:
                raise AssertionError("Invalid local voice-cloning output")
        report["voice_cloning_ms"] = (time.perf_counter() - start) * 1000
        report["clone_reference_kind"] = "synthetic diagnostic audio, not a human similarity evaluation"

        # Real Nano synthesis and one native device stream must finish every
        # chunk even while Qt stalls. TTFA audits intentionally stop early;
        # this separate diagnostic verifies complete, uninterrupted playback.
        player = PCMPlayer()
        player.setVolume(0)
        speech = SpeechController(synthesizer=synthesizer, player=player)
        source = SpeechStream(key="native streaming diagnostic")
        source.put("Chúng ta nên rời đi trước khi các vệ sĩ quay lại. Mang theo chìa khóa và đợi tôi ở cổng.")
        source.put("Hãy nói chuyện tiếp khi chúng ta đến nơi an toàn.")
        source.finish()
        loop = QEventLoop()
        errors = []
        player.finished.connect(loop.quit)
        player.error.connect(lambda error: (errors.append(error), loop.quit()))
        blocker = QTimer()
        blocker.timeout.connect(lambda: time.sleep(0.35))
        blocker.start(400)
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        timeout.start(30000)
        try:
            speech.submit(source, "models/VieNeu-TTS-v3-Nano", config.tts_speaker_id)
            loop.exec()
            if errors or player.diagnostics.get("processed_seconds", 0) < 4:
                raise AssertionError(f"Streaming playback failed: {errors}, {player.diagnostics}")
            if player.diagnostics["underruns"]:
                raise AssertionError(f"Streaming playback underrun: {player.diagnostics}")
            callback_host = player.diagnostics["host_api"] in ("Windows WDM-KS", "ASIO")
            if player.diagnostics["streaming"] != (not callback_host):
                raise AssertionError(f"Unexpected native playback mode: {player.diagnostics}")
            report["streaming_playback_under_ui_stall"] = player.diagnostics.copy()
        finally:
            blocker.stop()
            timeout.stop()
            speech.close()
            speech._executor.shutdown(wait=True, cancel_futures=True)

        # The real worker must wait for the source to settle and emit speech
        # once across repeated frames, using real OCR and the local translator.
        pipeline_config = copy.deepcopy(config)
        pipeline_config.source_language = "English"
        pipeline_config.output_mode = OutputMode.TTS
        pipeline_config.tts_enabled = True
        worker = ProcessWorker(pipeline_config)
        worker._ocr, worker._translator = ocr, translator
        spoken = []
        worker.speech_ready.connect(lambda text, generation: spoken.append(text))
        worker.speech_stream_ready.connect(lambda stream, generation: spoken.append(stream.key))
        image = Image.new("RGB", (900, 100), "white")
        ImageDraw.Draw(image).text(
            (15, 10), "Welcome brave hero", fill="black", font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 36)
        )
        frame = np.concatenate((np.asarray(image)[:, :, ::-1], np.full((100, 900, 1), 255, dtype=np.uint8)), axis=2)
        worker._process_frame(frame, 1)
        if spoken:
            raise AssertionError("Speech emitted before source text settled")
        time.sleep(0.4)
        worker._process_frame(frame, 1)
        worker._process_frame(frame, 1)
        if len(spoken) != 1:
            raise AssertionError(f"Expected one stable utterance, got {spoken}")
        report["stable_pipeline_utterances"] = len(spoken)

        from ludivoxa.ocr import OCRResult

        class JitterOCR:
            confidence_threshold = config.ocr_confidence

            def extract_text_regions(self, image):
                return [OCRResult(r.text.replace("hero", "her0"), r.bbox) for r in ocr.extract_text_regions(image)]

        worker._ocr = JitterOCR()
        worker._process_frame(frame, 1)
        time.sleep(0.4)
        worker._process_frame(frame, 1)
        if len(spoken) != 1:
            raise AssertionError(f"Unchanged text pixels repeated speech after stable OCR jitter: {spoken}")
        report["stable_ocr_jitter_utterances"] = len(spoken)
        shaded = frame.copy()
        shaded[:, :, :3] = np.maximum(shaded[:, :, :3].astype(np.int16) - 8, 0).astype(np.uint8)
        worker._process_frame(shaded, 1)
        time.sleep(0.4)
        worker._process_frame(shaded, 1)
        if len(spoken) != 1:
            raise AssertionError(f"Minor dialogue shading repeated the same text: {spoken}")
        report["stable_shaded_dialogue_utterances"] = len(spoken)

        # The same production crop helper must keep outside menu text out of
        # OCR while restoring detection positions for the full-window overlay.
        from types import SimpleNamespace

        from ludivoxa.gui.main_window import MainWindow

        canvas = Image.new("RGB", (1100, 500), "white")
        ImageDraw.Draw(canvas).text(
            (15, 10),
            "Do not translate this menu",
            fill="black",
            font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 36),
        )
        rgb = np.asarray(canvas)
        full = np.concatenate((rgb[:, :, ::-1], np.full((500, 1100, 1), 255, dtype=np.uint8)), axis=2)
        full[300:400, 100:1000] = frame
        area = {"x": 100 / 1100, "y": 300 / 500, "width": 900 / 1100, "height": 100 / 500}
        crop, origin = MainWindow._prepare_ocr_frame(SimpleNamespace(_selected_ocr_region=lambda: area), full)
        if crop.shape != (100, 900, 4) or origin != (100, 300):
            raise AssertionError("Selected OCR box was not cropped correctly")
        worker._ocr = ocr
        positioned = []
        worker.ocr_results_ready.connect(lambda regions, generation: positioned.append(regions))
        worker._process_frame(crop, 2, origin, preview_only=True)
        if len(spoken) != 1:
            raise AssertionError("OCR area preview triggered speech")
        worker._process_frame(crop, 2, origin)
        time.sleep(0.4)
        worker._process_frame(crop, 2, origin)
        worker._process_frame(crop, 2, origin)
        if len(spoken) != 2 or " ".join(r.text for r in positioned[-1]) != "Welcome brave hero":
            raise AssertionError("Selected OCR box included outside text or repeated speech")
        if any(r.bbox and (r.bbox["x"] < origin[0] or r.bbox["y"] < origin[1]) for r in positioned[-1]):
            raise AssertionError("Cropped OCR positions were not restored")
        report["selected_box_pipeline_utterances"] = len(spoken) - 1
        report["selected_box_input_shape"] = list(crop.shape)
        report["selected_box_origin"] = list(origin)

        # Same local OCR and translator, two named speakers, one already loaded
        # Nano model. Names must not be sent to the LLM or treated as dialogue.
        character_config = copy.deepcopy(pipeline_config)
        character_config.tts_model_path = "models/VieNeu-TTS-v3-Nano"
        profile = CharacterVoices(
            True,
            {"x": 0, "y": 0, "width": 0.2, "height": 0.1},
            character_config.tts_model_path,
            {"Alice": 1, "Bob": 7},
        )
        character_worker = ProcessWorker(character_config)
        character_worker._ocr, character_worker._translator = ocr, translator
        routed, detected = [], []
        character_worker.speech_stream_ready.connect(lambda stream, _: routed.append(stream))
        character_worker.character_detected.connect(lambda name, mapped, _: detected.append((name, mapped)))
        for name in ("Alice", "Bob"):
            name_image = Image.new("RGB", (360, 90), "white")
            ImageDraw.Draw(name_image).text(
                (15, 10), name, fill="black", font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 36)
            )
            name_frame = np.concatenate(
                (np.asarray(name_image)[:, :, ::-1], np.full((90, 360, 1), 255, np.uint8)), axis=2
            )
            for _ in range(5):
                character_worker._process_frame(frame, 3, speaker_context=("Diagnostic", profile, name_frame))
                time.sleep(0.16)
        if len(routed) != 2 or [stream.speaker_id for stream in routed] != [1, 7]:
            raise AssertionError(f"Character voice routing failed: {detected}, {[s.speaker_id for s in routed]}")
        engine_before = synthesizer._vieneu._engine
        character_audio, synthesis_times = [], []
        for stream in routed:
            start = time.perf_counter()
            pcm = synthesizer.synthesize(" ".join(stream), character_config.tts_model_path, stream.speaker_id)
            character_audio.append(pcm)
            synthesis_times.append((time.perf_counter() - start) * 1000)
        report["character_voice_routing"] = {
            "utterances": len(routed),
            "speaker_ids": [s.speaker_id for s in routed],
            "detected_names": [name for name, mapped in detected if mapped],
            "model_reused": synthesizer._vieneu._engine is engine_before,
            "different_waveforms": character_audio[0] != character_audio[1],
            "synthesis_ms": synthesis_times,
        }
        import meikiocr  # Legacy backend remains importable in the executable.
        import sentencepiece
        import windows_capture

        report["legacy_backend_modules"] = [meikiocr.__name__, sentencepiece.__name__, windows_capture.__name__]
        report["voice_models"] = [
            {"path": path, "speakers": len(voice_details(path))} for _, path in available_voices(config.tts_model_path)
        ]
        report["voice_count"] = sum(voice["speakers"] for voice in report["voice_models"])
        report.update(
            ok=True,
            version=__version__,
            model=config.llm.model,
            legacy_backends_importable=True,
            network_destinations=destinations,
        )
        app.processEvents()
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        heartbeat_stop.set()
        if heartbeat:
            heartbeat.join()
        if ocr is not None:
            close = getattr(ocr, "close", None)
            if close:
                close()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
