# Starts the built program and checks that it really opens its port. Used by the build workflow.
#   smoke.ps1 -Exe dist\Jisr\Jisr.exe -Port 47899 -ExtraArgs "--nogui"
param(
    [string]$Exe,
    [int]$Port,
    [string[]]$ExtraArgs = @()
)
$ErrorActionPreference = "Stop"
$exePath = (Resolve-Path $Exe).Path
$p = Start-Process -FilePath $exePath -ArgumentList ($ExtraArgs + @("--port", "$Port")) -PassThru
$ok = $false
try {
    for ($i = 0; $i -lt 40 -and -not $ok; $i++) {
        Start-Sleep -Seconds 1
        if ($p.HasExited) { throw "$exePath exited early (code $($p.ExitCode))" }
        $c = New-Object System.Net.Sockets.TcpClient
        try { $c.Connect("127.0.0.1", $Port); $ok = $true } catch { } finally { $c.Close() }
    }
} finally {
    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
}
if (-not $ok) { throw "Nothing is listening on port $Port after 40 seconds" }
Write-Host "OK: $exePath listens on port $Port"
