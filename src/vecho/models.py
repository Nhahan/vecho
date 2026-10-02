"""The AI models vecho needs, chosen and fetched for the user.

Nobody should have to know a model name: the summary model is picked from the computer's
memory, Ollama is started when it is installed but not running, and models are downloaded
with progress reports.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

# (minimum memory in GB, Ollama model, context tokens, transcript characters per prompt):
# the largest model the computer runs comfortably next to speech recognition, with a context
# window whose memory it can also afford. Qwen models write Korean well at every size.
LLM_TIERS = (
    (32, "qwen3.8:27b", 32768, 16000),  # ~18 GB
    (16, "qwen3:8b", 16384, 8000),  # ~5 GB
    (0, "qwen3:4b", 8192, 4000),  # ~2.5 GB
)

PullProgress = Callable[[str, int, int], None]  # (status, completed bytes, total bytes)


def total_memory_gb() -> float:
    """Installed memory in GB (0 when it cannot be read)."""
    try:
        if sys.platform == "win32":

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
            return status.ullTotalPhys / 1024**3
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (AttributeError, OSError, ValueError):
        return 0.0


def llm_tier(memory_gb: float | None = None) -> tuple[int, str, int, int]:
    memory = total_memory_gb() if memory_gb is None else memory_gb
    # a little slack: "32 GB" machines report slightly less
    return next(tier for tier in LLM_TIERS if memory >= tier[0] * 0.9)


def default_llm_model(memory_gb: float | None = None) -> str:
    return llm_tier(memory_gb)[1]


# -- Ollama ------------------------------------------------------------------------------


def ollama_app() -> Path | None:
    """The installed Ollama desktop app (which starts the server), if any."""
    if sys.platform == "darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            if (root / "Ollama.app").exists():
                return root / "Ollama.app"
    elif sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        candidate = local / "Programs" / "Ollama" / "ollama app.exe"
        if candidate.exists():
            return candidate
    return None


def ollama_installed() -> bool:
    return ollama_app() is not None or shutil.which("ollama") is not None


def ollama_running(host: str) -> bool:
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/version", timeout=2):
            return True
    except (OSError, ValueError):
        return False


def start_ollama(host: str, wait: float = 30.0) -> bool:
    """Start Ollama if it is installed but not running; True once it answers."""
    if ollama_running(host):
        return True
    app = ollama_app()
    with contextlib.suppress(OSError):
        if app is not None and sys.platform == "darwin":
            subprocess.run(["open", "-g", "-a", str(app)], check=False, timeout=10)
        elif app is not None and sys.platform == "win32":
            subprocess.Popen([str(app)], close_fds=True)
        elif shutil.which("ollama"):
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        else:
            return False
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if ollama_running(host):
            return True
        time.sleep(0.5)
    return False


def pull_model(host: str, model: str, on_progress: PullProgress | None = None) -> None:
    """Download an Ollama model (a no-op when it is already there), reporting progress."""
    request = urllib.request.Request(
        host.rstrip("/") + "/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    parts: dict[str, tuple[int, int]] = {}  # a model is several files: report them as one
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            for line in response:
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise RuntimeError(event["error"])
                if event.get("digest") and event.get("total"):
                    parts[event["digest"]] = (int(event.get("completed") or 0), int(event["total"]))
                if on_progress:
                    on_progress(
                        str(event.get("status", "")),
                        sum(done for done, _ in parts.values()),
                        sum(size for _, size in parts.values()),
                    )
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Ollama answered HTTP {exc.code}") from exc
