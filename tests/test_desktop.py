import io
import json
import sys
import threading
import time

import pytest

from vecho import desktop


def test_second_launch_reuses_the_running_instance(config, monkeypatch):
    opened = []
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)
    out = io.StringIO()
    stop = threading.Event()
    thread = threading.Thread(target=desktop.run, args=(config, 0, "none", out, stop), daemon=True)
    thread.start()
    deadline = time.monotonic() + 30  # a cold, slow machine takes a while
    while desktop.running_instance(config) is None and time.monotonic() < deadline:
        time.sleep(0.05)
    url = desktop.running_instance(config)
    files = sorted(p.name for p in config.home.iterdir()) if config.home.is_dir() else []
    assert url and url.startswith("http://127.0.0.1:"), (out.getvalue(), thread.is_alive(), files)

    info_file = config.home / "app.json"
    if sys.platform != "win32":  # Windows has no such permission bits
        assert oct(info_file.stat().st_mode & 0o777) == "0o600"  # it holds the API token

    second = io.StringIO()
    assert desktop.run(config, 0, "browser", second) == 0
    assert "already running" in second.getvalue() and opened == [url]

    stop.set()  # like Ctrl+C for the headless first instance
    thread.join(5)
    assert not thread.is_alive()
    assert not info_file.exists()
    assert desktop.running_instance(config) is None


def test_stale_instance_file_is_ignored(config):
    config.home.mkdir(parents=True)
    (config.home / "app.json").write_text(
        json.dumps({"url": "http://127.0.0.1:9/", "token": "x", "pid": 1}), "utf-8"
    )
    assert desktop.running_instance(config) is None


def test_simultaneous_launches_start_only_one_app(config, monkeypatch):
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: None)
    stop = threading.Event()
    first = threading.Thread(target=desktop.run, args=(config, 0, "none", io.StringIO(), stop))
    first.start()
    deadline = time.monotonic() + 10
    while desktop.running_instance(config) is None and time.monotonic() < deadline:
        time.sleep(0.02)
    outputs = [io.StringIO() for _ in range(3)]  # these three race each other
    others = [
        threading.Thread(target=desktop.run, args=(config, 0, "none", out, threading.Event()))
        for out in outputs
    ]
    for t in others:
        t.start()
    for t in others:
        t.join(10)
    assert all("already running" in out.getvalue() for out in outputs)
    stop.set()
    first.join(5)
    lock = desktop._instance_lock(config)  # released on exit
    assert lock is not None
    lock.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_stop_signal_closes_the_window_and_helpers_can_still_be_stopped():
    import os
    import signal
    import subprocess
    import threading

    closed = threading.Event()
    window = type("Window", (), {"destroy": lambda self: closed.set()})()
    restore = desktop._close_on_signal(window)
    try:
        helper = subprocess.Popen(["sleep", "30"])  # started while the app runs
        os.kill(os.getpid(), signal.SIGTERM)
        assert closed.wait(5)
        helper.terminate()  # not ignored: the stop signals are not blocked for children
        assert helper.wait(5) == -signal.SIGTERM
    finally:
        restore()
