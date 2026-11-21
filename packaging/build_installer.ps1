param(
    [string]$Compiler = 'build/tools/InnoSetup/ISCC.exe',
    [string]$Python = '.venv/Scripts/python.exe',
    [string]$AppDirectory = 'dist/Ludivoxa',
    [string]$OllamaDirectory = '.test-models/ollama-runtime',
    [string]$ModelStore = '.test-models/ollama-models',
    [string]$OutputDirectory = 'dist/installer'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
$compilerPath = (Resolve-Path -LiteralPath $Compiler).Path
$stageName = 'offline-payload-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$stagePath = Join-Path $repoRoot (Join-Path 'build' $stageName)
$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
& $Python packaging/prepare_offline_bundle.py --app $AppDirectory --runtime $OllamaDirectory --store $ModelStore --output $stagePath
if ($LASTEXITCODE -ne 0) { throw 'Offline bundle preparation failed.' }
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
$releaseVersion = & $Python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the application version.' }
$numericVersion = ($releaseVersion -split 'rc', 2)[0] + '.0'
& $compilerPath ("/DAppVersion=" + $releaseVersion) ("/DAppNumericVersion=" + $numericVersion) ("/DPayloadDir=" + $stagePath) ("/DOutputDir=" + $outputPath) packaging/windows_installer.iss *> build/installer-compile.log
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed; see build/installer-compile.log.' }
Write-Host "Offline setup written to $outputPath. Keep Setup.exe and every .bin file together."
