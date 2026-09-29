"""Exception types for failures the user can act on."""


class VechoError(Exception):
    """Base class for expected, user-facing failures."""


class ConfigError(VechoError):
    """Invalid or unreadable configuration."""


class SessionError(VechoError):
    """A session could not be created, found, or read."""


class AudioError(VechoError):
    """Audio devices or recording failed."""


class TranscriptionError(VechoError):
    """Speech-to-text failed."""


class SummarizationError(VechoError):
    """The local LLM could not produce a summary."""
