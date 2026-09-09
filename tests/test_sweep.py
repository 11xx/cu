"""Caller-visible regressions for input races and observation/replay fidelity."""

import copy
import json
import subprocess
import time

import pytest

from cu import actions, browser, capture, checks, cli, config, exits, frames, observe, png, recipes


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(config, "log_path", lambda cfg: tmp_path / "actions.jsonl")
    monitor = {"name": "OUT", "scale": 1, "logical_rect": {"x": 0, "y": 0, "w": 200, "h": 100}}
    window = {
        "address": "0x1", "class": "test-app", "title": "fixture",
        "logical_rect": {"x": 0, "y": 0, "w": 200, "h": 100},
        "visible": True, "hidden": False, "focus_order": 0, "focused": True,
    }
    snap = {"monitors": [monitor], "windows": [window], "focused": window,
            "cursor_logical": {"x": 10, "y": 10}, "layer_surfaces": []}
    monkeypatch.setattr(observe, "snapshot", lambda: copy.deepcopy(snap))
    monkeypatch.setattr(observe, "monitors", lambda: snap["monitors"])
    monkeypatch.setattr(observe, "windows", lambda *_: snap["windows"])
    monkeypatch.setattr(observe, "focused", lambda: snap["focused"])
    monkeypatch.setattr(observe, "cursor", lambda: snap["cursor_logical"])
    monkeypatch.setattr(checks, "_processes", lambda **_: [])
    monkeypatch.setattr(capture, "grab_region", lambda rect, dest, scale: dest.write_bytes(
        png.encode(2, 1, bytes([10, 20, 30, 255]) * 2)))
    return snap


@pytest.mark.parametrize("option", ["--timeout", "--quiet-ms", "--poll-ms"])
def test_pixel_wait_rejects_nonfinite_budgets(option, world):
    assert cli.main(["shot", "--after", "f999", "--watch", "full", option, "nan"]) == exits.USAGE


def test_hung_capture_tool_returns_an_environment_refusal(world, monkeypatch, capsys):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("grim", 10)
    monkeypatch.setattr(capture.shutil, "which", lambda name: name)
    monkeypatch.setattr(capture.subprocess, "run", timeout)
    monkeypatch.setattr(capture, "grab_region", lambda r, p, s: capture._run(["grim", str(p)]))
    assert cli.main(["shot", "--timings", "--json"]) == exits.PRECONDITION_FAILED
    report = json.loads(capsys.readouterr().err.split("cu metrics: ", 1)[1])
    assert report["code"] == exits.PRECONDITION_FAILED
    assert report["frames"] == []


def test_shot_reports_bounded_nonprivate_timing(world, capsys):
    assert cli.main(["shot", "--timings", "--json"]) == 0
    output = capsys.readouterr()
    frame = json.loads(output.out)
    report = json.loads(output.err.split("cu metrics: ", 1)[1])
    assert report["code"] == 0
    assert report["total_seconds"] >= report["seconds"]["acquisition"]
    assert report["operations"]["acquisition"] == 1
    assert report["frames"][0]["id"] == frame["id"]
    assert report["frames"][0]["encoded_bytes"] == frame["encoded_bytes"]
    assert "fixture" not in output.err
    assert "0x1" not in output.err


def test_shot_refuses_encoded_bytes_beyond_profile_budget(world, monkeypatch, capsys):
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["capture"]["profiles"]["generic"]["max_bytes"] = 1
    monkeypatch.setattr(config, "load", lambda: cfg)
    assert cli.main(["shot", "--for", "generic", "--json"]) == 29
    assert capsys.readouterr().out == ""
    assert not list(frames.store_dir().glob("f*.json"))


def test_refused_shot_still_reports_total_time(world, capsys):
    world["windows"][0]["visible"] = False
    assert cli.main(["shot", "--timings", "--json"]) == exits.TARGET_NOT_VISIBLE
    output = capsys.readouterr()
    report = json.loads(output.err.split("cu metrics: ", 1)[1])
    assert report["code"] == exits.TARGET_NOT_VISIBLE
    assert report["frames"] == []
    assert report["total_seconds"] >= 0


