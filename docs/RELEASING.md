# Tagged releases

Ludivoxa uses tags such as `v2.0.0`. Rebuild an archived release from its tag
with the packaging instructions in that revision.

The first release with `Ludivoxa.exe` (v2.0.0) needs the standard app update for v1.7.0
and earlier. The installer keeps the existing AppId, configuration, OCR/TTS
models and Ollama runtime. Publish deltas only between builds that both use
the new executable layout.

Publish stable releases as latest. The packager maps PEP 440 RC versions, such
as `1.8.0rc1`, to tags such as `v1.8.0-rc.1`. Keep Windows numeric versions in
the form `1.8.0.0` and mark RC releases as prereleases.

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

This reuses the existing Inno installer and AppId. It includes the EXE, native
libraries, bundled Korean recognizer, license and runtime-stop helper. It
leaves user config, runtime and separately installed models in place, and
refuses a fresh empty installation. Publish it only when those
existing model/runtime formats remain compatible; otherwise publish only the
full offline installer so the client uses that fallback.

Pass its versioned EXE to `package_release.py --app-update PATH` alongside
the full installer directory. The helper includes it in both
`release-manifest.json` and `SHA256SUMS.txt`; upload **all returned files**.
The client prefers `Ludivoxa-X.Y.Z-App-Update.exe`, or downloads the full
setup plus every numbered BIN when app-only is absent. Checksums and expected
byte counts are required and verified again immediately before launch.

### Smaller Windows updates

For a stable release with compatible model and runtime formats, keep the
standard app-only installer and optionally build a delta for a recent stable
version. Use the **exact archived PyInstaller onedir build** shipped with that
version. A rebuild of the same tag can have different file hashes.

```powershell
$previous = '<stable version using Ludivoxa.exe>'
$version = '<new stable version>'
$baseApp = "build/v$previous-app/Ludivoxa"
$targetApp = "build/v$version-app/Ludivoxa"
$appUpdate = "dist/app-update/v$version/Ludivoxa-$version-App-Update.exe"
$deltaDir = "dist/deltas/v$version"
uv run python packaging/build_delta_update.py --base $baseApp --base-version $previous --app $targetApp --output $deltaDir
uv run python packaging/package_release.py --output "dist/releases/v$version" --source-commit $releaseCommit `
  --app "$targetApp/Ludivoxa.exe" --app-update $appUpdate `
  --delta "$deltaDir/Ludivoxa-$version-Delta-from-$previous.exe"
```

The builder includes only changed and new app files, plus the installer's
small support files. It records removed app files for cleanup. The `.exe` and
its matching `.base.sha256` both go into the release checksums. Repeat `--delta`
to support additional base versions. The packager requires a standard app
update and a total delta size smaller than the standard installer.

The new updater checks the installed version and verifies the app files against
the release's base manifest before downloading the delta executable. The delta
installer also verifies the base with its embedded copy before modifying files.
If the files differ or the delta fails verification, the user can choose the
standard download at its displayed size. Check an upgrade from an intact base
and rejection of a changed base in an isolated install before publishing.
Use the standard app update to repair an interrupted installation.

Older updaters download the standard installer even when the first release with
delta support also publishes a smaller installer. Users on the matching base
can run that release's `Delta-from` executable manually after verifying its
checksum. New installations still start with the full offline installer.

### Install and restart

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
Describe game tests by platform or scenario in public release notes and docs.
Keep titles from personal playtests and user reports out of published materials.
Release publishing still uses the maintainer's Git/GitHub authentication
tooling; credentials must never be committed or embedded in app defaults.

Reference: [GitHub release size limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).
