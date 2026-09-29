"""In-memory stand-in for the `soundcard` library (Windows/Linux loopback capture)."""

import time

import numpy as np


class Speaker:
    def __init__(self, name):
        self.name = name
        self.id = name


class Stream:
    def __init__(self, card, speaker):
        self.card, self.speaker = card, speaker

    def __enter__(self):
        if self.card.fail_open:
            raise RuntimeError(self.card.fail_open)
        self.card.opened.append(self.speaker.name)
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        self.card.reads += 1
        if self.card.fail_after is not None and self.card.reads > self.card.fail_after:
            self.card.fail_after = None
            raise RuntimeError("device vanished")
        time.sleep(numframes / 48000)  # a real device delivers audio in real time
        value = self.card.level
        return np.full((numframes, 2), value, dtype=np.float32)


class Microphone:
    def __init__(self, card, speaker):
        self.card, self.speaker = card, speaker

    def recorder(self, samplerate, channels=None):
        assert samplerate == 48000
        return Stream(self.card, self.speaker)


class FakeSoundcard:
    def __init__(self, speaker="Speakers", level=0.5):
        self.speaker = Speaker(speaker) if speaker else None
        self.level = level
        self.opened = []
        self.reads = 0
        self.fail_open = None
        self.fail_after = None

    def default_speaker(self):
        return self.speaker

    def get_microphone(self, id, include_loopback=False):
        assert include_loopback
        return Microphone(self, self.speaker)
