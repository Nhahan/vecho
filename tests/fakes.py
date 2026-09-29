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


class FakeMulti:
    """Stands in for the CoreAudio helper: 'creating' the device just makes it appear."""

    def __init__(self, switcher, main=None):
        self.switcher = switcher
        self.main = main
        self.created: list[str | None] = []
        self.removed = False
        self.fail = False

    def main_device(self):
        return self.main

    def create(self, output=None):
        if self.fail:
            raise AudioError("CoreAudio refused")
        self.created.append(output)
        self.main = output
        if "vecho Multi-Output" not in self.switcher._outputs:
            self.switcher._outputs.append("vecho Multi-Output")
        return f"vecho Multi-Output: {output} + BlackHole 2ch"

    def remove(self):
        existed = "vecho Multi-Output" in self.switcher._outputs
        self.removed = True
        if existed:
            self.switcher._outputs.remove("vecho Multi-Output")
        return existed
