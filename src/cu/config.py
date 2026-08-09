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

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

#: What a terminal listens for. The two empty chords are unmappings rather
#: than omissions: an absent action falls through to the wildcard, and there
#: the chords mean something else entirely — ctrl+a is a shell's
#: beginning-of-line or a multiplexer's prefix, and ctrl+z suspends whatever is
#: running. Stating them empty is how a terminal refuses instead of acting.
TERMINAL_KEYS = {
    "paste": "ctrl+shift+v",
    "copy": "ctrl+shift+c",
    "select-all": "",
    "undo": "",
}

TERMINALS = (
    "Alacritty",
    "kitty",
    "foot",
    "org.wezfurlong.wezterm",
    "konsole",
    "org.gnome.Terminal",
    "XTerm",
)

#: Emacs shares none of the wildcard's chords, and every one of them does
#: something there: ctrl+v scrolls a page, ctrl+c is a prefix key that leaves
#: the editor waiting for a second keystroke, ctrl+a goes to the start of the
#: line, and ctrl+z suspends the frame. Its yank and kill-ring-save read and
#: write the system clipboard, so `cu type` works through them unchanged.
#: Select-all is `C-x h`, a two-key sequence and not a chord, so it is unmapped
#: rather than approximated.
EMACS_KEYS = {
    "paste": "ctrl+y",
    "copy": "alt+w",
    "undo": "ctrl+/",
    "select-all": "",
}

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
        # How long an injecting command waits for another agent to release the
        # desktop before refusing with exit 42. Two agents reaching for it at
        # nearly the same moment is ordinary and worth absorbing; one holding
        # it for longer than this is a fact the caller needs told rather than
        # a stall to hide.
        "busy_wait_ms": 5000,
    },
    "log": {
        # Append-only record of every action, injected or not.
        "path": "~/.local/state/cu/actions.jsonl",
    },
    # What a capture is worth to whoever reads it. Every profile inherits
    # "generic" and states only its deltas, so teaching cu about a new consumer
    # is a stanza here rather than a change to the code. Values that are not
    # measured are better left inherited than guessed: the base is deliberately
    # the most conservative reading of each field.
    "capture": {
        "profile": "claude",
        "profiles": {
            # Small enough to survive any consumer's own downscaling, and
            # costed as though patches were the smaller of the two sizes in
            # use, so an unmeasured consumer is over- rather than under-priced.
            "generic": {
                "max_long_edge": 1280,
                "ceiling": 1568,
                "patch_px": 28,
                "token_budget": 1568,
                "token_multiplier": 1.0,
                "max_bytes": 5_000_000,
                "max_images_per_request": 20,
            },
            # Anthropic bills 28x28 patches. Claude 4.7 and later read a
            # 2576px long edge and 4784 visual tokens; older models cap at
            # 1568 for both, which is what the base already assumes. 1920 is
            # both the recommended balance for computer use and the largest
            # capture that reaches those models without being downscaled.
            "claude": {
                "max_long_edge": 1920,
                "ceiling": 2576,
                "token_budget": 4784,
                "max_bytes": 10_000_000,
                "max_images_per_request": 100,
            },
            # OpenAI bills 32x32 patches and caps by patch count rather than
            # by pixels. GPT-5.6 states no budget at all, hence zero; a
            # 1920x1080 capture is 2040 patches, inside the 2500 the models
            # that do cap allow.
            "openai": {
                "max_long_edge": 1920,
                "ceiling": 2576,
                "patch_px": 32,
                "token_budget": 0,
                "max_images_per_request": 100,
            },
        },
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
        # Which path keystrokes take. "uinput" produces genuine libinput
        # events, which is what makes them indistinguishable from a human, and
        # is the right default. "compositor" injects through Hyprland instead:
        # it does not count as user activity, so it leaves sleeping monitors
        # asleep, at the cost of presenting no input device to the client.
        # Pointer actions ignore this — there is no compositor button
        # dispatcher, so they always take the uinput path.
        "key_backend": "uinput",
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
        **{name: dict(TERMINAL_KEYS) for name in TERMINALS},
        "emacs": dict(EMACS_KEYS),
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
    """The effective configuration: the defaults, with the user's file over them.

    Deep-copied, because a shallow copy hands the caller the module's own
    nested tables — one caller adding a capture profile to what it was given
    would be editing the defaults every later caller reads.
    """
    path = path or config_path()
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    try:
        user = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return _merge(copy.deepcopy(DEFAULTS), user)


def abort_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise ConfigError("XDG_RUNTIME_DIR is unset; the abort flag has nowhere to live")
    return Path(runtime) / "cu" / ABORT_FILENAME


def set_abort() -> None:
    """Set the user's abort flag, creating its runtime directory if needed."""
    path = abort_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def clear_abort() -> bool:
    """Clear the abort flag and say whether it had been set."""
    path = abort_path()
    was_set = path.exists()
    path.unlink(missing_ok=True)
    return was_set


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

    An empty chord means the application is known *not* to have this action,
    which is a different fact from having no table entry: it stops the `*`
    fallback rather than falling through to it. Without that distinction an
    application listed for one action inherits the wildcard for every other,
    which is how a chord ends up performing something else entirely.
    """
    table = cfg.get("keys", {})
    for name, actions in table.items():
        if name.lower() == window_class.lower() and action in actions:
            return actions[action] or None
    return table.get("*", {}).get(action) or None
