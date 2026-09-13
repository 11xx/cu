"""The input path: chords, characters, configuration, and every refusal.

Nothing here creates a real device or touches a real screen. What is worth
testing is the decision-making that happens before an event is written, since
that is where a wrong answer becomes a click on the wrong pixel or a password
in the wrong window.
"""

from __future__ import annotations

import json

import pytest

from cu import actions, checks, cli, compositor, config, exits, observe, safety, uinput
from cu.safety import Refused

CFG = config.DEFAULTS


def window(address="0x1", cls="Alacritty", title="shell", **extra):
    return {
        "address": address,
        "class": cls,
        "title": title,
        "visible": True,
        "hidden": False,
        "focus_order": 0,
        **extra,
    }


def at(x, y, w, h, **extra):
    return window(logical_rect={"x": x, "y": y, "w": w, "h": h}, **extra)


# --- chords ---------------------------------------------------------------


def test_chord_splits_modifiers_from_the_key():
    held, final = uinput.parse_chord("ctrl+alt+shift+f4")
    assert held == [29, 56, 42]
    assert final == uinput.KEYS["f4"]


def test_chord_accepts_dashes_and_mixed_case():
    assert uinput.parse_chord("Ctrl-V") == uinput.parse_chord("ctrl+v")


def test_unknown_key_is_refused_rather_than_approximated():
    """The nearest chord is a different command, so there is no nearest chord."""
    with pytest.raises(uinput.ChordError):
        uinput.parse_chord("ctrl+nosuchkey")


def test_a_punctuation_key_may_be_written_as_itself():
    """`ctrl+-` is a browser's zoom out, and it must not read as a bare Ctrl:
    the kernel would accept that and the application would report nothing."""
    assert uinput.parse_chord("ctrl+-") == uinput.parse_chord("ctrl+minus")
    assert uinput.parse_chord("ctrl+/") == uinput.parse_chord("ctrl+slash")


def test_a_shifted_character_carries_its_own_shift():
    assert uinput.parse_chord("ctrl+?") == uinput.parse_chord("ctrl+shift+slash")


def test_an_empty_part_is_refused_rather_than_dropped():
    """Dropping it silently shortens the chord into a different command."""
    for chord in ("ctrl++", "ctrl+", "", "+"):
        with pytest.raises(uinput.ChordError):
            uinput.parse_chord(chord)


def test_a_modifier_in_the_final_position_is_still_a_key():
    _, final = uinput.parse_chord("ctrl")
    assert final == uinput.KEYS["ctrl"]


def test_a_key_in_a_modifier_position_is_refused():
    with pytest.raises(uinput.ChordError):
        uinput.parse_chord("f4+ctrl")


# --- characters -----------------------------------------------------------


@pytest.mark.parametrize(
    "char,name,shifted",
    [("a", "a", False), ("A", "a", True), ("{", "leftbrace", True),
     ("[", "leftbrace", False), (" ", "space", False), ("?", "slash", True)],
)
def test_characters_map_to_a_keystroke(char, name, shifted):
    assert uinput.char_to_key(char) == (name, shifted)


def test_unmappable_character_returns_none():
    """Refusing to type 'é' is correct; typing something else is not."""
    assert uinput.char_to_key("é") is None


# --- configuration --------------------------------------------------------


