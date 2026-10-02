"""Choosing and fetching AI models, and the setup steps around them."""

import json
import plistlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from vecho import models, shortcut
from vecho.config import Config


@pytest.mark.parametrize(
    ("memory", "model"),
    [
        (128, "qwen3.8:27b"),
        (32, "qwen3.8:27b"),
        (31.8, "qwen3.8:27b"),
        (16, "qwen3:8b"),
        (8, "qwen3:4b"),
        (0, "qwen3:4b"),
    ],
)
def test_the_summary_model_fits_the_computer(memory, model):
    assert models.default_llm_model(memory) == model


def test_auto_picks_a_model_and_a_named_one_is_kept(monkeypatch):
    monkeypatch.setattr(models, "total_memory_gb", lambda: 16.0)
    assert Config().llm_model == "qwen3:8b"
    assert Config(llm_model="auto").llm_model == "qwen3:8b"
    assert Config(llm_model="llama3:8b").llm_model == "llama3:8b"


def test_memory_is_read():
    assert models.total_memory_gb() > 0


def test_a_model_download_reports_progress():
    events = [
        {"status": "pulling manifest"},
        {"status": "pulling abc", "digest": "abc", "completed": 50, "total": 100},
        {"status": "pulling def", "digest": "def", "completed": 10, "total": 20},
        {"status": "pulling abc", "digest": "abc", "completed": 100, "total": 100},
        {"status": "success"},
    ]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = b"".join(json.dumps(e).encode() + b"\n" for e in events)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    seen = []
    try:
        models.pull_model(
            f"http://127.0.0.1:{server.server_port}", "qwen3:4b", lambda *a: seen.append(a)
        )
    finally:
        server.shutdown()
    # one figure for the whole model, however many files it is made of
    assert [(c, t) for _, c, t in seen[1:4]] == [(50, 100), (60, 120), (110, 120)]
    assert seen[-1] == ("success", 110, 120)


@pytest.mark.skipif(sys.platform == "win32", reason="a macOS app bundle")
def test_the_mac_app_asks_for_the_microphone_and_system_audio(tmp_path):
    bundle = shortcut._mac_app(tmp_path / "bin" / "vecho", tmp_path / "vecho.app")
    info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "vecho" and info["CFBundleIdentifier"]
    assert info["NSMicrophoneUsageDescription"] and info["NSAudioCaptureUsageDescription"]
    launcher = bundle / "Contents" / "MacOS" / "vecho"
    assert launcher.stat().st_mode & 0o111 and launcher.read_bytes()[:4] == b"\xca\xfe\xba\xbe"
    command = (bundle / "Contents" / "Resources" / "command").read_text()
    assert command == f"{tmp_path / 'bin' / 'vecho'}\napp\n"
    assert info["LSUIElement"] is True  # the launcher stays out of the Dock; vecho shows there
    assert (bundle / "Contents" / "Resources" / "vecho.icns").stat().st_size > 1000
    strings = (bundle / "Contents" / "Resources" / "ko.lproj" / "InfoPlist.strings").read_text(
        "utf-8"
    )
    assert "마이크" in strings


def test_the_linux_menu_entry_starts_the_app(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    entry = shortcut._linux_entry(tmp_path / "vecho", tmp_path)
    text = entry.read_text()
    assert f'Exec="{tmp_path / "vecho"}" app' in text and "Icon=" in text


def test_the_context_fits_the_computer_unless_set(monkeypatch):
    monkeypatch.setattr(models, "total_memory_gb", lambda: 8.0)
    small = Config()
    assert (small.llm_model, small.llm_num_ctx, small.chunk_chars) == ("qwen3:4b", 8192, 4000)
    chosen = Config(llm_model="qwen3.8:27b", llm_num_ctx=32768)
    assert (chosen.llm_num_ctx, chosen.chunk_chars) == (32768, 4000)
    assert Config(llm_num_ctx=6000).chunk_chars == 3000  # never more than half the context


@pytest.mark.skipif(sys.platform == "win32", reason="a macOS app bundle")
def test_an_old_copy_is_replaced_but_nothing_else_is_touched(tmp_path, monkeypatch):
    shared, personal = tmp_path / "Applications" / "vecho.app", tmp_path / "home" / "vecho.app"
    monkeypatch.setattr(shortcut, "mac_app_locations", lambda home=None: [shared, personal])
    shortcut._mac_app(tmp_path / "vecho", personal)  # an older install in the home folder
    shortcut._mac_app(tmp_path / "vecho", tmp_path / "elsewhere" / "vecho.app")
    assert personal.exists()  # an app made anywhere else (like this test) leaves it alone
    shortcut._mac_app(tmp_path / "vecho", shared)
    assert shared.exists() and not personal.exists()  # one vecho in Applications, not two
