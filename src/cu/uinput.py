"""A virtual keyboard and pointer, created directly on /dev/uinput.

No root and no resident daemon: the device is built by the process that needs
it and destroyed when that process leaves. Destruction releases every held key
at the kernel level, which makes even a crash safe — but the release is also
written explicitly, because relying on a side effect for a safety property is
how a stuck modifier ends up rewriting someone's session.

Positioning is not done here. A relative pointer travels through libinput's
acceleration curve, so ten units of movement are not ten logical pixels; the
compositor's absolute warp is the only exact way to place a cursor. This device
supplies what the warp cannot: buttons, wheels, and keys.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import signal
import struct
import time
from typing import Any, Self

DEVICE_PATH = "/dev/uinput"


class UinputError(RuntimeError):
    """The device could not be created or written to."""


class ChordError(ValueError):
    """A chord names a key or modifier that does not exist.

    A ValueError rather than a device error, because it is the caller's text
    that is wrong and the correct exit code is a usage failure.
    """


def _ioc(direction: int, typ: str, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


#: struct uinput_setup: input_id (8) + char name[80] + __u32 ff_effects_max.
_SETUP_SIZE = 92

UI_DEV_CREATE = _ioc(0, "U", 1, 0)
UI_DEV_DESTROY = _ioc(0, "U", 2, 0)
UI_DEV_SETUP = _ioc(1, "U", 3, _SETUP_SIZE)
UI_SET_EVBIT = _ioc(1, "U", 100, 4)
UI_SET_KEYBIT = _ioc(1, "U", 101, 4)
UI_SET_RELBIT = _ioc(1, "U", 102, 4)

EV_SYN, EV_KEY, EV_REL = 0x00, 0x01, 0x02
SYN_REPORT = 0
REL_WHEEL, REL_HWHEEL = 0x08, 0x06
REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES = 0x0B, 0x0C

BUTTONS = {
    "left": 0x110,
    "right": 0x111,
    "middle": 0x112,
    "back": 0x113,
    "forward": 0x114,
}

#: Linux key codes by the name `cu` accepts in a chord. Names are the kernel's,
#: lowercased and stripped of the KEY_ prefix, plus the aliases a caller is
#: likely to write.
KEYS: dict[str, int] = {
    "esc": 1, "escape": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6,
    "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "minus": 12, "equal": 13, "backspace": 14, "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21,
    "u": 22, "i": 23, "o": 24, "p": 25,
    "leftbrace": 26, "rightbrace": 27, "enter": 28, "return": 28,
    "ctrl": 29, "leftctrl": 29,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35,
    "j": 36, "k": 37, "l": 38,
    "semicolon": 39, "apostrophe": 40, "grave": 41,
    "shift": 42, "leftshift": 42, "backslash": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    "comma": 51, "dot": 52, "period": 52, "slash": 53,
    "rightshift": 54, "kpasterisk": 55,
    "alt": 56, "leftalt": 56, "space": 57, "capslock": 58,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68,
    "numlock": 69, "scrolllock": 70,
    "f11": 87, "f12": 88,
    "rightctrl": 97, "rightalt": 100, "altgr": 100,
    "home": 102, "up": 103, "pageup": 104, "left": 105, "right": 106,
    "end": 107, "down": 108, "pagedown": 109, "insert": 110, "delete": 111,
    "mute": 113, "volumedown": 114, "volumeup": 115,
    "super": 125, "meta": 125, "leftmeta": 125, "rightmeta": 126,
    "menu": 127,
}

MODIFIERS = {"ctrl": 29, "shift": 42, "alt": 56, "super": 125, "altgr": 100}

#: Characters reachable on a US layout, as (key name, needs shift). The `keys`
#: text method is a fallback for applications that reject synthetic paste; the
#: clipboard method is layout-independent and is what `cu type` uses by default.
_UNSHIFTED = {
    "1": "1", "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7",
    "8": "8", "9": "9", "0": "0", "-": "minus", "=": "equal", "[": "leftbrace",
    "]": "rightbrace", ";": "semicolon", "'": "apostrophe", "`": "grave",
    "\\": "backslash", ",": "comma", ".": "dot", "/": "slash", " ": "space",
    "\n": "enter", "\t": "tab",
}
_SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "minus", "+": "equal", "{": "leftbrace",
    "}": "rightbrace", ":": "semicolon", '"': "apostrophe", "~": "grave",
    "|": "backslash", "<": "comma", ">": "dot", "?": "slash",
}


def char_to_key(char: str) -> tuple[str, bool] | None:
    """The keystroke that produces a character on a US layout, or None.

    None means `cu` cannot type that character by synthesis and must say so,
    rather than emitting an approximation.
    """
    if char.islower() and char.isascii() and char.isalpha():
        return (char, False)
    if char.isupper() and char.isascii() and char.isalpha():
        return (char.lower(), True)
    if char in _UNSHIFTED:
        return (_UNSHIFTED[char], False)
    if char in _SHIFTED:
        return (_SHIFTED[char], True)
    return None


def parse_chord(chord: str) -> tuple[list[int], int]:
    """Split `Ctrl+Alt+Shift+F4` into held modifiers and the key they qualify.

    Raises on an unknown name. A chord that cannot be parsed must never be
    approximated: the nearest chord is a different command.
    """
    parts = [p.strip().lower() for p in chord.split("+")]
    # `Ctrl-Alt-F4` is the other spelling in circulation, and it is tried only
    # when `+` found no separator at all. Splitting on both would read `ctrl+-`
    # — a browser's zoom out — as a bare Ctrl press, which is not an error the
    # kernel or the application would report.
    if len(parts) == 1 and "-" in parts[0][1:]:
        dashed = [p.strip().lower() for p in parts[0].split("-")]
        if all(dashed):
            parts = dashed
    if not all(parts):
        raise ChordError(f"{chord!r} has an empty part; name every key it holds")

    *held, final = parts
    codes = []
    for name in held:
        if name not in MODIFIERS:
            raise ChordError(f"{name!r} is not a modifier (in chord {chord!r})")
        codes.append(MODIFIERS[name])

    if final not in KEYS:
        # A caller who writes the character rather than the kernel's name for
        # it means the key that produces it, and a shifted character says so.
        typed = char_to_key(final) if len(final) == 1 else None
        if typed is None:
            raise ChordError(f"unknown key {final!r} (in chord {chord!r})")
        final, shifted = typed
        if shifted and MODIFIERS["shift"] not in codes:
            codes.append(MODIFIERS["shift"])
    return codes, KEYS[final]


class Device:
    """A virtual keyboard and pointer, alive for the duration of a `with`.

    Every key this device presses is tracked, so release is exhaustive even
    when an exception unwinds through the middle of a chord.
    """

    def __init__(self, name: str = "cu-virtual-input", settle_ms: int = 40) -> None:
        self.name = name
        self.settle_ms = settle_ms
        self._fd: int | None = None
        self._held: set[int] = set()
        self._restore: list[tuple[int, Any]] = []

    def __enter__(self) -> Self:
        try:
            fd = os.open(DEVICE_PATH, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            raise UinputError(
                f"{DEVICE_PATH} not writable as uid {os.getuid()}: {exc.strerror}"
            ) from exc
        self._fd = fd
        try:
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_REL)
            for code in set(KEYS.values()) | set(BUTTONS.values()):
                fcntl.ioctl(fd, UI_SET_KEYBIT, code)
            for code in (REL_WHEEL, REL_HWHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES):
                fcntl.ioctl(fd, UI_SET_RELBIT, code)
            setup = struct.pack(
                "<HHHH80sI", 0x03, 0x1D1E, 0x0001, 1, self.name.encode()[:79], 0
            )
            fcntl.ioctl(fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(fd, UI_DEV_CREATE)
        except OSError as exc:
            os.close(fd)
            self._fd = None
            raise UinputError(f"creating the virtual device failed: {exc}") from exc

        # Signals must not bypass release. The previous handlers are restored on
        # exit so a caller that installed its own keeps it.
        # Off the main thread this raises, and the context manager still releases.
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with contextlib.suppress(ValueError, OSError):
                self._restore.append((sig, signal.signal(sig, self._on_signal)))

        # A device the compositor has not enumerated yet accepts events at the
        # kernel and drops them before any client sees them.
        time.sleep(self.settle_ms / 1000)
        return self

    def _on_signal(self, signum: int, _frame: Any) -> None:
        self.close()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            for code in sorted(self._held):
                self._write(EV_KEY, code, 0)
            self._sync()
        except OSError:
            pass  # destroying the device below releases them regardless
        finally:
            self._held.clear()
            with contextlib.suppress(OSError):
                fcntl.ioctl(fd, UI_DEV_DESTROY)
            os.close(fd)
            for sig, handler in self._restore:
                with contextlib.suppress(ValueError, OSError, TypeError):
                    signal.signal(sig, handler)
            self._restore.clear()

    def _write(self, typ: int, code: int, value: int) -> None:
        """Emit one event, tolerating a device that is already gone.

        A signal can destroy the device while a chord is mid-flight, and the
        `finally` that releases its modifiers then runs against a closed
        descriptor. Releasing a key on a destroyed device is a no-op rather
        than an error — the kernel released it when the device went away — and
        raising there would replace the real reason for the unwind with a
        confusing one. A *press* after close is still a bug worth reporting.
        """
        if self._fd is None:
            if typ == EV_SYN or (typ == EV_KEY and value == 0):
                return
            raise UinputError("the virtual device is closed")
        os.write(self._fd, struct.pack("@llHHi", 0, 0, typ, code, value))

    def _sync(self) -> None:
        self._write(EV_SYN, SYN_REPORT, 0)

    def key_down(self, code: int) -> None:
        self._write(EV_KEY, code, 1)
        self._held.add(code)
        self._sync()

    def key_up(self, code: int) -> None:
        self._write(EV_KEY, code, 0)
        self._held.discard(code)
        self._sync()

    def tap(self, code: int, press_ms: int = 12) -> None:
        self.key_down(code)
        time.sleep(press_ms / 1000)
        self.key_up(code)

    def chord(self, chord: str, press_ms: int = 12) -> None:
        held, final = parse_chord(chord)
        for code in held:
            self.key_down(code)
        time.sleep(press_ms / 1000)
        try:
            self.tap(final, press_ms)
        finally:
            for code in reversed(held):
                self.key_up(code)

    def click(self, button: str = "left", press_ms: int = 12, count: int = 1) -> None:
        if button not in BUTTONS:
            raise UinputError(f"unknown button {button!r}; expected one of {sorted(BUTTONS)}")
        code = BUTTONS[button]
        for index in range(count):
            if index:
                time.sleep(0.06)  # inside the double-click window toolkits expect
            self.key_down(code)
            time.sleep(press_ms / 1000)
            self.key_up(code)

    def scroll(self, dy: int = 0, dx: int = 0) -> None:
        """Wheel detents. Hi-res axes are emitted alongside because clients that
        prefer them ignore the coarse axis entirely."""
        if dy:
            self._write(EV_REL, REL_WHEEL, dy)
            self._write(EV_REL, REL_WHEEL_HI_RES, dy * 120)
        if dx:
            self._write(EV_REL, REL_HWHEEL, dx)
            self._write(EV_REL, REL_HWHEEL_HI_RES, dx * 120)
        if dy or dx:
            self._sync()
