"""Opt-in GitHub release checks and verified Windows installer downloads."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

REPOSITORY = "hungdinhxuan/Ludivoxa"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
API = f"https://api.github.com/repos/{REPOSITORY}/releases"
CDN_HOSTS = {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}


class UpdateError(Exception):
    pass


class UpdateCancelled(UpdateError):
    pass


def version_tuple(version):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise UpdateError("This release does not have a supported stable version tag.")
    return tuple(int(n) for n in match.groups())


def _installed_version_key(version):
    """RC installations may upgrade to the corresponding stable release."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:rc|-rc\.)(\d+)", version)
    if match:
        major, minor, patch, candidate = (int(n) for n in match.groups())
        return major, minor, patch, 0, candidate
    return (*version_tuple(version), 1, 0)


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@dataclass(frozen=True)
class Asset:
    name: str
    id: int
    size: int


@dataclass(frozen=True)
class Release:
    tag: str
    notes: str
    assets: tuple[Asset, ...]
    app_only: bool

    @property
    def version(self):
        return self.tag.removeprefix("v")

    @property
    def installers(self):
        prefix = f"Ludivoxa-{self.version}-"
        if self.app_only:
            names = [prefix + "App-Update.exe"]
        else:
            base = prefix + "Full-Offline-Setup"
            bins = sorted(
                (a for a in self.assets if re.fullmatch(re.escape(base) + r"-\d+\.bin", a.name)),
                key=lambda a: int(a.name.rsplit("-", 1)[1][:-4]),
            )
            if not bins or [a.name for a in bins] != [f"{base}-{i}.bin" for i in range(1, len(bins) + 1)]:
                raise UpdateError("The release is missing installer data files.")
            names = [base + ".exe", *(a.name for a in bins)]
        lookup = {a.name: a for a in self.assets}
        try:
            return tuple(lookup[name] for name in names)
        except KeyError as exc:
            raise UpdateError("This release has no compatible Windows installer.") from exc

    @property
    def download_size(self):
        return sum(a.size for a in self.installers)


@dataclass(frozen=True)
class DownloadedUpdate:
    release: Release
    directory: Path
    checksums: dict[str, str]

    @property
    def installer(self):
        return self.directory / self.release.installers[0].name

    def verify(self):
        root = self.directory.resolve()
        for asset in self.release.installers:
            path = root / asset.name
            if (
                path.is_symlink()
                or path.parent != root
                or not path.is_file()
                or path.stat().st_size != asset.size
                or file_hash(path) != self.checksums.get(asset.name)
            ):
                raise UpdateError("An update file changed or failed verification. Download the update again.")


