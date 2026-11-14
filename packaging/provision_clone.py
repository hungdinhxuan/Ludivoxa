"""Explicit, online provisioning of the optional local cloning engine's weights."""

import argparse
import importlib.util
import json
from pathlib import Path

from huggingface_hub import snapshot_download

REVISION = "aba295eb96a6fa6003ebe417cc1f2802a7adc1dc"
FILES = ["text_encoder.onnx", "duration_predictor.onnx", "vector_estimator.onnx",
         "codec_decoder.onnx", "config.json", "constants.npz", "speaker_encoder.onnx",
         "codec_encoder.onnx", "reference_encoder.onnx", "README.md"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="models/VieNeu-TTS-v3-Nano")
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    spec = importlib.util.find_spec("vieneu")
    if spec is None:
        raise SystemExit("Install the clone extra first: uv sync --extra tts --extra clone")
    snapshot_download("pnnbao-ump/VieNeu-TTS-v3-Nano", revision=REVISION,
                      local_dir=str(directory), allow_patterns=FILES)
    voices = json.loads((Path(spec.origin).parent / "assets/voices_v3_nano.json").read_text(encoding="utf-8"))
    (directory / "voices.json").write_text(json.dumps({"voices": list(voices["presets"])}, ensure_ascii=False), encoding="utf-8")
    print(f"Local voice-cloning models ready: {directory}")


if __name__ == "__main__":
    main()
