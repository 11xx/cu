"""The input path: chords, characters, configuration, and every refusal.

Nothing here creates a real device or touches a real screen. What is worth
testing is the decision-making that happens before an event is written, since
that is where a wrong answer becomes a click on the wrong pixel or a password
in the wrong window.
"""

from __future__ import annotations

import pytest

from cu import config, exits, observe, safety, uinput
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


def test_an_output_frame_asserts_no_particular_window():
    safety.check_target({"id": "f1", "window": None}, window("0xanything"))


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
