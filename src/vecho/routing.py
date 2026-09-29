"""System output routing, so capturing the other party needs no manual audio setup.

To record what comes out of the speakers, macOS must send it to a loopback device
(BlackHole) as well as to the speakers. That takes a "Multi-Output" device. The device
is created once by ``vecho setup``; while recording, :class:`OutputRouter` makes it the
system output and puts the previous output back afterwards. (Multi-Output devices have
no volume control, so it is not something to leave selected.)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from importlib import resources
from pathlib import Path
from types import TracebackType
from typing import Any

from .errors import AudioError

MULTI_OUTPUT_NAME = "vecho Multi-Output"
SWITCH_BINARY = "SwitchAudioSource"

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
        return [
            line.strip() for line in self._call("-a", "-t", "output").splitlines() if line.strip()
        ]

    def set(self, name: str) -> None:
        self._call("-t", "output", "-s", name)


def _helper_command(action: str, *extra: str) -> list[str]:
    swift = shutil.which("swift")
    if swift is None:
        raise AudioError(
            "`swift` is missing; install the Xcode command line tools: xcode-select --install"
        )
    script = resources.files("vecho").joinpath("resources", "multi_output.swift")
    return [swift, str(script), action, "--name", MULTI_OUTPUT_NAME, *extra]


def create_multi_output(output: str | None = None, run: Runner = run_command) -> str:
    """Create (or recreate) the Multi-Output device; returns a one-line description."""
    extra = ["--output", output] if output else []
    result = run(_helper_command("create", *extra), timeout=180)
    _, _, speakers, loopback = (result.strip().split("\t") + ["", "", "", ""])[:4]
    return f"{MULTI_OUTPUT_NAME}: {speakers} + {loopback}"


def remove_multi_output(run: Runner = run_command) -> bool:
    """Delete the Multi-Output device. Returns False if it did not exist."""
    return not run(_helper_command("destroy"), timeout=180).startswith("absent")


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
    ) -> None:
        self.state_file = state_file
        self.warn = warn
        self.enabled = enabled
        self.switcher = switcher or OutputSwitcher()
        self._previous: str | None = None

    def __enter__(self) -> OutputRouter:
        if self.enabled:
            self._engage()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._restore()

    def _stale_previous(self) -> str | None:
        """Output remembered by an earlier run that died before it could restore."""
        try:
            data: Any = json.loads(self.state_file.read_text(encoding="utf-8"))
            return str(data["previous"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _engage(self) -> None:
        switcher = self.switcher
        if not switcher.available:
            self.warn(f"{SWITCH_INSTALL_HELP}; system audio must be routed to BlackHole by hand")
            return
        try:
            outputs = switcher.outputs()
            current = switcher.current()
            if MULTI_OUTPUT_NAME not in outputs:
                self.warn("no Multi-Output device yet; run `vecho setup` once to create it")
                return
            if current == MULTI_OUTPUT_NAME:
                # Chosen on purpose, or a previous run died while it was active.
                self._previous = self._stale_previous()
                return
            self._write_state(current)
            switcher.set(MULTI_OUTPUT_NAME)
            self._previous = current
        except AudioError as exc:
            self.warn(f"could not route system audio: {exc}")

    def _restore(self) -> None:
        previous, self._previous = self._previous, None
        if previous is None:
            return
        try:
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
