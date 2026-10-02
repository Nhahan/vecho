"""``vecho setup``: get everything ready in one go, in words anyone can follow.

Run by the installer after vecho itself is installed (and safe to run again): starts the
summary AI, downloads both AI models with progress, prepares system audio capture and makes
a shortcut to double-click.
"""

from __future__ import annotations

import contextlib
import locale
import os
import subprocess
import sys
import time
from typing import TextIO

from . import models, shortcut, systemaudio
from .config import Config
from .errors import VechoError
from .transcribe import MlxTranscriber, make_transcriber, model_cached

TEXT = {
    "ko": {
        "title": "vecho 준비를 시작합니다. 처음에는 AI 모델을 내려받느라 시간이 걸립니다.",
        "ollama": "요약 AI(Ollama) 켜는 중",
        "no_ollama": "요약 AI(Ollama)가 설치되어 있지 않습니다. 설치 명령을 다시 실행하거나 "
        "https://ollama.com/download 에서 설치한 뒤 'vecho setup'을 실행하세요.",
        "ollama_off": "요약 AI(Ollama)가 켜지지 않았습니다. Ollama 앱을 한 번 열어 준 뒤 "
        "'vecho setup'을 다시 실행하세요.",
        "llm": "요약 AI 모델 내려받는 중: {model}",
        "speech": "음성 인식 모델 내려받는 중 (약 1.5GB)",
        "speech_ready": "음성 인식 모델이 이미 있습니다",
        "system_audio": "상대방 소리 녹음 준비 중",
        "system_audio_no": "상대방 소리 녹음은 이 컴퓨터에서 쓸 수 없습니다: {why}",
        "shortcut": "바로가기 만드는 중",
        "done": "준비가 끝났습니다!",
        "open_mac": "응용 프로그램(또는 Launchpad)에서 'vecho'를 찾아 실행하세요.",
        "open_windows": "바탕 화면이나 시작 메뉴의 'vecho'를 눌러 실행하세요.",
        "open_other": "앱 메뉴의 'vecho'를 눌러 실행하세요.",
        "permission": "처음 녹음할 때 마이크와 '시스템 오디오 녹음' 허용 창이 뜨면 "
        "'허용'을 눌러 주세요.",
        "failed": "문제가 생겼습니다: {error}",
        "ok": "완료",
    },
    "en": {
        "title": "Getting vecho ready. The first time, downloading the AI models takes a while.",
        "ollama": "Starting the summary AI (Ollama)",
        "no_ollama": "The summary AI (Ollama) is not installed. Run the install command again, "
        "or install it from https://ollama.com/download and then run 'vecho setup'.",
        "ollama_off": "The summary AI (Ollama) did not start. Open the Ollama app once, then "
        "run 'vecho setup' again.",
        "llm": "Downloading the summary model: {model}",
        "speech": "Downloading the speech recognition model (about 1.5 GB)",
        "speech_ready": "The speech recognition model is already here",
        "system_audio": "Preparing to record the other side",
        "system_audio_no": "Recording the other side is not available on this computer: {why}",
        "shortcut": "Creating a shortcut",
        "done": "All set!",
        "open_mac": "Find 'vecho' in Applications (or Launchpad) to start it.",
        "open_windows": "Click 'vecho' on the desktop or in the Start menu.",
        "open_other": "Start 'vecho' from your applications menu.",
        "permission": "The first time you record, click 'Allow' when asked about the microphone "
        "and system audio recording.",
        "failed": "Something went wrong: {error}",
        "ok": "done",
    },
}


def language() -> str:
    """'ko' when the computer is set to Korean."""
    candidates = [os.environ.get(name, "") for name in ("LC_ALL", "LC_MESSAGES", "LANG")]
    if sys.platform == "darwin":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            out = subprocess.run(
                ["defaults", "read", "-g", "AppleLanguages"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
            candidates.insert(0, out.replace("(", "").strip().strip('"'))
    elif sys.platform == "win32":
        with contextlib.suppress(Exception):
            import ctypes

            if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x12:  # type: ignore[attr-defined]
                return "ko"
    with contextlib.suppress(ValueError):
        candidates.append(locale.getlocale()[0] or "")
    return "ko" if any(c.lower().startswith("ko") for c in candidates if c) else "en"


class Steps:
    def __init__(self, out: TextIO, lang: str) -> None:
        self.out = out
        self.text = TEXT[lang]

    def say(self, key: str, **values: object) -> None:
        print(self.text[key].format(**values), file=self.out, flush=True)

    def step(self, key: str, **values: object) -> None:
        print(f"\n▶ {self.text[key].format(**values)}", file=self.out, flush=True)

    def bar(self, completed: int, total: int) -> None:
        if not total:
            return
        share = min(1.0, completed / total)
        filled = round(share * 24)
        print(
            f"\r  {'█' * filled}{'░' * (24 - filled)} {share * 100:3.0f}%"
            f"  {completed / 1e9:.1f} / {total / 1e9:.1f} GB",
            end="",
            file=self.out,
            flush=True,
        )


def run(config: Config, out: TextIO | None = None, open_app: bool = True) -> int:
    out = out or sys.stdout
    steps = Steps(out, language())
    steps.say("title")
    try:
        steps.step("ollama")
        if not models.ollama_installed() and not models.ollama_running(config.llm_host):
            steps.say("no_ollama")
            return 1
        if not models.start_ollama(config.llm_host):
            steps.say("ollama_off")
            return 1

        steps.step("llm", model=config.llm_model)
        last = [0.0]

        def progress(status: str, completed: int, total: int) -> None:
            if time.monotonic() - last[0] > 0.2 or completed == total:
                last[0] = time.monotonic()
                steps.bar(completed, total)

        models.pull_model(config.llm_host, config.llm_model, progress)
        print(f"\n  {steps.text['ok']}", file=out)

        if model_cached(config):
            steps.step("speech_ready")
        else:
            steps.step("speech")
            download_speech_model(config)

        if sys.platform == "darwin":
            steps.step("system_audio")
            try:
                systemaudio.prepare(config.home / "bin")
            except VechoError as exc:
                steps.say("system_audio_no", why=exc)

        steps.step("shortcut")
        place = shortcut.install()
        print(f"  {place}", file=out)
    except (VechoError, OSError, RuntimeError) as exc:
        print(file=out)
        steps.say("failed", error=exc)
        return 1

    print(file=out)
    steps.say("done")
    if sys.platform == "darwin":
        steps.say("open_mac")
    elif sys.platform == "win32":
        steps.say("open_windows")
    else:
        steps.say("open_other")
    steps.say("permission")
    if open_app:
        _open(place)
    return 0


def download_speech_model(config: Config) -> None:
    engine = make_transcriber(config)
    if isinstance(engine, MlxTranscriber):
        from huggingface_hub import snapshot_download

        snapshot_download(engine.repo)
    else:
        from faster_whisper.utils import download_model

        download_model(config.whisper_model)


def _open(place: object) -> None:
    with contextlib.suppress(OSError):
        if sys.platform == "darwin":
            subprocess.run(["open", str(place)], check=False)
        elif sys.platform == "win32":
            os.startfile(str(place))  # type: ignore[attr-defined]
