"""Browsers: discovered, not assumed, and driven by semantic actions.

Two facts shape everything here.

**Chrome is operated by keyboard, page content by coordinates.** Six browsers
with divergent and user-reconfigurable interfaces make pixel-hunting for a URL
bar unwinnable, while page content is the same everywhere.

**But the keyboard is not a constant either — on this desktop it is the most
customized layer there is.** Of four chords one would reflexively call
universal, `Ctrl+T` survives, `Ctrl+L` and `Ctrl+W` do not, and nothing about a
chord's ordinariness predicts which. So `cu` exposes *actions*, never chords,
and resolves each one from the browser's own configuration.

An action with no resolvable chord is refused. That is the point of the module:
falling back to a documented default would not fail — it would silently perform
some *other* command, which is worse than doing nothing.
"""

from __future__ import annotations

import configparser
import json
import re
import shutil
from pathlib import Path
from typing import Any

from . import uinput

#: Engine family by executable name. This is a hint about lineage, not about
#: bindings: it decides *where* to look for a keymap, and a wrong guess yields
#: no mapping rather than a wrong one. Confirmed for Gecko by the presence of a
#: profile directory, which is what actually holds the bindings.
GECKO = {"firefox", "zen", "zen-bin", "floorp", "librewolf", "waterfox", "torbrowser-launcher"}
CHROMIUM = {
    "chromium", "google-chrome", "google-chrome-stable", "thorium-browser",
    "helium-browser", "vivaldi", "vivaldi-stable", "brave", "brave-browser",
    "microsoft-edge", "opera",
}

#: Where each Gecko browser keeps its profiles. The directory name does not
#: follow the executable name, so it is listed rather than derived.
GECKO_HOMES = {
    "zen": "~/.zen",
    "zen-bin": "~/.zen",
    "firefox": "~/.mozilla/firefox",
    "floorp": "~/.floorp",
    "librewolf": "~/.librewolf",
    "waterfox": "~/.waterfox",
}

#: `cu`'s vocabulary, mapped to the command ids Gecko uses internally. The left
#: column is the only thing an agent ever writes.
GECKO_ACTIONS = {
    "focus-urlbar": "focusURLBar",
    "new-tab": "key_newNavigatorTab",
    "close-tab": "key_close",
    "reopen-tab": "key_restoreLastClosedTabOrWindowOrSession",
    "new-window": "key_newNavigator",
    "close-window": "key_closeWindow",
    "private-window": "key_privatebrowsing",
    "back": "goBackKb",
    "forward": "goForwardKb",
    "home": "goHome",
    "reload": "key_reload",
    "devtools": "key_toggleToolbox",
    "save-page": "key_savePage",
    "open-file": "openFileKb",
    "downloads": "key_openDownloads",
    "addons": "key_openAddons",
    "show-all-tabs": "key_showAllTabs",
    "zoom-reset": "key_fullZoomReset",
    "page-info": "key_viewInfo",
    "find": "key_find",
    "find-again": "key_findAgain",
    "quit": "key_quitApplication",
}

#: Window classes an installed browser is known to produce, for entries whose
#: `Exec=` line is authoritative but that declare no `StartupWMClass`. Each one
#: is a verified reading of a running window, not a guess from the binary name:
#: a wrong class here would assert focus against a window that is not there.
WINDOW_CLASSES = {
    "helium-browser": "helium",
}

#: Keymaps declared per browser, for browsers that keep no readable binding
#: file of their own. Chromium-family builds compile their accelerators in, so
#: there is nothing on disk to read and nothing a user edit could change short
#: of an extension — which makes a table the honest source rather than a guess.
#:
#: The key is the executable name, never the family. A fork is free to rebind
#: anything, so membership in `CHROMIUM` says only where the binary came from;
#: an entry here says someone established what this build actually listens for.
DECLARED_KEYMAPS: dict[str, dict[str, str]] = {
    "helium-browser": {
        "focus-urlbar": "ctrl+l",
        "new-tab": "ctrl+t",
        "close-tab": "ctrl+w",
        "reopen-tab": "ctrl+shift+t",
        "new-window": "ctrl+n",
        "close-window": "ctrl+shift+w",
        "private-window": "ctrl+shift+n",
        "back": "alt+left",
        "forward": "alt+right",
        "home": "alt+home",
        "reload": "ctrl+r",
        "save-page": "ctrl+s",
        "open-file": "ctrl+o",
        "downloads": "ctrl+j",
        "find": "ctrl+f",
        "find-again": "ctrl+g",
        "zoom-reset": "ctrl+0",
        "devtools": "f12",
    },
}

