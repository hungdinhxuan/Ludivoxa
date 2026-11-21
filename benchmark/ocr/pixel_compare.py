"""Measure native OneOCR or PaddleOCR on the same local annotated dialogue crops."""

from __future__ import annotations

import argparse
import random
import sys
import time
from importlib.metadata import version
from pathlib import Path

from benchlib import edit_counts, load_json, modal_prediction, normalize_text, percentile, sha256_file, write_json
from runner import _read_bgra


def load_engine(args):
    if args.engine == "oneocr":
        if sys.platform != "win32" or not args.oneocr_dir:
            raise ValueError("OneOCR needs Windows and --oneocr-dir pointing to the local Snipping Tool runtime.")
        import oneocr

        oneocr.CONFIG_DIR = str(args.oneocr_dir.resolve())
        model = oneocr.OcrEngine()

        def recognize(image):
            result = model.recognize_cv2(image)
            if "error" in result:
                raise ValueError(result["error"])
            return result["text"]

        return recognize, {
            "package_version": version("oneocr"),
            "model_sha256": sha256_file(args.oneocr_dir / "oneocr.onemodel"),
        }

    if not args.detector or not args.recognizer:
        raise ValueError("PaddleOCR needs --detector and --recognizer pointing to local model directories.")
    from ludivoxa.config import PaddleSettings
    from ludivoxa.paddle_ocr import PaddleOCR

    settings = PaddleSettings(
        str(args.detector.resolve()), str(args.recognizer.resolve()), cpu_threads=args.threads, max_side=args.max_side
    )
    model = PaddleOCR(settings)
    model.confidence_threshold = args.confidence
    model.load()

    def recognize(image):
        return " ".join(region.text for region in model.extract_text_regions(image))

    return recognize, {
        "package_version": version("paddleocr"),
        "detector": args.detector.name,
        "recognizer": args.recognizer.name,
        "threads": args.threads,
        "max_side": args.max_side,
        "confidence": args.confidence,
    }


def summarize(rows):
    timings = [elapsed for row in rows for elapsed in row["milliseconds"]]
    characters = sum(len(normalize_text(row["text"])) for row in rows)
    return {
        "images": len(rows),
        "p50_ms": round(percentile(timings, 0.50), 2),
        "p95_ms": round(percentile(timings, 0.95), 2),
        "cer": sum(row["edits"] for row in rows) / characters,
        "exact": sum(row["edits"] == 0 for row in rows),
        "empty": sum(not row["prediction"].strip() for row in rows),
    }


def run(args):
    manifest = load_json(args.manifest)
    rows = []
    images = []
    for item in manifest["samples"]:
        group = item["group"]
        if args.group and group not in args.group:
            continue
        if not normalize_text(item["text"]):
            raise ValueError("Each crop must have a non-empty reference transcription.")
        path = args.manifest.parent / item["image"]
        rows.append({**item, "image_sha256": sha256_file(path), "milliseconds": []})
        images.append(_read_bgra(path))
    if not rows:
        raise ValueError("No matching crops.")
    recognizes, metadata = load_engine(args)
    for _ in range(args.warmups):
        recognizes(images[0])
    predictions = [[] for row in rows]
    rng = random.Random(1729)
    for _ in range(args.repeats):
        order = list(range(len(rows)))
        rng.shuffle(order)
        for index in order:
            started = time.perf_counter()
            prediction = recognizes(images[index])
            rows[index]["milliseconds"].append((time.perf_counter() - started) * 1000)
            predictions[index].append(prediction)
    for row, repeated in zip(rows, predictions, strict=True):
        row["prediction"] = modal_prediction(repeated)
        row["edits"] = edit_counts(row["text"], row["prediction"])["distance"]
    summary = {
        group: summarize([row for row in rows if row["group"] == group])
        for group in sorted({row["group"] for row in rows})
    }
    write_json(
        args.output,
        {
            "engine": args.engine,
            "settings": metadata,
            "repeats": args.repeats,
            "warmups": args.warmups,
            "summary": summary,
            "rows": rows,
        },
    )
    for group, result in summary.items():
        print(f"{group}  {result}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine", choices=("oneocr", "paddle"))
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--group", action="append")
    parser.add_argument("--oneocr-dir", type=Path)
    parser.add_argument("--detector", type=Path)
    parser.add_argument("--recognizer", type=Path)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--max-side", type=int, default=640)
    parser.add_argument("--confidence", type=float, default=0.6)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1 or args.warmups < 0:
        parser.error("Use at least one repeat and a nonnegative warmup count.")
    run(args)


if __name__ == "__main__":
    main()
