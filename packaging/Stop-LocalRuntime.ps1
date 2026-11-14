$ErrorActionPreference = 'Stop'
$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'runtime'))
$recordPath = Join-Path $runtimeRoot 'ollama.pid.json'
if (-not (Test-Path -LiteralPath $recordPath)) { exit 0 }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$expected = Join-Path $runtimeRoot 'ollama.exe'
$server = Get-CimInstance Win32_Process -Filter ("ProcessId=" + [int]$record.pid)
if ($server -and $server.ExecutablePath -eq $expected) {
    # Stop only descendants from this installed runtime, never a shared Ollama.
    Get-CimInstance Win32_Process -Filter ("ParentProcessId=" + [int]$record.pid) |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($runtimeRoot + '\', [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Stop-Process -Id $server.ProcessId -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $recordPath -Force