def test_user_config_overrides_only_what_it_names(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[safety]\nframe_max_age = 5.0\n')
    cfg = config.load(path)
    assert cfg["safety"]["frame_max_age"] == 5.0
    assert cfg["safety"]["denylist_classes"] == config.DEFAULTS["safety"]["denylist_classes"]


def test_a_shortened_list_is_honoured_rather_than_merged(tmp_path):
    """A user who removes an entry from the denylist means it."""
    path = tmp_path / "config.toml"
    path.write_text('[safety]\ndenylist_classes = ["only-this"]\n')
    assert config.load(path)["safety"]["denylist_classes"] == ["only-this"]


def test_an_unknown_configured_key_backend_is_refused_before_injection(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "config"
    path = root / "cu" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('[input]\nkey_backend = "compositr"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    opened = []
    monkeypatch.setattr(uinput, "Device", lambda **_kwargs: opened.append(True))

    assert cli.main(["key", "--chord", "a"]) == exits.PRECONDITION_FAILED
    error = capsys.readouterr().err
    assert "compositr" in error
    assert "compositor" in error
    assert "uinput" in error
    assert opened == []


@pytest.mark.parametrize("backend", ["compositor", "uinput"])
def test_configured_key_backend_matches_the_path_that_ran(
    backend, tmp_path, monkeypatch
):
    path = tmp_path / "config.toml"
    path.write_text(f'[input]\nkey_backend = "{backend}"\n')
    cfg = config.load(path)
    focused = window(cls="zen")
    sent = []

    monkeypatch.setattr(
        observe,
        "snapshot",
        lambda: {"focused": focused, "windows": [focused]},
    )
    monkeypatch.setattr(observe, "focused", lambda: focused)
    monkeypatch.setattr(safety, "preflight_keyboard", lambda *_args: None)
    monkeypatch.setattr(safety, "record", lambda *_args: None)
    monkeypatch.setattr(compositor, "send_chord", lambda chord: sent.append(("compositor", chord)))

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def chord(self, chord, _press_ms):
            sent.append(("uinput", chord))

    monkeypatch.setattr(uinput, "Device", Device)
    result = actions.key(cfg, action=None, chord="a", dry_run=False)

    assert sent == [(backend, "a")]
    assert result["backend"] == backend


def test_terminal_paste_beats_the_wildcard():
    assert config.chord_for(CFG, "Alacritty", "paste") == "ctrl+shift+v"
    assert config.chord_for(CFG, "zen", "paste") == "ctrl+v"


def test_an_unmapped_action_resolves_to_nothing():
    assert config.chord_for(CFG, "zen", "focus-urlbar") is None


def test_a_terminal_does_not_inherit_the_wildcard_it_would_misread():
    """ctrl+a is a shell's beginning-of-line and ctrl+z suspends the job.

    Inheriting either from the wildcard is the failure this whole module
    exists to prevent: the keystroke arrives and does something else.
    """
    assert config.chord_for(CFG, "Alacritty", "select-all") is None
    assert config.chord_for(CFG, "Alacritty", "undo") is None
    assert config.chord_for(CFG, "zen", "select-all") == "ctrl+a"


def test_emacs_uses_its_own_bindings_for_all_four_wildcard_actions():
    """Every wildcard chord does something else in Emacs: ctrl+v scrolls,
    ctrl+c waits for a second key, ctrl+a goes to the line start, and ctrl+z
    suspends the frame. None of them would report having done so."""
    assert config.chord_for(CFG, "emacs", "paste") == "ctrl+y"
    assert config.chord_for(CFG, "emacs", "copy") == "alt+w"
    assert config.chord_for(CFG, "emacs", "undo") == "ctrl+/"


def test_emacs_select_all_is_unmapped_because_it_is_not_a_chord():
    """`C-x h` is a two-key sequence, so there is nothing to approximate it
    with; verification reports unverified rather than comparing garbage."""
    assert config.chord_for(CFG, "emacs", "select-all") is None


def test_every_bound_default_chord_parses():
    """A chord in the shipped keymap that cannot be parsed would fail at the
    moment of injection, which is the worst place to discover it."""
    for window_class, table in config.DEFAULTS["keys"].items():
        for action, chord in table.items():
            if chord:
                assert uinput.parse_chord(chord), f"{window_class}.{action}"


def test_an_empty_chord_in_a_user_table_unmaps_the_wildcard(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[keys.zen]\nundo = ""\n')
    cfg = config.load(path)
    assert config.chord_for(cfg, "zen", "undo") is None
    assert config.chord_for(cfg, "zen", "paste") == "ctrl+v"


def test_output_frame_preflight_uses_snapshot_monitors_without_another_query(
    monkeypatch
):
    monitor = {
        "name": "DP-1",
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100},
    }
    stored = {
        "id": "f1",
        "captured_at": __import__("time").time(),
        "logical_rect": monitor["logical_rect"],
        "image_size": {"w": 100, "h": 100},
        "window": None,
        "monitor": monitor,
    }
    snapshot = {
        "monitors": [monitor],
        "windows": [],
        "cursor_logical": {"x": 0, "y": 0},
        "layer_surfaces": [],
    }
    monkeypatch.setattr(actions.frames, "load", lambda _frame_id: stored)
    monkeypatch.setattr(observe, "snapshot", lambda: snapshot)
    monkeypatch.setattr(
        observe,
        "monitors",
        lambda: pytest.fail("frame preflight queried monitors again"),
    )
    monkeypatch.setattr(
        observe,
        "windows",
        lambda _monitors: pytest.fail("frame preflight queried clients again"),
    )

    resolved = actions.resolve_target("f1", (10, 10), CFG)
    assert resolved["aimed"]["logical"] == {"x": 10, "y": 10}


@pytest.mark.parametrize("replacement_class", ["second", "first"])
def test_target_window_refuses_replacement_before_the_button_event(
    monkeypatch, replacement_class
):
    """A target assertion prevents a delayed replacement from receiving input."""
    monitor = {
        "name": "DP-1",
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100},
    }
    target = at(0, 0, 100, 100, address="0x1", cls="first")
    replacement = {**target, "address": "0x2", "class": replacement_class}
    frame = {
        "id": "f1",
        "captured_at": __import__("time").time(),
        "logical_rect": monitor["logical_rect"],
        "image_size": {"w": 100, "h": 100},
        "window": None,
        "target_window": {
            key: target[key] for key in ("address", "class", "title", "logical_rect")
        },
        "monitor": monitor,
    }
    current = [target]
    snapshot = {
        "monitors": [monitor],
        "windows": current,
        "cursor_logical": {"x": 10, "y": 10},
        "layer_surfaces": [],
    }
    clicked = []

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            current[:] = [replacement]
            return self

        def __exit__(self, *_args):
            pass

        def click(self, *args):
            clicked.append(args)

    monkeypatch.setattr(actions.frames, "load", lambda _frame_id: frame)
    monkeypatch.setattr(observe, "snapshot", lambda: snapshot)
    monkeypatch.setattr(observe, "monitors", lambda: [monitor])
    monkeypatch.setattr(observe, "windows", lambda _monitors: current)
    monkeypatch.setattr(observe, "cursor", lambda: {"x": 50, "y": 50})
    monkeypatch.setattr(observe, "layer_surfaces", lambda: [])
    monkeypatch.setattr(actions.hypr, "move_cursor", lambda *_args: None)
    monkeypatch.setattr(actions.uinput, "Device", Device)
    monkeypatch.setattr(checks, "_processes", lambda **_kwargs: [])

    assert cli.main(["click", "--frame", "f1", "--at", "50,50"]) == exits.FOCUS_CHANGED
    assert clicked == []


def test_target_window_refuses_scale_drift_after_device_settlement(monkeypatch):
    captured_monitor = {
        "name": "DP-1",
        "scale": 1.0,
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100},
    }
    live_monitor = {**captured_monitor}
    target = at(0, 0, 100, 100, address="0x1", cls="first")
    frame = {
        "id": "f1",
        "captured_at": __import__("time").time(),
        "logical_rect": captured_monitor["logical_rect"],
        "image_size": {"w": 100, "h": 100},
        "window": None,
        "target_window": target,
        "monitor": captured_monitor,
    }
    current = [target]
    snapshot = {
        "monitors": [captured_monitor],
        "windows": current,
        "focused": target,
        "cursor_logical": {"x": 50, "y": 50},
        "layer_surfaces": [],
    }
    clicked = []

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            live_monitor["scale"] = 1.25
            return self

        def __exit__(self, *_args):
            pass

        def click(self, *args):
            clicked.append(args)

    monkeypatch.setattr(actions.frames, "load", lambda _frame_id: frame)
    monkeypatch.setattr(observe, "snapshot", lambda: snapshot)
    monkeypatch.setattr(observe, "monitors", lambda: [live_monitor])
    monkeypatch.setattr(observe, "windows", lambda _monitors: current)
    monkeypatch.setattr(observe, "cursor", lambda: {"x": 50, "y": 50})
    monkeypatch.setattr(observe, "layer_surfaces", lambda: [])
    monkeypatch.setattr(actions.hypr, "move_cursor", lambda *_args: None)
    monkeypatch.setattr(actions.uinput, "Device", Device)
    monkeypatch.setattr(checks, "_processes", lambda **_kwargs: [])

    assert cli.main(["click", "--frame", "f1", "--at", "50,50"]) == exits.STALE_FRAME
    assert clicked == []


def test_window_frame_refuses_a_layer_appearing_after_device_settlement(monkeypatch):
    monitor = {
        "name": "DP-1",
        "scale": 1.0,
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100},
    }
    target = at(0, 0, 100, 100, address="0x1", cls="first")
    frame = {
        "id": "f1",
        "captured_at": __import__("time").time(),
        "logical_rect": monitor["logical_rect"],
        "image_size": {"w": 100, "h": 100},
        "window": {key: target[key] for key in ("address", "class", "title", "logical_rect")},
        "monitor": monitor,
    }
    layers = []
    snapshot = {
        "monitors": [monitor],
        "windows": [target],
        "focused": target,
        "cursor_logical": {"x": 50, "y": 50},
        "layer_surfaces": layers,
    }
    clicked = []

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            layers[:] = [{
                "address": "layer-1",
                "namespace": "modal",
                "level": 3,
                "logical_rect": monitor["logical_rect"],
            }]
            return self

        def __exit__(self, *_args):
            pass

        def click(self, *args):
            clicked.append(args)

    monkeypatch.setattr(actions.frames, "load", lambda _frame_id: frame)
    monkeypatch.setattr(observe, "snapshot", lambda: snapshot)
    monkeypatch.setattr(observe, "monitors", lambda: [monitor])
    monkeypatch.setattr(observe, "windows", lambda _monitors: [target])
    monkeypatch.setattr(observe, "cursor", lambda: {"x": 50, "y": 50})
    monkeypatch.setattr(observe, "layer_surfaces", lambda: layers)
    monkeypatch.setattr(actions.hypr, "move_cursor", lambda *_args: None)
    monkeypatch.setattr(actions.uinput, "Device", Device)
    monkeypatch.setattr(checks, "_processes", lambda **_kwargs: [])

    assert cli.main(["click", "--frame", "f1", "--at", "50,50"]) == exits.TARGET_AMBIGUOUS
    assert clicked == []


