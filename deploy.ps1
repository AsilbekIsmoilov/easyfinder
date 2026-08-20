$ErrorActionPreference = "Stop"
if (-not (Test-Path "backend/.env.production")) {
    Copy-Item "backend/.env.prod.example" "backend/.env.production"
    Write-Host "backend/.env.production yaratildi. CHANGE_ME qiymatlarini to'ldiring."
    exit 1
}
if (-not (Test-Path ".env.mysql")) {
    Copy-Item ".env.mysql.example" ".env.mysql"
    Write-Host ".env.mysql yaratildi. CHANGE_ME qiymatlarini to'ldiring."
    exit 1
}
$envText = (Get-Content "backend/.env.production" -Raw) + (Get-Content ".env.mysql" -Raw)
if ($envText.Contains("CHANGE_ME")) {
    throw "Env fayllarda CHANGE_ME qolgan. Avval secretlarni to'ldiring."
}
docker compose -f docker-compose.prod.yml up -d --build
if ($LASTEXITCODE -ne 0) { throw "Docker deploy xato bilan tugadi" }
docker compose -f docker-compose.prod.yml ps