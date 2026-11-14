"""Portable offline payloads and isolated launcher behavior, without real downloads."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
import yaml


def load_helper(name):
    path = Path(__file__).parents[1] / "packaging" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_payload_checksums_and_portable_defaults(tmp_path, monkeypatch):
    helper = load_helper("prepare_offline_bundle")
    app, runtime, store, output = (tmp_path / part for part in ("app", "runtime", "store", "output"))
    app.mkdir()
    runtime.mkdir()
    (app / "Interpreter.exe").write_bytes(b"app")
    (runtime / "ollama.exe").write_bytes(b"runtime")
    (runtime / "ollama.zip").write_bytes(b"redundant distribution archive")
    (runtime / "ollama.pid.json").write_text('{"pid": 12345}')
    (app / "runtime").mkdir()
    (app / "runtime/old-runtime.txt").write_text("old portable runtime must not be reused")
    (app / "llm-models").mkdir()
    (app / "llm-models/unrelated-model.txt").write_text("not the selected model")
    (runtime / "lib").mkdir()
    (runtime / "lib/inference.dll").write_bytes(b"required runtime")
    (app / "_internal").mkdir()
    (app / "_internal/config.yml").write_text("required internal configuration")
    config = {
        "llm": {"model": "old", "api_key": "private-key"},
        "paddle": {},
        "tts_reference_audio": "C:/private/recording.wav",
        "tts_model_path": "C:/private/voice",
        "window_title": "Private game window",
        "ocr_regions": {"Private game window": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}},
    }
    (app / "config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    blob = b"offline test weights"
    sha = hashlib.sha256(blob).hexdigest()
    monkeypatch.setattr(helper, "WEIGHTS", sha)
    manifest = store / "manifests-v2/ollama.com/library/qwen3-hauhau-aggressive/4b-q4_k_m"
    manifest.parent.mkdir(parents=True)
    entry = {"digest": f"sha256:{sha}", "size": len(blob)}
    manifest.write_text(json.dumps({"config": entry, "layers": []}), encoding="utf-8")
    (store / "blobs").mkdir()
    (store / "blobs" / f"sha256-{sha}").write_bytes(blob)
    helper.prepare(app, runtime, store, output)
    settings = yaml.safe_load((output / "config.default.yml").read_text(encoding="utf-8"))
    assert settings["llm"]["api_key"] == "" and settings["tts_reference_audio"] == ""
    assert settings["llm"]["base_url"] == "http://127.0.0.1:11435"
    assert settings["window_title"] == "" and settings["ocr_regions"] == {}
    assert "Private game window" not in (output / "config.default.yml").read_text(encoding="utf-8")
    assert not (output / "config.yml").exists()
    assert (output / "_internal/config.yml").read_text() == "required internal configuration"
    assert not (output / "runtime/ollama.zip").exists()
    assert not (output / "runtime/ollama.pid.json").exists()
    assert not (output / "runtime/old-runtime.txt").exists()
    assert not (output / "llm-models/unrelated-model.txt").exists()
    assert (output / "runtime/lib/inference.dll").read_bytes() == b"required runtime"
    assert (runtime / "ollama.zip").is_file()  # Source provisioning is not modified.
    with pytest.raises(ValueError, match="empty"):
        helper.prepare(app, runtime, store, output)
    (store / "blobs" / f"sha256-{sha}").write_bytes(b"wrong".ljust(len(blob), b"!"))
    with pytest.raises(ValueError, match="checksum"):
        helper.prepare(app, runtime, store, tmp_path / "different-output")


def test_bundled_launcher_leaves_external_endpoints_alone(tmp_path, monkeypatch):
    helper = load_helper("local_runtime")
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/ollama.exe").touch()
    (tmp_path / "config.yml").write_text("llm:\n  provider: ollama\n  base_url: http://192.168.1.5:11434\n")
    spawn = MagicMock()
    monkeypatch.setattr(helper.subprocess, "Popen", spawn)
    helper.ensure_bundled_ollama(tmp_path)
    spawn.assert_not_called()


def test_bundled_launcher_uses_own_store_and_offline_environment(tmp_path, monkeypatch):
    helper = load_helper("local_runtime")
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/ollama.exe").touch()
    (tmp_path / "config.yml").write_text("llm:\n  provider: ollama\n  base_url: http://127.0.0.1:11435\n")
    session = MagicMock()
    session.get.side_effect = [requests.ConnectionError(), MagicMock(status_code=200)]
    monkeypatch.setattr(helper.requests, "Session", lambda: session)
    monkeypatch.setattr(helper.Path, "home", lambda: tmp_path / "home")
    spawn = MagicMock(return_value=MagicMock(pid=12345))
    monkeypatch.setattr(helper.subprocess, "Popen", spawn)
    job = MagicMock()
    monkeypatch.setattr(helper, "_WindowsRuntimeJob", lambda directory: job)
    previous = dict(os.environ)
    assert helper.ensure_bundled_ollama(tmp_path) is job
    environment = spawn.call_args.kwargs["env"]
    assert environment["OLLAMA_NO_CLOUD"] == "1"
    assert environment["OLLAMA_MODELS"] == str(tmp_path / "llm-models")
    assert environment["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:9"
    assert dict(os.environ) == previous
    assert json.loads((tmp_path / "runtime/ollama.pid.json").read_text())["pid"] == 12345
    job.add.assert_called_once_with(12345)
    job.close.assert_not_called()
