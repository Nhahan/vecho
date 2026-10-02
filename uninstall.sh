#!/bin/sh
# Removes vecho from macOS or Linux:
#
#   curl -fsSL https://raw.githubusercontent.com/Nhahan/vecho/main/uninstall.sh | sh
#
# Your recordings (~/.vecho) and the summary AI (Ollama) are kept; the message at the end
# says how to remove them too.
set -u

lang=en
case "${LC_ALL:-${LANG:-}}" in ko*) lang=ko ;; esac
if [ "$(uname -s)" = Darwin ] && defaults read -g AppleLanguages 2>/dev/null | grep -q '"*ko'; then
    lang=ko
fi
say() { if [ "$lang" = ko ]; then printf '%s\n' "$1"; else printf '%s\n' "$2"; fi; }

# quit a running vecho first (it saves a recording in progress)
pkill -f "vecho.app/Contents/MacOS/vecho" 2>/dev/null
pkill -f "bin/vecho app" 2>/dev/null
sleep 2

UV=$(command -v uv 2>/dev/null || echo "${XDG_BIN_HOME:-$HOME/.local/bin}/uv")
[ -x "$UV" ] && "$UV" tool uninstall vecho >/dev/null 2>&1

for app in /Applications/vecho.app "$HOME/Applications/vecho.app"; do
    [ -f "$app/Contents/Resources/command" ] && rm -rf "$app"
done
data="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -f "$data/applications/vecho.desktop" "$data/icons/hicolor/512x512/apps/vecho.png"

say "vecho를 지웠습니다." "vecho is removed."
say "녹음은 그대로 있습니다 ($HOME/.vecho). 녹음까지 지우려면 이 줄을 붙여 넣으세요:" \
    "Your recordings are still in $HOME/.vecho. To delete them too, paste this line:"
printf '    rm -rf ~/.vecho\n'
say "요약 AI(Ollama)도 지우려면: Mac은 응용 프로그램 폴더의 Ollama를 휴지통에 버리세요." \
    "To remove the summary AI (Ollama) too: on a Mac, move Ollama from Applications to the Trash."
