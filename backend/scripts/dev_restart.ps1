# Lokal ishlab chiqish uchun: hamma jarayonni to'xtatib, aniq bittadan ko'taradi.
#
# Nega kerak: Start-Process bilan qo'lda ko'tarilganda eski jarayon orfan bo'lib
# qolishi mumkin. Ikkita scheduler ishlasa har soat create/update IKKI MARTA
# navbatga qo'yiladi va Claude ikki barobar pul yeydi.
#
# Serverda bu muammo yo'q — Docker har xizmatga bitta konteyner kafolatlaydi.
#
# Ishlatish:  powershell -File scripts\dev_restart.ps1

$ErrorActionPreference = 'Stop'
$backend = Split-Path -Parent $PSScriptRoot
$python = Join-Path $backend '.venv\Scripts\python.exe'
$pattern = 'app\.scheduler|app\.worker|uvicorn app\.main:app'

Write-Host '--- to''xtatilmoqda ---'
$running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match $pattern })
foreach ($p in $running) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Host ("  {0} ta jarayon to'xtatildi" -f $running.Count)
Start-Sleep -Seconds 4

$left = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match $pattern })
if ($left.Count -gt 0) {
    throw "Ba'zi jarayonlar to'xtamadi: $($left.ProcessId -join ', ')"
}

Write-Host '--- ishga tushirilmoqda ---'
$services = @(
    @{ Name = 'api';       Args = @('-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8001') },
    @{ Name = 'worker';    Args = @('-m', 'app.worker') },
    @{ Name = 'scheduler'; Args = @('-m', 'app.scheduler') }
)
foreach ($s in $services) {
    $proc = Start-Process -FilePath $python -ArgumentList $s.Args -WorkingDirectory $backend `
        -RedirectStandardOutput (Join-Path $backend "$($s.Name).out.log") `
        -RedirectStandardError (Join-Path $backend "$($s.Name).err.log") `
        -WindowStyle Hidden -PassThru
    $proc.Id | Out-File (Join-Path $backend ".$($s.Name).pid") -Encoding ascii -NoNewline
    Write-Host ("  {0,-10} pid={1}" -f $s.Name, $proc.Id)
}

Start-Sleep -Seconds 9

Write-Host '--- tekshiruv ---'
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8001 -ErrorAction SilentlyContinue)
Write-Host ("  8001 tinglovchilari : {0}" -f $listeners.Count)

$log = Join-Path $backend 'scheduler.err.log'
$jobs = @(Select-String -Path $log -Pattern 'jadvali' -ErrorAction SilentlyContinue)
foreach ($j in ($jobs | Select-Object -Last 2)) {
    Write-Host ('  ' + ($j.Line -replace '^.*INFO ', ''))
}

try {
    $health = Invoke-RestMethod 'http://127.0.0.1:8001/api/health' -TimeoutSec 20
    Write-Host ("  health              : {0}" -f ($health | ConvertTo-Json -Compress))
}
catch {
    Write-Host "  health              : javob yo'q"
}
