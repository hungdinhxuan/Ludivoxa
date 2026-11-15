"""Release boundary checks and deterministic checksums without large fixtures."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def package(tmp_path):
    script = Path(__file__).parents[1] / "packaging/package_release.py"
    spec = importlib.util.spec_from_file_location("release_packaging", script)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    source = tmp_path / "installer"
    source.mkdir()
    base = f"Ludivoxa-{helper.VERSION}-Full-Offline-Setup"
    (source / (base + ".exe")).write_bytes(b"installer")
    (source / (base + "-1.bin")).write_bytes(b"first data slice")
    return helper, source, tmp_path / "release", base


def test_release_manifest_tracks_version_source_and_all_checksums(package):
    helper, source, output, base = package
    commit = "a" * 40
    assets = helper.prepare(source, output, source_commit=commit)
    data = json.loads((output / "release-manifest.json").read_text(encoding="utf-8"))
    assert data["version"] == helper.VERSION and data["tag"] == "v" + helper.VERSION
    assert data["source_commit"] == commit and data["installer"] == base + ".exe"
    for line in (output / "SHA256SUMS.txt").read_text().splitlines():
        expected, name = line.split("  ", 1)
        assert helper.sha256(output / name) == expected
    assert len(assets) == 5
    with pytest.raises(ValueError, match="empty"):
        helper.prepare(source, output)


def test_release_refuses_missing_slice_instead_of_publishing_incomplete_setup(package):
    helper, source, output, base = package
    (source / (base + "-3.bin")).write_bytes(b"a gap")
    with pytest.raises(ValueError, match="contiguous"):
        helper.prepare(source, output)
    assert not output.exists()


def test_release_checks_per_file_upload_limit_before_copying(package, monkeypatch):
    helper, source, output, _base = package
    monkeypatch.setattr(helper, "GITHUB_ASSET_LIMIT", 8)
    with pytest.raises(ValueError, match="2 GiB"):
        helper.prepare(source, output)
    assert not output.exists()


def test_release_refuses_overlapping_directories_and_invalid_commit(package):
    helper, source, output, _base = package
    with pytest.raises(ValueError, match="separate"):
        helper.prepare(source, source / "release")
    with pytest.raises(ValueError, match="full Git commit"):
        helper.prepare(source, output, source_commit="main")


def test_release_can_include_checksummed_app_only_installer(package, tmp_path):
    helper, source, output, _base = package
    update = tmp_path / f"Ludivoxa-{helper.VERSION}-App-Update.exe"
    update.write_bytes(b"app update")
    helper.prepare(source, output, app_update=update)
    checksums = (output / "SHA256SUMS.txt").read_text()
    assert f"{helper.sha256(update)}  {update.name}\n" in checksums
    manifest = json.loads((output / "release-manifest.json").read_text())
    assert any(asset["name"] == update.name for asset in manifest["assets"])
