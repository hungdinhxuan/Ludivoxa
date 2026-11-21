"""Assemble an empty installer staging directory from already-provisioned local files."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

import yaml

MODEL = "qwen3-hauhau-aggressive:4b-q4_k_m"
WEIGHTS = "6615b7b5184931e4df9c6d0ae9cd29ca9319b73908d4423283d4cc401a12a1cd"


def prepare(app, runtime, store, output):
    app, runtime, store, output = (Path(p).resolve() for p in (app, runtime, store, output))
    if any(
        output == source or output.is_relative_to(source) or source.is_relative_to(output)
        for source in (app, runtime, store)
    ):
        raise ValueError("Installer staging must not overlap any source directory.")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Installer staging directory must be empty; existing files are never deleted.")
    if not (app / "Ludivoxa.exe").is_file() or not (runtime / "ollama.exe").is_file():
        raise ValueError("Build the Windows app and provision the Ollama runtime first.")
    manifest_relative = Path("manifests-v2/ollama.com/library/qwen3-hauhau-aggressive/4b-q4_k_m")
    manifest_path = store / manifest_relative
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    layers = [manifest["config"], *manifest["layers"]]
    digests = [layer["digest"] for layer in layers]
    if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) for digest in digests):
        raise ValueError("Invalid model-store digest")
    if f"sha256:{WEIGHTS}" not in digests:
        raise ValueError("The selected manifest does not contain the expected HauhauCS Q4_K_M weights.")
    for layer in layers:
        source = store / "blobs" / layer["digest"].replace(":", "-")
        if source.stat().st_size != layer["size"]:
            raise ValueError(f"Incomplete model blob: {source.name}")
        with source.open("rb") as data:
            digest = hashlib.file_digest(data, "sha256").hexdigest()
        if digest != layer["digest"].split(":")[1]:
            raise ValueError(f"Model checksum mismatch: {source.name}")

    def ignore_private_root(current, names):
        # Runtime/store are assembled explicitly below, even if the input app
        # is already a full portable bundle. Do not copy old PID records/models.
        return {"config.yml", "debug_capture.png", "runtime", "llm-models"} if Path(current) == app else set()

    def ignore_runtime_downloads(current, names):
        # The downloaded distribution archive is already extracted here.
        # Keeping it doubles much of Ollama and adds ~1.6 GB to the installer.
        return {"ollama.zip", "ollama.pid.json"} if Path(current) == runtime else set()

    shutil.copytree(app, output, dirs_exist_ok=True, ignore=ignore_private_root)
    shutil.copytree(runtime, output / "runtime", dirs_exist_ok=True, ignore=ignore_runtime_downloads)
    target_store = output / "llm-models"
    (target_store / manifest_relative).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_path, target_store / manifest_relative)
    (target_store / "blobs").mkdir(parents=True, exist_ok=True)
    for digest in digests:
        name = digest.replace(":", "-")
        shutil.copy2(store / "blobs" / name, target_store / "blobs" / name)
        cached = store / "metadata" / f"{name}.json"
        if cached.is_file():
            (target_store / "metadata").mkdir(exist_ok=True)
            shutil.copy2(cached, target_store / "metadata" / cached.name)
    # Release packages never inherit developer prompts, game profiles, device
    # identifiers, reference recordings or API keys from the local app config.
    config = yaml.safe_load(Path(__file__).with_name("config.release.yml").read_text(encoding="utf-8"))
    (output / "config.default.yml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    shutil.copy2(Path(__file__).with_name("Stop-LocalRuntime.ps1"), output / "Stop-LocalRuntime.ps1")
    shutil.copy2(Path(__file__).with_name("THIRD_PARTY_NOTICES.txt"), output / "THIRD_PARTY_NOTICES.txt")
    shutil.copy2(Path(__file__).with_name("OFFLINE_SETUP_README.txt"), output / "OFFLINE_SETUP_README.txt")
    inventory = {
        "application": "Ludivoxa",
        "model": MODEL,
        "weights_sha256": WEIGHTS,
        "endpoint": "http://127.0.0.1:11435",
        "runtime_version": "0.40.16",
        "offline": True,
    }
    (output / "offline-bundle.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    return inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", default="dist/Ludivoxa")
    parser.add_argument("--runtime", default=".test-models/ollama-runtime")
    parser.add_argument("--store", default=".test-models/ollama-models")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.app, args.runtime, args.store, args.output), indent=2))


if __name__ == "__main__":
    main()