def test_warp_refuses_a_replacement_after_compositor_settlement(monkeypatch):
    monitor = {
        "name": "DP-1",
        "scale": 1.0,
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100},
    }
    target = at(0, 0, 100, 100, address="0x1", cls="first")
    replacement = {**target, "address": "0x2", "class": "second"}
    current = [target]
    frame = {
        "id": "f1",
        "captured_at": __import__("time").time(),
        "logical_rect": monitor["logical_rect"],
        "image_size": {"w": 100, "h": 100},
        "window": None,
        "target_window": target,
        "monitor": monitor,
    }
    snapshot = {
        "monitors": [monitor],
        "windows": current,
        "focused": target,
        "cursor_logical": {"x": 50, "y": 50},
        "layer_surfaces": [],
    }

    def move_cursor(*_args):
        current[:] = [replacement]

    monkeypatch.setattr(actions.frames, "load", lambda _frame_id: frame)
    monkeypatch.setattr(observe, "snapshot", lambda: snapshot)
    monkeypatch.setattr(observe, "monitors", lambda: [monitor])
    monkeypatch.setattr(observe, "windows", lambda _monitors: current)
    monkeypatch.setattr(observe, "cursor", lambda: {"x": 50, "y": 50})
    monkeypatch.setattr(observe, "layer_surfaces", lambda: [])
    monkeypatch.setattr(actions.hypr, "move_cursor", move_cursor)
    monkeypatch.setattr(checks, "_processes", lambda **_kwargs: [])

    assert cli.main(["warp", "--frame", "f1", "--at", "50,50"]) == exits.FOCUS_CHANGED


@pytest.mark.parametrize("child_after_click", [False, True])
def test_aimed_type_rechecks_the_target_before_text(
    monkeypatch, child_after_click
):
    monitor = {
        "name": "DP-1",
        "scale": 1.0,
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100},
    }
    target = at(0, 0, 100, 100, address="0x1", cls="first")
    child = {**target, "address": "0x2", "class": "dialog", "focus_order": 1}
    current = [target]
    frame = {
        "id": "f1",
        "captured_at": __import__("time").time(),
        "logical_rect": monitor["logical_rect"],
        "image_size": {"w": 100, "h": 100},
        "window": None,
        "target_window": target,
        "monitor": monitor,
    }
    snapshot = {
        "monitors": [monitor],
        "windows": current,
        "focused": target,
        "cursor_logical": {"x": 50, "y": 50},
        "layer_surfaces": [],
    }
    events = []

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def click(self, *args):
            events.append(("button", args))
            if child_after_click:
                current.append(child)

        def chord(self, chord, _press_ms):
            events.append(("text", chord))

    monkeypatch.setattr(actions.frames, "load", lambda _frame_id: frame)
    monkeypatch.setattr(observe, "snapshot", lambda: snapshot)
    monkeypatch.setattr(observe, "monitors", lambda: [monitor])
    monkeypatch.setattr(observe, "windows", lambda _monitors: current)
    monkeypatch.setattr(observe, "focused", lambda: target)
    monkeypatch.setattr(observe, "cursor", lambda: {"x": 50, "y": 50})
    monkeypatch.setattr(observe, "layer_surfaces", lambda: [])
    monkeypatch.setattr(actions.hypr, "move_cursor", lambda *_args: None)
    monkeypatch.setattr(actions.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(actions.uinput, "Device", Device)
    monkeypatch.setattr(checks, "_processes", lambda **_kwargs: [])

    code = cli.main([
        "type", "probe", "--method", "keys", "--frame", "f1", "--at", "50,50"
    ])
    assert code == (exits.TARGET_AMBIGUOUS if child_after_click else exits.OK)
    assert [kind for kind, _args in events] == (
        ["button"] if child_after_click else ["button", "text", "text", "text", "text", "text"]
    )


def test_target_window_refuses_an_ambiguous_overlap():
    target = {
        "address": "0x1",
        "class": "first",
        "title": "first",
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100},
    }
    other = {**target, "address": "0x2", "class": "second", "focus_order": 1}
    frame = {"id": "f1", "target_window": target}
    with pytest.raises(Refused) as caught:
        safety.check_target(frame, target, covering=[target, other])
    assert caught.value.code == exits.TARGET_AMBIGUOUS


def test_aim_outside_active_output_union_refuses_before_warp(monkeypatch):
    monitors = [
        {"name": "LEFT", "logical_rect": {"x": 0, "y": 0, "w": 10, "h": 10}},
        {"name": "RIGHT", "logical_rect": {"x": 20, "y": 0, "w": 10, "h": 10}},
    ]
    frame = {
        "id": "f1",
        "captured_at": __import__("time").time(),
        "logical_rect": {"x": 0, "y": 0, "w": 30, "h": 10},
        "image_size": {"w": 30, "h": 10},
        "monitor": monitors[0],
        "window": None,
    }
    snapshot = {
        "monitors": monitors,
        "windows": [],
        "cursor_logical": {"x": 1, "y": 1},
        "layer_surfaces": [],
    }
    warps = []
    monkeypatch.setattr(actions.frames, "load", lambda _frame_id: frame)
    monkeypatch.setattr(observe, "snapshot", lambda: snapshot)
    monkeypatch.setattr(actions.hypr, "move_cursor", lambda *args: warps.append(args))
    monkeypatch.setattr(checks, "_processes", lambda **_kwargs: [])
    assert cli.main(["warp", "--frame", "f1", "--at", "15,1"]) == exits.UNREACHABLE_AIM
    assert warps == []


def test_a_field_cu_cannot_select_reports_unverified(monkeypatch):
    """No select-all means no read-back, which is not the same as a mismatch:
    reporting failure would send an agent to retype text that did arrive.

    Nothing may be injected or written to the clipboard on the way to that
    answer — the user's selection belongs to them.
    """
    touched = []
    monkeypatch.setattr(actions, "send_chords", lambda *a, **k: touched.append("keys"))
    monkeypatch.setattr(actions, "_clipboard_write", lambda data: touched.append("copy"))
    monkeypatch.setattr(actions, "_clipboard_read", lambda: touched.append("paste"))
    result = actions.read_back(CFG, window(cls="Alacritty"), "hello", "uinput")
    assert result["status"] == "unverified"
    assert touched == []