class GitHubUpdates:
    def __init__(self, session=None, cancel=None):
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.auth = None
        self.session.headers.pop("Authorization", None)
        self.cancel = cancel or threading.Event()

    def _cancelled(self):
        if self.cancel.is_set():
            raise UpdateCancelled("Update cancelled.")

    def _get(self, url, accept):
        for _ in range(4):
            self._cancelled()
            parsed = urlsplit(url)
            api = parsed.hostname == "api.github.com" and parsed.path.startswith(f"/repos/{REPOSITORY}/releases/")
            if (
                parsed.scheme != "https"
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
                or not (api or parsed.hostname in CDN_HOSTS)
            ):
                raise UpdateError("GitHub returned an unexpected update download address.")
            headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
            try:
                response = self.session.get(url, headers=headers, stream=True, timeout=(5, 20), allow_redirects=False)
            except requests.RequestException as exc:
                raise UpdateError("Cannot reach GitHub. Check your connection and try again.") from exc
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "")
                response.close()
                if not location:
                    raise UpdateError("GitHub returned an invalid download redirect.")
                url = urljoin(url, location)
                continue
            if response.status_code != 200:
                status = response.status_code
                response.close()
                if status == 404:
                    raise UpdateError("Release unavailable. No published release or update file was found.")
                if status in (401, 403):
                    raise UpdateError("GitHub denied this request or its rate limit was reached. Try again later.")
                raise UpdateError(f"GitHub update request failed (HTTP {status}).")
            return response
        raise UpdateError("Too many update download redirects.")

    def _chunks(self, response):
        try:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                self._cancelled()
                if chunk:
                    yield chunk
        except requests.RequestException as exc:
            raise UpdateError("Update download interrupted. Try again when connected.") from exc
        finally:
            response.close()

    def _small(self, url, accept, limit):
        result = bytearray()
        for chunk in self._chunks(self._get(url, accept)):
            result.extend(chunk)
            if len(result) > limit:
                raise UpdateError("The update metadata is too large.")
        return bytes(result)

    def check(self, current):
        try:
            data = json.loads(self._small(API + "/latest", "application/vnd.github+json", 1024 * 1024))
            tag = data["tag_name"]
            newest = version_tuple(tag)
            if data.get("draft") or data.get("prerelease"):
                raise UpdateError("No stable published update is available.")
            if (*newest, 1, 0) <= _installed_version_key(current):
                return None
            assets = tuple(
                Asset(a["name"], a["id"], a["size"]) for a in data.get("assets", []) if a.get("state") == "uploaded"
            )
            if len({a.name for a in assets}) != len(assets) or any(
                not isinstance(a.name, str)
                or Path(a.name).name != a.name
                or "/" in a.name
                or "\\" in a.name
                or not isinstance(a.id, int)
                or a.id <= 0
                or not isinstance(a.size, int)
                or a.size <= 0
                for a in assets
            ):
                raise UpdateError("Invalid release asset metadata.")
            release = Release(
                tag,
                str(data.get("body") or ""),
                assets,
                any(a.name == f"Ludivoxa-{tag.removeprefix('v')}-App-Update.exe" for a in assets),
            )
            _ = release.installers  # Validate before offering the download.
            if not any(a.name == "SHA256SUMS.txt" for a in assets):
                raise UpdateError("This release does not contain update checksums.")
            return release
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise UpdateError("GitHub returned invalid release metadata.") from exc

    def download(self, release, root, progress=lambda *_: None):
        checksum_asset = next(a for a in release.assets if a.name == "SHA256SUMS.txt")
        raw = self._small(API + f"/assets/{checksum_asset.id}", "application/octet-stream", 256 * 1024)
        checksums = {}
        try:
            for line in raw.decode("utf-8-sig").splitlines():
                match = re.fullmatch(r"([a-fA-F0-9]{64})  (.+)", line)
                if not match or match[2] in checksums:
                    raise ValueError()
                checksums[match[2]] = match[1].lower()
        except (ValueError, UnicodeError) as exc:
            raise UpdateError("The release checksum file is invalid.") from exc
        if any(a.name not in checksums for a in release.installers):
            raise UpdateError("The release is missing installer checksums.")
        root = Path(root).resolve() / release.tag
        root.mkdir(parents=True, exist_ok=True)
        total = release.download_size
        if shutil.disk_usage(root).free < total:
            raise UpdateError("Not enough free space to download this update.")
        done = 0
        for asset in release.installers:
            self._cancelled()
            path = root / asset.name
            part = root / (asset.name + ".part")
            if path.is_symlink() or part.is_symlink():
                raise UpdateError("The update cache contains an unexpected file link.")
            if path.is_file() and path.stat().st_size == asset.size and file_hash(path) == checksums[asset.name]:
                done += asset.size
                progress(done, total)
                continue
            digest, count = hashlib.sha256(), 0
            try:
                with part.open("wb") as output:
                    for chunk in self._chunks(self._get(API + f"/assets/{asset.id}", "application/octet-stream")):
                        count += len(chunk)
                        if count > asset.size:
                            raise UpdateError("Update file exceeds the release's declared size.")
                        output.write(chunk)
                        digest.update(chunk)
                        progress(done + count, total)
                if count != asset.size or digest.hexdigest() != checksums[asset.name]:
                    raise UpdateError("Update checksum mismatch. No installer was launched.")
                part.replace(path)
            finally:
                part.unlink(missing_ok=True)
            done += count
        downloaded = DownloadedUpdate(release, root, checksums)
        downloaded.verify()
        return downloaded

    def close(self):
        self.session.close()


def launch_update(update, executable=None, cancel=None):
    """Only the user's Install and restart action calls this function."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise UpdateError(
            "Automatic installation is available in the Windows EXE. Use the release installer for source runs."
        )
    executable = Path(executable or sys.executable).resolve()
    if executable.name.lower() != "interpreter.exe":
        raise UpdateError("Cannot identify this application installation.")
    update.verify()  # Recheck immediately before executing a cached installer.
    if cancel is not None and cancel.is_set():
        raise UpdateCancelled("Update cancelled.")
    return subprocess.Popen(
        [
            str(update.installer),
            "/SILENT",
            "/NORESTART",
            f"/DIR={executable.parent}",
            "/LUDIVOXARESTART=1",
            f"/LOG={update.directory / 'install.log'}",
        ],
        cwd=update.directory,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
    )
