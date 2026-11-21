# Ludivoxa Windows packaging

See [development and provisioning](../docs/DEVELOPMENT.md) for complete build
instructions, and [tagged releases](../docs/RELEASING.md) for publishing.

- `build_windows.ps1`: build the onedir EXE with local OCR/TTS models.
- `prepare_offline_bundle.py`: verify the selected Hauhau store, stage the
  runtime/model payload and apply the public `config.release.yml` defaults.
- `build_installer.ps1`: compile the full Windows x64 installer using Inno Setup 7.
- `build_app_update.ps1`: compile an app-only installer for existing installations;
  preserve config, OCR/TTS models, LLM weights and the Ollama runtime.
- `package_release.py`: prepare separate upload assets, checksums, a source
  commit manifest and an optional local ZIP.
- `local_runtime.py`: ownership-checked Windows job leases for private Ollama.
- `windows_entry.py`: frozen app startup and final runtime cleanup.
- `frozen_smoke.py`: actual local inference, audio, crop and repeat diagnostics.
- `Stop-LocalRuntime.ps1`: installer upgrade/uninstall cleanup for its own runtime.
- `provision_ocr_onnx.py`, `provision_clone.py`: explicit provisioning commands;
  the app/build never invoke them automatically.
- `HauhauCS.Modelfile`: non-thinking instruction template for the selected model.
- `THIRD_PARTY_NOTICES.txt`: bundled component attribution/licenses.

Runtime and model binaries belong in release assets. Keep the setup EXE and
all numbered BIN slices together when using a full offline installer.
The onedir executable is `Ludivoxa.exe`. The standard Windows update recognizes
previous Ludivoxa installations using the same AppId and preserves user data.
