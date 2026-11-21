"""Start the installer-owned offline Ollama service without touching other servers."""

import hashlib
import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import requests
import yaml

LOCAL_URL = "http://127.0.0.1:11435"


class _WindowsRuntimeJob:
    """Shared per-install lease: Windows stops the process tree on last close.

    Job handles are non-inheritable. Closing/crashing one GUI leaves the server
    alive if another GUI from the same installation still holds its own handle.
    https://learn.microsoft.com/windows/win32/procthread/job-objects
    """

    def __init__(self, directory):
        import ctypes
        from ctypes import wintypes

        self._job = self._mutex = None
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
                wintypes.BOOL,
            ),
            "CreateMutexW": ([ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "ReleaseMutex": ([wintypes.HANDLE], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "IsProcessInJob": ([wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
            "QueryFullProcessImageNameW": (
                [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)],
                wintypes.BOOL,
            ),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self._kernel, name)
            function.argtypes, function.restype = arguments, result

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong),
                ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD),
                ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t),
                ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io_counters", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        identity = hashlib.sha256(str(Path(directory).resolve()).casefold().encode("utf-8")).hexdigest()[:24]
        name = "Local\\LudivoxaOllama-" + identity
        try:
            self._job = self._kernel.CreateJobObjectW(None, name)
            if not self._job:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self._kernel.SetInformationJobObject(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            self._mutex = self._kernel.CreateMutexW(None, False, name + "-startup")
            if not self._mutex:
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

    @contextmanager
    def startup(self):
        # Serialize ready-check/spawn/PID-record creation across GUI instances.
        status = self._kernel.WaitForSingleObject(self._mutex, 35000)
        if status not in (0, 0x80):  # WAIT_OBJECT_0, WAIT_ABANDONED
            raise TimeoutError("Timed out waiting for the private Ollama startup lock.")
        try:
            yield
        finally:
            self._kernel.ReleaseMutex(self._mutex)

    def add(self, pid):
        import ctypes

        handle = self._kernel.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._kernel.AssignProcessToJobObject(self._job, handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel.CloseHandle(handle)

    def contains(self, pid, executable):
        """Validate both kernel membership and image path; never trust a PID alone."""
        import ctypes
        from ctypes import wintypes

        handle = self._kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            member = wintypes.BOOL()
            image = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(image))
            return bool(
                self._kernel.IsProcessInJob(handle, self._job, ctypes.byref(member))
                and member.value
                and self._kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length))
                and os.path.normcase(image.value) == os.path.normcase(str(executable))
            )
        finally:
            self._kernel.CloseHandle(handle)

    def close(self):
        for name in ("_job", "_mutex"):
            handle = getattr(self, name, None)
            if handle:
                setattr(self, name, None)
                self._kernel.CloseHandle(handle)


def ensure_bundled_ollama(directory):
    directory = Path(directory).resolve()
    executable = directory / "runtime" / "ollama.exe"
    if not executable.is_file():
        return  # The portable app can still use an external local/LAN service.
    settings = yaml.safe_load((directory / "config.yml").read_text(encoding="utf-8")) or {}
    llm = settings.get("llm") or {}
    if llm.get("provider", "ollama") != "ollama" or llm.get("base_url", "").rstrip("/") != LOCAL_URL:
        return
    session = requests.Session()
    session.trust_env = False

    def ready():
        try:
            session.get(f"{LOCAL_URL}/api/tags", timeout=0.5, allow_redirects=False)
            # Any HTTP listener owns this port. Do not start a second server
            # over a user's authenticated/custom endpoint; the GUI reports it.
            return True
        except requests.RequestException:
            pass
        return False

    environment = os.environ.copy()
    environment.update(
        OLLAMA_HOST="127.0.0.1:11435",
        OLLAMA_MODELS=str(directory / "llm-models"),
        OLLAMA_NO_CLOUD="1",
        # Bundled inference is offline. Optional Go HTTP background checks
        # cannot reach external hosts; the app's own client ignores proxies.
        HTTP_PROXY="http://127.0.0.1:9",
        HTTPS_PROXY="http://127.0.0.1:9",
        ALL_PROXY="http://127.0.0.1:9",
        NO_PROXY="127.0.0.1,localhost,::1",
    )
    logs = Path.home() / ".ludivoxa" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    job, retain = None, False
    try:
        job = _WindowsRuntimeJob(directory)
        with job.startup():
            if ready():
                try:
                    record = json.loads((executable.parent / "ollama.pid.json").read_text(encoding="utf-8"))
                    pid = int(record["pid"])
                    if pid > 0 and job.contains(pid, executable):
                        retain = True
                        return job  # Share the lease until the last GUI closes.
                except (OSError, ValueError, KeyError, TypeError):
                    pass
                return None  # A listener we do not own must remain untouched.
            with (logs / "bundled-ollama.log").open("a", encoding="utf-8") as output:
                process = subprocess.Popen(
                    [str(executable), "serve"],
                    cwd=str(executable.parent),
                    env=environment,
                    stdout=output,
                    stderr=output,
                    close_fds=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            try:
                job.add(process.pid)
            except Exception:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                raise
            record = {"pid": process.pid, "executable": str(executable), "started_at": time.time()}
            (executable.parent / "ollama.pid.json").write_text(json.dumps(record), encoding="utf-8")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if ready():
                    retain = True
                    return job
                if process.poll() is not None:
                    break
                time.sleep(0.2)
            print(f"Bundled Ollama is not ready; see {logs / 'bundled-ollama.log'}")
    except Exception as error:
        # Do not leave a server running without shutdown ownership or silently
        # fall back to another service. The existing GUI reports endpoint failure.
        print(f"Cannot manage private Ollama: {error}")
    finally:
        session.close()
        if job and not retain:
            job.close()
