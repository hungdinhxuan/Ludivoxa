"""Compile the production app-update file rules into an isolated native fixture."""

import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = ROOT / "build/tools/InnoSetup/ISCC.exe"


@pytest.mark.skipif(os.name != "nt" or not COMPILER.is_file(), reason="Native Inno Setup compiler required")
@pytest.mark.parametrize("app_version", ["1.0.1", "1.1.2rc1"])
def test_app_update_preserves_models_and_configuration_in_isolated_install(tmp_path, app_version):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "Interpreter.exe").write_bytes(b"new-app-file")
    (payload / "_internal").mkdir()
    (payload / "_internal/library.dat").write_bytes(b"new-native-library")
    (payload / "_internal/interpreter_v2-1.0.1.dist-info").mkdir()
    (payload / "_internal/interpreter_v2-1.0.1.dist-info/METADATA").write_text("Version: 1.0.1\n")
    (payload / "_internal/restart-test.ps1").write_text(
        "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'restart-observed.txt'), 'ok')"
    )
    for name in ("models", "llm-models", "runtime"):
        (payload / name).mkdir()
        (payload / name / "keep.dat").write_bytes(b"must-not-be-packaged")
    destination = tmp_path / "install with spaces"
    destination.mkdir()
    (destination / "Interpreter.exe").write_bytes(b"old-app-file")
    (destination / "config.yml").write_text("window_title: My game\n")
    (destination / "_internal/interpreter_v2-1.0.0.dist-info").mkdir(parents=True)
    (destination / "_internal/interpreter_v2-1.0.0.dist-info/METADATA").write_text("Version: 1.0.0\n")
    (destination / "_internal/other_package-1.0.0.dist-info").mkdir()
    (destination / "_internal/other_package-1.0.0.dist-info/METADATA").write_text("Version: 1.0.0\n")
    for name in ("models", "llm-models", "runtime"):
        (destination / name).mkdir()
        (destination / name / "keep.dat").write_bytes(b"original-local-data")
    text = (ROOT / "packaging/windows_installer.iss").read_text()
    text = text.replace(
        "AppId={{6C80F14D-818C-4A3F-B431-5389B73CA221}",
        "AppId=Ludivoxa-Isolated-Updater-Test\nUninstallable=no\nCreateUninstallRegKey=no\nUsePreviousAppDir=no",
    )
    # Keep the production [Files] and restart checks, but make this fixture
    # incapable of creating production uninstall entries or Start menu icons.
    start, end = text.index("[Icons]"), text.index("[Run]")
    text = text[:start] + text[end:]
    text = text.replace(
        'Filename: "{app}\\Interpreter.exe"; WorkingDir: "{app}"; Flags: nowait runasoriginaluser; Check: IsUpdateRestart',
        'Filename: "{sys}\\WindowsPowerShell\\v1.0\\powershell.exe"; '
        'Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\\_internal\\restart-test.ps1"""; '
        'WorkingDir: "{app}"; Flags: nowait runasoriginaluser; Check: IsUpdateRestart',
    )
    for old, new in (
        (r"..\LICENSE", ROOT / "LICENSE"),
        (r"..\src\interpreter\resources\icons\icon.ico", ROOT / "src/interpreter/resources/icons/icon.ico"),
        ('"Stop-LocalRuntime.ps1"', '"' + str(ROOT / "packaging/Stop-LocalRuntime.ps1") + '"'),
        ('"THIRD_PARTY_NOTICES.txt"', '"' + str(ROOT / "packaging/THIRD_PARTY_NOTICES.txt") + '"'),
    ):
        text = text.replace(old, str(new))
    definition = tmp_path / "isolated.iss"
    definition.write_text(text, encoding="utf-8-sig")
    result = subprocess.run(
        [
            str(COMPILER),
            "/DAppUpdateOnly=1",
            f"/DAppVersion={app_version}",
            f"/DPayloadDir={payload}",
            f"/DOutputDir={tmp_path}",
            str(definition),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    setup = tmp_path / f"Ludivoxa-{app_version}-App-Update.exe"
    result = subprocess.run(
        [
            str(setup),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/NOICONS",
            f"/DIR={destination}",
            f"/LOG={tmp_path / 'install.log'}",
        ],
        timeout=30,
    )
    assert result.returncode == 0
    assert (destination / "Interpreter.exe").read_bytes() == b"new-app-file"
    assert (destination / "_internal/library.dat").read_bytes() == b"new-native-library"
    assert not (destination / "_internal/interpreter_v2-1.0.0.dist-info").exists()
    assert (destination / "_internal/interpreter_v2-1.0.1.dist-info/METADATA").read_text() == "Version: 1.0.1\n"
    assert (destination / "_internal/other_package-1.0.0.dist-info/METADATA").read_text() == "Version: 1.0.0\n"
    assert (destination / "config.yml").read_text() == "window_title: My game\n"
    for name in ("models", "llm-models", "runtime"):
        assert (destination / name / "keep.dat").read_bytes() == b"original-local-data"
    assert not list(destination.glob("unins*.exe"))
    marker = destination / "_internal/restart-observed.txt"
    assert not marker.exists()
    restarted = subprocess.run(
        [
            str(setup),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/NOICONS",
            "/LUDIVOXARESTART=1",
            f"/LOG={tmp_path / 'restart.log'}",
            f"/DIR={destination}",
        ],
        timeout=30,
    )
    assert restarted.returncode == 0
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.read_text() == "ok"
    fresh = tmp_path / "empty installation"
    rejected = subprocess.run(
        [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", f"/DIR={fresh}"],
        timeout=30,
    )
    assert rejected.returncode != 0
    assert not (fresh / "Interpreter.exe").exists()
