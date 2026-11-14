"""Explicit, one-time download of pinned official PaddleOCR ONNX exports.

The app and build scripts never invoke this downloader automatically.
"""

import argparse
import hashlib
import json
from pathlib import Path

import requests

EXPORTS = {
    "PP-OCRv5_mobile_det": (
        "e6f4fa85f00e168c862bc462aebca69eef9b3d3d",
        "a431985659dc921974177a95adcfbb90fd9e51989a5e04d70d0b75f597b6e61d",
    ),
    "en_PP-OCRv5_mobile_rec": (
        "3fafbc3b5dcf93dd72add9f48368be8a3a2cd33b",
        "b5f833dfc5d0eb71da397b4efa06ebeee9b431b690a47d6af40d77d8eabc557f",
    ),
    "PP-OCRv5_mobile_rec": (
        "ed152b8b495f84de93cda5709d768548a9127622",
        "da72dc72ca4dc220df0dfde68c1dedc31c58d3e76a25871122e5056227d50092",
    ),
}


def provision(destination):
    for name, (revision, checksum) in EXPORTS.items():
        repo = f"PaddlePaddle/{name}_onnx"
        folder = Path(destination) / (name + "_onnx")
        folder.mkdir(parents=True, exist_ok=True)
        for filename in ("inference.yml", "inference.onnx", "README.md"):
            target = folder / filename
            if filename == "inference.onnx" and target.is_file():
                if hashlib.sha256(target.read_bytes()).hexdigest() == checksum:
                    continue
            response = requests.get(f"https://huggingface.co/{repo}/resolve/{revision}/{filename}", timeout=120)
            response.raise_for_status()
            if filename == "inference.onnx" and hashlib.sha256(response.content).hexdigest() != checksum:
                raise ValueError(f"Checksum mismatch for {repo}/{filename}; model was not installed.")
            partial = target.with_suffix(target.suffix + ".part")
            partial.write_bytes(response.content)
            partial.replace(target)
        (folder / "source.json").write_text(
            json.dumps({"repo": repo, "revision": revision, "onnx_sha256": checksum}, indent=2), encoding="utf-8"
        )
        print(f"Verified {folder}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", default="models")
    provision(parser.parse_args().destination)