@pytest.mark.parametrize("obstruction", ["hidden", "overlap", "layer"])
def test_window_capture_refuses_obstructed_identity(world, obstruction, capsys):
    if obstruction == "hidden":
        world["windows"][0]["visible"] = False
    elif obstruction == "overlap":
        world["windows"].append({**world["windows"][0], "address": "0x2", "focused": False})
    else:
        world["layer_surfaces"] = [
            {"level": 3, "logical_rect": world["windows"][0]["logical_rect"]}
        ]
    assert cli.main(["shot", "--json"]) == 27
    assert capsys.readouterr().out == ""
    assert not list(frames.store_dir().glob("f*.json"))


def test_window_hidden_during_capture_publishes_no_frame(world, monkeypatch, capsys):
    def grab(rect, dest, scale):
        dest.write_bytes(png.encode(1, 1, b"\0\0\0\xff"))
        world["windows"][0]["visible"] = False
    monkeypatch.setattr(capture, "grab_region", grab)
    assert cli.main(["shot", "--json"]) == exits.TARGET_NOT_VISIBLE
    assert capsys.readouterr().out == ""
    assert not list(frames.store_dir().glob("f*.json"))


def test_after_refuses_moved_window_instead_of_relabelling_pixels(world, capsys):
    assert cli.main(["shot", "--json"]) == 0
    reference = json.loads(capsys.readouterr().out)
    world["windows"][0]["logical_rect"]["x"] = 5
    assert cli.main([
        "shot", "--after", reference["id"], "--watch", "full", "--timeout", "0.001",
    ]) == exits.STALE_FRAME


def test_off_workspace_client_is_not_visible(monkeypatch):
    from cu import hypr
    monitors = [{"id": 7, "name": "OUT", "active_workspace": "1"}]
    client = {"address": "0x1", "class": "fixture", "title": "fixture", "pid": 1,
              "monitor": 7, "workspace": {"name": "2"}, "at": [0, 0], "size": [10, 10],
              "visible": True, "floating": False, "xwayland": False}
    monkeypatch.setattr(hypr, "query", lambda name: [client])
    window = observe.windows(monitors)[0]
    assert window["visible"] is False
    assert window["monitor"] == "OUT"


def test_key_rechecks_live_denylisted_title(world, monkeypatch):
    sent = []
    def send(cfg, chords, backend, *, before_send):
        world["focused"] = {**world["focused"], "title": "Password prompt"}
        before_send()
        sent.extend(chords)
    monkeypatch.setattr(actions, "send_chords", send)
    assert cli.main(["key", "--chord", "a"]) == exits.DENIED_BY_POLICY
    assert sent == []


def test_key_refuses_missing_focus(world, monkeypatch):
    world["windows"] = []
    world["focused"] = None
    sent = []
    monkeypatch.setattr(actions, "send_chords", lambda *a, **k: sent.append(True))
    assert cli.main(["key", "--chord", "a"]) == exits.TARGET_NOT_FOUND
    assert sent == []


def test_click_rechecks_geometry_after_device_settlement(world, monkeypatch, capsys):
    assert cli.main(["shot", "--json"]) == exits.OK
    frame = json.loads(capsys.readouterr().out)
    clicked = []
    monkeypatch.setattr(actions, "warp", lambda *a, **k: world["cursor_logical"])
    class Device:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            world["windows"][0]["logical_rect"]["w"] = 190
            return self
        def __exit__(self, *args):
            pass
        def click(self, *args):
            clicked.append(True)
    monkeypatch.setattr(actions.uinput, "Device", Device)
    assert cli.main(["click", "--frame", frame["id"], "--at", "0.1,0.1"]) == exits.STALE_FRAME
    assert clicked == []


