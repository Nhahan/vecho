import json
import sys
import time

import pytest

from fakes import FakeMulti, FakeSwitcher
from vecho import routing
from vecho.errors import AudioError
from vecho.routing import MULTI_OUTPUT_NAME, MultiOutput, OutputRouter, OutputSwitcher

REAL_OUTPUTS = "MacBook Pro 스피커\nvecho Multi-Output\n\n"


def make_router(tmp_path, switcher, multi, **kwargs):
    warnings: list[str] = []
    kwargs.setdefault("poll_sec", 0.01)
    router = OutputRouter(
        tmp_path / "state.json", warnings.append, switcher=switcher, multi=multi, **kwargs
    )
    return router, warnings


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def rig():
    switcher = FakeSwitcher(outputs=["Speakers", "AirPods"], current="Speakers")
    return switcher, FakeMulti(switcher)


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


@pytest.mark.parametrize(
    ("name", "virtual"),
    [
        ("BlackHole 2ch", True),
        ("vecho Multi-Output", True),
        ("ZoomAudioDevice", True),
        ("MacBook Pro 스피커", False),
        ("Sunning's AirPods Pro", False),
        ("LG HDR 4K", False),
    ],
)
def test_virtual_output_detection(name, virtual):
    assert routing.is_virtual_output(name) is virtual


# ---- OutputRouter: follows whatever the user listens on --------------------------------


def test_router_builds_multi_output_around_the_current_output_and_restores(tmp_path, rig):
    switcher, multi = rig
    router, warnings = make_router(tmp_path, switcher, multi)
    with router:
        assert switcher.current() == MULTI_OUTPUT_NAME
        assert multi.created == ["Speakers"]
        assert json.loads((tmp_path / "state.json").read_text())["previous"] == "Speakers"
    assert switcher.current() == "Speakers"
    assert not (tmp_path / "state.json").exists()
    assert warnings == []


def test_router_supports_any_output_device(tmp_path, rig):
    switcher, multi = rig
    switcher._current = "AirPods"
    router, _ = make_router(tmp_path, switcher, multi)
    with router:
        assert multi.created == ["AirPods"]
    assert switcher.current() == "AirPods"


def test_router_reuses_a_device_that_already_matches(tmp_path, rig):
    switcher, multi = rig
    multi.main = "Speakers"
    switcher._outputs.append(MULTI_OUTPUT_NAME)
    router, _ = make_router(tmp_path, switcher, multi)
    with router:
        assert switcher.current() == MULTI_OUTPUT_NAME
    assert multi.created == []


def test_router_rebuilds_a_device_bound_to_another_output(tmp_path, rig):
    switcher, multi = rig
    multi.main = "AirPods"  # left over from yesterday
    switcher._outputs.append(MULTI_OUTPUT_NAME)
    router, _ = make_router(tmp_path, switcher, multi)
    with router:
        assert multi.created == ["Speakers"]


def test_router_restores_when_the_body_raises(tmp_path, rig):
    switcher, multi = rig
    router, _ = make_router(tmp_path, switcher, multi)
    with pytest.raises(RuntimeError), router:
        raise RuntimeError("recording blew up")
    assert switcher.current() == "Speakers"


def test_router_does_nothing_when_disabled(tmp_path, rig):
    switcher, multi = rig
    router, _ = make_router(tmp_path, switcher, multi, enabled=False)
    with router:
        pass
    assert switcher.calls == [] and multi.created == []


def test_router_warns_when_switch_tool_is_missing(tmp_path, rig):
    switcher, multi = rig
    switcher.available = False
    router, warnings = make_router(tmp_path, switcher, multi)
    with router:
        pass
    assert any("switchaudio-osx" in w for w in warnings)


def test_router_leaves_a_deliberate_multi_output_selection_alone(tmp_path, rig):
    switcher, multi = rig
    switcher._outputs.append(MULTI_OUTPUT_NAME)
    switcher._current = MULTI_OUTPUT_NAME
    router, _ = make_router(tmp_path, switcher, multi)
    with router:
        pass
    assert switcher.calls == [] and multi.created == []
    assert switcher.current() == MULTI_OUTPUT_NAME


def test_router_recovers_output_left_behind_by_a_crashed_run(tmp_path, rig):
    switcher, multi = rig
    switcher._outputs.append(MULTI_OUTPUT_NAME)
    switcher._current = MULTI_OUTPUT_NAME
    (tmp_path / "state.json").write_text(json.dumps({"previous": "AirPods"}), encoding="utf-8")
    router, _ = make_router(tmp_path, switcher, multi)
    with router:
        pass
    assert switcher.current() == "AirPods"
    assert not (tmp_path / "state.json").exists()


def test_virtual_current_output_is_reported_not_routed(tmp_path, rig):
    switcher, multi = rig
    switcher._outputs.append("BlackHole 2ch")
    switcher._current = "BlackHole 2ch"
    router, warnings = make_router(tmp_path, switcher, multi)
    with router:
        pass
    assert multi.created == [] and switcher.calls == []
    assert any("virtual" in w for w in warnings)


def test_device_creation_failure_warns_and_recording_goes_on(tmp_path, rig):
    switcher, multi = rig
    multi.fail = True
    router, warnings = make_router(tmp_path, switcher, multi)
    with router:  # must not raise: recording matters more than routing
        assert switcher.current() == "Speakers"
    assert any("could not capture audio played through 'Speakers'" in w for w in warnings)
    assert not (tmp_path / "state.json").exists()