#: Gecko modifier names. `accel` is the platform's primary modifier, which on
#: Linux is Control.
GECKO_MODIFIERS = {
    "accel": "ctrl",
    "control": "ctrl",
    "ctrl": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "meta": "super",
    "os": "super",
}

DESKTOP_DIRS = ("/usr/share/applications", "~/.local/share/applications")


class BrowserError(RuntimeError):
    pass


def _parse_desktop(path: Path) -> dict[str, str] | None:
    parser = configparser.RawConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
        entry = parser["Desktop Entry"]
    except (OSError, KeyError, configparser.Error):
        return None
    return {key.lower(): value for key, value in entry.items()}


def _exec_binary(exec_line: str) -> str:
    """The executable out of an `Exec=` line, without its field codes.

    The binary name does not follow the product name — Helium is on PATH as
    `helium-browser` — so this is read rather than guessed, and a guessed name
    that happens not to exist would read as "not installed".
    """
    first = re.sub(r"%[a-zA-Z]", "", exec_line).strip().split()
    return first[0] if first else ""


def discover() -> list[dict[str, Any]]:
    """Every installed browser, from desktop entries rather than from PATH.

    Desktop entries are authoritative about how a browser is actually launched;
    PATH name-matching is a hint that misses anything installed under a name
    other than its product name.
    """
    found: dict[str, dict[str, Any]] = {}
    for directory in DESKTOP_DIRS:
        base = Path(directory).expanduser()
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.desktop")):
            entry = _parse_desktop(path)
            if not entry or "WebBrowser" not in entry.get("categories", ""):
                continue
            if entry.get("nodisplay", "").lower() == "true":
                continue
            binary = _exec_binary(entry.get("exec", ""))
            name = Path(binary).name
            if not name or name in found:
                continue
            family = "gecko" if name in GECKO else "chromium" if name in CHROMIUM else "unknown"
            found[name] = {
                "id": path.stem,
                "name": entry.get("name", name),
                "binary": binary,
                "resolved": shutil.which(binary) or (binary if Path(binary).exists() else None),
                "family": family,
                # StartupWMClass is the entry's own statement of the window
                # class it will produce, which is what focus assertion needs.
                "window_class": entry.get("startupwmclass") or WINDOW_CLASSES.get(name),
                "profile_home": _profile_home(name),
            }
    return sorted(found.values(), key=lambda b: b["name"].lower())


def _profile_home(binary_name: str) -> str | None:
    home = GECKO_HOMES.get(binary_name)
    if home is None:
        return None
    path = Path(home).expanduser()
    return str(path) if path.is_dir() else None


def default_profile(profile_home: Path) -> Path | None:
    """The profile a browser actually starts with, per its own profiles.ini.

    Guessing by directory name breaks as soon as a second profile exists, and
    the wrong profile carries a different keymap.
    """
    ini = profile_home / "profiles.ini"
    if not ini.is_file():
        return None
    parser = configparser.RawConfigParser(interpolation=None, strict=False)
    try:
        parser.read(ini, encoding="utf-8")
    except (OSError, configparser.Error):
        return None
    # Installs pin a default; older layouts flag it on the profile section.
    for section in parser.sections():
        if section.startswith("Install") and parser.has_option(section, "Default"):
            candidate = profile_home / parser.get(section, "Default")
            if candidate.is_dir():
                return candidate
    for section in parser.sections():
        flagged = parser.get(section, "Default", fallback="") == "1"
        if flagged and parser.has_option(section, "Path"):
            candidate = profile_home / parser.get(section, "Path")
            if candidate.is_dir():
                return candidate
    return None


def _gecko_chord(binding: dict[str, Any]) -> str | None:
    """One `customKeys.json` entry as a cu chord, or None when it is unbound.

    An empty object means the command is explicitly *unbound*, which must read
    as "no mapping" rather than "use the default" — the default is exactly what
    the user disabled.
    """
    if not binding:
        return None
    parts = []
    for raw in binding.get("modifiers", "").replace(" ", ",").split(","):
        name = GECKO_MODIFIERS.get(raw.strip().lower())
        if name and name not in parts:
            parts.append(name)

    keycode = binding.get("keycode", "")
    key = binding.get("key", "")
    if keycode:
        final = keycode[3:].lower() if keycode.upper().startswith("VK_") else keycode.lower()
    elif key:
        mapped = uinput.char_to_key(key if len(key) > 1 else key.lower())
        if mapped is None:
            return None
        final, needs_shift = mapped
        # A character such as `?` carries shift implicitly; the modifier list
        # may or may not already say so.
        if needs_shift and "shift" not in parts:
            parts.append("shift")
    else:
        return None

    if final not in uinput.KEYS:
        return None
    return "+".join([*parts, final])


