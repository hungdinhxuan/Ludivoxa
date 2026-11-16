"""Public release selection, anonymous downloads, checksums and safe installation."""

import hashlib
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from interpreter import updates as u


class Response:
    def __init__(self, body=b"", status=200, location=None, chunks=None):
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self.body = body
        self.chunks = chunks
        self.closed = False

    def iter_content(self, **_kwargs):
        yield from self.chunks if self.chunks is not None else [self.body]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.headers = {}
        self.auth = None
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


def metadata(app_only=True, tag="v1.0.1"):
    version = tag.removeprefix("v")
    names = (
        [f"Ludivoxa-{version}-App-Update.exe"]
        if app_only
        else [
            f"Ludivoxa-{version}-Full-Offline-Setup.exe",
            f"Ludivoxa-{version}-Full-Offline-Setup-1.bin",
            f"Ludivoxa-{version}-Full-Offline-Setup-2.bin",
        ]
    )
    names.append("SHA256SUMS.txt")
    return {
        "tag_name": tag,
        "body": "Release notes",
        "draft": False,
        "prerelease": False,
        "assets": [{"name": n, "id": i + 1, "size": 4, "state": "uploaded"} for i, n in enumerate(names)],
    }


def client(data):
    return u.GitHubUpdates(session=Session([Response(json.dumps(data).encode())]))


def test_numeric_versions_and_preferred_app_only_package():
    data = metadata()
    data["assets"].append({"name": "Ludivoxa-1.0.1-Full-Offline-Setup.exe", "id": 99, "size": 999, "state": "uploaded"})
    release = client(data).check("1.0.0")
    assert release.app_only and release.download_size == 4
    assert len(release.installers) == 1 and release.installers[0].name.endswith("App-Update.exe")
    assert client(metadata(tag="v1.2.0")).check("1.10.0") is None
    assert client(metadata(tag="v1.0.0")).check("1.0.0") is None


@pytest.mark.parametrize("current", ["1.1.2rc1", "v1.1.2-rc.1"])
def test_rc_installation_can_upgrade_to_same_base_stable_but_not_older(current):
    assert client(metadata(tag="v1.1.1")).check(current) is None
    assert client(metadata(tag="v1.1.2")).check(current).tag == "v1.1.2"
    assert client(metadata(tag="v1.1.3")).check(current).tag == "v1.1.3"


@pytest.mark.parametrize("tag", ["../bad", "main", "v1.1.0-beta", "v1.0", "v1.0.0/evil"])
def test_unsupported_tags_are_not_download_paths(tag):
    with pytest.raises(u.UpdateError):
        client(metadata(tag=tag)).check("1.0.0")


def test_full_installer_requires_all_contiguous_data_slices():
    data = metadata(False)
    release = client(data).check("1.0.0")
    assert not release.app_only and len(release.installers) == 3
    data["assets"][1]["name"] = "Ludivoxa-1.0.1-Full-Offline-Setup-3.bin"
    with pytest.raises(u.UpdateError, match="missing installer data"):
        client(data).check("1.0.0")


@pytest.mark.parametrize("mutation", ["traversal", "duplicates", "checksum", "draft", "prerelease"])
def test_incomplete_or_unpublished_release_is_not_offered(mutation):
    data = metadata()
    if mutation == "traversal":
        data["assets"][0]["name"] = "../payload.exe"
    elif mutation == "duplicates":
        data["assets"].append(data["assets"][0])
    elif mutation == "checksum":
        data["assets"].pop()
    else:
        data[mutation] = True
    with pytest.raises(u.UpdateError):
        client(data).check("1.0.0")


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_auth_or_connection_error_is_never_reported_as_up_to_date(status):
    c = u.GitHubUpdates(session=Session([Response(status=status)]))
    with pytest.raises(u.UpdateError):
        c.check("1.0.0")
    c = u.GitHubUpdates(session=Session([requests.ConnectionError("offline")]))
    with pytest.raises(u.UpdateError, match="Cannot reach GitHub"):
        c.check("1.0.0")


def test_public_download_ignores_credentials_and_uses_anonymous_requests(monkeypatch):
    monkeypatch.setenv("LUDIVOXA_GITHUB_TOKEN", "unused-environment-token")
    session = Session(
        [Response(status=302, location="https://release-assets.githubusercontent.com/a?sig=opaque"), Response(b"data")]
    )
    session.headers["Authorization"] = "Bearer global-secret"
    session.auth = ("user", "unused-password")
    c = u.GitHubUpdates(session=session)
    assert c._small(u.API + "/assets/1", "application/octet-stream", 10) == b"data"
    assert all("Authorization" not in kwargs["headers"] for _, kwargs in session.calls)
    assert "Authorization" not in session.headers
    assert session.auth is None
    assert not session.trust_env and all(not kwargs["allow_redirects"] for _, kwargs in session.calls)


