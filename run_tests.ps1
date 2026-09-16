# Runs the automated test suite using the project's virtual environment.
#
# Usage: powershell -ExecutionPolicy Bypass -File .\run_tests.ps1

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "Virtual environment not found. Run setup.ps1 first:" -ForegroundColor Red
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\setup.ps1"
    exit 1
}

& ".\.venv\Scripts\python.exe" -m pytest -v
