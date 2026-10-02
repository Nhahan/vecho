# Removes vecho from Windows. In PowerShell:
#
#   irm https://raw.githubusercontent.com/Nhahan/vecho/main/uninstall.ps1 | iex
#
# Your recordings (%USERPROFILE%\.vecho) and the summary AI (Ollama) are kept; the message
# at the end says how to remove them too.

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Korean = (Get-UICulture).Name -like 'ko*'
function Say($ko, $en) { if ($Korean) { Write-Host $ko } else { Write-Host $en } }

# quit a running vecho first: its programs are in use and could not be removed
Get-Process -Name 'vecho', 'vecho-app' -ErrorAction SilentlyContinue | Stop-Process -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$uv = Get-Command uv -ErrorAction SilentlyContinue
$uv = if ($uv) { $uv.Source } else { Join-Path $env:USERPROFILE '.local\bin\uv.exe' }
if (Test-Path $uv) { & $uv tool uninstall vecho *> $null }

$links = @(
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\vecho.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'vecho.lnk'),
    (Join-Path $env:LOCALAPPDATA 'vecho\vecho.ico')
)
foreach ($link in $links) { Remove-Item $link -ErrorAction SilentlyContinue }

Say 'vecho를 지웠습니다.' 'vecho is removed.'
Say "녹음은 그대로 있습니다 ($env:USERPROFILE\.vecho). 녹음까지 지우려면 그 폴더를 지우세요." `
    "Your recordings are still in $env:USERPROFILE\.vecho. Delete that folder to remove them too."
Say '요약 AI(Ollama)도 지우려면: 설정 → 앱 → 설치된 앱에서 Ollama를 제거하세요.' `
    'To remove the summary AI (Ollama) too: Settings → Apps → Installed apps → Ollama → Uninstall.'
