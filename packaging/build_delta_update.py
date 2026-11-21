"""Build a Windows app update that reuses files from one exact release build."""

import argparse
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from package_release import ROOT, VERSION, sha256


def app_files(root, version):
    internal = root / "_internal"
    exe = root / "Ludivoxa.exe"
    metadata = internal / f"ludivoxa-{version}.dist-info/METADATA"
    if not exe.is_file() or not metadata.is_file():
        raise ValueError(f"Expected a PyInstaller onedir build for {version} in {root}.")

    def reject_link(path):
        attrs = getattr(path.lstat(), "st_file_attributes", 0)
        if path.is_symlink() or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"App build contains a file or directory link: {path}")

    def fail_walk(error):
        raise error

    for path in (root, internal, exe):
        reject_link(path)
    files = {"Ludivoxa.exe": sha256(exe)}
    for parent, dirs, names in os.walk(internal, onerror=fail_walk):
        for name in [*dirs, *names]:
            reject_link(Path(parent) / name)
        for name in names:
            path = Path(parent) / name
            files[path.relative_to(root).as_posix()] = sha256(path)
    return files


def build(base, app, output, compiler, base_version, version=VERSION, *, definition=None):
    versions = (base_version, version)
    if not all(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", v) for v in versions) or not (
        tuple(map(int, base_version.split("."))) < tuple(map(int, version.split(".")))
    ):
        raise ValueError("Delta updates require a newer stable version and an older stable base.")
    base, app, output = (Path(p).absolute() for p in (base, app, output))
    if any(output.resolve().is_relative_to(root.resolve()) for root in (base, app)):
        raise ValueError("Delta output must be outside both app build directories.")
    old, new = app_files(base, base_version), app_files(app, version)
    changed = sorted(name for name, digest in new.items() if old.get(name) != digest)
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    if "Ludivoxa.exe" not in changed:
        raise ValueError("The target build must have a new Ludivoxa.exe.")
    spellings = {name.casefold() for name in old | new}
    if len(spellings) != len(old | new) or any(
        parent.as_posix().casefold() in spellings for name in old | new for parent in Path(name).parents
    ):
        raise ValueError("Publish a standard update for app files that change case or swap with directories.")

    stem = f"Ludivoxa-{version}-Delta-from-{base_version}"
    installer, manifest = output / (stem + ".exe"), output / (stem + ".base.sha256")
    if any(os.path.lexists(path) for path in (installer, manifest)):
        raise ValueError("Delta assets already exist; use a new output directory.")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".delta-", dir=output) as temp:
        stage = Path(temp)
        base_manifest = stage / "ludivoxa-delta-base.sha256"
        base_manifest.write_text(
            "".join(f"{digest}  {name}\n" for name, digest in sorted(old.items()))
            + "".join(f"-  {name}\n" for name in added),
            encoding="utf-8",
        )
        files = stage / "files.iss"
        entries = []
        for name in changed:
            path = Path(name)
            dest = "{app}" if path.parent == Path(".") else "{app}\\" + str(path.parent).replace("{", "{{")
            entries.append(f'Source: "{app / path}"; DestDir: "{dest}"; Flags: ignoreversion\n')
        files.write_text("".join(entries), encoding="utf-8-sig")
        deletes = stage / "deletes.iss"
        entries = [f'Type: files; Name: "{{app}}\\{str(Path(name)).replace("{", "{{")}"\n' for name in removed]
        parents = {parent for name in removed for parent in Path(name).parents if len(parent.parts) > 1}
        entries.extend(
            f'Type: dirifempty; Name: "{{app}}\\{str(parent).replace("{", "{{")}"\n'
            for parent in sorted(parents, key=lambda p: (-len(p.parts), str(p)))
        )
        deletes.write_text("".join(entries), encoding="utf-8-sig")
        result = subprocess.run(
            [
                str(Path(compiler).resolve(strict=True)),
                "/Qp",
                "/DAppUpdateOnly=1",
                f"/DAppVersion={version}",
                f"/DAppNumericVersion={version}.0",
                f"/DDeltaFrom={base_version}",
                f"/DDeltaManifest={base_manifest}",
                f"/DDeltaFiles={files}",
                f"/DDeltaDeletes={deletes}",
                f"/DPayloadDir={app}",
                f"/DOutputDir={stage}",
                str(definition or ROOT / "packaging/windows_installer.iss"),
            ],
            capture_output=True,
            text=True,
            errors="replace",
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        shutil.move(stage / installer.name, installer)
        shutil.move(base_manifest, manifest)
    return installer, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Exact PyInstaller onedir build shipped with the base release")
    parser.add_argument("--base-version", required=True)
    parser.add_argument("--app", required=True, help="New PyInstaller onedir build")
    parser.add_argument("--version", default=VERSION, help="Target app version, defaults to pyproject.toml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--compiler", default=ROOT / "build/tools/InnoSetup/ISCC.exe")
    args = parser.parse_args()
    for asset in build(args.base, args.app, args.output, args.compiler, args.base_version, args.version):
        print(f"{asset.name}: {asset.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
