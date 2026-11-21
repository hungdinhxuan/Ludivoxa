"""Private runtime ownership, shutdown leases and native Windows process trees."""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests


def load_helper(name):
    path = Path(__file__).parents[1] / "packaging" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"shutdown_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    helper = load_helper("local_runtime")
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/ollama.exe").touch()
    (tmp_path / "config.yml").write_text(
        "llm:\n  provider: ollama\n  base_url: http://127.0.0.1:11435\n", encoding="utf-8"
    )
    session = MagicMock()
    monkeypatch.setattr(helper.requests, "Session", lambda: session)
    job = MagicMock()
    monkeypatch.setattr(helper, "_WindowsRuntimeJob", lambda directory: job)
    process = MagicMock(pid=12345)
    process.poll.return_value = None
    spawn = MagicMock(return_value=process)
    monkeypatch.setattr(helper.subprocess, "Popen", spawn)
    monkeypatch.setattr(helper.Path, "home", lambda: tmp_path / "home")
    return helper, tmp_path, session, job, process, spawn


@pytest.mark.parametrize("valid_record", [False, True])
def test_existing_listener_without_verified_job_membership_is_not_owned(runtime, valid_record):
    helper, root, _session, job, _process, spawn = runtime
    if valid_record:
        (root / "runtime/ollama.pid.json").write_text(json.dumps({"pid": 12345}), encoding="utf-8")
    job.contains.return_value = False
    assert helper.ensure_bundled_ollama(root) is None
    spawn.assert_not_called()
    job.add.assert_not_called()
    job.close.assert_called_once()


def test_existing_managed_service_joins_lease_without_spawning(runtime):
    helper, root, _session, job, _process, spawn = runtime
    (root / "runtime/ollama.pid.json").write_text(json.dumps({"pid": 12345}), encoding="utf-8")
    job.contains.return_value = True
    assert helper.ensure_bundled_ollama(root) is job
    spawn.assert_not_called()
    job.close.assert_not_called()
    job.contains.assert_called_once_with(12345, root / "runtime/ollama.exe")


def test_unmanaged_child_is_stopped_if_assignment_fails(runtime):
    helper, root, session, job, process, _spawn = runtime
    session.get.side_effect = requests.ConnectionError()
    job.add.side_effect = OSError("cannot assign job")
    assert helper.ensure_bundled_ollama(root) is None
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)
    job.close.assert_called_once()
    assert not (root / "runtime/ollama.pid.json").exists()


def test_failed_service_start_releases_job_instead_of_leaving_background_work(runtime):
    helper, root, session, job, process, _spawn = runtime
    session.get.side_effect = requests.ConnectionError()
    process.poll.return_value = 1
    assert helper.ensure_bundled_ollama(root) is None
    job.add.assert_called_once_with(12345)
    job.close.assert_called_once()


@pytest.mark.parametrize("mode", ["gui", "failure", "self-test"])
def test_entry_releases_runtime_even_when_gui_raises(tmp_path, monkeypatch, mode):
    import ludivoxa

    entry = load_helper("windows_entry")
    monkeypatch.setattr(entry, "freeze_support", lambda: None)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Ludivoxa.exe"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["Ludivoxa.exe"] + (["--self-test", str(tmp_path / "result.json")] if mode == "self-test" else []),
    )
    monkeypatch.chdir(tmp_path)
    job = MagicMock()
    monkeypatch.setitem(sys.modules, "local_runtime", SimpleNamespace(ensure_bundled_ollama=lambda directory: job))
    monkeypatch.setitem(sys.modules, "frozen_smoke", SimpleNamespace(run_smoke=lambda report: 12))
    main = MagicMock(return_value=12)
    if mode == "failure":
        main.side_effect = RuntimeError("GUI failure")
    monkeypatch.setattr(ludivoxa, "main", main)
    if mode == "failure":
        with pytest.raises(RuntimeError, match="GUI failure"):
            entry.run()
    else:
        assert entry.run() == 12
    job.close.assert_called_once()


def wait_for_file(path, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size:
            return
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {path.name}")


@pytest.mark.skipif(os.name != "nt", reason="Native Windows job objects")
def test_native_job_stops_server_and_child_only_after_last_gui_lease_closes(tmp_path):
    from ctypes import wintypes

    helper = load_helper("local_runtime")
    first, second = helper._WindowsRuntimeJob(tmp_path), helper._WindowsRuntimeJob(tmp_path)
    executable = Path(sys._base_executable).resolve()
    record = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time; from pathlib import Path; sys.stdin.readline(); "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)"
    )
    parent = subprocess.Popen(
        [str(executable), "-c", code, str(record)], stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW
    )
    child_handle = None
    kernel = first._kernel
    try:
        first.add(parent.pid)
        parent.stdin.write(b"go\n")
        parent.stdin.flush()
        wait_for_file(record)
        child_pid = int(record.read_text())
        assert first.contains(parent.pid, executable) and second.contains(child_pid, executable)
        assert not first.contains(child_pid, executable.with_name("wrong.exe"))
        child_handle = kernel.OpenProcess(0x100000 | 0x0001, False, child_pid)
        assert child_handle
        first.close()
        assert parent.poll() is None
        assert second.contains(child_pid, executable)
        second.close()
        parent.wait(timeout=5)
        assert parent.poll() is not None
        assert kernel.WaitForSingleObject(child_handle, 5000) == 0
    finally:
        first.close()
        second.close()
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        parent.stdin.close()
        if child_handle:
            if kernel.WaitForSingleObject(child_handle, 1000) != 0:
                kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                kernel.TerminateProcess.restype = wintypes.BOOL
                kernel.TerminateProcess(child_handle, 1)
            kernel.CloseHandle(child_handle)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows job objects")
def test_crashed_owner_does_not_leave_its_service_running(tmp_path):
    from ctypes import wintypes

    helper = load_helper("local_runtime")
    executable = Path(sys._base_executable).resolve()
    helper_folder = Path(__file__).parents[1] / "packaging"
    record = tmp_path / "service.pid"
    code = (
        "import subprocess,sys,time; from pathlib import Path; sys.path.insert(0,sys.argv[4]); sys.path.insert(0,sys.argv[1]); "
        "from local_runtime import _WindowsRuntimeJob; job=_WindowsRuntimeJob(sys.argv[2]); "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "job.add(p.pid); Path(sys.argv[3]).write_text(str(p.pid)); time.sleep(60)"
    )
    owner = subprocess.Popen(
        [
            str(executable),
            "-c",
            code,
            str(helper_folder),
            str(tmp_path),
            str(record),
            str(Path(requests.__file__).parents[1]),
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    probe = helper._WindowsRuntimeJob(tmp_path / "probe")
    handle = None
    try:
        wait_for_file(record)
        handle = probe._kernel.OpenProcess(0x100000 | 0x0001, False, int(record.read_text()))
        assert handle
        owner.kill()
        owner.wait(timeout=5)
        assert probe._kernel.WaitForSingleObject(handle, 5000) == 0
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=5)
        if handle:
            if probe._kernel.WaitForSingleObject(handle, 1000) != 0:
                probe._kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                probe._kernel.TerminateProcess.restype = wintypes.BOOL
                probe._kernel.TerminateProcess(handle, 1)
            probe._kernel.CloseHandle(handle)
        probe.close()