def test_if_changed_does_not_relabel_another_windows_frame(world, capsys):
    assert cli.main(["shot", "--json"]) == exits.OK
    first = json.loads(capsys.readouterr().out)
    world["windows"][0]["address"] = "0x2"
    assert cli.main(["shot", "--if-changed", "--json"]) == exits.OK
    second = json.loads(capsys.readouterr().out)
    assert second["window"]["address"] == "0x2"
    assert second["id"] != first["id"]


def test_magnified_output_remains_actionable(world, capsys):
    with frames.capture_scratch() as scratch:
        scratch.write_bytes(png.encode(2, 1, bytes([0, 0, 0, 255]) * 2))
        original = frames.new(
            source={"type": "output", "name": "OUT"},
            logical_rect=world["monitors"][0]["logical_rect"],
            image_size={"w": 2, "h": 1}, scratch=scratch,
            monitor=world["monitors"][0], captured_at=time.time(),
        )
    assert cli.main(["magnify", "--frame", original["id"], "--scale", "2", "--json"]) == 0
    enlarged = json.loads(capsys.readouterr().out)
    assert cli.main(["move", "--frame", enlarged["id"], "--at", "1,1", "--dry-run"]) == 0


def test_recipe_replay_preserves_repeat(world, monkeypatch):
    recipe = recipes.from_log([
        {"action": "key", "chord": "a", "repeat": 3, "dry_run": False}
    ], "repeat", "", "", "generic")
    recipe["reviewed_hash"] = recipes.content_hash(recipe)
    recipes.save(recipe)
    calls = []
    monkeypatch.setattr(actions, "key", lambda *a, **k: calls.append(k) or {})
    assert cli.main(["recipe", "replay", "repeat"]) == 0
    assert calls[0]["repeat"] == 3


@pytest.mark.parametrize("initial", ["old value", ""])
def test_explicit_replacement_selects_and_verifies_exactly(
    initial, world, monkeypatch, capsys,
):
    field = [initial]
    selected = [False]
    clipboard = [b"previous"]
    monkeypatch.setattr(actions.shutil, "which", lambda name: name)
    monkeypatch.setattr(actions, "_clipboard_read", lambda: clipboard[0])
    monkeypatch.setattr(actions, "_clipboard_write", lambda value: clipboard.__setitem__(0, value))
    def send(cfg, chords, backend, *, before_send):
        for chord in chords:
            before_send()
            if chord == "ctrl+a":
                selected[0] = True
            elif chord == "ctrl+v":
                field[0] = ("" if selected[0] else field[0]) + clipboard[0].decode()
                selected[0] = False
            elif chord == "ctrl+c":
                clipboard[0] = field[0].encode()
    monkeypatch.setattr(actions, "send_chords", send)
    monkeypatch.setattr(actions.time, "sleep", lambda _: None)
    assert cli.main(["type", "new value", "--replace", "--verify", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert field[0] == "new value"
    assert result["verified"]["status"] == "ok"
    assert clipboard[0] == b"previous"


def test_replacement_stops_when_focus_moves_after_selection(world, monkeypatch):
    sent = []
    def send(cfg, chords, backend, *, before_send):
        for chord in chords:
            before_send()
            sent.append(chord)
            world["focused"] = {**world["focused"], "address": "0x2"}
    monkeypatch.setattr(actions, "send_chords", send)
    monkeypatch.setattr(actions, "_clipboard_read", lambda: b"previous")
    monkeypatch.setattr(actions, "_clipboard_write", lambda _: None)
    assert cli.main(["type", "value", "--replace"]) == exits.FOCUS_CHANGED
    assert sent == ["ctrl+a"]


def test_replacement_without_selection_mapping_sends_nothing(world, monkeypatch):
    world["windows"][0]["class"] = "Alacritty"
    sent = []
    monkeypatch.setattr(actions, "send_chords", lambda *a, **k: sent.append(True))
    assert cli.main(["type", "value", "--replace"]) == exits.NOT_MAPPED
    assert sent == []


def test_replacement_verification_rejects_inserted_substring():
    assert actions.compare_text("new", "oldnew", exact=True)["status"] == "mismatch"


def test_failed_verification_is_not_promoted():
    entry = {"action": "type", "length": 3, "verified": {"status": "mismatch"}}
    assert recipes.step_from_log(entry) is None


def test_gecko_unknown_modifier_never_shortens_a_chord():
    assert browser._gecko_chord({"modifiers": "accel,unknown", "key": "a"}) is None


def test_batch_stops_before_type_when_focus_fails(world, tmp_path, monkeypatch, capsys):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps([["focus", "missing"], ["type", "secret marker"]]))
    typed = []
    monkeypatch.setattr(actions, "type_text", lambda *a, **k: typed.append(True))
    assert cli.main(["batch", str(path), "--json"]) == exits.TARGET_NOT_FOUND
    result = json.loads(capsys.readouterr().out)
    assert result["completed"] == []
    assert result["failed"]["step"] == 1
    assert result["rollback"] is False
    assert typed == []
    assert "secret marker" not in json.dumps(result)


@pytest.mark.parametrize("drift,code", [("abort", 40), ("geometry", 20), ("content", 26)])
def test_batch_stops_after_completed_prefix(world, tmp_path, monkeypatch, capsys, drift, code):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps([["key", "--chord", "a"], ["type", "never sent"]]))
    def send(*args, **kwargs):
        if drift == "abort":
            config.set_abort()
        elif drift == "geometry":
            world["windows"][0]["logical_rect"]["w"] = 190
        else:
            world["windows"][0]["title"] = "different document"
    monkeypatch.setattr(actions, "send_chords", send)
    assert cli.main(["batch", str(path), "--json"]) == code
    result = json.loads(capsys.readouterr().out)
    assert len(result["completed"]) == 1
    assert result["failed"]["step"] == 2