# --- the denylist ---------------------------------------------------------


def test_denylisted_class_is_refused():
    with pytest.raises(Refused) as caught:
        safety.check_denylist(CFG, window(cls="KeePassXC"))
    assert caught.value.code == exits.DENIED_BY_POLICY


def test_denylist_matches_a_title_too():
    """The same secret behind a different toolkit is still the same secret."""
    with pytest.raises(Refused) as caught:
        safety.check_denylist(CFG, window(cls="zen", title="Sign in — Bank"))
    assert caught.value.code == exits.DENIED_BY_POLICY


def test_an_ordinary_window_passes():
    safety.check_denylist(CFG, window(title="README.md"))


# --- pointer and focus assertions ----------------------------------------


def test_pointer_yields_to_a_hand_on_the_mouse():
    with pytest.raises(Refused) as caught:
        safety.confirm_pointer(CFG, (500.0, 500.0), {"x": 900.0, "y": 500.0})
    assert caught.value.code == exits.HUMAN_ACTIVE
    assert caught.value.code in exits.RETRYABLE


def test_rounding_of_the_readback_is_not_a_human():
    safety.confirm_pointer(CFG, (500.4, 500.6), {"x": 500.0, "y": 501.0})


def test_keystrokes_refuse_a_window_that_lost_focus():
    with pytest.raises(Refused) as caught:
        safety.check_focus(CFG, window("0x1"), window("0x2", cls="zen"))
    assert caught.value.code == exits.FOCUS_CHANGED


def test_a_window_frame_refuses_a_point_something_else_now_covers():
    frame = {"id": "f1", "window": {"address": "0x1", "class": "Alacritty"}}
    with pytest.raises(Refused) as caught:
        safety.check_target(frame, window("0x9", cls="zen"))
    assert caught.value.code == exits.FOCUS_CHANGED


def test_a_click_on_an_unfocused_window_is_allowed():
    """Clicking a window is how it becomes focused; requiring focus first
    would forbid the ordinary case."""
    frame = {"id": "f1", "window": {"address": "0x1", "class": "Alacritty"}}
    safety.check_target(frame, window("0x1"))


def test_a_click_rechecks_the_target_after_device_settlement(monkeypatch):
    target = at(0, 0, 100, 100)
    overlay = at(0, 0, 100, 100, address="0x2", cls="zen")
    frame = {
        "id": "f1",
        "captured_at": __import__("time").time(),
        "logical_rect": target["logical_rect"],
        "image_size": {"w": 100, "h": 100},
        "window": {"address": target["address"], "class": target["class"],
                   "logical_rect": target["logical_rect"]},
    }
    snapshot = {
        "windows": [target], "monitors": [], "focused": target,
        "cursor_logical": {"x": 10, "y": 10},
    }
    monkeypatch.setattr(actions.frames, "load", lambda _id: frame)
    monkeypatch.setattr(observe, "snapshot", lambda: snapshot)
    monkeypatch.setattr(observe, "windows", lambda _monitors: [overlay])
    monkeypatch.setattr(observe, "cursor", lambda: {"x": 10, "y": 10})
    monkeypatch.setattr(observe, "monitors", lambda: [])
    monkeypatch.setattr(observe, "layer_surfaces", lambda: [])
    monkeypatch.setattr(actions, "warp", lambda *a, **k: {"x": 10, "y": 10})
    monkeypatch.setattr(safety, "_common", lambda *a, **k: None)

    clicks = []

    class Device:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def click(self, *_args): clicks.append(True)

    monkeypatch.setattr(uinput, "Device", Device)
    with pytest.raises(Refused) as caught:
        actions.click(CFG, "f1", (10, 10), dry_run=False)
    assert caught.value.code == exits.FOCUS_CHANGED
    assert clicks == []


def test_focus_confirms_the_dispatch_took_effect(monkeypatch):
    wanted = window("0x1")
    monkeypatch.setattr(safety, "check_abort", lambda: None)
    monkeypatch.setattr(safety, "check_session", lambda: None)
    monkeypatch.setattr(safety, "check_denylist", lambda *a: None)
    monkeypatch.setattr(actions.hypr, "lua", lambda _code: "")
    monkeypatch.setattr(observe, "focused", lambda: window("0x2", cls="zen"))
    with pytest.raises(Refused) as caught:
        actions.focus(CFG, wanted, dry_run=False)
    assert caught.value.code == exits.FOCUS_CHANGED


def test_lock_screen_appearing_between_repeated_chords_is_caught(monkeypatch):
    focused = window(cls="zen")
    snapshots = iter(
        [
            [],
            [],
            [(7, ["/usr/bin/hyprlock"])],
        ]
    )
    sent = []
    monkeypatch.setattr(
        observe,
        "snapshot",
        lambda: {"focused": focused, "windows": [focused]},
    )
    monkeypatch.setattr(observe, "focused", lambda: focused)
    monkeypatch.setattr(checks, "_processes", lambda *, fresh=False: next(snapshots))
    monkeypatch.setattr(safety, "check_abort", lambda: None)
    monkeypatch.setattr(safety, "check_denylist", lambda *_args: None)

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def chord(self, chord, _press_ms):
            sent.append(chord)

    monkeypatch.setattr(uinput, "Device", Device)

    with pytest.raises(Refused) as caught:
        actions.key(CFG, action=None, chord="a", repeat=2, dry_run=False)
    assert caught.value.code == exits.DENIED_BY_POLICY
    assert sent == ["a"]


def test_an_output_frame_asserts_no_particular_window():
    safety.check_target({"id": "f1", "window": None}, window("0xanything"))


def _native_move_fixture(monkeypatch):
    current = [{
        "address": "0x1", "class": "zen", "title": "fixture",
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100},
        "visible": True, "hidden": False, "focus_order": 0,
    }]
    frame = {
        "id": "f1", "captured_at": __import__("time").time(),
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100},
        "image_size": {"w": 100, "h": 100},
        "window": {"address": "0x1", "class": "zen", "title": "fixture",
                   "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100}},
    }
    cursor = [{"x": 10.0, "y": 10.0}]
    snapshot = {
        "windows": current, "monitors": [], "focused": current[0],
        "cursor_logical": cursor[0], "layer_surfaces": [],
    }
    monkeypatch.setattr(actions.frames, "load", lambda _frame_id: frame)
    monkeypatch.setattr(observe, "snapshot", lambda: snapshot)
    monkeypatch.setattr(observe, "focused", lambda: current[0])
    monkeypatch.setattr(observe, "windows", lambda _monitors: current)
    monkeypatch.setattr(observe, "cursor", lambda: cursor[0])
    monkeypatch.setattr(observe, "layer_surfaces", lambda: [])
    monkeypatch.setattr(checks, "_processes", lambda **_kwargs: [])
    return frame, cursor


