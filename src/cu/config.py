"""Configuration: shipped defaults, user overrides merged over them.

The denylist, the abort lockfile, the staleness threshold, and the semantic
keymap are all data rather than code, because every one of them is a property
of a particular desktop. Defaults live here so that a host with no config file
still behaves correctly; a user file names only what it changes.

Nothing here guesses. An action absent from the keymap resolves to nothing at
all, and the caller refuses rather than falling back to a documented default —
that fallback is precisely how injection performs some *other* action while
reporting success.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "safety": {
        # Classes that must never receive synthetic input. Matched
        # case-insensitively as substrings, because window classes vary in
        # capitalisation across toolkits and XWayland.
        "denylist_classes": [
            "hyprlock",
            "swaylock",
            "gtklock",
            "waylock",
            "keepassxc",
            "bitwarden",
            "1password",
            "gcr-prompter",
            "polkit",
            "pinentry",
            "seahorse",
        ],
        # Title fragments carrying the same weight as a class. A password
        # prompt inside an ordinary window is still a password prompt.
        "denylist_titles": ["password", "passphrase", "unlock", "sign in", "2fa"],
        # A frame older than this no longer describes the screen.
        "frame_max_age": 60.0,
        # How far the cursor may sit from where cu warped it before cu
        # concludes a human is driving the mouse and yields.
        "cursor_drift_tolerance": 12.0,
        # Whether input commands assert the expected window still has focus.
        "require_focus": True,
    },
    "log": {
        # Append-only record of every action, injected or not.
        "path": "~/.local/state/cu/actions.jsonl",
    },
    "input": {
        # Pause between press and release, and between successive keystrokes.
        # Instant transitions are both a fingerprint and, in some toolkits, a
        # dropped event.
        "press_ms": 12,
        "gap_ms": 8,
        # How long to wait for the compositor to enumerate a new device before
        # the first event is written. Events emitted before enumeration are
        # accepted by the kernel and dropped by the compositor.
        "settle_ms": 40,
    },
    # Semantic action -> chord, per window class, with "*" as the entry for
    # applications that have no table of their own. `cu key --action` resolves
    # here and refuses when neither matches, because guessing a chord performs
    # a different action rather than failing.
    #
    # Only actions whose binding is genuinely near-universal belong under "*".
    # Paste qualifies and terminals are its known exception, which is why they
    # are enumerated rather than left to a guess.
    "keys": {
        "*": {
            "paste": "ctrl+v",
            "copy": "ctrl+c",
            "select-all": "ctrl+a",
            "undo": "ctrl+z",
        },
        "Alacritty": {"paste": "ctrl+shift+v", "copy": "ctrl+shift+c"},
        "kitty": {"paste": "ctrl+shift+v", "copy": "ctrl+shift+c"},
        "foot": {"paste": "ctrl+shift+v", "copy": "ctrl+shift+c"},
        "org.wezfurlong.wezterm": {"paste": "ctrl+shift+v", "copy": "ctrl+shift+c"},
        "konsole": {"paste": "ctrl+shift+v", "copy": "ctrl+shift+c"},
        "org.gnome.Terminal": {"paste": "ctrl+shift+v", "copy": "ctrl+shift+c"},
        "XTerm": {"paste": "ctrl+shift+v", "copy": "ctrl+shift+c"},
    },
}

#: Where an abort flag lives. Deliberately under XDG_RUNTIME_DIR: it is
#: session state, it must not survive a reboot, and it is the one interlock the
#: agent is forbidden to clear.
ABORT_FILENAME = "abort"


class ConfigError(RuntimeError):
    pass


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "cu"


def config_path() -> Path:
    return config_dir() / "config.toml"


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Overlay `over` onto `base`, recursing into tables only.

    Lists are replaced wholesale rather than concatenated. A user who shortens
    the denylist means it, and a merge that silently re-added entries would be
    impossible to reason about.
    """
    merged = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load(path: Path | None = None) -> dict[str, Any]:
    path = path or config_path()
    if not path.exists():
        return dict(DEFAULTS)
    try:
        user = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return _merge(DEFAULTS, user)


def abort_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise ConfigError("XDG_RUNTIME_DIR is unset; the abort flag has nowhere to live")
    return Path(runtime) / "cu" / ABORT_FILENAME


def aborted() -> bool:
    try:
        return abort_path().exists()
    except ConfigError:
        # No runtime dir means no session, which is itself a refusal to act.
        return True


def log_path(cfg: dict[str, Any]) -> Path:
    return Path(cfg["log"]["path"]).expanduser()


def chord_for(cfg: dict[str, Any], window_class: str, action: str) -> str | None:
    """The chord a given application listens for, or None if unknown.

    None is a refusal, not an invitation to substitute a default.
    """
    table = cfg.get("keys", {})
    for name, actions in table.items():
        if name.lower() == window_class.lower() and action in actions:
            return actions[action]
    fallback = table.get("*", {})
    return fallback.get(action)
