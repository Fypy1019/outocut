$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$engineRoot = Join-Path $repoRoot 'engine'
Push-Location $engineRoot
try {
    python -m PyInstaller --noconfirm --clean --onedir --noconsole `
        --name outocut-engine `
        --collect-submodules uvicorn `
        --collect-submodules fastapi `
        --collect-submodules pydantic `
        --collect-submodules outocut_engine `
        outocut_engine/__main__.py
} finally {
    Pop-Location
}