def test_native_move_uses_relative_device_without_a_final_warp(monkeypatch):
    _frame, cursor = _native_move_fixture(monkeypatch)
    deltas = []

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def move_relative(self, dx, dy):
            deltas.append((dx, dy))
            cursor[0] = {"x": cursor[0]["x"] + dx, "y": cursor[0]["y"] + dy}

    monkeypatch.setattr(actions.uinput, "Device", Device)
    monkeypatch.setattr(
        actions.hypr, "move_cursor", lambda *_args: pytest.fail("native move warped")
    )
    monkeypatch.setattr(actions.time, "sleep", lambda _seconds: None)

    result = actions.move(CFG, "f1", (30, 30), duration=0.1, hz=10, timeout=1, dry_run=False)

    assert result["transport"] == "uinput-relative-v1"
    assert result["status"] == "converged"
    assert result["cursor"] == {"x": 30.0, "y": 30.0}
    assert deltas


@pytest.mark.parametrize("duration, expected_events", [(0.2, 1), (0.1, 2)])
def test_native_move_yields_when_pointer_moves_between_samples(
    monkeypatch, duration, expected_events
):
    _frame, cursor = _native_move_fixture(monkeypatch)
    clock = [0.0]
    moves = []
    bumped = [False]
    monkeypatch.setattr(actions.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        clock[0] += seconds
        if moves and not bumped[0]:
            cursor[0] = {"x": 70.0, "y": 70.0}
            bumped[0] = True

    monkeypatch.setattr(actions.time, "sleep", sleep)

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def move_relative(self, dx, dy):
            moves.append((dx, dy))
            cursor[0] = {"x": cursor[0]["x"] + dx, "y": cursor[0]["y"] + dy}

    monkeypatch.setattr(actions.uinput, "Device", Device)
    with pytest.raises(Refused) as caught:
        # Keep the target beyond one bounded native sample so the injected
        # human displacement is observed at the next boundary.
        actions.move(CFG, "f1", (80, 80), duration=duration, hz=10, timeout=1, dry_run=False)
    assert caught.value.code == exits.HUMAN_ACTIVE
    assert len(moves) == expected_events


def test_native_move_accepts_delayed_own_event_delivery(monkeypatch):
    _frame, cursor = _native_move_fixture(monkeypatch)
    clock = [0.0]
    pending = []

    def sleep(seconds):
        clock[0] += seconds
        while pending:
            dx, dy = pending.pop(0)
            cursor[0] = {"x": cursor[0]["x"] + dx, "y": cursor[0]["y"] + dy}

    monkeypatch.setattr(actions.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(actions.time, "sleep", sleep)

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def move_relative(self, dx, dy):
            pending.append((dx, dy))

    monkeypatch.setattr(actions.uinput, "Device", Device)
    result = actions.move(
        CFG, "f1", (40, 40), duration=0.2, hz=10, timeout=1, dry_run=False
    )
    assert result["status"] == "converged"
    assert result["cursor"] == {"x": 40.0, "y": 40.0}


def test_native_move_refuses_a_start_outside_the_inspected_frame(monkeypatch):
    frame, _cursor = _native_move_fixture(monkeypatch)
    frame["logical_rect"] = {"x": 20, "y": 20, "w": 80, "h": 80}
    created = []
    monkeypatch.setattr(actions.uinput, "Device", lambda **_kwargs: created.append(True))
    with pytest.raises(Refused) as caught:
        actions.move(CFG, "f1", (30, 30), dry_run=False)
    assert caught.value.code == exits.MOTION_UNSAFE_ROUTE
    assert created == []


def test_native_move_reports_non_convergence(monkeypatch):
    _frame, _cursor = _native_move_fixture(monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(actions.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        actions.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def move_relative(self, _dx, _dy):
            pass

    monkeypatch.setattr(actions.uinput, "Device", Device)
    with pytest.raises(Refused) as caught:
        actions.move(CFG, "f1", (30, 30), duration=0.1, hz=10, timeout=0.3, dry_run=False)
    assert caught.value.code == exits.MOTION_NOT_CONVERGED


def test_native_move_deadline_includes_route_preflight(monkeypatch):
    _frame, _cursor = _native_move_fixture(monkeypatch)
    clock = [0.0]
    created = []
    monkeypatch.setattr(actions.time, "monotonic", lambda: clock[0])
    original_guard = actions._motion_route_point_guard

    def slow_guard(*args, **kwargs):
        clock[0] += 0.11
        return original_guard(*args, **kwargs)

    monkeypatch.setattr(actions, "_motion_route_point_guard", slow_guard)
    monkeypatch.setattr(actions.uinput, "Device", lambda **_kwargs: created.append(True))
    with pytest.raises(Refused) as caught:
        actions.move(CFG, "f1", (30, 30), duration=0.1, hz=10, timeout=0.1, dry_run=False)
    assert caught.value.code == exits.MOTION_NOT_CONVERGED
    assert "preflight" in caught.value.reason
    assert created == []


def test_native_move_refuses_a_tiny_motion_region(monkeypatch):
    frame, _cursor = _native_move_fixture(monkeypatch)
    frame["logical_rect"] = {"x": 9, "y": 9, "w": 3, "h": 3}
    with pytest.raises(Refused) as caught:
        actions.move(CFG, "f1", (50, 50), dry_run=False)
    assert caught.value.code == exits.MOTION_UNSAFE_ROUTE
    assert "inspected region" in caught.value.reason


def test_native_move_timings_report_phases_and_read_counts(monkeypatch, capsys, tmp_path):
    _frame, cursor = _native_move_fixture(monkeypatch)
    monkeypatch.setattr(config, "log_path", lambda _cfg: tmp_path / "actions.jsonl")

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def move_relative(self, dx, dy):
            cursor[0] = {"x": cursor[0]["x"] + dx, "y": cursor[0]["y"] + dy}

    monkeypatch.setattr(actions.uinput, "Device", Device)
    monkeypatch.setattr(actions.time, "sleep", lambda _seconds: None)
    assert cli.main([
        "move", "--frame", "f1", "--at", "30,30", "--duration", "0.1",
        "--hz", "10", "--timings", "--json",
    ]) == exits.OK
    captured = capsys.readouterr()
    report = json.loads(captured.err.removeprefix("cu metrics: ").strip())
    assert report["seconds"]["native_preflight"] >= 0
    assert report["seconds"]["native_delivery"] >= 0
    assert report["seconds"]["native_convergence"] >= 0
    assert report["operations"]["native_snapshot"] == 1
    assert report["operations"]["native_delta_event"] >= 1
    assert report["operations"]["native_cursor_read"] >= 1


def test_closing_a_device_writes_explicit_releases(monkeypatch):
    writes = []
    device = uinput.Device(settle_ms=0)
    device._fd = 123
    device._held = {uinput.KEYS["ctrl"]}
    monkeypatch.setattr(uinput.os, "write", lambda fd, data: writes.append((fd, data)) or len(data))
    monkeypatch.setattr(uinput.os, "close", lambda _fd: None)
    monkeypatch.setattr(uinput.fcntl, "ioctl", lambda *_args: None)
    device.close()
    assert len(writes) == 2  # key-up, then SYN_REPORT
    assert device._fd is None


def test_relative_move_emits_native_axes_and_a_sync(monkeypatch):
    writes = []
    device = uinput.Device(settle_ms=0)
    device._fd = 123
    monkeypatch.setattr(uinput.os, "write", lambda fd, data: writes.append((fd, data)) or len(data))
    device.move_relative(3, -2)
    events = [uinput.struct.unpack("@llHHi", data)[2:] for _fd, data in writes]
    assert events == [
        (uinput.EV_REL, uinput.REL_X, 3),
        (uinput.EV_REL, uinput.REL_Y, -2),
        (uinput.EV_SYN, uinput.SYN_REPORT, 0),
    ]


def test_typing_restores_an_initially_empty_clipboard(monkeypatch):
    focused = window(cls="zen")
    writes = []
    cleared = []
    monkeypatch.setattr(observe, "snapshot", lambda: {"focused": focused, "windows": [focused]})
    monkeypatch.setattr(safety, "_common", lambda *a: None)
    monkeypatch.setattr(actions.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(actions, "_clipboard_read", lambda: None)
    monkeypatch.setattr(actions, "_clipboard_write", writes.append)
    monkeypatch.setattr(actions, "_clipboard_clear", lambda: cleared.append(True))
    monkeypatch.setattr(actions, "send_chords", lambda *a, **k: None)
    monkeypatch.setattr(actions.time, "sleep", lambda _seconds: None)
    actions.type_text(CFG, "private text", dry_run=False)
    assert writes == [b"private text"]
    assert cleared == [True]


@pytest.mark.parametrize("call", [
    lambda: actions.click(CFG, "f1", (1, 1), count=0, dry_run=True),
    lambda: actions.key(CFG, action=None, chord="a", repeat=0, dry_run=True),
])
def test_non_positive_action_counts_are_usage_errors(call):
    with pytest.raises(ValueError):
        call()


@pytest.mark.parametrize("argv", [["scroll"], ["scroll", "--dx", "0", "--dy", "0"]])
def test_zero_scroll_is_usage_and_never_reaches_the_action(argv, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(actions, "scroll", lambda *_args, **_kwargs: called.append(True))

    assert cli.main(argv) == exits.USAGE
    assert called == []
    error = capsys.readouterr().err
    assert "--dx" in error
    assert "--dy" in error


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["scroll", "--dy", "-2"], {"dy": -2, "dx": 0}),
        (["scroll", "--dx", "3"], {"dy": 0, "dx": 3}),
    ],
)
def test_nonzero_scroll_reaches_the_action(argv, expected, monkeypatch):
    called = []

    def scroll(_cfg, **kwargs):
        called.append({"dy": kwargs["dy"], "dx": kwargs["dx"]})
        return {
            "action": "scroll",
            "dy": kwargs["dy"],
            "dx": kwargs["dx"],
            "frame": None,
            "relative": None,
            "cursor": {"x": 0, "y": 0},
            "window": None,
            "dry_run": kwargs["dry_run"],
        }

    monkeypatch.setattr(actions, "scroll", scroll)
    assert cli.main(argv) == exits.OK
    assert called == [expected]


def test_warp_and_move_have_distinct_cli_contracts():
    parser = cli.build_parser()
    warp = parser.parse_args(["warp", "--frame", "f1", "--at", "1,1"])
    move = parser.parse_args(["move", "--frame", "f1", "--at", "1,1"])
    assert warp.command == "warp"
    assert not hasattr(warp, "duration")
    assert move.command == "move"
    assert move.duration == actions.DEFAULT_MOVE_DURATION
    assert move.hz == actions.DEFAULT_MOVE_HZ


def test_key_rechecks_focus_immediately_before_the_chord(monkeypatch):
    expected = window("0x1")
    monkeypatch.setattr(
        observe,
        "snapshot",
        lambda: {"focused": expected, "windows": [expected]},
    )
    monkeypatch.setattr(safety, "_common", lambda *a: None)
    monkeypatch.setattr(observe, "focused", lambda: window("0x2", cls="zen"))

    def send(_cfg, _chords, _backend, *, before_send):
        before_send()

    monkeypatch.setattr(actions, "send_chords", send)
    with pytest.raises(Refused) as caught:
        actions.key(CFG, action=None, chord="a", dry_run=False)
    assert caught.value.code == exits.FOCUS_CHANGED


# --- what is under a point ------------------------------------------------


def test_topmost_window_wins_a_contested_point():
    """Lower focus_order means more recently focused, the best stacking proxy
    Hyprland exposes."""
    lower = at(0, 0, 100, 100, address="0xlow", focus_order=5)
    upper = at(50, 50, 100, 100, address="0xup", focus_order=1)
    assert observe.window_at(60, 60, [lower, upper])["address"] == "0xup"


def test_invisible_windows_are_not_under_anything():
    hidden = at(0, 0, 100, 100, visible=False)
    assert observe.window_at(10, 10, [hidden]) is None


def test_a_point_outside_every_window_resolves_to_none():
    assert observe.window_at(999, 999, [at(0, 0, 100, 100)]) is None


def test_the_far_edge_belongs_to_the_next_window():
    """Half-open rectangles, so adjacent tiled windows cannot both claim a pixel."""
    assert observe.window_at(100, 50, [at(0, 0, 100, 100)]) is None


# --- the abort flag -------------------------------------------------------


def test_abort_is_refused_and_never_retried(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "cu").mkdir()
    (tmp_path / "cu" / config.ABORT_FILENAME).touch()
    with pytest.raises(Refused) as caught:
        safety.check_abort()
    assert caught.value.code == exits.ABORTED_BY_USER
    assert caught.value.code not in exits.RETRYABLE


def test_no_runtime_dir_reads_as_aborted(monkeypatch):
    """No session is itself a reason not to inject."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert config.aborted() is True


# --- the compositor keyboard backend --------------------------------------


def test_chords_translate_to_the_dispatcher_spelling():
    assert compositor.split_chord("ctrl+shift+v") == ("CTRL SHIFT", "v")
    assert compositor.split_chord("ctrl+alt+shift+f4") == ("CTRL ALT SHIFT", "F4")


def test_keysyms_differ_from_the_names_a_chord_uses():
    """`enter` is Return and `pagedown` is Next; sending the cu name would be
    accepted and do nothing."""
    assert compositor.keysym_for("enter") == "Return"
    assert compositor.keysym_for("pagedown") == "Next"
    assert compositor.keysym_for("dot") == "period"


def test_a_key_with_no_keysym_says_so_rather_than_guessing():
    with pytest.raises(compositor.QuietError):
        compositor.keysym_for("volumeup")


def test_both_backends_reject_the_same_bad_chord():
    """Backends must not disagree about what a valid chord is."""
    with pytest.raises(uinput.ChordError):
        compositor.split_chord("ctrl+nosuchkey")


# --- flag placement -------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "observe"],
        ["observe", "--json"],
        ["--json", "recipe", "list"],
        ["recipe", "list", "--json"],
        ["recipe", "replay", "x", "--dry-run"],
        ["browser", "list", "--json"],
    ],
)
def test_global_flags_are_accepted_on_either_side_of_a_subcommand(argv):
    """`cu recipe list --json` is the form an agent writes, and argparse honours
    a flag only on the parser that declares it — so every level repeats them."""
    from cu import cli

    args = cli.build_parser().parse_args(argv)
    assert getattr(args, "json", False) or getattr(args, "dry_run", False)


# --- reading typed text back ----------------------------------------------


def test_text_that_arrived_intact_verifies():
    assert actions.compare_text("hello", "hello")["status"] == "ok"


def test_a_field_that_already_held_text_still_verifies():
    """select-all returns the whole field, so containment is the right test."""
    assert actions.compare_text("hello", "say hello there")["status"] == "ok"


def test_dropped_leading_characters_are_a_mismatch():
    """The failure the settle delay causes: the beginning is eaten and the
    rest lands, which looks like success at the call site."""
    result = actions.compare_text("hello", "llo")
    assert result["status"] == "mismatch"
    assert "agree for the first 0" in result["detail"]


def test_a_truncated_tail_reports_how_far_they_agreed():
    result = actions.compare_text("hello world", "hello")
    assert result["status"] == "mismatch"
    assert "agree for the first 5" in result["detail"]


def test_an_empty_read_back_is_unverified_not_a_failure():
    """A field that cannot be read back must not be reported as verified, and
    must not be reported as a failure either."""
    assert actions.compare_text("hello", "")["status"] == "unverified"


def test_verification_failure_is_not_retryable():
    """Retyping blindly can duplicate whatever did land."""
    assert exits.VERIFY_FAILED not in exits.RETRYABLE


# --- aimed typing ---------------------------------------------------------


def _aimed(monkeypatch, clicked, focused, *, sent=None):
    """Stub the click half of an aimed type and report what typing saw."""
    monkeypatch.setattr(safety, "_common", lambda *a: None)
    monkeypatch.setattr(
        observe, "snapshot", lambda: {"focused": focused, "windows": [focused]}
    )
    monkeypatch.setattr(
        actions, "_aimed_click", lambda *a, **k: clicked
    )
    monkeypatch.setattr(actions.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(actions, "_clipboard_read", lambda: None)
    monkeypatch.setattr(actions, "_clipboard_write", (sent if sent is not None else []).append)
    monkeypatch.setattr(actions, "_clipboard_clear", lambda: None)
    monkeypatch.setattr(actions, "send_chords", lambda *a, **k: None)
    monkeypatch.setattr(actions.time, "sleep", lambda _seconds: None)


def _click_result(win):
    return {
        "frame": "f23", "at": {"x": 455, "y": 320},
        "_frame": {"id": "f23", "window": {"address": win["address"], "class": win["class"],
                                          "title": win["title"]}},
        "logical": {"x": 500.0, "y": 400.0},
        "relative": {"x": 0.5, "y": 0.4},
        "button": "left", "count": 1,
        "window": {"address": win["address"], "class": win["class"], "title": win["title"]},
        "window_size": {"w": 800, "h": 600},
    }


def test_an_aimed_type_records_both_halves(monkeypatch):
    field = window(cls="zen", logical_rect={"x": 0, "y": 0, "w": 800, "h": 600})
    sent = []
    _aimed(monkeypatch, _click_result(field), field, sent=sent)
    result = actions.type_text(CFG, "443", frame_id="f23", at=(455, 320), dry_run=False)
    assert sent == [b"443"]
    assert result["pointer"]["at"] == {"x": 455, "y": 320}
    assert result["pointer"]["relative"] == {"x": 0.5, "y": 0.4}


def test_a_click_that_did_not_take_focus_refuses_before_any_text(monkeypatch):
    """The click landing somewhere the keyboard did not follow is exactly the
    gap this command exists to close, so nothing is typed."""
    aimed_at = window(address="0x1", cls="zen", logical_rect={"x": 0, "y": 0, "w": 8, "h": 6})
    elsewhere = window(address="0x2", cls="Alacritty")
    sent = []
    _aimed(monkeypatch, _click_result(aimed_at), elsewhere, sent=sent)
    with pytest.raises(Refused) as caught:
        actions.type_text(CFG, "443", frame_id="f23", at=(455, 320), dry_run=False)
    assert caught.value.code == exits.FOCUS_CHANGED
    assert sent == []


def test_half_an_aim_is_a_usage_error():
    with pytest.raises(ValueError):
        actions.type_text(CFG, "x", frame_id="f1", dry_run=True)


def test_an_unaimed_type_is_unchanged(monkeypatch):
    focused = window(cls="zen")
    sent = []
    _aimed(monkeypatch, None, focused, sent=sent)
    result = actions.type_text(CFG, "hello", dry_run=False)
    assert "pointer" not in result
    assert sent == [b"hello"]


def test_a_recipe_replays_an_aimed_type_as_one_step():
    from cu import recipes

    entry = {
        "action": "type", "length": 3, "method": "clipboard",
        "window": {"address": "0x1", "class": "zen"},
        "pointer": {
            "relative": {"x": 0.5, "y": 0.4}, "window_size": {"w": 800, "h": 600},
            "window": {"class": "zen"}, "button": "left", "count": 1,
        },
    }
    step = recipes.step_from_log(entry)
    assert step["action"] == "type"
    assert step["relative"] == {"x": 0.5, "y": 0.4}
    assert step["window_class"] == "zen"


def test_an_aim_that_lands_on_no_window_types_nothing(monkeypatch):
    """An aim with no window under it cannot say which field was selected, so
    the text would go wherever focus already was."""
    focused = window(address="0x2", cls="Alacritty")
    clicked = _click_result(window(cls="zen"))
    clicked["window"] = None
    sent = []
    _aimed(monkeypatch, clicked, focused, sent=sent)
    recorded = []
    monkeypatch.setattr(safety, "record", lambda _cfg, entry: recorded.append(entry))
    with pytest.raises(Refused) as caught:
        actions.type_text(CFG, "443", frame_id="f23", at=(455, 320), dry_run=False)
    assert caught.value.code == exits.TARGET_NOT_FOUND
    assert sent == []
    # The click still happened, so the log says so.
    assert [e["action"] for e in recorded] == ["click"]
    assert recorded[0]["typed"] is False


def test_a_click_whose_typing_refuses_is_still_logged(monkeypatch):
    """Nothing else writes that click down: the aimed action owns it."""
    aimed_at = window(address="0x1", cls="zen")
    elsewhere = window(address="0x2", cls="Alacritty")
    _aimed(monkeypatch, _click_result(aimed_at), elsewhere)
    recorded = []
    monkeypatch.setattr(safety, "record", lambda _cfg, entry: recorded.append(entry))
    with pytest.raises(Refused):
        actions.type_text(CFG, "443", frame_id="f23", at=(455, 320), dry_run=False)
    assert [e["action"] for e in recorded] == ["click"]
    assert "_frame" not in recorded[0]


def test_a_page_that_navigates_under_the_click_stops_the_text(monkeypatch):
    """A click can navigate as well as select, and the plan was about the
    document that was there before it."""
    field = window(cls="zen", title="page A", logical_rect={"x": 0, "y": 0, "w": 8, "h": 6})
    clicked = _click_result(field)
    clicked["_frame"] = {
        "id": "f23",
        "window": {"address": field["address"], "class": "zen", "title": "page A"},
    }
    navigated = window(cls="zen", title="page B", logical_rect={"x": 0, "y": 0, "w": 8, "h": 6})
    sent = []
    _aimed(monkeypatch, clicked, navigated, sent=sent)
    with pytest.raises(Refused) as caught:
        actions.type_text(CFG, "443", frame_id="f23", at=(455, 320), dry_run=False)
    assert caught.value.code == exits.CONTENT_CHANGED
    assert sent == []


def test_a_pointer_without_a_replayable_position_is_not_promoted():
    """Downgrading it to an unaimed step would replay by typing into whatever
    holds focus, which is the gap the aimed action closes — and dropping it
    would promote a different procedure from the one recorded."""
    from cu import recipes

    entry = {
        "action": "type", "length": 3, "method": "clipboard",
        "window": {"address": "0x1", "class": "zen"},
        "pointer": {"button": "left", "count": 1, "window": None},
    }
    with pytest.raises(recipes.RecipeError):
        recipes.step_from_log(entry)


def test_a_click_left_behind_by_a_refused_type_is_not_replayable():
    """It was part of a failure, not of a working run."""
    from cu import recipes

    entry = {
        "action": "click", "typed": False, "button": "left", "count": 1,
        "relative": {"x": 0.5, "y": 0.4},
        "window": {"address": "0x1", "class": "zen",
                   "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600}},
    }
    assert recipes.step_from_log(entry) is None


def test_text_that_landed_is_never_logged_as_a_bare_click(monkeypatch):
    """Once keystrokes are on their way the action is the typing, so a failure
    in the cleanup after it must not read as a click that did nothing."""
    field = window(cls="zen", logical_rect={"x": 0, "y": 0, "w": 800, "h": 600})
    _aimed(monkeypatch, _click_result(field), field)
    recorded = []
    monkeypatch.setattr(safety, "record", lambda _cfg, entry: recorded.append(entry))
    monkeypatch.setattr(actions, "_clipboard_read", lambda: b"before")

    def restore_fails(data):
        if data == b"before":
            raise actions.ClipboardError("wl-copy exited 1")

    monkeypatch.setattr(actions, "_clipboard_write", restore_fails)
    with pytest.raises(actions.ClipboardError):
        actions.type_text(CFG, "443", frame_id="f23", at=(455, 320), dry_run=False)
    assert [e["action"] for e in recorded] == []


@pytest.mark.parametrize("gain", [0.5, 1.0, 1.5, 2.0])
@pytest.mark.parametrize("delayed", [False, True])
@pytest.mark.parametrize("quantized", [False, True])
def test_native_cli_classifies_scaled_and_quantized_delivery(
    monkeypatch, capsys, tmp_path, gain, delayed, quantized
):
    _frame, cursor = _native_move_fixture(monkeypatch)
    monkeypatch.setattr(config, "log_path", lambda _cfg: tmp_path / "actions.jsonl")
    clock = [0.0]
    pending = []
    position = dict(cursor[0])
    emitted = []

    def apply(dx, dy):
        position["x"] += gain * dx
        position["y"] += gain * dy
        cursor[0] = {
            axis: round(value) if quantized else value
            for axis, value in position.items()
        }

    def sleep(seconds):
        clock[0] += seconds
        while pending:
            apply(*pending.pop(0))

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def move_relative(self, dx, dy):
            emitted.append((dx, dy))
            if delayed:
                pending.append((dx, dy))
            else:
                apply(dx, dy)

    monkeypatch.setattr(actions.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(actions.time, "sleep", sleep)
    monkeypatch.setattr(actions.uinput, "Device", Device)
    monkeypatch.setattr(actions.hypr, "move_cursor", lambda *_: pytest.fail("native move warped"))
    code = cli.main(["move", "--frame", "f1", "--at", "30,30", "--json"])
    if quantized and gain == 0.5:
        # The first half-pixel delta rounds back to the origin. With no
        # observable delivery, CU must not label it human or stack more input.
        assert code == exits.MOTION_NOT_CONVERGED
        assert len(emitted) == 1
        return
    assert code == exits.OK
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "converged"
    assert sum((result["cursor"][axis] - 30) ** 2 for axis in ("x", "y")) <= (
        actions.MOTION_TOLERANCE ** 2
    )


@pytest.mark.parametrize("direction", ["reverse", "transverse"])
def test_native_cli_yields_to_delivery_inconsistent_with_its_delta(
    monkeypatch, direction
):
    _frame, cursor = _native_move_fixture(monkeypatch)
    emitted = []

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def move_relative(self, dx, dy):
            emitted.append((dx, dy))
            mx, my = (-dx, -dy) if direction == "reverse" else (-dy, dx)
            cursor[0] = {"x": cursor[0]["x"] + mx, "y": cursor[0]["y"] + my}

    monkeypatch.setattr(actions.uinput, "Device", Device)
    monkeypatch.setattr(actions.time, "sleep", lambda _seconds: None)
    assert cli.main(["move", "--frame", "f1", "--at", "30,30"]) == exits.HUMAN_ACTIVE
    assert len(emitted) == 1


def test_native_cli_honors_abort_while_waiting_for_delivery(monkeypatch):
    _native_move_fixture(monkeypatch)
    clock = [0.0]
    submitted = [False]
    aborted = [False]

    def sleep(seconds):
        clock[0] += seconds
        if submitted[0]:
            aborted[0] = True

    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def move_relative(self, _dx, _dy):
            submitted[0] = True

    monkeypatch.setattr(actions.uinput, "Device", Device)
    monkeypatch.setattr(actions.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(actions.time, "sleep", sleep)
    monkeypatch.setattr(config, "aborted", lambda: aborted[0])
    assert cli.main([
        "move", "--frame", "f1", "--at", "30,30", "--duration", "0.1", "--hz", "10",
    ]) == exits.ABORTED_BY_USER
