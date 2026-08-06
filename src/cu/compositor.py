"""Keyboard injection through the compositor instead of the kernel.

The uinput device produces genuine libinput events, which is what makes it
indistinguishable from a human at the application. That same property has a
cost: libinput activity resets the session idle timer, so every injected
keystroke wakes monitors that had gone to sleep.

Hyprland's own key dispatcher does not travel that path. Keys injected here
reach the focused client without counting as user activity, so a keyboard
workflow can run while the physical displays stay powered down. Measured on
this host: warping the cursor and sending keys this way both leave DPMS off,
while a single uinput event turns every monitor back on.

What this backend cannot do is press a button or turn a wheel — the compositor
exposes no dispatcher for either — so pointer actions stay on uinput and stay
loud. It is also a different kind of injection at the client: an application
that distinguishes real input devices sees no device here at all.

Two argument shapes matter and both fail silently when wrong:

- `mods` is a **name**, not a bitmask. `mods = 4` is accepted and ignored,
  producing an unmodified keystroke. Several names separate with spaces.
- `key` is an **X11 keysym name** (`"a"`, `"Return"`), or an **xkb keycode**,
  which is the evdev code plus 8. A kernel constant like `"KEY_A"` is accepted
  and does nothing.
"""

from __future__ import annotations

from . import hypr, uinput

#: cu's modifier names as the dispatcher spells them.
MODS = {29: "CTRL", 42: "SHIFT", 56: "ALT", 125: "SUPER", 100: "ALT"}

#: Keys whose keysym name differs from the name cu accepts in a chord. Letters,
#: digits, and anything already spelled as its keysym fall through unchanged.
KEYSYMS = {
    "enter": "Return",
    "return": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "space": "space",
    "backspace": "BackSpace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "Prior",
    "pagedown": "Next",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "minus": "minus",
    "equal": "equal",
    "comma": "comma",
    "dot": "period",
    "period": "period",
    "slash": "slash",
    "backslash": "backslash",
    "semicolon": "semicolon",
    "apostrophe": "apostrophe",
    "grave": "grave",
    "leftbrace": "bracketleft",
    "rightbrace": "bracketright",
    "capslock": "Caps_Lock",
    "menu": "Menu",
}


class QuietError(RuntimeError):
    pass


def keysym_for(name: str) -> str:
    lowered = name.lower()
    if lowered in KEYSYMS:
        return KEYSYMS[lowered]
    if lowered.startswith("f") and lowered[1:].isdigit():
        return lowered.upper()  # function keys are F1..F12
    if len(lowered) == 1:
        return lowered
    raise QuietError(
        f"no keysym known for {name!r}; use --backend uinput for this chord"
    )


def split_chord(chord: str) -> tuple[str, str]:
    """A cu chord as the dispatcher's `(mods, key)` pair.

    Parsed by the same function the uinput path uses, so an unknown key is
    refused identically rather than diverging between backends.
    """
    held, final = uinput.parse_chord(chord)
    names = []
    for code in held:
        name = MODS.get(code)
        if name is None:
            raise QuietError(f"no compositor modifier for code {code}")
        if name not in names:
            names.append(name)
    reverse = {code: key for key, code in uinput.KEYS.items()}
    return " ".join(names), keysym_for(reverse[final])


def send_chord(chord: str) -> None:
    """Press and release one chord at the compositor.

    The release is emitted even if the press raised, because a key left down
    here is held by the compositor and there is no device to destroy that would
    clear it.
    """
    mods, key = split_chord(chord)
    payload = f'{{mods = "{mods}", key = "{key}"'
    try:
        hypr.lua(f'hl.dispatch(hl.dsp.send_key_state({payload}, state = "down"}}))')
    finally:
        hypr.lua(f'hl.dispatch(hl.dsp.send_key_state({payload}, state = "up"}}))')
