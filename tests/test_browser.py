"""Browser discovery and keymap resolution.

The one thing that must never happen here is a plausible chord for an action
the browser does not actually bind, so most of these tests are about refusing.
"""

from __future__ import annotations

import json

import pytest

from cu import browser, config


def entry(**extra):
    return {"id": "x", "name": "X", "binary": "x", "resolved": "/usr/bin/x",
            "family": "gecko", "window_class": None, "profile_home": None, **extra}


# --- reading a desktop entry ---------------------------------------------


@pytest.mark.parametrize(
    "line,expected",
    [
        ("/usr/lib/firefox/firefox %u", "/usr/lib/firefox/firefox"),
        ("helium-browser %U", "helium-browser"),
        ("qutebrowser --untrusted-args %u", "qutebrowser"),
    ],
)
def test_exec_line_yields_the_binary_without_field_codes(line, expected):
    assert browser._exec_binary(line) == expected


def test_an_empty_exec_line_yields_nothing_rather_than_a_guess():
    assert browser._exec_binary("%u") == ""


# --- Gecko chord translation ---------------------------------------------


def test_accel_is_control_on_this_platform():
    assert browser._gecko_chord({"modifiers": "accel", "key": "T"}) == "ctrl+t"


def test_a_keycode_becomes_its_key_name():
    binding = {"modifiers": "accel,alt,shift", "keycode": "VK_F4"}
    assert browser._gecko_chord(binding) == "ctrl+alt+shift+f4"


def test_an_explicitly_unbound_command_has_no_chord():
    """An empty object means the user disabled it. Reading that as "use the
    default" would send exactly the chord they turned off."""
    assert browser._gecko_chord({}) is None


def test_a_shifted_character_carries_its_own_shift():
    """`?` is shift+slash, whether or not the modifier list says shift."""
    assert browser._gecko_chord({"modifiers": "accel", "key": "?"}) == "ctrl+shift+slash"


def test_a_binding_with_neither_key_nor_keycode_resolves_to_nothing():
    assert browser._gecko_chord({"modifiers": "accel"}) is None


def test_an_unrepresentable_key_resolves_to_nothing():
    assert browser._gecko_chord({"modifiers": "accel", "keycode": "VK_NONSENSE"}) is None


# --- resolution order -----------------------------------------------------


def test_a_chromium_browser_resolves_nothing_from_its_own_config():
    """It keeps no readable keymap, and a table of defaults would be a guess
    wearing a schema."""
    assert browser.keymap_for(entry(family="chromium")) == {}


def test_the_users_config_overrides_the_browsers_own_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[keys.zen]\nfocus-urlbar = "ctrl+l"\n')
    cfg = config.load(path)
    chord, source = browser.resolve(entry(window_class="zen"), "focus-urlbar", cfg)
    assert chord == "ctrl+l"
    assert source == "cu config"


def test_an_unresolvable_action_says_so(tmp_path):
    cfg = config.load(tmp_path / "absent.toml")
    chord, source = browser.resolve(entry(family="chromium", window_class="vivaldi"),
                                    "focus-urlbar", cfg)
    assert chord is None
    assert source == "unresolved"


# --- reading a keymap off disk -------------------------------------------


def test_the_profiles_own_keymap_wins(tmp_path):
    """The profile's copy is the file the running browser reads."""
    (tmp_path / "profiles.ini").write_text("[Profile0]\nPath=p1\nDefault=1\n")
    (tmp_path / "p1").mkdir()
    (tmp_path / "p1" / "customKeys.json").write_text(
        json.dumps({"focusURLBar": {"modifiers": "accel", "key": "K"}})
    )
    (tmp_path / "customKeys.json").write_text(
        json.dumps({"focusURLBar": {"modifiers": "accel", "key": "L"}})
    )
    assert browser.gecko_keymap(tmp_path) == {"focus-urlbar": "ctrl+k"}


def test_a_missing_keymap_is_empty_rather_than_an_error(tmp_path):
    assert browser.gecko_keymap(tmp_path) == {}


def test_commands_absent_from_the_file_are_not_invented(tmp_path):
    (tmp_path / "customKeys.json").write_text(
        json.dumps({"focusURLBar": {"modifiers": "accel", "key": "L"}})
    )
    keymap = browser.gecko_keymap(tmp_path)
    assert "close-tab" not in keymap


# --- matching a live window ----------------------------------------------


def test_a_window_matches_the_class_its_desktop_entry_declares():
    browsers = [entry(window_class="zen", binary="zen-bin", name="Zen")]
    assert browser.for_window_class("zen", browsers)["name"] == "Zen"


def test_a_window_falls_back_to_the_executable_name():
    """Helium and Vivaldi declare no class, and their windows still have to be
    recognised."""
    browsers = [entry(window_class=None, binary="/opt/x/helium-browser", name="Helium")]
    assert browser.for_window_class("helium", browsers)["name"] == "Helium"


def test_an_unknown_window_matches_no_browser():
    assert browser.for_window_class("Alacritty", [entry(window_class="zen")]) is None


def test_a_named_browser_can_be_the_focused_browser(monkeypatch, capsys):
    from argparse import Namespace

    from cu import cli

    target = entry(id="zen.desktop", name="Zen", binary="zen", window_class="zen")
    focused = {"address": "0x1", "class": "zen", "title": "page"}
    monkeypatch.setattr(browser, "discover", lambda: [target])
    monkeypatch.setattr(cli.observe, "snapshot", lambda: {"focused": focused})
    monkeypatch.setattr(browser, "resolve", lambda *_args: ("ctrl+l", "test"))
    monkeypatch.setattr(
        cli.actions,
        "browser_action",
        lambda *_args, **_kwargs: {
            "dry_run": True,
            "semantic": "focus-urlbar",
            "chord": "ctrl+l",
            "browser": "Zen",
            "resolved_from": "test",
        },
    )
    args = Namespace(
        action="focus-urlbar", browser="zen", backend=None, dry_run=True, json=True
    )
    assert cli.cmd_browser(args) == 0
    assert '"browser": "Zen"' in capsys.readouterr().out
