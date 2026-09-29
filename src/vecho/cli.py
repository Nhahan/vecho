"""Command-line interface: ``vecho app | record | import | transcribe | summarize | ...``."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import math
import shutil
import signal
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

from . import __version__, audio, recording, roles, systemaudio
from .config import Config, load_config
from .errors import SessionError, VechoError
from .session import SUMMARY_MD, TRANSCRIPT_MD, Session, SessionStore
from .summarize import OllamaClient, summarize_session
from .transcribe import engine_label, transcribe_session
from .transcript import format_duration


def _eprint(*parts: object) -> None:
    print(*parts, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- recording


def _meter(level: float, width: int = 8) -> str:
    filled = round(min(1.0, math.sqrt(level * 4)) * width)
    return "█" * filled + "░" * (width - filled)


@contextlib.contextmanager
def _stop_on_signal(event: threading.Event) -> Iterator[None]:
    """Turn Ctrl+C / SIGTERM into a clean stop request instead of an exception."""
    previous = {
        sig: signal.signal(sig, lambda signum, frame: event.set())
        for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _wait_for_stop(recorder: audio.Recorder, config: Config) -> None:
    stop = threading.Event()
    live = sys.stderr.isatty()
    with _stop_on_signal(stop):
        while not stop.wait(0.2):
            if not live:
                continue
            levels = recorder.levels()
            meters = "  ".join(
                f"{config.label_for(role)} {_meter(level)}" for role, level in levels.items()
            )
            print(
                f"\r● REC {format_duration(recorder.elapsed)}  {meters}\x1b[K",
                end="",
                file=sys.stderr,
                flush=True,
            )
    if live:
        print(file=sys.stderr)


def cmd_record(args: argparse.Namespace, config: Config) -> int:
    config = _overrides(config, args)
    live = recording.LiveRecording(
        config,
        title=args.title or "",
        mic=args.mic,
        remote=args.remote,
        mic_only=args.mic_only,
    )
    live.start()
    try:
        _eprint(f"Recording to {live.session.dir}")
        for role, source in live.sources:
            _eprint(f"  {config.label_for(role)}: {recording.describe_source(source)}")
        _eprint("Press Ctrl+C to stop.")
        _wait_for_stop(live.recorder, config)
    except BaseException:
        # Finalize the WAV files even when interrupted before the stop handler was installed.
        live.abort()
        raise
    result = live.stop()
    session = result.session

    _eprint(f"Saved {format_duration(session.meta.duration_sec)} of audio.")
    for warning in result.warnings:
        _eprint(f"warning: {warning}")
    if result.too_short:
        _eprint("The recording is too short to process; the audio was kept.")
        return 0
    if args.no_process:
        _eprint(f"Next: vecho transcribe {session.id} && vecho summarize {session.id}")
        return 0
    return _process(session, config)


# ---------------------------------------------------------------------- processing


def _run_transcribe(session: Session, config: Config) -> None:
    _eprint(f"Transcribing with {config.whisper_model} (the first run downloads the model)...")
    live = sys.stderr.isatty()

    def report(role: str, position: float, total: float) -> None:
        if live:
            percent = f"{min(100.0, position / total * 100):3.0f}%" if total else "..."
            print(f"\r  {config.label_for(role) or role}: {percent}\x1b[K", end="", file=sys.stderr)

    segments = transcribe_session(session, config, on_progress=report)
    if live:
        print(file=sys.stderr)
    _eprint(f"Transcribed {len(segments)} segments -> {session.path_for(TRANSCRIPT_MD)}")
    if not segments:
        _eprint("warning: no speech was detected.")


def _run_summarize(session: Session, config: Config) -> str:
    _eprint(f"Summarizing with {config.llm_model}...")

    def report(stage: str, step: int, total: int) -> None:
        _eprint(f"  {stage}: {step}/{total}")

    markdown = summarize_session(session, config, on_progress=report)
    _eprint(f"Summary -> {session.path_for(SUMMARY_MD)}")
    return markdown


def _process(session: Session, config: Config) -> int:
    """Transcribe then summarize; the recording is already safe if this fails."""
    try:
        _run_transcribe(session, config)
    except VechoError as exc:
        _eprint(f"error: {exc}")
        _eprint(f"The audio is saved. Retry with: vecho transcribe {session.id}")
        return 1
    try:
        markdown = _run_summarize(session, config)
    except VechoError as exc:
        _eprint(f"error: {exc}")
        _eprint(f"The transcript is saved. Retry with: vecho summarize {session.id}")
        return 1
    print(markdown)
    return 0


def _overrides(config: Config, args: argparse.Namespace) -> Config:
    return config.with_overrides(
        whisper_model=getattr(args, "model", None),
        language=getattr(args, "language", None),
        llm_model=getattr(args, "llm_model", None),
        summary_language=getattr(args, "summary_language", None),
    )


def cmd_transcribe(args: argparse.Namespace, config: Config) -> int:
    config = _overrides(config, args)
    session = SessionStore(config.sessions_dir).resolve(args.session)
    _run_transcribe(session, config)
    return 0


def cmd_summarize(args: argparse.Namespace, config: Config) -> int:
    config = _overrides(config, args)
    session = SessionStore(config.sessions_dir).resolve(args.session)
    print(_run_summarize(session, config))
    return 0


def cmd_import(args: argparse.Namespace, config: Config) -> int:
    config = _overrides(config, args)
    sources = {
        roles.ME: args.me,
        roles.REMOTE: args.remote,
        roles.MIXED: args.mixed,
    }
    sources = {role: Path(path).expanduser() for role, path in sources.items() if path}
    if not sources:
        raise SessionError("give at least one of --me, --remote or --mixed")
    for path in sources.values():
        if not path.is_file():
            raise SessionError(f"audio file not found: {path}")

    title = args.title or next(iter(sources.values())).stem
    session = SessionStore(config.sessions_dir).create(title)
    for role, path in sources.items():
        name = f"{role}{path.suffix.lower()}"
        shutil.copy2(path, session.path_for(name))
        session.meta.tracks[role] = name
    session.save()
    _eprint(f"Imported into {session.dir}")

    if args.no_process:
        _eprint(f"Next: vecho transcribe {session.id} && vecho summarize {session.id}")
        return 0
    return _process(session, config)


# ------------------------------------------------------------------------ browsing


def cmd_list(args: argparse.Namespace, config: Config) -> int:
    sessions = SessionStore(config.sessions_dir).list()
    if not sessions:
        print("No sessions yet. Start one with `vecho record`.")
        return 0
    print(f"{'ID':<34} {'DURATION':<9} {'STATE':<12} TITLE")
    for session in sessions:
        meta = session.meta
        state = (
            "summarized"
            if session.has_summary
            else "transcribed"
            if session.has_transcript
            else "recorded"
        )
        print(f"{session.id:<34} {format_duration(meta.duration_sec):<9} {state:<12} {meta.title}")
    return 0


def cmd_show(args: argparse.Namespace, config: Config) -> int:
    session = SessionStore(config.sessions_dir).resolve(args.session)
    if args.path:
        print(session.dir)
        return 0
    name, hint = (
        (TRANSCRIPT_MD, "vecho transcribe") if args.transcript else (SUMMARY_MD, "vecho summarize")
    )
    path = session.path_for(name)
    if not path.is_file():
        raise SessionError(f"session {session.id} has no {name}; run `{hint} {session.id}`")
    print(path.read_text(encoding="utf-8"), end="")
    return 0


# ------------------------------------------------------------------------ diagnosis


def cmd_devices(args: argparse.Namespace, config: Config) -> int:
    devices = audio.list_input_devices()
    if systemaudio.is_supported():
        name = systemaudio.SYSTEM_AUDIO_NAME
        print(f"  -  {name:<36} (default for the other party; --remote system)")
    if not devices:
        print("No audio input devices found.")
        return 1
    for device in devices:
        note = "  (default)" if device.is_default else ""
        print(
            f"{device.index:>3}  {device.name:<36} {device.channels}ch "
            f"{device.default_samplerate:.0f}Hz{note}"
        )
    return 0


def _doctor_remote(report: Callable[[str, str], None], config: Config) -> None:
    """Whether the other party (the system audio) can be captured."""
    try:
        systemaudio.prepare(config.home / "bin")
    except VechoError as exc:
        report("FAIL", f"system audio capture unavailable: {exc} ({systemaudio.install_hint()})")
        return
    report("OK", "system audio capture: built in (no driver or setup needed)")
    report(
        "INFO",
        "if the other party's track is silent, allow your terminal in System Settings > "
        "Privacy & Security > Screen & System Audio Recording",
    )


def cmd_doctor(args: argparse.Namespace, config: Config) -> int:
    failures = 0

    def report(status: str, message: str) -> None:
        nonlocal failures
        failures += status == "FAIL"
        print(f"[{status:>4}] {message}")

    try:
        devices = audio.list_input_devices()
    except VechoError as exc:
        devices = []
        report("FAIL", str(exc))
    else:
        mic = next((d for d in devices if d.is_default), None)
        if mic:
            report("OK", f"microphone: {mic.name}")
        else:
            report("FAIL", "no default microphone (check macOS microphone permission)")
        _doctor_remote(report, config)

    if importlib.util.find_spec("faster_whisper"):
        report("OK", f"speech recognition: {engine_label(config)}")
    else:
        report("FAIL", "faster-whisper is not installed")

    client = OllamaClient(config.llm_host, config.llm_model, timeout=5)
    try:
        if client.has_model():
            report("OK", f"Ollama at {config.llm_host} has '{config.llm_model}'")
        else:
            report("FAIL", f"Ollama lacks the model; run `ollama pull {config.llm_model}`")
    except VechoError as exc:
        report("FAIL", str(exc))

    report("OK", f"data directory: {config.home}")
    return 1 if failures else 0


# ---------------------------------------------------------------------------- parser


def _add_transcribe_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", help="Whisper model name (default: large-v3-turbo)")
    parser.add_argument("--language", help="spoken language code such as ko or en (default: auto)")


def _add_summarize_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--llm-model", help="Ollama model used for the summary")
    parser.add_argument("--summary-language", help="language of the summary (default: Korean)")


def _add_pipeline_options(parser: argparse.ArgumentParser) -> None:
    _add_transcribe_options(parser)
    _add_summarize_options(parser)
    parser.add_argument(
        "--no-process", action="store_true", help="only save audio; skip transcription and summary"
    )


def cmd_app(args: argparse.Namespace, config: Config) -> int:
    from . import desktop

    mode = "none" if args.no_open else "browser" if args.browser else "auto"
    return desktop.run(config, port=args.port, mode=mode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vecho",
        description="Record two-way voice conversations, then transcribe and summarize them "
        "locally.",
    )
    parser.add_argument("--version", action="version", version=f"vecho {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    def add(name: str, func: object, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, description=help_text)
        sub.set_defaults(func=func)
        return sub

    app = add("app", cmd_app, "open the vecho app (a window, or your web browser)")
    app.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free)")
    app.add_argument("--browser", action="store_true", help="use the web browser, not a window")
    app.add_argument("--no-open", action="store_true", help="only run the server")

    record = add("record", cmd_record, "record microphone + system audio until Ctrl+C")
    record.add_argument("-t", "--title", help="session title")
    record.add_argument("--mic", help="microphone device index or name (default: system default)")
    record.add_argument(
        "--remote",
        help="how to capture the other party: 'system' (default: everything the computer plays) "
        "or an input device index/name",
    )
    record.add_argument("--mic-only", action="store_true", help="record only the microphone")
    _add_pipeline_options(record)

    imp = add("import", cmd_import, "create a session from existing audio files")
    imp.add_argument("--me", help="audio file with your side of the conversation")
    imp.add_argument("--remote", help="audio file with the other party")
    imp.add_argument("--mixed", help="a single file where both sides are already mixed")
    imp.add_argument("-t", "--title", help="session title (default: first file name)")
    _add_pipeline_options(imp)

    transcribe = add("transcribe", cmd_transcribe, "transcribe a recorded session")
    transcribe.add_argument("session", nargs="?", default="latest", help="id, prefix or 'latest'")
    _add_transcribe_options(transcribe)

    summarize = add("summarize", cmd_summarize, "summarize a transcribed session")
    summarize.add_argument("session", nargs="?", default="latest", help="id, prefix or 'latest'")
    _add_summarize_options(summarize)

    add("list", cmd_list, "list recorded sessions")

    show = add("show", cmd_show, "print a session's summary (or transcript)")
    show.add_argument("session", nargs="?", default="latest", help="id, prefix or 'latest'")
    show.add_argument("--transcript", action="store_true", help="print the transcript instead")
    show.add_argument("--path", action="store_true", help="print the session directory")

    add("devices", cmd_devices, "list audio input devices")
    add("doctor", cmd_doctor, "check microphone, system audio, Whisper and Ollama")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args, load_config()))
    except KeyboardInterrupt:
        _eprint("\ninterrupted")
        return 130
    except BrokenPipeError:
        return 0
    except VechoError as exc:
        _eprint(f"error: {exc}")
        return 1
