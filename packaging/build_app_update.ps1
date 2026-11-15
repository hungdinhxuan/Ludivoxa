param(
    [string]$Compiler = 'build/tools/InnoSetup/ISCC.exe',
    [string]$Python = '.venv/Scripts/python.exe',
    [string]$AppDirectory = 'dist/Interpreter',
    [string]$OutputDirectory = 'dist/app-update'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
$compilerPath = (Resolve-Path -LiteralPath $Compiler).Path
$appPath = (Resolve-Path -LiteralPath $AppDirectory).Path
if (-not (Test-Path -LiteralPath (Join-Path $appPath 'Interpreter.exe'))) { throw 'Build the app EXE first.' }
if (-not (Test-Path -LiteralPath (Join-Path $appPath '_internal'))) { throw 'The complete PyInstaller onedir bundle is required.' }
$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
$releaseVersion = & $Python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the application version.' }
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
& $compilerPath '/DAppUpdateOnly=1' ("/DAppVersion=" + $releaseVersion) ("/DPayloadDir=" + $appPath) ("/DOutputDir=" + $outputPath) packaging/windows_installer.iss *> build/app-update-compile.log
if ($LASTEXITCODE -ne 0) { throw 'App-update installer failed; see build/app-update-compile.log.' }
Write-Host "App-only update written to $outputPath. OCR/TTS/Ollama model files and user config are excluded."
