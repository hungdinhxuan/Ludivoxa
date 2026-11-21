"""Opt-in real CPU inference. Provision models/fonts first; this test forbids networking."""

import os
import socket
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from interpreter.config import Config, OCRBackend, PaddleSettings
from interpreter.language_detection import detect_language
from interpreter.ocr import create_ocr
from interpreter.paddle_ocr import IsolatedPaddleOCR, PaddleOCR

ROOT = os.environ.get("INTERPRETER_TEST_MODEL_ROOT")
pytestmark = pytest.mark.skipif(
    not ROOT or os.name != "nt", reason="Set INTERPRETER_TEST_MODEL_ROOT; Windows fonts required"
)

CASES = [
    ("English", "Welcome brave hero", "arial.ttf", "PP-OCRv5_mobile_rec"),
    ("Japanese", "こんにちは世界", "msgothic.ttc", "PP-OCRv5_mobile_rec"),
]
CASES += [(language, text, font, recognizer + "_onnx") for language, text, font, recognizer in CASES.copy()]


@pytest.mark.parametrize("language,text,font,recognizer", CASES)
def test_real_offline_inference(monkeypatch, language, text, font, recognizer):
    root = Path(ROOT)
    font_path = Path("C:/Windows/Fonts") / font
    if not font_path.is_file() or not (root / recognizer).is_dir():
        pytest.skip(f"Missing font or local model for {language}")

    def forbid_network(*args, **kwargs):
        raise AssertionError("Network access attempted during offline OCR")

    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    suffix = "_onnx" if recognizer.endswith("_onnx") else ""
    ocr = PaddleOCR(PaddleSettings(str(root / f"PP-OCRv5_mobile_det{suffix}"), str(root / recognizer)))
    start = time.perf_counter()
    ocr.load()
    load_ms = (time.perf_counter() - start) * 1000
    image = Image.new("RGB", (900, 100), "white")
    ImageDraw.Draw(image).text((15, 10), text, font=ImageFont.truetype(str(font_path), 36), fill="black")
    frame = np.concatenate((np.asarray(image)[:, :, ::-1], np.full((100, 900, 1), 255, dtype=np.uint8)), axis=2)
    timings = []
    for _ in range(2):
        start = time.perf_counter()
        regions = ocr.extract_text_regions(frame)
        timings.append(round((time.perf_counter() - start) * 1000, 1))
    recognized = " ".join(region.text for region in regions)
    assert recognized
    assert detect_language(recognized) == language
    print(f"{language}: load={load_ms:.1f}ms OCR={timings}ms text={recognized!r}")


@pytest.mark.parametrize(
    "language,text,font",
    [
        ("Chinese", "欢迎来到村庄", "msyh.ttc"),
        ("Chinese", "歡迎來到村莊", "msjh.ttc"),
        ("Korean", "안녕하세요 용사님", "malgun.ttf"),
    ],
)
def test_real_language_profile_uses_isolated_onnx(language, text, font):
    root = Path(ROOT)
    font_path = Path("C:/Windows/Fonts") / font
    if not font_path.is_file():
        pytest.skip(f"Missing Windows font {font}")
    config = Config(
        ocr_backend=OCRBackend.PADDLE,
        source_language=language,
        paddle=PaddleSettings(
            str(root / "PP-OCRv5_mobile_det_onnx"), str(root / "en_PP-OCRv5_mobile_rec_onnx"), max_side=640
        ),
    )
    ocr = create_ocr(config)
    try:
        ocr.load()
        timings = []
        for left in range(15, 22):
            image = Image.new("RGBA", (640, 100), "white")
            ImageDraw.Draw(image).text((left, 10), text, font=ImageFont.truetype(str(font_path), 36), fill="black")
            frame = np.asarray(image)[:, :, [2, 1, 0, 3]].copy()
            start = time.perf_counter()
            regions = ocr.extract_text_regions(frame)
            timings.append((time.perf_counter() - start) * 1000)
            assert " ".join(region.text for region in regions) == text
        print(f"{language} ({font}): warm OCR median={np.median(timings[2:]):.1f} ms")
    finally:
        ocr.close()


@pytest.mark.parametrize("suffix", ["", "_onnx"])
def test_isolated_ocr_keeps_parent_responsive_and_reuses_static_frames(suffix):
    root = Path(ROOT)
    if not (root / f"PP-OCRv5_mobile_rec{suffix}").is_dir():
        pytest.skip("Local exports not provisioned")
    engine = IsolatedPaddleOCR(
        PaddleSettings(str(root / f"PP-OCRv5_mobile_det{suffix}"), str(root / f"PP-OCRv5_mobile_rec{suffix}"))
    )
    ticks = []
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(0.01):
            ticks.append(time.perf_counter())

    thread = threading.Thread(target=heartbeat)
    thread.start()
    try:
        engine.load()
        assert engine._process.pid != os.getpid()
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x400, False, engine._process.pid)
        assert handle
        try:
            assert kernel.GetPriorityClass(ctypes.c_void_p(handle)) == 0x4000
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
        for text, font in (("Welcome brave hero", "arial.ttf"), ("こんにちは世界", "msgothic.ttc")):
            image = Image.new("RGB", (1280, 720), "white")
            ImageDraw.Draw(image).text(
                (15, 550), text, font=ImageFont.truetype(f"C:/Windows/Fonts/{font}", 36), fill="black"
            )
            frame = np.concatenate(
                (np.asarray(image)[:, :, ::-1], np.full((720, 1280, 1), 255, dtype=np.uint8)), axis=2
            )
            regions = engine.extract_text_regions(frame)
            assert " ".join(r.text for r in regions) == text
            start = time.perf_counter()
            assert engine.extract_text_regions(frame.copy()) is regions
            assert time.perf_counter() - start < 0.1
    finally:
        engine.close()
        stopped.set()
        thread.join()
    assert len(ticks) > 10
    max_pause = max(np.diff(ticks))
    print(f"Parent heartbeat maximum pause during real isolated OCR: {max_pause * 1000:.1f}ms")
    assert max_pause < 0.2
