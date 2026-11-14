# Tagged releases

Ludivoxa's first version is **1.0.0**, tagged **v1.0.0**. Upstream Interpreter
version numbers do not define this fork's release sequence.

1. Update the project version in `pyproject.toml`, its editable entry in
   `uv.lock`, and `AppVersion` in `packaging/windows_installer.iss`.
2. Refresh `CHANGELOG.md`, public guides and `docs/releases/vX.Y.Z.md`.
3. Sync the environment, run the source suite, build the Windows EXE and verify
   its reported version and native version resource. Run the frozen diagnostic
   with real OCR/Ollama/TTS and confirm private runtime cleanup.
4. Build a new installer output directory. Inspect public defaults and licenses;
   do not package a developer's configuration or reference recordings.
5. Commit the reviewed source and push `main`. Verify CI, then create and push
   an annotated version tag without rewriting existing tags.
6. Prepare release assets with the exact source commit:

```powershell
$releaseCommit = git rev-parse HEAD
uv run python packaging/package_release.py `
  --installer dist/installer-v1.0.0 `
  --output dist/releases/v1.0.0 `
  --source-commit $releaseCommit `
  --local-zip dist/Ludivoxa-1.0.0-Full-Offline-Setup.zip
```

The helper validates contiguous data slices and the per-file upload limit,
writes `SHA256SUMS.txt` and `release-manifest.json`, and can optionally create
a CRC-checked local ZIP. The model and EXE checksums appear in the manifest.

GitHub release files must each be **under 2 GiB**. The full offline ZIP is
approximately 4.9 GB, so upload the installer EXE and each BIN separately,
together with README/checksums/manifest. The current Inno slice size is
2,100,000,000 bytes, below that limit.

Create the release as a draft, upload all assets, check their reported byte
counts/digests, then publish it for the existing tag. With GitHub CLI:

```powershell
git tag -a v1.0.0 -m 'Ludivoxa v1.0.0'
git push origin v1.0.0
gh release create v1.0.0 --draft --verify-tag --title 'Ludivoxa v1.0.0' --notes-file docs/releases/v1.0.0.md
gh release upload v1.0.0 dist/releases/v1.0.0/*
gh release edit v1.0.0 --draft=false --latest
```

GitHub's automatic source archives exclude the binary/model distribution.
The release workflow builds Python source distributions as Actions artifacts;
it does **not** publish this fork to upstream's PyPI package.

Keep repository visibility unchanged unless its owner explicitly requests a
change. Retain upstream attribution and third-party license/model-card files.
The private GitHub credential is supplied by Git/GitHub authentication tooling,
never committed to the repository or embedded in app defaults.

Reference: [GitHub release size limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).
