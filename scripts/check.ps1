# Local CI gate: the same two commands as .github/workflows/ci.yml.
# Run from anywhere:  scripts\check.cmd
# (or: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check.ps1)
$ErrorActionPreference = "Stop"
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    python -m ruff check .
    if ($LASTEXITCODE -ne 0) { throw "ruff check failed" }

    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "pytest failed" }

    Write-Host "check.ps1: OK" -ForegroundColor Green
}
finally {
    Pop-Location
}
