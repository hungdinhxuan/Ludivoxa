# uninstall.ps1 - Uninstaller for ludivoxa
# Usage: powershell -ExecutionPolicy Bypass -File uninstall.ps1

$ErrorActionPreference = 'Stop'

Write-Host ""
Write-Host "=== ludivoxa Uninstaller ===" -ForegroundColor Cyan
Write-Host ""

function Remove-LudivoxaPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    try {
        Remove-Item -LiteralPath $Path -Recurse -Force
        Write-Host "     Removed $Description" -ForegroundColor Green
    } catch {
        Write-Host "     Could not remove $Description`: $($_.Exception.Message)" -ForegroundColor Red
    }
}

# A custom install location (see install.ps1) holds the tool environment, the
# uv-managed Python, and the model cache. LUDIVOXA_HOME wins over the pointer
# file the installer wrote, matching the app and the installer.
$configDir = Join-Path $env:USERPROFILE ".ludivoxa"
$installRootFile = Join-Path $configDir "install-dir"
$installRoot = $null
if ($env:LUDIVOXA_HOME -and $env:LUDIVOXA_HOME.Trim()) {
    $installRoot = $env:LUDIVOXA_HOME.Trim()
} elseif (Test-Path -LiteralPath $installRootFile -PathType Leaf) {
    $installRoot = (Get-Content -LiteralPath $installRootFile -Raw).Trim()
    if (-not $installRoot) { $installRoot = $null }
}
if ($installRoot) {
    $installRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($installRoot).TrimEnd('\')
    $env:UV_TOOL_DIR = Join-Path $installRoot "uv\tools"
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $installRoot "uv\python"
    Write-Host "Install location: $installRoot" -ForegroundColor Gray
    Write-Host ""
}

# uv's installer adds itself to ~/.local/bin. Look there as a fallback because
# the current shell may not have picked up the PATH change yet.
$uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$uvExecutable = if ($uvCommand) { $uvCommand.Source } else { $null }
$defaultUvExecutable = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
if (-not $uvExecutable -and (Test-Path -LiteralPath $defaultUvExecutable -PathType Leaf)) {
    $uvExecutable = $defaultUvExecutable
}

# Resolve uv's configured directories when possible, while retaining defaults
# for interrupted uv installs or environments that are no longer registered.
$toolRoot = if ($env:UV_TOOL_DIR) { $env:UV_TOOL_DIR } else { Join-Path $env:APPDATA "uv\tools" }
$toolBin = if ($env:UV_TOOL_BIN_DIR) { $env:UV_TOOL_BIN_DIR } else { Join-Path $env:USERPROFILE ".local\bin" }
if ($uvExecutable) {
    $reportedToolRoot = & $uvExecutable tool dir 2>$null
    if ($LASTEXITCODE -eq 0 -and $reportedToolRoot) {
        $toolRoot = ($reportedToolRoot | Select-Object -Last 1).Trim()
    }

    $reportedToolBin = & $uvExecutable tool dir --bin 2>$null
    if ($LASTEXITCODE -eq 0 -and $reportedToolBin) {
        $toolBin = ($reportedToolBin | Select-Object -Last 1).Trim()
    }
}

$toolEnvironment = Join-Path $toolRoot "ludivoxa"
$toolExecutable = Join-Path $toolBin "ludivoxa.exe"

Write-Host "[1/5] Uninstalling ludivoxa..." -ForegroundColor Yellow
if ($uvExecutable) {
    $toolList = & $uvExecutable tool list 2>$null
    if ($toolList -match "ludivoxa" -or (Test-Path -LiteralPath $toolEnvironment)) {
        $ErrorActionPreference = 'Continue'
        & $uvExecutable tool uninstall ludivoxa
        $uninstallExitCode = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'

        if ($uninstallExitCode -eq 0) {
            Write-Host "     ludivoxa uninstalled" -ForegroundColor Green
        } else {
            Write-Host "     uv could not uninstall the tool; cleaning up its files directly" -ForegroundColor Yellow
        }
    } else {
        Write-Host "     ludivoxa is not registered with uv" -ForegroundColor Gray
    }
} else {
    Write-Host "     uv was not found; cleaning up its files directly" -ForegroundColor Yellow
}

# Remove orphan files left by an interrupted install or a stale uv registry.
Write-Host "[2/5] Cleaning up orphan files..." -ForegroundColor Yellow
if (Test-Path -LiteralPath $toolExecutable) {
    Remove-LudivoxaPath -Path $toolExecutable -Description "orphan executable"
} else {
    Write-Host "     No orphan executable found" -ForegroundColor Gray
}
if (Test-Path -LiteralPath $toolEnvironment) {
    Remove-LudivoxaPath -Path $toolEnvironment -Description "stale tool environment"
} else {
    Write-Host "     No stale tool environment found" -ForegroundColor Gray
}
# An install that was later moved to a custom location may have left the
# default environment behind.
$defaultToolEnvironment = Join-Path $env:APPDATA "uv\tools\ludivoxa"
if ($installRoot -and (Test-Path -LiteralPath $defaultToolEnvironment)) {
    Remove-LudivoxaPath -Path $defaultToolEnvironment -Description "tool environment left in the user profile"
}

# New installers use this dedicated cache so it can always be removed without
# disturbing other uv users. Older installers used uv's shared cache; prune only
# entries uv knows are unreachable and leave reusable downloads alone.
Write-Host "[3/5] Removing package downloads..." -ForegroundColor Yellow
$installCacheDir = Join-Path $env:LOCALAPPDATA "ludivoxa\uv-cache"
if (Test-Path -LiteralPath $installCacheDir) {
    Remove-LudivoxaPath -Path $installCacheDir -Description "ludivoxa package cache"
}

if ($uvExecutable) {
    $ErrorActionPreference = 'Continue'
    & $uvExecutable cache prune
    $pruneExitCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'

    if ($pruneExitCode -eq 0) {
        Write-Host "     Unreachable uv cache entries removed" -ForegroundColor Green
    } else {
        Write-Host "     Some uv cache entries could not be removed; close other uv processes and retry" -ForegroundColor Red
    }

    $sharedCacheDir = & $uvExecutable cache dir 2>$null
    if ($LASTEXITCODE -eq 0 -and $sharedCacheDir) {
        $sharedCacheDir = ($sharedCacheDir | Select-Object -Last 1).Trim()
        Write-Host "     Older installers may have left reusable downloads in uv's shared cache:" -ForegroundColor Gray
        Write-Host "     $sharedCacheDir" -ForegroundColor Gray
        Write-Host "     Run 'uv cache clean' to clear it, including downloads cached by other uv projects." -ForegroundColor Gray
    }
} else {
    $defaultCacheDir = if ($env:UV_CACHE_DIR) { $env:UV_CACHE_DIR } else { Join-Path $env:LOCALAPPDATA "uv\cache" }
    Write-Host "     uv was not found, so its cache could not be pruned" -ForegroundColor Yellow
    Write-Host "     To clear every uv download later, reinstall uv and run 'uv cache clean': $defaultCacheDir" -ForegroundColor Gray
}

# Remove user data
Write-Host "[4/5] Removing user data..." -ForegroundColor Yellow

if ($env:HF_HUB_CACHE) {
    $modelsDir = $env:HF_HUB_CACHE
} elseif ($env:HF_HOME) {
    $modelsDir = Join-Path $env:HF_HOME "hub"
} elseif ($env:XDG_CACHE_HOME) {
    $modelsDir = Join-Path $env:XDG_CACHE_HOME "huggingface\hub"
} else {
    $modelsDir = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
}

# Remove config
if (Test-Path -LiteralPath $configDir) {
    Remove-LudivoxaPath -Path $configDir -Description "config directory"
} else {
    Write-Host "     Config directory not found" -ForegroundColor Gray
}

# Remove the model repositories used by this application. With a custom
# install location the models live under it and are removed below, but an
# install that was moved there later may have left models in this cache.
$modelCacheNames = @(
    "models--rtr46--meiki.text.detect.v0",
    "models--rtr46--meiki.txt.recognition.v0",
    "models--entai2965--sugoi-v4-ja-en-ctranslate2"
)
$removedModel = $false
if (Test-Path -LiteralPath $modelsDir -PathType Container) {
    foreach ($modelCacheName in $modelCacheNames) {
        $modelPath = Join-Path $modelsDir $modelCacheName
        if (Test-Path -LiteralPath $modelPath) {
            Remove-LudivoxaPath -Path $modelPath -Description $modelCacheName
            $removedModel = $true
        }

        $modelLockPath = Join-Path (Join-Path $modelsDir ".locks") $modelCacheName
        if (Test-Path -LiteralPath $modelLockPath) {
            Remove-LudivoxaPath -Path $modelLockPath -Description "$modelCacheName lock files"
        }
    }

}
if (-not $removedModel) {
    Write-Host "     Cached models not found" -ForegroundColor Gray
}

# Remove the custom install location. Only the folders the installer creates
# are deleted, only when the installer's marker file proves it created this
# root, and the root itself only once it is empty. A mistyped LUDIVOXA_HOME
# or a root shared with other files is never wiped wholesale.
Write-Host "[5/5] Removing install location..." -ForegroundColor Yellow
$installMarker = if ($installRoot) { Join-Path $installRoot ".ludivoxa" } else { $null }
if (-not $installRoot) {
    Write-Host "     No custom install location" -ForegroundColor Gray
} elseif (-not (Test-Path -LiteralPath $installMarker -PathType Leaf)) {
    Write-Host "     $installRoot was not created by the ludivoxa installer; leaving it alone" -ForegroundColor Yellow
} else {
    Remove-LudivoxaPath -Path (Join-Path $installRoot "uv") -Description "tool environment and Python"
    Remove-LudivoxaPath -Path (Join-Path $installRoot "uv-cache") -Description "ludivoxa package cache"
    Remove-LudivoxaPath -Path (Join-Path $installRoot "models") -Description "downloaded models"
    Remove-Item -LiteralPath $installMarker -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $installRoot -PathType Container) {
        $leftovers = Get-ChildItem -LiteralPath $installRoot -Force -ErrorAction SilentlyContinue
        if ($leftovers) {
            Write-Host "     Kept $installRoot because it still contains other files" -ForegroundColor Gray
        } else {
            Remove-LudivoxaPath -Path $installRoot -Description "install location $installRoot"
        }
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Uninstall complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
