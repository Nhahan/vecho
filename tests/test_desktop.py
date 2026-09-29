import io
import json
import threading
import time

from vecho import desktop


def test_second_launch_reuses_the_running_instance(config, monkeypatch):
    opened = []
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)
    out = io.StringIO()
    stop = threading.Event()
    thread = threading.Thread(target=desktop.run, args=(config, 0, "none", out, stop), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while desktop.running_instance(config) is None and time.monotonic() < deadline:
        time.sleep(0.02)
    url = desktop.running_instance(config)
    assert url and url.startswith("http://127.0.0.1:")

    info_file = config.home / "app.json"
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
