# Installs (or updates) vecho on Windows 10/11. In PowerShell:
#
#   irm https://raw.githubusercontent.com/Nhahan/vecho/main/install.ps1 | iex
#
# Everything goes into your user account: uv (runs vecho's Python), vecho itself, Ollama
# (the summary AI), the AI models, and "vecho" shortcuts on the desktop and in the Start menu.
# Running it again updates vecho.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # the progress display makes downloads very slow
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Source = if ($env:VECHO_SOURCE) { $env:VECHO_SOURCE } else { 'https://github.com/Nhahan/vecho/archive/refs/heads/main.zip' }
$Korean = (Get-UICulture).Name -like 'ko*'

function Say($ko, $en) { if ($Korean) { Write-Host $ko } else { Write-Host $en } }
function Step($ko, $en) { Write-Host ''; if ($Korean) { Write-Host $ko -ForegroundColor Cyan } else { Write-Host $en -ForegroundColor Cyan } }
function Fail($ko, $en) {
    Write-Host ''
    if ($Korean) { Write-Host $ko -ForegroundColor Red } else { Write-Host $en -ForegroundColor Red }
    throw 'vecho install failed'
}

Say 'vecho를 설치합니다. 인터넷 속도에 따라 수십 분이 걸릴 수 있습니다. 창을 닫지 말고 기다려 주세요.' `
    'Installing vecho. Depending on your internet connection this can take a while; keep this window open.'

Step '1/4  기본 도구 준비' '1/4  Preparing the basics'
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
    $uv = $uv.Source
} else {
    # in its own PowerShell, so nothing it does can close this window
    & powershell -NoProfile -ExecutionPolicy ByPass -Command 'irm https://astral.sh/uv/install.ps1 | iex' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail '기본 도구(uv)를 설치하지 못했습니다. 인터넷 연결을 확인하고 다시 실행하세요.' `
            'Could not install the basics (uv). Check the internet connection and run this again.'
    }
    $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
}

Step '2/4  vecho 설치' '2/4  Installing vecho'
$ErrorActionPreference = 'Continue'  # native programs report progress on stderr
& $uv tool install --force --python 3.12 "vecho[desktop] @ $Source"
$installed = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($installed -ne 0) {
    Fail 'vecho를 설치하지 못했습니다. 인터넷 연결을 확인하고 다시 실행하세요.' `
        'Could not install vecho. Check the internet connection and run this again.'
}
# the 'vecho' command in new windows; it reports on stderr, which must not stop the script
$ErrorActionPreference = 'Continue'
& $uv tool update-shell *> $null
$bin = (& $uv tool dir --bin 2>$null | Select-Object -First 1).Trim()
$ErrorActionPreference = 'Stop'
$vecho = Join-Path $bin 'vecho.exe'

Step '3/4  요약 AI(Ollama) 설치' '3/4  Installing the summary AI (Ollama)'
$ollamaApp = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe'
if ((Test-Path $ollamaApp) -or (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Say '이미 설치되어 있습니다.' 'Already installed.'
} else {
    $setup = Join-Path $env:TEMP 'OllamaSetup.exe'
    try {
        Invoke-WebRequest https://ollama.com/download/OllamaSetup.exe -OutFile $setup -UseBasicParsing
    } catch {
        Fail 'Ollama를 내려받지 못했습니다. 다시 실행해 주세요.' 'Could not download Ollama. Please run this again.'
    }
    Start-Process $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait
    Remove-Item $setup -ErrorAction SilentlyContinue
    Say '설치했습니다.' 'Installed.'
}

Step '4/4  AI 모델 내려받기와 마무리' '4/4  Downloading the AI models and finishing up'
$ErrorActionPreference = 'Continue'
if ($env:VECHO_NO_OPEN) { & $vecho setup --no-open } else { & $vecho setup }
if ($LASTEXITCODE -ne 0) { throw 'vecho setup failed' }
