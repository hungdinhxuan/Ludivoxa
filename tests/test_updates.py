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


@pytest.fixture
def delta_package(tmp_path):
    install = tmp_path / "installed app"
    (install / "_internal").mkdir(parents=True)
    exe = install / "Interpreter.exe"
    exe.write_bytes(b"base app")
    library = install / "_internal/Bình (sample).dll"
    library.write_bytes(b"reused dependency")
    (install / "config.yml").write_text("window_title: Game\n")
    manifest = (
        f"{u.file_hash(exe)}  Interpreter.exe\n"
        f"{u.file_hash(library)}  _internal/{library.name}\n"
        "-  _internal/new directory/new file.dat\n"
    ).encode()
    data = metadata()
    standard = data["assets"][0]
    standard_payload, patch = b"full installer" * 50, b"small installer"
    standard["size"] = len(standard_payload)
    delta_name = "Ludivoxa-1.0.1-Delta-from-1.0.0"
    files = {standard["name"]: standard_payload, delta_name + ".exe": patch, delta_name + ".base.sha256": manifest}
    data["assets"].extend(
        {"name": name, "id": i, "size": len(files[name]), "state": "uploaded"}
        for i, name in enumerate(list(files)[1:], start=3)
    )
    checksums = "".join(f"{hashlib.sha256(content).hexdigest()}  {name}\n" for name, content in files.items()).encode()
    return data, exe, library, checksums, manifest, patch, standard_payload


def test_delta_is_selected_only_for_exact_base_and_smaller_complete_pair(delta_package):
    data, exe, *_ = delta_package
    assert not client(data).check("1.0.0").delta  # Source run.
    assert not client(data).check("0.9.9", executable=exe).delta
    assert not client(data).check("1.0.0rc1", executable=exe).delta
    delta = client(data).check("1.0.0", executable=exe).delta
    assert delta.directory == exe.parent and delta.installer.name.endswith("Delta-from-1.0.0.exe")
    data["assets"][-2]["size"] = data["assets"][0]["size"]
    assert not client(data).check("1.0.0", executable=exe).delta
    data["assets"].pop()
    assert not client(data).check("1.0.0", executable=exe).delta


def test_delta_preflights_base_downloads_only_small_assets_and_launches_verified_patch(
    delta_package, tmp_path, monkeypatch
):
    data, exe, _, checksums, manifest, patch, _ = delta_package
    release = client(data).check("1.0.0", executable=exe)
    session = Session([Response(checksums), Response(manifest), Response(patch)])
    progress = []
    update = u.GitHubUpdates(session=session).download(release, tmp_path / "cache", lambda *args: progress.append(args))
    total = len(manifest) + len(patch)
    assert progress == [(len(manifest), total), (total, total)]
    assert [url.rsplit("/", 1)[-1] for url, _ in session.calls] == ["2", "4", "3"]
    assert update.installer.read_bytes() == patch
    monkeypatch.setattr(u.sys, "frozen", True, raising=False)
    monkeypatch.setattr(u, "os", SimpleNamespace(name="nt", environ={}))
    popen = MagicMock()
    monkeypatch.setattr(u.subprocess, "Popen", popen)
    u.launch_update(update, exe)
    assert popen.call_args.args[0][0] == str(update.installer)


@pytest.mark.parametrize("damage", ["missing_library", "changed_library", "occupied_new_path", "manifest", "patch"])
def test_invalid_delta_offers_full_update_without_automatically_downloading_it(delta_package, tmp_path, damage):
    data, exe, library, checksums, manifest, patch, standard = delta_package
    release = client(data).check("1.0.0", executable=exe)
    if damage == "missing_library":
        library.unlink()
    elif damage == "changed_library":
        library.write_bytes(b"modified")
    elif damage == "occupied_new_path":
        other = exe.parent / "_internal/new directory/new file.dat"
        other.parent.mkdir()
        other.write_bytes(b"private")
    elif damage == "manifest":
        manifest = b"x" + manifest[1:]
    elif damage == "patch":
        patch = b"x" + patch[1:]
    session = Session([Response(checksums), Response(manifest), Response(patch)])
    cache = tmp_path / "cache"
    with pytest.raises(u.DeltaUnavailable):
        u.GitHubUpdates(session=session).download(release, cache)
    assert len(session.calls) == (3 if damage == "patch" else 2)
    assert not list(cache.rglob("*.exe")) and not list(cache.rglob("*.part"))
    downloaded = u.GitHubUpdates(session=Session([Response(checksums), Response(standard)])).download(
        release.standard(), cache
    )
    assert downloaded.installer.name.endswith("App-Update.exe") and downloaded.installer.read_bytes() == standard


def test_delta_base_rejects_traversal_and_honors_cancellation(delta_package):
    _, exe, _, _, manifest, *_ = delta_package
    with pytest.raises(ValueError, match="manifest"):
        u.verify_delta_base(manifest + b"-  _internal/../config.yml\n", exe.parent)

    def cancel():
        raise u.UpdateCancelled

    with pytest.raises(u.UpdateCancelled):
        u.verify_delta_base(manifest, exe.parent, cancel)