def test_switch_failure_warns(tmp_path, rig):
    switcher, multi = rig
    switcher.fail_on_set = True
    router, warnings = make_router(tmp_path, switcher, multi)
    with router:
        pass
    assert any("could not capture" in w for w in warnings)


def test_failed_restore_warns_and_keeps_the_state_file(tmp_path, rig):
    switcher, multi = rig
    router, warnings = make_router(tmp_path, switcher, multi, poll_sec=60)
    with router:
        switcher.fail_on_set = True
    assert any("select 'Speakers' manually" in w for w in warnings)
    assert (tmp_path / "state.json").exists()  # a later run can still recover


def test_restore_reports_a_vanished_output(tmp_path, rig):
    switcher, multi = rig
    router, warnings = make_router(tmp_path, switcher, multi, poll_sec=60)
    with router:
        switcher._outputs.remove("Speakers")  # e.g. the headphones were switched off
    assert any("no longer available" in w for w in warnings)


# ---- following a change of output during a recording ----------------------------------


def test_router_follows_an_output_change_mid_recording(tmp_path, rig):
    switcher, multi = rig
    router, warnings = make_router(tmp_path, switcher, multi)
    with router:
        assert multi.created == ["Speakers"]
        switcher._current = "AirPods"  # macOS moved the output when AirPods connected
        assert wait_for(lambda: switcher.current() == MULTI_OUTPUT_NAME)
        assert multi.created == ["Speakers", "AirPods"]
    assert switcher.current() == "AirPods"  # the *latest* real output comes back
    assert warnings == []


def test_router_stops_retrying_an_output_it_cannot_capture(tmp_path, rig):
    switcher, multi = rig
    switcher._outputs.append("BlackHole 2ch")
    router, warnings = make_router(tmp_path, switcher, multi)
    with router:
        switcher._current = "BlackHole 2ch"
        assert wait_for(lambda: len(warnings) >= 1)
        time.sleep(0.15)  # many polls later...
    assert len([w for w in warnings if "virtual" in w]) == 1  # ...still just one warning


def test_no_watcher_runs_when_routing_did_not_start(tmp_path, rig):
    switcher, multi = rig
    switcher.available = False
    router, _ = make_router(tmp_path, switcher, multi)
    with router:
        assert router._watcher is None


# ---- Multi-Output helper ---------------------------------------------------------------


def test_main_device_parses_the_helper_output(monkeypatch):
    monkeypatch.setattr(routing.shutil, "which", lambda name: "/usr/bin/swift")
    seen = {}

    def run(command, timeout):
        seen["command"] = command
        return "main\tMacBook Speakers\tBuiltInSpeakerDevice\n"

    assert MultiOutput(run=run).main_device() == "MacBook Speakers"
    assert seen["command"][2] == "describe"
    assert MultiOutput(run=lambda c, timeout: "absent\n").main_device() is None


def test_create_invokes_the_swift_helper(monkeypatch):
    monkeypatch.setattr(routing.shutil, "which", lambda name: "/usr/bin/swift")
    seen = {}

    def run(command, timeout):
        seen["command"] = command
        return "created\tvecho Multi-Output\tMacBook Speakers\tBlackHole 2ch\n"

    message = MultiOutput(run=run).create(output="MacBook Speakers")
    command = seen["command"]
    assert command[0] == "/usr/bin/swift"
    assert command[1].endswith("multi_output.swift")
    assert command[2] == "create" and "--output" in command and "MacBook Speakers" in command
    assert message == "vecho Multi-Output: MacBook Speakers + BlackHole 2ch"


def test_remove_reports_whether_it_existed(monkeypatch):
    monkeypatch.setattr(routing.shutil, "which", lambda name: "/usr/bin/swift")
    assert MultiOutput(run=lambda c, timeout: "destroyed\tx\n").remove() is True
    assert MultiOutput(run=lambda c, timeout: "absent\n").remove() is False


def test_helper_requires_swift(monkeypatch):
    monkeypatch.setattr(routing.shutil, "which", lambda name: None)
    with pytest.raises(AudioError, match="xcode-select"):
        MultiOutput().create()


def test_swift_helper_ships_with_the_package():
    from importlib import resources

    script = resources.files("vecho").joinpath("resources", "multi_output.swift")
    source = script.read_text(encoding="utf-8")
    assert "AudioHardwareCreateAggregateDevice" in source and '"describe"' in source


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


def test_restore_does_not_override_an_output_the_user_chose_meanwhile(tmp_path, rig):
    switcher, multi = rig
    router, _ = make_router(tmp_path, switcher, multi, poll_sec=60)
    with router:
        switcher._current = "AirPods"  # user picked something else just before stopping
    assert switcher.current() == "AirPods"
    assert not (tmp_path / "state.json").exists()


def test_failed_follow_leaves_the_users_new_output_in_place(tmp_path, rig):
    switcher, multi = rig
    router, warnings = make_router(tmp_path, switcher, multi)
    with router:
        multi.fail = True  # the new device cannot be combined with the loopback
        switcher._current = "AirPods"
        assert wait_for(lambda: any("could not capture" in w for w in warnings))
    assert switcher.current() == "AirPods"  # the user keeps hearing their audio
