"""The input path: chords, characters, configuration, and every refusal.

Nothing here creates a real device or touches a real screen. What is worth
testing is the decision-making that happens before an event is written, since
that is where a wrong answer becomes a click on the wrong pixel or a password
in the wrong window.
"""

from __future__ import annotations

import pytest

from cu import actions, compositor, config, exits, observe, safety, uinput
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


def test_an_output_frame_asserts_no_particular_window():
    safety.check_target({"id": "f1", "window": None}, window("0xanything"))


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


def test_typing_restores_an_initially_empty_clipboard(monkeypatch):
    focused = window(cls="zen")
    writes = []
    cleared = []
    monkeypatch.setattr(observe, "snapshot", lambda: {"focused": focused, "windows": [focused]})
    monkeypatch.setattr(safety, "preflight_keyboard", lambda *a: None)
    monkeypatch.setattr(actions.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(actions, "_clipboard_read", lambda: None)
    monkeypatch.setattr(actions, "_clipboard_write", writes.append)
    monkeypatch.setattr(actions, "_clipboard_clear", lambda: cleared.append(True))
    monkeypatch.setattr(actions, "send_chords", lambda *a: None)
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
