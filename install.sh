#!/bin/sh
# Installs (or updates) vecho on macOS and Linux:
#
#   curl -fsSL https://raw.githubusercontent.com/Nhahan/vecho/main/install.sh | sh
#
# Everything goes into your user account (no administrator password on macOS):
#   uv (runs vecho's Python), vecho itself, Ollama (the summary AI), the AI models,
#   and a "vecho" app you can double-click. Running it again updates vecho.
set -eu

# (VECHO_SOURCE: install from elsewhere, e.g. a local checkout; VECHO_NO_OPEN=1: don't open
# the app at the end. Both are for testing.)
SOURCE="${VECHO_SOURCE:-https://github.com/Nhahan/vecho/archive/refs/heads/main.zip}"

lang=en
case "${LC_ALL:-${LANG:-}}" in ko*) lang=ko ;; esac
if [ "$(uname -s)" = Darwin ] && defaults read -g AppleLanguages 2>/dev/null | grep -q '"*ko'; then
    lang=ko
fi
say() { if [ "$lang" = ko ]; then printf '%s\n' "$1"; else printf '%s\n' "$2"; fi; }
step() { printf '\n\033[1m%s\033[0m\n' "$(say "$1" "$2")"; }
fail() {
    printf '\n\033[31m%s\033[0m\n' "$(say "$1" "$2")" >&2
    exit 1
}

os=$(uname -s)
case "$os" in
Darwin)
    major=$(sw_vers -productVersion | cut -d. -f1)
    if [ "$major" -lt 13 ]; then
        fail "vecho는 macOS 13(Ventura) 이상에서 동작합니다. macOS를 업데이트한 뒤 다시 시도하세요." \
            "vecho needs macOS 13 (Ventura) or newer. Update macOS and try again."
    fi
    extra="[desktop]"
    ;;
Linux) extra="" ;; # the app opens in the web browser
*) fail "이 컴퓨터는 지원하지 않습니다 ($os)." "This computer is not supported ($os)." ;;
esac

say "vecho를 설치합니다. 인터넷 속도에 따라 수십 분이 걸릴 수 있습니다. 창을 닫지 말고 기다려 주세요." \
    "Installing vecho. Depending on your internet connection this can take a while; keep this window open."

step "1/4  기본 도구 준비" "1/4  Preparing the basics"
if command -v uv >/dev/null 2>&1; then
    UV=$(command -v uv)
else
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 ||
        fail "기본 도구(uv)를 설치하지 못했습니다. 인터넷 연결을 확인하고 다시 실행하세요." \
            "Could not install the basics (uv). Check the internet connection and run this again."
    UV="${XDG_BIN_HOME:-$HOME/.local/bin}/uv"
fi

if [ "$os" = Linux ] && ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
    say "소리 장치용 부품을 설치합니다. 관리자 비밀번호를 물으면 입력해 주세요." \
        "Installing the sound library. Enter your password if asked."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get install -y libportaudio2 >/dev/null
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y portaudio >/dev/null
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm portaudio >/dev/null
    fi || say "PortAudio를 설치하지 못했습니다. 녹음하려면 직접 설치해 주세요." \
        "Could not install PortAudio; install it yourself to record."
fi

step "2/4  vecho 설치" "2/4  Installing vecho"
"$UV" tool install --force --python 3.12 "vecho$extra @ $SOURCE" ||
    fail "vecho를 설치하지 못했습니다. 인터넷 연결을 확인하고 다시 실행하세요." \
        "Could not install vecho. Check the internet connection and run this again."
"$UV" tool update-shell >/dev/null 2>&1 || true # the 'vecho' command in new terminals
VECHO="$("$UV" tool dir --bin)/vecho"

step "3/4  요약 AI(Ollama) 설치" "3/4  Installing the summary AI (Ollama)"
if [ "$os" = Darwin ]; then
    if [ -d /Applications/Ollama.app ] || [ -d "$HOME/Applications/Ollama.app" ] || command -v ollama >/dev/null 2>&1; then
        say "이미 설치되어 있습니다." "Already installed."
    else
        tmp=$(mktemp -d)
        curl -fL --progress-bar -o "$tmp/Ollama.zip" https://ollama.com/download/Ollama-darwin.zip ||
            fail "Ollama를 내려받지 못했습니다. 다시 실행해 주세요." "Could not download Ollama. Please run this again."
        unzip -q "$tmp/Ollama.zip" -d "$tmp"
        target=/Applications
        [ -w "$target" ] || { target="$HOME/Applications"; mkdir -p "$target"; }
        mv "$tmp/Ollama.app" "$target/"
        rm -rf "$tmp"
        say "설치했습니다. (Ollama 환영 창이 뜨면 닫아도 됩니다.)" \
            "Installed. (If an Ollama welcome window appears, you can close it.)"
    fi
else
    if command -v ollama >/dev/null 2>&1; then
        say "이미 설치되어 있습니다." "Already installed."
    else
        say "관리자 비밀번호를 물으면 입력해 주세요." "Enter your password if asked."
        curl -fsSL https://ollama.com/install.sh | sh ||
            fail "Ollama를 설치하지 못했습니다. 다시 실행해 주세요." "Could not install Ollama. Please run this again."
    fi
fi

step "4/4  AI 모델 내려받기와 마무리" "4/4  Downloading the AI models and finishing up"
if [ -n "${VECHO_NO_OPEN:-}" ]; then "$VECHO" setup --no-open; else "$VECHO" setup; fi
