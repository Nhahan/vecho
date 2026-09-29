import json
import sys

import pytest

from fakes import FakeSwitcher
from vecho import routing
from vecho.errors import AudioError
from vecho.routing import MULTI_OUTPUT_NAME, OutputRouter, OutputSwitcher

REAL_OUTPUTS = "MacBook Pro 스피커\nvecho Multi-Output\n\n"


def make_router(tmp_path, switcher, **kwargs):
    warnings: list[str] = []
    router = OutputRouter(tmp_path / "state.json", warnings.append, switcher=switcher, **kwargs)
    return router, warnings


# ---- OutputSwitcher: talks to SwitchAudioSource ------------------------------------


def test_switcher_parses_output_and_builds_commands():
    calls = []

    def run(command):
        calls.append(list(command))
        return REAL_OUTPUTS if "-a" in command else "MacBook Pro 스피커\n"

    switcher = OutputSwitcher(run=run, binary="/bin/sas")
    assert switcher.available
    assert switcher.outputs() == ["MacBook Pro 스피커", "vecho Multi-Output"]
    assert switcher.current() == "MacBook Pro 스피커"
    switcher.set("vecho Multi-Output")
    assert calls[-1] == ["/bin/sas", "-t", "output", "-s", "vecho Multi-Output"]


def test_switcher_without_binary_explains_install(monkeypatch):
    monkeypatch.setattr(routing.shutil, "which", lambda name: None)
    switcher = OutputSwitcher()
    assert not switcher.available
    with pytest.raises(AudioError, match="brew install switchaudio-osx"):
        switcher.current()


# ---- OutputRouter -------------------------------------------------------------------


def test_router_switches_then_restores(tmp_path):
    fake = FakeSwitcher(outputs=["Speakers", MULTI_OUTPUT_NAME])
    router, warnings = make_router(tmp_path, fake)
    with router:
        assert fake.current() == MULTI_OUTPUT_NAME
        assert json.loads((tmp_path / "state.json").read_text())["previous"] == "Speakers"
    assert fake.current() == "Speakers"
    assert fake.calls == [MULTI_OUTPUT_NAME, "Speakers"]
    assert not (tmp_path / "state.json").exists()
    assert warnings == []


def test_router_restores_when_the_body_raises(tmp_path):
    fake = FakeSwitcher(outputs=["Speakers", MULTI_OUTPUT_NAME])
    router, _ = make_router(tmp_path, fake)
    with pytest.raises(RuntimeError), router:
        raise RuntimeError("recording blew up")
    assert fake.current() == "Speakers"


def test_router_does_nothing_when_disabled(tmp_path):
    fake = FakeSwitcher(outputs=["Speakers", MULTI_OUTPUT_NAME])
    router, _ = make_router(tmp_path, fake, enabled=False)
    with router:
        pass
    assert fake.calls == []


def test_router_warns_when_multi_output_is_missing(tmp_path):
    fake = FakeSwitcher(outputs=["Speakers"])
    router, warnings = make_router(tmp_path, fake)
    with router:
        pass
    assert fake.calls == []
    assert any("vecho setup" in w for w in warnings)


def test_router_warns_when_switch_tool_is_missing(tmp_path):
    fake = FakeSwitcher(available=False)
    router, warnings = make_router(tmp_path, fake)
    with router:
        pass
    assert any("switchaudio-osx" in w for w in warnings)


def test_router_leaves_a_deliberate_multi_output_selection_alone(tmp_path):
    fake = FakeSwitcher(outputs=["Speakers", MULTI_OUTPUT_NAME], current=MULTI_OUTPUT_NAME)
    router, _ = make_router(tmp_path, fake)
    with router:
        pass
    assert fake.calls == []
    assert fake.current() == MULTI_OUTPUT_NAME


def test_router_recovers_output_left_behind_by_a_crashed_run(tmp_path):
    fake = FakeSwitcher(outputs=["Speakers", MULTI_OUTPUT_NAME], current=MULTI_OUTPUT_NAME)
    (tmp_path / "state.json").write_text(json.dumps({"previous": "Speakers"}), encoding="utf-8")
    router, _ = make_router(tmp_path, fake)
    with router:
        pass
    assert fake.current() == "Speakers"
    assert not (tmp_path / "state.json").exists()


def test_router_survives_switch_failures(tmp_path):
    fake = FakeSwitcher(outputs=["Speakers", MULTI_OUTPUT_NAME])
    fake.fail_on_set = True
    router, warnings = make_router(tmp_path, fake)
    with router:  # must not raise: recording matters more than routing
        pass
    assert any("could not route" in w for w in warnings)


def test_failed_restore_warns_and_keeps_the_state_file(tmp_path):
    fake = FakeSwitcher(outputs=["Speakers", MULTI_OUTPUT_NAME])
    router, warnings = make_router(tmp_path, fake)
    with router:
        fake.fail_on_set = True
    assert any("select 'Speakers' manually" in w for w in warnings)
    assert (tmp_path / "state.json").exists()  # a later run can still recover


def test_restore_reports_a_vanished_output(tmp_path):
    fake = FakeSwitcher(outputs=["AirPods", MULTI_OUTPUT_NAME], current="AirPods")
    router, warnings = make_router(tmp_path, fake)
    with router:
        fake._outputs.remove("AirPods")  # e.g. the headphones were switched off
    assert any("no longer available" in w for w in warnings)


# ---- Multi-Output device helper --------------------------------------------------------


def test_create_multi_output_invokes_the_swift_helper(monkeypatch):
    monkeypatch.setattr(routing.shutil, "which", lambda name: "/usr/bin/swift")
    seen = {}

    def run(command, timeout):
        seen["command"] = command
        return "created\tvecho Multi-Output\tMacBook Speakers\tBlackHole 2ch\n"

    message = routing.create_multi_output(output="MacBook Speakers", run=run)
    command = seen["command"]
    assert command[0] == "/usr/bin/swift"
    assert command[1].endswith("multi_output.swift")
    assert command[2] == "create" and "--output" in command and "MacBook Speakers" in command
    assert message == "vecho Multi-Output: MacBook Speakers + BlackHole 2ch"


def test_remove_multi_output_reports_whether_it_existed(monkeypatch):
    monkeypatch.setattr(routing.shutil, "which", lambda name: "/usr/bin/swift")
    assert routing.remove_multi_output(run=lambda c, timeout: "destroyed\tx\n") is True
    assert routing.remove_multi_output(run=lambda c, timeout: "absent\n") is False


def test_helper_requires_swift(monkeypatch):
    monkeypatch.setattr(routing.shutil, "which", lambda name: None)
    with pytest.raises(AudioError, match="xcode-select"):
        routing.create_multi_output()


def test_swift_helper_ships_with_the_package():
    from importlib import resources

    script = resources.files("vecho").joinpath("resources", "multi_output.swift")
    assert "AudioHardwareCreateAggregateDevice" in script.read_text(encoding="utf-8")


# ---- run_command ---------------------------------------------------------------------


def test_run_command_returns_stdout():
    assert routing.run_command([sys.executable, "-c", "print('hi')"]).strip() == "hi"


def test_run_command_reports_failures():
    with pytest.raises(AudioError, match="boom"):
        routing.run_command([sys.executable, "-c", "import sys; sys.exit('boom')"])
    with pytest.raises(AudioError, match="not found"):
        routing.run_command(["/definitely/not/a/binary"])
    with pytest.raises(AudioError, match="did not finish"):
        routing.run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)
