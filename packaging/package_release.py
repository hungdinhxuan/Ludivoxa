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
TAG = "v" + re.sub(r"rc(\d+)$", r"-rc.\1", VERSION)
GITHUB_ASSET_LIMIT = 2 * 1024**3


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(installer, output, *, app=None, source_commit=None, app_update=None, deltas=()):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Release output must be empty; existing assets are never overwritten.")
    if source_commit is not None and not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("source_commit must be a full Git commit SHA.")
    source_assets = []
    if installer is not None:
        installer = Path(installer).resolve()
        if output == installer or output.is_relative_to(installer) or installer.is_relative_to(output):
            raise ValueError("Release output and installer input must be separate directories.")
        base = f"Ludivoxa-{VERSION}-Full-Offline-Setup"
        executable = installer / (base + ".exe")
        parts = sorted(installer.glob(base + "-*.bin"), key=lambda p: p.name)
        expected = [f"{base}-{i}.bin" for i in range(1, len(parts) + 1)]
        if not executable.is_file() or not parts or [p.name for p in parts] != expected:
            raise ValueError("The installer EXE and contiguous numbered BIN files are required.")
        source_assets.extend([executable, *parts])
    for path in source_assets:
        if not 0 < path.stat().st_size < GITHUB_ASSET_LIMIT:
            raise ValueError(f"Release asset must be non-empty and smaller than 2 GiB: {path.name}")
    if app_update is not None:
        app_update = Path(app_update).resolve()
        if (
            app_update.name != f"Ludivoxa-{VERSION}-App-Update.exe"
            or not app_update.is_file()
            or not 0 < app_update.stat().st_size < GITHUB_ASSET_LIMIT
        ):
            raise ValueError("The app-update installer must have the release version name and be smaller than 2 GiB.")
        if app_update.is_relative_to(output):
            raise ValueError("Release output and app-update input must be separate directories.")
        source_assets.append(app_update)
    if not source_assets:
        raise ValueError("A full installer or app-update installer is required.")
    delta_names = set()
    for delta in deltas:
        delta = Path(delta).resolve()
        match = re.fullmatch(rf"Ludivoxa-{re.escape(VERSION)}-Delta-from-([0-9]+\.[0-9]+\.[0-9]+)\.exe", delta.name)
        base = delta.with_suffix(".base.sha256")
        if (
            not match
            or not app_update
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", VERSION)
            or tuple(map(int, match[1].split("."))) >= tuple(map(int, VERSION.split(".")))
            or delta.name in delta_names
            or any(path.is_relative_to(output) or not path.is_file() for path in (delta, base))
            or not 0 < base.stat().st_size <= 4 * 1024 * 1024
            or not 0 < delta.stat().st_size < app_update.stat().st_size - base.stat().st_size
        ):
            raise ValueError("Each delta needs a matching base manifest and a larger standard app-update fallback.")
        delta_names.add(delta.name)
        source_assets.extend([delta, base])
    output.mkdir(parents=True, exist_ok=True)
    for path in source_assets:
        shutil.copy2(path, output / path.name)
    readme = ROOT / "packaging" / ("OFFLINE_SETUP_README.txt" if installer else "APP_UPDATE_README.txt")
    (output / "README.txt").write_text(
        readme.read_text(encoding="utf-8").replace("{version}", VERSION).replace("{tag}", TAG), encoding="utf-8"
    )
    assets = [output / p.name for p in source_assets] + [output / "README.txt"]
    manifest = {
        "application": "Ludivoxa",
        "version": VERSION,
        "tag": TAG,
        "source_commit": source_commit,
        "platform": "windows-x64",
        "installer": executable.name if installer else app_update.name,
        "models_included": installer is not None,
        "assets": [{"name": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)} for p in assets],
    }
    if installer is not None:
        manifest.update(
            model="qwen3-hauhau-aggressive:4b-q4_k_m",
            model_weights_sha256="6615b7b5184931e4df9c6d0ae9cd29ca9319b73908d4423283d4cc401a12a1cd",
        )
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
    parser.add_argument("--installer", help="Full offline setup directory; omit for an app-update-only release")
    parser.add_argument("--output", required=True)
    parser.add_argument("--app", default="dist/Interpreter/Interpreter.exe")
    parser.add_argument("--source-commit")
    parser.add_argument("--app-update", help="Optional app-only installer, preferred by the in-app updater")
    parser.add_argument("--delta", action="append", default=[], help="Optional delta installer; can be repeated")
    parser.add_argument("--local-zip", help="Optional local archive; too large for a single GitHub release asset")
    args = parser.parse_args()
    assets = prepare(
        args.installer,
        args.output,
        app=args.app,
        source_commit=args.source_commit,
        app_update=args.app_update,
        deltas=args.delta,
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
