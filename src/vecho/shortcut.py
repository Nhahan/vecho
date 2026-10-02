"""A double-clickable way to start vecho: an app in Applications (macOS), Start menu and
desktop shortcuts (Windows), or a menu entry (Linux)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

from .errors import VechoError

BUNDLE_ID = "io.github.nhahan.vecho"


def _resource(*parts: str) -> bytes:
    return resources.files("vecho").joinpath("resources", *parts).read_bytes()


def _icon(name: str) -> bytes:
    return _resource("icon", name)


def vecho_executable() -> Path:
    """The installed ``vecho`` command (the one running now)."""
    found = shutil.which("vecho")
    if found:
        return Path(found)
    return Path(sys.argv[0]).resolve()


def install(executable: Path | None = None, home: Path | None = None) -> Path:
    """Create the shortcut and return where it is."""
    executable = executable or vecho_executable()
    home = home or Path.home()
    if sys.platform == "darwin":
        return _mac_app(executable, home / "Applications" / "vecho.app")
    if sys.platform == "win32":
        return _windows_shortcuts(executable)
    return _linux_entry(executable, home)


def _mac_app(executable: Path, bundle: Path) -> Path:
    contents = bundle / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    (contents / "Resources").mkdir(parents=True, exist_ok=True)
    # The usage descriptions are what macOS shows when asking for the microphone and for
    # the computer's own sound; without them it would refuse without asking.
    (contents / "Info.plist").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleName</key><string>vecho</string>
  <key>CFBundleDisplayName</key><string>vecho</string>
  <key>CFBundleIdentifier</key><string>{BUNDLE_ID}</string>
  <key>CFBundleExecutable</key><string>vecho</string>
  <key>CFBundleIconFile</key><string>vecho</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Recording your side of the conversation needs the microphone.</string>
  <key>NSAudioCaptureUsageDescription</key>
  <string>Recording the other side of the conversation needs the sound your Mac plays.</string>
</dict>
</plist>
""",
        encoding="utf-8",
    )
    # A real program (not a script) that keeps running and starts vecho as its child: macOS
    # then asks for permissions in the name of "vecho" (see resources/launcher.swift).
    launcher = contents / "MacOS" / "vecho"
    launcher.write_bytes(_resource("bin", "launcher"))
    launcher.chmod(0o755)
    (contents / "Resources" / "command").write_text(f"{executable}\napp\n", encoding="utf-8")
    (contents / "Resources" / "vecho.icns").write_bytes(_icon("vecho.icns"))
    korean = contents / "Resources" / "ko.lproj"
    korean.mkdir(exist_ok=True)
    (korean / "InfoPlist.strings").write_text(
        '"NSMicrophoneUsageDescription" = "내 목소리를 녹음하려면 마이크가 필요합니다.";\n'
        '"NSAudioCaptureUsageDescription" = '
        '"상대방 목소리를 녹음하려면 Mac에서 나는 소리를 들을 수 있어야 합니다.";\n',
        encoding="utf-8",
    )
    # Let Finder, Launchpad and Spotlight notice the app (and its icon) right away.
    lsregister = (
        "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
        "/Support/lsregister"
    )
    if Path(lsregister).exists():
        subprocess.run([lsregister, "-f", str(bundle)], check=False, capture_output=True)
    # Sign the whole app (ad hoc, nothing to buy) so macOS can tie permissions to it.
    if Path("/usr/bin/codesign").exists():
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", str(bundle)],
            check=False,
            capture_output=True,
        )
    bundle.touch()
    return bundle


def _windows_shortcuts(executable: Path) -> Path:
    gui = executable.with_name("vecho-app.exe")  # starts without a console window
    target, arguments = (gui, "") if gui.exists() else (executable, "app")
    icon = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "vecho" / "vecho.ico"
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(_icon("vecho.ico"))
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    start_menu = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "vecho.lnk"
    desktop = Path.home() / "Desktop" / "vecho.lnk"
    for link in (start_menu, desktop):
        if not link.parent.is_dir():
            continue
        script = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:VECHO_LINK);"
            "$s.TargetPath = $env:VECHO_TARGET; $s.Arguments = $env:VECHO_ARGS;"
            "$s.IconLocation = $env:VECHO_ICON; $s.Description = 'vecho'; $s.Save()"
        )
        env = dict(
            os.environ,
            VECHO_LINK=str(link),
            VECHO_TARGET=str(target),
            VECHO_ARGS=arguments,
            VECHO_ICON=str(icon),
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise VechoError(f"cannot create the shortcut: {result.stderr.strip()[-300:]}")
    return start_menu


def _linux_entry(executable: Path, home: Path) -> Path:
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    icon = data / "icons" / "hicolor" / "512x512" / "apps" / "vecho.png"
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(_icon("vecho.png"))
    entry = data / "applications" / "vecho.desktop"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=vecho\n"
        "Comment=Record, transcribe and summarize conversations\n"
        f'Exec="{executable}" app\n'
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;Office;\n",
        encoding="utf-8",
    )
    return entry
