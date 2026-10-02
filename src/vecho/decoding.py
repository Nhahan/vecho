"""Any audio file (mp3, m4a, wav, …) as 16 kHz mono samples, decoded frame by frame.

vecho decodes with PyAV (FFmpeg) itself rather than through faster-whisper's helper, whose
PyAV options broke with PyAV 19. Decoding piece by piece also keeps a long recording from
ever sitting in memory as a whole.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


def pcm_blocks(path: Path | str, rate: int = SAMPLE_RATE) -> Iterator[np.ndarray]:
    """16-bit mono samples, in the decoder's own block sizes."""
    import av

    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                yield converted.to_ndarray().reshape(-1)
        for converted in resampler.resample(None):  # what the resampler still holds
            yield converted.to_ndarray().reshape(-1)


def float_blocks(path: Path | str, size: int, rate: int = SAMPLE_RATE) -> Iterator[np.ndarray]:
    """float32 samples in blocks of ``size`` (the last one shorter)."""
    pending: list[np.ndarray] = []
    held = 0
    for block in pcm_blocks(path, rate):
        pending.append(block)
        held += len(block)
        while held >= size:
            joined = np.concatenate(pending)
            yield joined[:size].astype(np.float32) / 32768.0
            pending, held = [joined[size:]], held - size
    if held:
        yield np.concatenate(pending).astype(np.float32) / 32768.0


def duration(path: Path | str) -> float:
    """Length in seconds: from the container when it says, else by decoding."""
    import av

    with av.open(str(path)) as container:
        if container.duration:
            return container.duration / 1_000_000
    return sum(len(block) for block in pcm_blocks(path)) / SAMPLE_RATE
