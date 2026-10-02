"""Imported recordings are decoded by vecho itself (PyAV), piece by piece."""

from pathlib import Path

import numpy as np

from vecho import decoding

SAMPLE = Path(__file__).with_name("data") / "sample-ko.m4a"  # about 8.7 s of Korean speech


def test_a_compressed_recording_decodes_to_16k_mono_blocks():
    blocks = list(decoding.float_blocks(SAMPLE, size=16000 * 3))
    assert [len(b) for b in blocks[:-1]] == [16000 * 3] * (len(blocks) - 1)
    audio = np.concatenate(blocks)
    assert 8.0 < len(audio) / 16000 < 9.5 and 0.1 < float(np.abs(audio).max()) <= 1.0
    assert audio.dtype == np.float32


def test_the_length_comes_from_the_file():
    assert 8.0 < decoding.duration(SAMPLE) < 9.5
