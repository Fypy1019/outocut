$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$target = Join-Path $repoRoot 'vendor\ffmpeg'
New-Item -ItemType Directory -Path $target -Force | Out-Null
$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$ffprobe = (Get-Command ffprobe -ErrorAction Stop).Source
Copy-Item -LiteralPath $ffmpeg -Destination (Join-Path $target 'ffmpeg.exe') -Force
Copy-Item -LiteralPath $ffprobe -Destination (Join-Path $target 'ffprobe.exe') -Force

$ffmpegRoot = Split-Path -Parent (Split-Path -Parent $ffmpeg)
$licensePath = Join-Path $ffmpegRoot 'LICENSE'
$readmePath = Join-Path $ffmpegRoot 'README.txt'

if (Test-Path -LiteralPath $licensePath) {
    Copy-Item -LiteralPath $licensePath -Destination (Join-Path $target 'LICENSE.txt') -Force
}

if (Test-Path -LiteralPath $readmePath) {
    Copy-Item -LiteralPath $readmePath -Destination (Join-Path $target 'BUILD-INFO.txt') -Force
}

Write-Host "FFmpeg binaries and available license files copied to $target"
