"""System output routing, so capturing the other party needs no manual audio setup.

To record what comes out of the speakers, macOS must send it to a loopback device
(BlackHole) as well as to whatever the user listens on. That takes a "Multi-Output"
device, which is bound to one physical output. So :class:`OutputRouter` builds it around
the *current* system output when recording starts (speakers, AirPods, a USB headset, a
monitor...), selects it, and puts the previous output back afterwards. While recording it
also watches for the output changing (headphones connected mid-call) and follows.
Multi-Output devices have no volume control, so it is never left selected.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from importlib import resources
from pathlib import Path
from types import TracebackType
from typing import Any

from .errors import AudioError

MULTI_OUTPUT_NAME = "vecho Multi-Output"
SWITCH_BINARY = "SwitchAudioSource"

# Outputs that cannot be stacked with a loopback device (they are virtual themselves).
VIRTUAL_OUTPUT_HINTS = ("blackhole", "multi-output", "loopback audio", "soundflower", "zoomaudio")

POLL_SEC = 2.0

Runner = Callable[..., str]

SWITCH_INSTALL_HELP = "SwitchAudioSource is not installed; run `brew install switchaudio-osx`"


def run_command(command: Sequence[str], timeout: float = 30.0) -> str:
    try:
        proc = subprocess.run(list(command), capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise AudioError(f"{command[0]} was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError(f"{command[0]} did not finish within {timeout:.0f}s") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"exit code {proc.returncode}"
        raise AudioError(f"{Path(command[0]).name} failed: {detail}")
    return proc.stdout


def is_virtual_output(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in VIRTUAL_OUTPUT_HINTS)


class OutputSwitcher:
    """Reads and changes the system output device through ``SwitchAudioSource``."""

    def __init__(self, run: Runner = run_command, binary: str | None = None) -> None:
        self._run = run
        self.binary = binary or shutil.which(SWITCH_BINARY)

    @property
    def available(self) -> bool:
        return self.binary is not None

    def _call(self, *args: str) -> str:
        if not self.binary:
            raise AudioError(SWITCH_INSTALL_HELP)
        return self._run([self.binary, *args])

    def current(self) -> str:
        return self._call("-c", "-t", "output").strip()

    def outputs(self) -> list[str]:
        listing = self._call("-a", "-t", "output")
        return [line.strip() for line in listing.splitlines() if line.strip()]

    def set(self, name: str) -> None:
        self._call("-t", "output", "-s", name)


class MultiOutput:
    """The stacked device (real output + loopback), managed by a small CoreAudio helper."""

    def __init__(self, run: Runner = run_command) -> None:
        self._run = run

    def _helper(self, action: str, *extra: str) -> list[str]:
        swift = shutil.which("swift")
        if swift is None:
            raise AudioError(
                "`swift` is missing; install the Xcode command line tools: xcode-select --install"
            )
        script = resources.files("vecho").joinpath("resources", "multi_output.swift")
        return [swift, str(script), action, "--name", MULTI_OUTPUT_NAME, *extra]

    def main_device(self) -> str | None:
        """Name of the output the existing device plays through, or None if it does not exist."""
        fields = self._run(self._helper("describe"), timeout=120).strip().split("\t")
        return fields[1] if fields[0] == "main" and len(fields) > 1 else None

    def create(self, output: str | None = None) -> str:
        """Create (or rebuild) the device around ``output``; returns a one-line description."""
        extra = ["--output", output] if output else []
        result = self._run(self._helper("create", *extra), timeout=180)
        _, _, speakers, loopback = (result.strip().split("\t") + ["", "", "", ""])[:4]
        return f"{MULTI_OUTPUT_NAME}: {speakers} + {loopback}"

    def remove(self) -> bool:
        """Delete the device. Returns False if it did not exist."""
        return not self._run(self._helper("destroy"), timeout=120).startswith("absent")


class OutputRouter:
    """Context manager: route system output through Multi-Output while recording.

    Never raises: routing is a convenience, and the recording itself must go ahead even if
    it cannot be arranged. Problems are reported through ``warn`` instead.
    """

    def __init__(
        self,
        state_file: Path,
        warn: Callable[[str], None],
        enabled: bool = True,
        switcher: OutputSwitcher | None = None,
        multi: MultiOutput | None = None,
        poll_sec: float = POLL_SEC,
    ) -> None:
        self.state_file = state_file
        self.warn = warn
        self.enabled = enabled
        self.switcher = switcher or OutputSwitcher()
        self.multi = multi or MultiOutput()
        self.poll_sec = poll_sec
        self._previous: str | None = None  # the real output to give back at the end
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self._gave_up_on: str | None = None

    def __enter__(self) -> OutputRouter:
        if self.enabled:
            self._engage()
            if self._previous is not None:
                self._watcher = threading.Thread(
                    target=self._watch, name="vecho-output-watch", daemon=True
                )
                self._watcher.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._watcher is not None:
            self._watcher.join()
        self._restore()

    # -- routing ------------------------------------------------------------------------

    def _route(self, current: str) -> None:
        """Rebuild Multi-Output around ``current`` if needed, then select it."""
        if self.multi.main_device() != current:
            self.multi.create(current)
            self._wait_until_listed()
        self.switcher.set(MULTI_OUTPUT_NAME)

    def _wait_until_listed(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while MULTI_OUTPUT_NAME not in self.switcher.outputs():
            if time.monotonic() > deadline:
                raise AudioError("macOS did not list the new Multi-Output device")
            time.sleep(0.1)

    def _follow(self, current: str) -> bool:
        """Route from ``current`` and remember it for restoring; False if that is impossible."""
        if is_virtual_output(current):
            self.warn(
                f"the sound output '{current}' is virtual, so system audio cannot be captured; "
                "choose your speakers or headphones"
            )
            return False
        self._write_state(current)
        try:
            self._route(current)
        except AudioError as exc:
            self.state_file.unlink(missing_ok=True)
            self.warn(f"could not capture audio played through '{current}': {exc}")
            return False
        self._previous = current
        return True

    def _engage(self) -> None:
        if not self.switcher.available:
            self.warn(f"{SWITCH_INSTALL_HELP}; system audio must be routed to BlackHole by hand")
            return
        try:
            current = self.switcher.current()
        except AudioError as exc:
            self.warn(f"could not read the sound output: {exc}")
            return
        if current == MULTI_OUTPUT_NAME:
            # Chosen on purpose, or a previous run died while it was active.
            self._previous = self._stale_previous()
            return
        self._follow(current)

    def _watch(self) -> None:
        """Follow the output if it changes mid-recording (e.g. AirPods connect)."""
        while not self._stop.wait(self.poll_sec):
            try:
                current = self.switcher.current()
            except AudioError:
                continue
            if current == MULTI_OUTPUT_NAME or current == self._gave_up_on:
                continue
            with self._lock:
                if self._stop.is_set():
                    return
                if not self._follow(current):
                    self._gave_up_on = current  # do not retry (and warn) every poll

    # -- restoring ----------------------------------------------------------------------

    def _stale_previous(self) -> str | None:
        """Output remembered by an earlier run that died before it could restore."""
        try:
            data: Any = json.loads(self.state_file.read_text(encoding="utf-8"))
            return str(data["previous"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _restore(self) -> None:
        previous, self._previous = self._previous, None
        if previous is None:
            return
        try:
            if self.switcher.current() != MULTI_OUTPUT_NAME:
                # The user (or a failed follow-up) already moved the output; do not override it.
                self.state_file.unlink(missing_ok=True)
                return
            if previous in self.switcher.outputs():
                self.switcher.set(previous)
            else:
                self.warn(f"'{previous}' is no longer available; pick an output in the sound menu")
        except AudioError as exc:
            self.warn(f"could not restore the sound output; select '{previous}' manually ({exc})")
            return
        self.state_file.unlink(missing_ok=True)

    def _write_state(self, previous: str) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"previous": previous}), encoding="utf-8")
