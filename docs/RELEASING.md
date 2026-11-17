# Tagged releases

Ludivoxa's first version is **1.0.0**, tagged **v1.0.0**. Upstream Interpreter
version numbers do not define this fork's release sequence.

Version **1.1.0** adds compatible features: the updater,
streaming speech and latency improvements. Use patch
version **1.1.1** fixes OCR/TTS repeats and audio formats. The published v1.0.0 has no updater;
its users must install the first updater-enabled version manually once.

The **v1.1.3 stable** release uses Python/AppVersion `1.1.3` and Windows numeric
version `1.1.3.0`. Build a fresh app stage, test it, then package only the app
update (existing models/runtime remain compatible):

```powershell
powershell -ExecutionPolicy Bypass -File packaging/build_app_update.ps1 -AppDirectory build/v1.1.3-app/Interpreter -OutputDirectory dist/app-update/v1.1.3
uv run python packaging/package_release.py --output dist/releases/v1.1.3 --source-commit $releaseCommit --app build/v1.1.3-app/Interpreter/Interpreter.exe --app-update dist/app-update/v1.1.3/Ludivoxa-1.1.3-App-Update.exe
```

Publish `v1.1.3` with `prerelease=false` and `make_latest=true`. Verify that
anonymous v1.1.1, v1.1.2rc1 and v1.1.2 clients can download and verify the stable
update. Retain the existing RC tag and assets unchanged. New users install
the full offline v1.1.1 package before this update.

For the earlier **v1.1.2-rc.1** prerelease, Python metadata uses `1.1.2rc1` and Windows
numeric resources use `1.1.2.0`. Keep `AppVersion` and `AppNumericVersion` in
the Inno defaults aligned. Publish with `prerelease=true` and `make_latest=false`.
Stable users do not receive RCs automatically; RC installations can upgrade
to the same base stable version later. Existing runtime/model formats are unchanged,
so this RC ships only the app-update installer, README, manifest and checksums:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/build_app_update.ps1 -AppDirectory build/v1.1.2-rc.1-app/Interpreter -OutputDirectory dist/app-update/v1.1.2-rc.1
uv run python packaging/package_release.py --output dist/releases/v1.1.2-rc.1 --source-commit $releaseCommit --app build/v1.1.2-rc.1-app/Interpreter/Interpreter.exe --app-update dist/app-update/v1.1.2-rc.1/Ludivoxa-1.1.2rc1-App-Update.exe
```

The packager maps PEP 440 RC metadata to the SemVer-style Git tag. It also
retains full-offline packaging when `--installer` is supplied. New RC testers
install the stable full offline setup first, then the manual app update.

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
  --installer dist/installer-v1.1.1 `
  --output dist/releases/v1.1.1 `
  --source-commit $releaseCommit `
  --app-update dist/app-update/Ludivoxa-1.1.1-App-Update.exe `
  --local-zip dist/Ludivoxa-1.1.1-Full-Offline-Setup.zip
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
git tag -a v1.1.1 -m 'Ludivoxa v1.1.1'
git push origin v1.1.1
gh release create v1.1.1 --draft --verify-tag --title 'Ludivoxa v1.1.1' --notes-file docs/releases/v1.1.1.md
gh release upload v1.1.1 dist/releases/v1.1.1/*
gh release edit v1.1.1 --draft=false --latest
```

GitHub's automatic source archives exclude the binary/model distribution.
The release workflow builds Python source distributions as Actions artifacts;
it does **not** publish this fork to upstream's PyPI package.

## In-app update assets

The updater checks stable tags in `hungdinhxuan/Ludivoxa`; draft,
prerelease, equal/older or malformed versions are never installed. Publish a
strictly newer stable tag to offer an update. Do not replace the immutable
v1.0.0 or v1.1.0 release to deliver later changes under the same version number.

Build the optional app-only installer after validating the new EXE:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/build_app_update.ps1
```

This reuses the existing Inno installer and AppId, with only the EXE/native
libraries/license/runtime-stop helper. It excludes config, runtime and model
files, and refuses a fresh empty installation. Publish it only when those
existing model/runtime formats remain compatible; otherwise publish only the
full offline installer so the client uses that fallback.

Pass its versioned EXE to `package_release.py --app-update PATH` alongside
the full installer directory. The helper includes it in both
`release-manifest.json` and `SHA256SUMS.txt`; upload **all returned files**.
The client prefers `Ludivoxa-X.Y.Z-App-Update.exe`, or downloads the full
setup plus every numbered BIN when app-only is absent. Checksums and expected
byte counts are required and verified again immediately before launch.

Use `/LUDIVOXARESTART=1` only for user-requested in-app installation; the
installer's extra Run entry restarts the app after a successful silent
upgrade. Normal setup/uninstall behavior and configuration retention remain.
Update checks and asset downloads use the public repository anonymously.
The application does not read Git credentials, environment tokens or the
Windows credential vault. GitHub rate-limit errors are shown with a retry
message. Keep releases public and upload a higher stable version tag for the
application to offer a new update. Startup checks remain optional and do not
automatically download or install anything.

Keep repository visibility unchanged unless its owner explicitly requests a
change. Retain upstream attribution and third-party license/model-card files.
Release publishing still uses the maintainer's Git/GitHub authentication
tooling; credentials must never be committed or embedded in app defaults.

Reference: [GitHub release size limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).
