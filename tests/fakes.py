from vecho.errors import AudioError


class FakeSwitcher:
    """In-memory stand-in for SwitchAudioSource so no test ever touches the real sound output."""

    def __init__(self, outputs=("Speakers",), current="Speakers", available=True):
        self.available = available
        self._outputs = list(outputs)
        self._current = current
        self.calls: list[str] = []
        self.fail_on_set = False

    def outputs(self):
        return list(self._outputs)

    def current(self):
        return self._current

    def set(self, name):
        if self.fail_on_set:
            raise AudioError("switch refused")
        if name not in self._outputs:
            raise AudioError(f"no output named {name}")
        self.calls.append(name)
        self._current = name
