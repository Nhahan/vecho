"""Settings, resolved as defaults < ``$VECHO_HOME/config.toml`` < ``VECHO_*`` env < CLI flags."""

from __future__ import annotations

import math
import os
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path

from . import roles
from .errors import ConfigError

ENV_PREFIX = "VECHO_"
DEFAULT_HOME = Path.home() / ".vecho"


@dataclass(frozen=True)
class Config:
    home: Path = DEFAULT_HOME
    # Speech-to-text (faster-whisper). ``language`` None means auto-detect per track.
    whisper_model: str = "large-v3-turbo"
    whisper_compute_type: str = "int8"
    whisper_backend: str = "auto"  # auto | mlx (Apple Silicon GPU) | faster-whisper
    language: str | None = None
    # Summarization (Ollama).
    llm_host: str = "http://127.0.0.1:11434"
    llm_model: str = "auto"  # auto: the largest model this computer's memory runs well
    llm_num_ctx: int = 0  # 0: what this computer's memory affords
    llm_timeout: float = 1800.0
    chunk_chars: int = 0  # 0: half the context (about one character per token in Korean)
    summary_language: str = "Korean"
    # Recording and transcript labels.
    sample_rate: int = 16000
    me_label: str = "나"
    remote_label: str = "상대방"

    def __post_init__(self) -> None:
        auto = {
            "llm_model": self.llm_model.strip().lower() in {"", "auto"},
            "llm_num_ctx": self.llm_num_ctx == 0,
            "chunk_chars": self.chunk_chars == 0,
        }
        if any(auto.values()):
            from .models import llm_tier

            _, model, context, chunk = llm_tier()
            for name, value in (("llm_model", model), ("llm_num_ctx", context)):
                if auto[name]:
                    object.__setattr__(self, name, value)
            if auto["chunk_chars"]:
                object.__setattr__(self, "chunk_chars", min(chunk, self.llm_num_ctx // 2))
        for name in ("llm_num_ctx", "chunk_chars", "sample_rate"):
            if getattr(self, name) <= 0:
                raise ConfigError(f"'{name}' must be a positive integer")
        if not math.isfinite(self.llm_timeout) or self.llm_timeout <= 0:
            raise ConfigError("'llm_timeout' must be a positive number of seconds")
        if self.whisper_backend not in {"auto", "mlx", "faster-whisper"}:
            raise ConfigError("'whisper_backend' must be auto, mlx or faster-whisper")

    @property
    def sessions_dir(self) -> Path:
        return self.home / "sessions"

    @property
    def templates_dir(self) -> Path:
        return self.home / "templates"

    def label_for(self, role: str) -> str:
        """Speaker label for a track role; empty when the speaker is unknown."""
        if role == roles.ME:
            return self.me_label
        if role == roles.REMOTE:
            return self.remote_label
        return ""

    def with_overrides(self, **values: object) -> Config:
        """Apply command-line values, ignoring those left unset (None)."""
        return _apply(self, {k: v for k, v in values.items() if v is not None}, "command line")


def _optional_str(raw: object) -> str | None:
    text = str(raw).strip()
    return None if text.lower() in {"", "auto", "none"} else text


_CASTERS: dict[str, Callable[[object], object]] = {
    "home": lambda raw: Path(str(raw)).expanduser(),
    "language": _optional_str,
    "llm_num_ctx": lambda raw: int(str(raw)),
    "chunk_chars": lambda raw: int(str(raw)),
    "sample_rate": lambda raw: int(str(raw)),
    "llm_timeout": lambda raw: float(str(raw)),
}


def _apply(config: Config, values: Mapping[str, object], source: str) -> Config:
    known = {f.name for f in fields(Config)}
    updates: dict[str, object] = {}
    for key, raw in values.items():
        if key not in known:
            raise ConfigError(f"unknown setting '{key}' in {source}")
        try:
            updates[key] = _CASTERS.get(key, str)(raw)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"invalid value for '{key}' in {source}: {raw!r}") from exc
    return replace(config, **updates)


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build the effective configuration from the config file and environment."""
    env = os.environ if env is None else env
    config = Config()
    if env.get(ENV_PREFIX + "HOME"):
        config = _apply(config, {"home": env[ENV_PREFIX + "HOME"]}, "environment")

    path = config.home / "config.toml"
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc
        if "home" in data:
            raise ConfigError(f"'home' cannot be set in {path}; use the VECHO_HOME variable")
        config = _apply(config, data, str(path))

    from_env = {
        f.name: env[ENV_PREFIX + f.name.upper()]
        for f in fields(Config)
        if ENV_PREFIX + f.name.upper() in env and (f.name != "home" or env[ENV_PREFIX + "HOME"])
    }
    return _apply(config, from_env, "environment")
