# Development and Windows builds

## Environment

Use Python 3.11 or 3.12. Python 3.12 is the tested Windows build interpreter.

```powershell
git clone https://github.com/hungdinhxuan/Ludivoxa.git
cd Ludivoxa
uv sync --locked --extra paddle --extra tts --extra clone --group build
Copy-Item config.example.yml config.yml
```

The distribution remains `interpreter-v2` for compatibility; `uv run ludivoxa`
and `uv run interpreter-v2` both start it. `config.yml`, virtual environments,
downloaded models, binary output and local audit reports are ignored by Git.
The root legacy install/uninstall scripts retain upstream Interpreter/PyPI
behavior and are not the installation route for this fork.

## Provision models explicitly

These operations need internet once. They are not run automatically by the app
or build scripts. Alternatively copy a previously provisioned model directory.

```powershell
uv run python packaging/provision_ocr_onnx.py models
uv run python packaging/provision_clone.py models/VieNeu-TTS-v3-Nano
```

The ONNX provisioning script pins official model revisions and checks hashes.
Its `_onnx` directories work directly with `config.example.yml`. For the Windows
builder's canonical names, populate these directories with matching local
`inference.yml` and `inference.onnx` files (or the original supported Paddle
inference weights):

```powershell
Copy-Item -Recurse models/PP-OCRv5_mobile_det_onnx models/PP-OCRv5_mobile_det
Copy-Item -Recurse models/PP-OCRv5_mobile_rec_onnx models/PP-OCRv5_mobile_rec
Copy-Item -Recurse models/en_PP-OCRv5_mobile_rec_onnx models/en_PP-OCRv5_mobile_rec
```

Only run those copies into absent destinations. If canonical folders already
exist, retain them: the builder merges an ONNX export only after its metadata
matches the existing model. CPU OCR uses ONNX when both selected models have
exports; no download or automatic runtime failover is performed on an error.

Chinese (Simplified and Traditional) reuses `PP-OCRv5_mobile_rec`. Korean uses
the dedicated `korean_PP-OCRv5_mobile_rec` model. For source runs, keep their
model folders beside the detection model, including the `_onnx` folders from
the provisioning script. The Windows builder places the Korean recognizer
under `_internal/ocr-models` so it ships with both full installs and app
updates while the user's existing model paths stay valid.

The full release also uses these Piper files under `models/voices`, each with
its matching `.onnx.json` and model card:

| Model | Source |
| --- | --- |
| `vi_VN-vais1000-medium.onnx` | `rhasspy/piper-voices`, `vi/vi_VN/vais1000/medium` |
| `vi_VN-25hours_single-low.onnx` | `rhasspy/piper-voices`, `vi/vi_VN/25hours_single/low` |
| `vi_VN-vivos-x_low.onnx` | `rhasspy/piper-voices`, `vi/vi_VN/vivos/x_low` |

The old `voice` directory name is also accepted. Nano reference files are
optional and must not be bundled from a developer's personal recordings.

For a source run, start your own Ollama at port 11434 and provision the model
with `packaging/HauhauCS.Modelfile`. The source profile does not manage external
servers. The full Windows bundle instead uses private port 11435.

## Build the executable

```powershell
powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1 -ModelDirectory models
```

Output is `dist/Interpreter/Interpreter.exe`. Keep the complete directory.
Its product/version information and GUI identify Ludivoxa; compatibility
filenames remain unchanged. This stage includes Python and local OCR/TTS
models, but does not automatically provision Ollama or model weights.

## Full offline bundle and installer

Provision a portable Ollama **0.40.16** runtime and create the Hauhau alias in
the same model store used below. The model store must contain the pinned
weights expected by `prepare_offline_bundle.py`; arbitrary similarly named
models are rejected. Set `OLLAMA_MODELS` on the provisioning server before
running `ollama serve`, and use that server for:

```powershell
ollama create qwen3-hauhau-aggressive:4b-q4_k_m -f packaging/HauhauCS.Modelfile
```

Use a separate provisioning port if a private runtime already occupies 11435.
Copy the runtime's extracted files, not just `ollama.exe`; native GPU libraries
are required. Download archives and stale PID files are excluded from packaging.

```powershell
powershell -ExecutionPolicy Bypass -File packaging/build_installer.ps1 `
  -Compiler C:/path/to/InnoSetup/ISCC.exe `
  -OllamaDirectory C:/path/to/ollama-runtime `
  -ModelStore C:/path/to/ollama-models `
  -OutputDirectory dist/installer-v1.0.0
```

Inno Setup 7 and Windows x64 are used for the current installer. The helper
validates model digests, stages a new directory, and supplies fresh public
defaults from `packaging/config.release.yml`. It does not publish local API
keys, prompts, game/device profiles or reference paths. Existing installed
configuration is retained by the installer.

For a full portable directory, call `prepare_offline_bundle.py` with the same
verified inputs and a separate empty output, then copy `config.default.yml` to
`config.yml` there. Keep its runtime/model store with the EXE. A models-only
rebuild must be assembled again before using private port 11435.

## Test

```powershell
uv run --locked pytest -q
```

Real models, native Windows fonts and an EXE are opt-in fixtures. To reproduce
the full Windows validation, set these to your provisioned paths:

```powershell
$env:INTERPRETER_TEST_MODEL_ROOT = 'models'
$env:INTERPRETER_TEST_VOICE = 'models/voices/vi_VN-vais1000-medium.onnx'
$env:INTERPRETER_TEST_CLONE_MODEL = 'models/VieNeu-TTS-v3-Nano'
$env:INTERPRETER_TEST_EXE = 'dist/Interpreter/Interpreter.exe'
uv run --locked pytest -q
```

Paddle inference fixtures also expect canonical Paddle-format weights for the
legacy runtime checks; those cases cannot run with only ONNX exports. The EXE
diagnostic requires a complete compatible app config and reachable local
Ollama. Its connection guard rejects non-loopback inference. Native lifecycle
tests verify shared runtime leases and owner crashes without remote servers.

No model weights or binaries belong in Git. See [releasing](RELEASING.md).
