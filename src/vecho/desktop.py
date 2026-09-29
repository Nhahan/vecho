"""``vecho app``: run the local web app and show it in a window or the default browser.

Only one instance runs at a time: a second ``vecho app`` just shows the running one.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import signal
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from .config import Config
from .server import App, VechoHTTPServer

_STOP_SIGNALS = {signal.SIGINT, signal.SIGTERM}

WINDOW = "window"
BROWSER = "browser"
HEADLESS = "none"


def _instance_file(config: Config) -> Path:
    return config.home / "app.json"


def running_instance(config: Config) -> str | None:
    """URL of an app that is already running for this data directory, if any."""
    try:
        info: dict[str, Any] = json.loads(_instance_file(config).read_text("utf-8"))
        request = urllib.request.Request(
            info["url"] + "api/state", headers={"X-Vecho-Token": info["token"]}
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            if response.status == 200:
                return str(info["url"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _write_instance(config: Config, url: str, token: str) -> None:
    path = _instance_file(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # holds the API token
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)  # also when an older, more readable file already existed
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"url": url, "token": token, "pid": os.getpid()}, handle)


def _clear_instance(config: Config, token: str) -> None:
    path = _instance_file(config)
    with contextlib.suppress(OSError, ValueError, KeyError):
        if json.loads(path.read_text("utf-8"))["token"] == token:
            path.unlink()


def _instance_lock(config: Config) -> Any:
    """An exclusive lock on the data directory held for the app's lifetime, or None if taken.

    Checking app.json alone is racy: two launches at the same moment would both start.
    """
    path = config.home / "app.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")  # noqa: SIM115 - kept open (and locked) until the app exits
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def window_available() -> bool:
    return importlib.util.find_spec("webview") is not None


def _show_window(url: str) -> None:
    import webview  # pywebview: native window on macOS, Windows and Linux

    with contextlib.suppress(Exception):
        webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window("vecho", url, width=1180, height=800, min_size=(720, 520))
    if hasattr(signal, "sigwait"):
        # The GUI event loop never returns to Python, so a normal signal handler would not run.
        # The signals are blocked in every thread (see run()); this thread receives them and
        # closes the window, which leads to the normal shutdown that saves a recording.
        def close_on_signal() -> None:
            signal.sigwait(_STOP_SIGNALS)
            window.destroy()

        threading.Thread(target=close_on_signal, name="vecho-signals", daemon=True).start()
    webview.start()  # returns when the window is closed


def _wait_for_signal(stop: threading.Event) -> None:
    if threading.current_thread() is not threading.main_thread():
        stop.wait()  # signal handlers only work on the main thread
        return
    previous = {
        sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        while not stop.wait(0.5):
            pass
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run(
    config: Config,
    port: int = 0,
    mode: str = "auto",
    out: Any = None,
    stop: threading.Event | None = None,
) -> int:
    out = out or sys.stderr
    stop = stop or threading.Event()
    lock = _instance_lock(config)
    if lock is None:  # another vecho app owns this data directory
        existing = None
        for _ in range(50):  # it may still be starting up
            existing = running_instance(config)
            if existing:
                break
            time.sleep(0.1)
        print(f"vecho is already running at {existing or 'another window'}", file=out)
        if existing and mode != HEADLESS:
            webbrowser.open(existing)
        return 0

    if mode == "auto":
        mode = WINDOW if window_available() else BROWSER
    if mode == WINDOW and hasattr(signal, "pthread_sigmask"):
        # Before any thread starts, so that every thread inherits the mask (see _show_window).
        signal.pthread_sigmask(signal.SIG_BLOCK, _STOP_SIGNALS)

    app = App(config)
    server = VechoHTTPServer(app, port)
    thread = threading.Thread(target=server.serve_forever, name="vecho-http", daemon=True)
    thread.start()
    _write_instance(config, server.url, app.token)
    print(f"vecho is running at {server.url}", file=out, flush=True)

    try:
        if mode == WINDOW:
            try:
                _show_window(server.url)
            finally:
                if hasattr(signal, "pthread_sigmask"):  # Ctrl+C works again while shutting down
                    signal.pthread_sigmask(signal.SIG_UNBLOCK, _STOP_SIGNALS)
        else:
            if mode == BROWSER:
                webbrowser.open(server.url)
                print("Close with Ctrl+C.", file=out, flush=True)
            _wait_for_signal(stop)
    finally:
        app.shutdown()  # saves a recording that is still running
        server.shutdown()
        server.server_close()
        _clear_instance(config, app.token)
        lock.close()
    return 0
