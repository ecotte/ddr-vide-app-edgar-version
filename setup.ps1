# Sets up a project-local virtual environment with all dependencies,
# installing the CPU-only build of PyTorch first so EasyOCR doesn't pull
# in a large CUDA build. Safe to re-run.
#
# Usage (from the project folder, in PowerShell):
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1

$ErrorActionPreference = "Stop"

Write-Host "== DDR Video Event Extractor: setup ==" -ForegroundColor Cyan

if (-not (Test-Path ".\.venv")) {
    Write-Host "Creating virtual environment in .venv ..."
    python -m venv .venv
} else {
    Write-Host "Virtual environment .venv already exists, reusing it."
}

$venvPython = ".\.venv\Scripts\python.exe"

Write-Host "Upgrading pip ..."
& $venvPython -m pip install --upgrade pip

Write-Host "Installing PyTorch (CPU-only build) ..."
& $venvPython -m pip install torch --index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not reach download.pytorch.org/whl/cpu (blocked network/proxy?)." -ForegroundColor Yellow
    Write-Host "Continuing - the next step installs torch from your normal package index instead," -ForegroundColor Yellow
    Write-Host "which is CPU-only by default on Windows for current PyTorch releases." -ForegroundColor Yellow
}

Write-Host "Installing remaining dependencies from requirements.txt ..."
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "Dependency installation failed. See the pip output above for details." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Run the app:    powershell -ExecutionPolicy Bypass -File .\run.ps1"
Write-Host "  2. Run the tests:  powershell -ExecutionPolicy Bypass -File .\run_tests.ps1"