def gecko_keymap(profile_home: Path) -> dict[str, str]:
    """Every action this Gecko browser has a resolvable chord for.

    The profile's copy wins over the one beside it, since that is the file the
    running browser reads.
    """
    candidates = []
    profile = default_profile(profile_home)
    if profile:
        candidates.append(profile / "customKeys.json")
    candidates.append(profile_home / "customKeys.json")

    raw: dict[str, Any] = {}
    for path in candidates:
        if path.is_file():
            try:
                raw = json.loads(path.read_text())
                break
            except (OSError, json.JSONDecodeError):
                continue
    if not raw:
        return {}

    keymap = {}
    for action, command in GECKO_ACTIONS.items():
        if command not in raw:
            continue  # absent means the built-in default, which cu will not guess
        chord = _gecko_chord(raw[command])
        if chord:
            keymap[action] = chord
    return keymap


def keymap_for(browser: dict[str, Any]) -> dict[str, str]:
    """The actions this browser can be driven by, read from its own config.

    A Gecko browser is read from its profile. A browser with no readable
    binding file resolves against `DECLARED_KEYMAPS`, which is keyed by
    executable name: a build nobody established bindings for yields nothing,
    and stays unmapped until a user states it under `[keys]` in cu's config.
    """
    if browser["family"] == "gecko" and browser["profile_home"]:
        return gecko_keymap(Path(browser["profile_home"]))
    return dict(DECLARED_KEYMAPS.get(Path(browser["binary"]).name, {}))


def unmapped_reason(browser: dict[str, Any]) -> str:
    """Why this browser resolves no actions, and what would give it some.

    An empty map is never a fault in the caller's request, so the report says
    which of the two silences it is: a profile that could not be read, or a
    build whose bindings nobody has established.
    """
    if browser["family"] == "gecko":
        if not browser["profile_home"]:
            return (
                "no profile directory was found, so its keymap cannot be read; "
                "state the actions under [keys] in cu's config"
            )
        return (
            "its profile declares no custom keys, and cu will not assume the "
            "built-in defaults; state the actions under [keys] in cu's config"
        )
    return (
        "it keeps no readable keymap and cu has no declared bindings for this "
        "build; state the actions under [keys] in cu's config"
    )


def resolve(
    browser: dict[str, Any], action: str, cfg: dict[str, Any]
) -> tuple[str | None, str]:
    """The chord for an action, and where it came from.

    A user's own configuration wins over the browser's file, because it is the
    only way to correct a resolution that turns out to be wrong.
    """
    from . import config as config_module

    window_class = browser.get("window_class") or browser["binary"]
    override = config_module.binding_for(cfg, window_class, action)
    if override is config_module.UNMAPPED:
        return None, "cu config"
    if isinstance(override, str):
        return override, "cu config"
    keymap = keymap_for(browser)
    if action in keymap:
        # Provenance is part of the answer: a binding read out of the running
        # browser's own profile and one declared here for a build that keeps
        # none are worth different amounts of trust when a chord misfires.
        declared = DECLARED_KEYMAPS.get(Path(browser["binary"]).name, {})
        origin = "cu declared keymap" if action in declared else f"{browser['name']} configuration"
        return keymap[action], origin
    return None, "unresolved"


def for_window_class(
    window_class: str, browsers: list[dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """Which installed browser a live window belongs to.

    Matched against the desktop entry's declared class first, then the
    executable name, since not every entry declares one — Helium and Vivaldi
    do not, and their windows still have to be recognised.
    """
    browsers = browsers if browsers is not None else discover()
    lowered = window_class.lower()
    for browser in browsers:
        declared = (browser.get("window_class") or "").lower()
        if declared and declared == lowered:
            return browser
    for browser in browsers:
        name = Path(browser["binary"]).name.lower()
        if lowered and (lowered in name or name.startswith(lowered)):
            return browser
    return None


def find(selector: str, browsers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    browsers = browsers if browsers is not None else discover()
    lowered = selector.lower()
    exact = [b for b in browsers if b["binary"].lower() == lowered or b["id"].lower() == lowered]
    if exact:
        return exact[0]
    loose = [b for b in browsers if lowered in b["name"].lower() or lowered in b["binary"].lower()]
    if not loose:
        raise BrowserError(f"no installed browser matches {selector!r}")
    if len(loose) > 1:
        names = ", ".join(b["name"] for b in loose)
        raise BrowserError(f"{selector!r} matches several browsers: {names}")
    return loose[0]