def test_batch_reports_interruption_without_rollback(world, tmp_path, monkeypatch, capsys):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps([["key", "--chord", "a"], ["type", "never sent"]]))
    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(actions, "send_chords", interrupted)
    assert cli.main(["batch", str(path), "--json"]) == exits.ERROR
    result = json.loads(capsys.readouterr().out)
    assert result["failed"]["step"] == 1
    assert result["failed"]["may_have_acted"] is True
    assert result["rollback"] is False


def test_batch_unverified_readback_stops_following_key(world, tmp_path, monkeypatch, capsys):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps([["type", "value", "--verify"], ["key", "--chord", "enter"]]))
    monkeypatch.setattr(actions, "type_text", lambda *a, **k: {
        "action": "type", "dry_run": False, "verified": {"status": "unverified"},
    })
    assert cli.main(["batch", str(path), "--json"]) == exits.VERIFY_UNAVAILABLE
    result = json.loads(capsys.readouterr().out)
    assert result["failed"]["step"] == 1


def test_batch_validates_all_steps_before_any_input(world, tmp_path, monkeypatch):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps([["key", "--chord", "a"], ["abort", "--clear"]]))
    sent = []
    monkeypatch.setattr(actions, "send_chords", lambda *a, **k: sent.append(True))
    assert cli.main(["batch", str(path)]) == exits.USAGE
    assert sent == []


@pytest.mark.parametrize("value", ["nan", "inf", "-1", '"no"'])
def test_invalid_safety_number_refuses_before_input(value, tmp_path, monkeypatch, world):
    path = tmp_path / "config.toml"
    path.write_text(f"[safety]\ncursor_drift_tolerance = {value}\n")
    monkeypatch.setattr(config, "config_path", lambda: path)
    assert cli.main(["key", "--chord", "a", "--dry-run"]) == exits.PRECONDITION_FAILED