@pytest.mark.parametrize(
    "redirect",
    [
        "http://release-assets.githubusercontent.com/a",
        "https://evil.example/a",
        "https://api.github.com/repos/evil/repo/releases/assets/1",
        "https://user:pass@release-assets.githubusercontent.com/a",
    ],
)
def test_download_redirects_are_restricted_to_expected_https_hosts(redirect):
    session = Session([Response(status=302, location=redirect)])
    c = u.GitHubUpdates(session=session)
    with pytest.raises(u.UpdateError):
        c._small(u.API + "/assets/1", "application/octet-stream", 10)
    assert len(session.calls) == 1


@pytest.fixture
def package():
    release = client(metadata()).check("1.0.0")
    data = b"test"
    checksum = (hashlib.sha256(data).hexdigest() + "  " + release.installers[0].name + "\n").encode()
    return release, data, checksum


def test_verified_download_cached_reuse_and_install_rechecks_hash(tmp_path, package, monkeypatch):
    release, data, checksum = package
    c = u.GitHubUpdates(session=Session([Response(checksum), Response(data)]))
    progress = []
    downloaded = c.download(release, tmp_path, lambda done, total: progress.append((done, total)))
    assert downloaded.installer.read_bytes() == data and progress[-1] == (4, 4)
    reuse = u.GitHubUpdates(session=Session([Response(checksum)]))
    reuse.download(release, tmp_path)
    assert len(reuse.session.calls) == 1  # No second installer download.
    downloaded.installer.write_bytes(b"evil")
    monkeypatch.setattr(u.sys, "frozen", True, raising=False)
    monkeypatch.setattr(u, "os", SimpleNamespace(name="nt", environ={}))
    popen = MagicMock()
    monkeypatch.setattr(u.subprocess, "Popen", popen)
    with pytest.raises(u.UpdateError, match="verification"):
        u.launch_update(downloaded, tmp_path / "app/Interpreter.exe")
    popen.assert_not_called()


@pytest.mark.parametrize("data", [b"bad!", b"too long", b"xx"])
def test_bad_downloads_leave_no_installable_partial_file(tmp_path, package, data):
    release, _, checksum = package
    c = u.GitHubUpdates(session=Session([Response(checksum), Response(data)]))
    with pytest.raises(u.UpdateError):
        c.download(release, tmp_path)
    assert not list(tmp_path.rglob("*.exe")) and not list(tmp_path.rglob("*.part"))


def test_cancel_and_network_failure_remove_partial_download(tmp_path, package):
    release, _, checksum = package
    event = threading.Event()

    def chunks():
        yield b"te"
        event.set()
        yield b"st"

    c = u.GitHubUpdates(session=Session([Response(checksum), Response(chunks=chunks())]), cancel=event)
    with pytest.raises(u.UpdateCancelled):
        c.download(release, tmp_path)
    assert not list(tmp_path.rglob("*.part"))


def test_launcher_quotes_paths_as_arguments_and_preserves_current_install_directory(tmp_path, package, monkeypatch):
    release, data, checksum = package
    update = u.GitHubUpdates(session=Session([Response(checksum), Response(data)])).download(release, tmp_path)
    monkeypatch.setattr(u.sys, "frozen", True, raising=False)
    monkeypatch.setattr(u, "os", SimpleNamespace(name="nt", environ={"EXISTING": "keep"}))
    popen = MagicMock()
    monkeypatch.setattr(u.subprocess, "Popen", popen)
    exe = tmp_path / "app with spaces & symbols/Interpreter.exe"
    u.launch_update(update, exe)
    args = popen.call_args.args[0]
    assert isinstance(args, list) and f"/DIR={exe.parent}" in args
    assert "/LUDIVOXARESTART=1" in args and "/NORESTART" in args
    assert popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert popen.call_args.kwargs.get("shell", False) is False


def test_old_config_stays_offline_and_opt_in_roundtrips(tmp_path):
    from interpreter.config import Config

    path = tmp_path / "config.yml"
    path.write_text("window_title: Game\n")
    c = Config.load(str(path))
    assert not c.update_check_on_startup
    c.update_check_on_startup = True
    c.save()
    assert Config.load(str(path)).update_check_on_startup
    assert "token" not in path.read_text()


def test_public_check_never_invokes_git_credential_helpers(monkeypatch):
    git = MagicMock(side_effect=AssertionError("Updater must not access Git credentials"))
    monkeypatch.setattr(u.subprocess, "run", git)
    assert client(metadata(tag="v1.0.0")).check("1.0.0") is None
    git.assert_not_called()
