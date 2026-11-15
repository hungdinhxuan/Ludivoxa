"""Prepare checksummed GitHub release assets; model weights stay out of Git."""

import argparse
import hashlib
import json
import re
import shutil
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
GITHUB_ASSET_LIMIT = 2 * 1024**3


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(installer, output, *, app=None, source_commit=None, app_update=None):
    installer, output = Path(installer).resolve(), Path(output).resolve()
    if output == installer or output.is_relative_to(installer) or installer.is_relative_to(output):
        raise ValueError("Release output and installer input must be separate directories.")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Release output must be empty; existing assets are never overwritten.")
    if source_commit is not None and not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("source_commit must be a full Git commit SHA.")
    base = f"Ludivoxa-{VERSION}-Full-Offline-Setup"
    executable = installer / (base + ".exe")
    parts = sorted(installer.glob(base + "-*.bin"), key=lambda p: p.name)
    expected = [f"{base}-{i}.bin" for i in range(1, len(parts) + 1)]
    if not executable.is_file() or not parts or [p.name for p in parts] != expected:
        raise ValueError("The installer EXE and contiguous numbered BIN files are required.")
    for path in [executable, *parts]:
        if not 0 < path.stat().st_size < GITHUB_ASSET_LIMIT:
            raise ValueError(f"Release asset must be non-empty and smaller than 2 GiB: {path.name}")
    if app_update is not None:
        app_update = Path(app_update)
        if (
            app_update.name != f"Ludivoxa-{VERSION}-App-Update.exe"
            or not app_update.is_file()
            or not 0 < app_update.stat().st_size < GITHUB_ASSET_LIMIT
        ):
            raise ValueError("The app-update installer must have the release version name and be smaller than 2 GiB.")
    output.mkdir(parents=True, exist_ok=True)
    for path in [executable, *parts]:
        shutil.copy2(path, output / path.name)
    shutil.copy2(ROOT / "packaging/OFFLINE_SETUP_README.txt", output / "README.txt")
    assets = [output / p.name for p in [executable, *parts]] + [output / "README.txt"]
    if app_update is not None:
        shutil.copy2(app_update, output / app_update.name)
        assets.append(output / app_update.name)
    manifest = {
        "application": "Ludivoxa",
        "version": VERSION,
        "tag": f"v{VERSION}",
        "source_commit": source_commit,
        "platform": "windows-x64",
        "installer": executable.name,
        "model": "qwen3-hauhau-aggressive:4b-q4_k_m",
        "model_weights_sha256": "6615b7b5184931e4df9c6d0ae9cd29ca9319b73908d4423283d4cc401a12a1cd",
        "assets": [{"name": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)} for p in assets],
    }
    if app is not None:
        manifest["application_exe_sha256"] = sha256(app)
    manifest_path = output / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    assets.append(manifest_path)
    checksums = output / "SHA256SUMS.txt"
    checksums.write_text("".join(f"{sha256(p)}  {p.name}\n" for p in assets), encoding="utf-8")
    return [*assets, checksums]


def local_zip(assets, destination):
    """Optional local archive; upload individual files to GitHub, not this ZIP."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Local ZIP already exists; choose a new output name.")
    temporary = destination.with_suffix(destination.suffix + ".building")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path in assets:
            archive.write(path, arcname="Ludivoxa-Offline-Setup/" + path.name)
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise ValueError("Local ZIP CRC validation failed.")
    temporary.replace(destination)
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{sha256(destination)}  {destination.name}\n", encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--app", default="dist/Interpreter/Interpreter.exe")
    parser.add_argument("--source-commit")
    parser.add_argument("--app-update", help="Optional app-only installer, preferred by the in-app updater")
    parser.add_argument("--local-zip", help="Optional local archive; too large for a single GitHub release asset")
    args = parser.parse_args()
    assets = prepare(
        args.installer, args.output, app=args.app, source_commit=args.source_commit, app_update=args.app_update
    )
    if args.local_zip:
        local_zip(assets, args.local_zip)
    print(
        json.dumps(
            {"version": VERSION, "assets": [{"name": p.name, "bytes": p.stat().st_size} for p in assets]}, indent=2
        )
    )


if __name__ == "__main__":
    main()
