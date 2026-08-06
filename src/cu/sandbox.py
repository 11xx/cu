"""Headless outputs: a deliberate, named place to work where nobody is looking.

`cu` acts on the visible desktop by default, and that is a safety property
rather than an implementation detail. A person watching an agent drive their
screen can see it going wrong and stop it; work performed somewhere invisible
removes the cheapest and fastest interlock there is. Nothing in `cu` selects a
headless output on its own — a bare `cu shot` captures the focused window on a
real monitor, and every command aims at whatever the user can see.

Headless outputs exist for one purpose: developing and testing the input path
without stealing focus or clicking something on a screen the user is reading.
That is worth doing explicitly, through a command that says so, rather than by
an agent improvising `hyprctl output` calls — which is how the two traps below
get rediscovered.

**Removal is the dangerous half.** `hyprctl output remove` will happily take
away a physical monitor and blank the desk. This module refuses to remove
anything but a headless output, which is a restriction the raw command has no
concept of.

**Names increment and are never reused.** Creating an output twice yields
`HEADLESS-1` then `HEADLESS-2`, so the name is read back from the compositor
after creation. Assuming it is how a cleanup step removes an output that no
longer exists while a live one stays behind.
"""

from __future__ import annotations

import time
from typing import Any

from . import hypr, observe

PREFIX = "HEADLESS"


class SandboxError(RuntimeError):
    pass


def is_headless(name: str) -> bool:
    return name.upper().startswith(PREFIX)


def existing() -> list[dict[str, Any]]:
    return [m for m in observe.monitors() if is_headless(m["name"])]


def create(settle: float = 0.6) -> dict[str, Any]:
    """Add a headless output and return the one that actually appeared.

    The new name is found by difference rather than by pattern, so a session
    that already has `HEADLESS-1` and `HEADLESS-2` still identifies its own
    new output correctly.
    """
    before = {m["name"] for m in existing()}
    hypr._run(["output", "create", "headless"])
    time.sleep(settle)
    appeared = [m for m in existing() if m["name"] not in before]
    if not appeared:
        raise SandboxError("the compositor reported no new headless output")
    return appeared[0]


def remove(name: str) -> None:
    """Take away a headless output, and refuse to take away anything else.

    The compositor's own command does not distinguish, so removing `DP-1` by a
    typo would blank a real monitor. That is the kind of irreversible mistake
    the binary exists to make impossible.
    """
    if not is_headless(name):
        raise SandboxError(
            f"{name!r} is not a headless output; cu removes only outputs it can create. "
            "Removing a physical monitor would blank the user's screen."
        )
    live = {m["name"] for m in existing()}
    if name not in live:
        raise SandboxError(f"no headless output named {name!r} (have: {sorted(live) or 'none'})")
    hypr._run(["output", "remove", name])


def remove_all() -> list[str]:
    removed = []
    for monitor in existing():
        remove(monitor["name"])
        removed.append(monitor["name"])
    return removed
