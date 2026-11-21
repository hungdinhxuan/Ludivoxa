"""Opt-in smoke test of the built executable without a Python environment on PATH."""

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest
import requests
import yaml

EXE = os.environ.get("LUDIVOXA_TEST_EXE")
pytestmark = pytest.mark.skipif(
    not EXE or os.name != "nt", reason="Set LUDIVOXA_TEST_EXE for a built Windows bundle"
)


def test_frozen_ocr_local_translation_and_speech(tmp_path):
    app_directory = Path(EXE).resolve().parent
    settings = yaml.safe_load((app_directory / "config.yml").read_text(encoding="utf-8"))
    private = (app_directory / "runtime/ollama.exe").is_file() and settings.get("llm", {}).get("base_url", "").rstrip(
        "/"
    ) == "http://127.0.0.1:11435"
    session = requests.Session()
    session.trust_env = False

    def private_running():
        try:
            session.get("http://127.0.0.1:11435/api/tags", timeout=0.5, allow_redirects=False)
            return True
        except requests.RequestException:
            return False

    was_running = private_running() if private else False
    report_path = tmp_path / "frozen-report.json"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PATH"] = str(Path(os.environ["SYSTEMROOT"]) / "System32")
    result = subprocess.run(
        [str(Path(EXE).resolve()), "--self-test", str(report_path)],
        cwd=tmp_path,
        env=environment,
        timeout=180,
        capture_output=True,
    )
    assert report_path.is_file(), f"Executable exited {result.returncode} without a report"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert result.returncode == 0 and report["ok"], report
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert report["version"] == project["project"]["version"]
    assert report["frozen"] is True
    assert report["ocr_isolated"] is True
    assert report["ocr_parent_max_pause_ms"] < 200
    assert report["ocr"] == ["Welcome brave hero", "こんにちは世界"]
    assert len(report["translations"]) == 2
    cjk = report["cjk_translations"]
    assert [item["source"] for item in cjk] == ["欢迎来到村庄", "歡迎來到村莊", "안녕하세요 용사님"]
    assert [item["language"] for item in cjk] == ["Chinese", "Chinese", "Korean"]
    assert all(item["translation"] and item["translation"] != item["source"] for item in cjk)
    assert [item["target_language"] for item in report["target_translations"]] == [
        "English",
        "Simplified Chinese",
        "Traditional Chinese",
        "Japanese",
        "Korean",
    ]
    assert report["audio_seconds"] > 1
    assert report["voice_count"] >= 67
    assert report["cloned_audio_seconds"] > 1
    assert report["stable_pipeline_utterances"] == 1
    assert report["stable_ocr_jitter_utterances"] == 1
    assert report["stable_shaded_dialogue_utterances"] == 1
    assert report["selected_box_pipeline_utterances"] == 1
    assert report["selected_box_input_shape"] == [100, 900, 4]
    assert report["selected_box_origin"] == [100, 300]
    characters = report["character_voice_routing"]
    assert characters["utterances"] == 2 and characters["speaker_ids"] == [1, 7]
    assert [name.casefold() for name in characters["detected_names"]] == ["alice", "bob"]
    assert characters["model_reused"] and characters["different_waveforms"]
    assert report["playback_under_ui_stall"]["elapsed_seconds"] < report["audio_seconds"] + 0.8
    assert report["streaming_playback_under_ui_stall"]["processed_seconds"] >= 4
    assert report["streaming_playback_under_ui_stall"]["underruns"] == 0
    assert all(address[0] in ("127.0.0.1", "::1") for address in report["network_destinations"])
    if private and not was_running:
        import time

        deadline = time.monotonic() + 5
        while private_running() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not private_running(), "The EXE left its private Ollama running after exit"
    session.close()
