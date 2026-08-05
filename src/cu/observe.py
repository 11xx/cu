"""Structural observation: what is on screen, without looking at pixels.

The compositor already knows which window is focused and where it sits, so
answering those questions costs a few hundred tokens instead of a screenshot.
Every rectangle here is in logical coordinates, the space Hyprland accepts back.
"""

from __future__ import annotations

from typing import Any

from . import hypr


def _rect(x: float, y: float, w: float, h: float) -> dict[str, float]:
    return {"x": x, "y": y, "w": w, "h": h}


def monitors() -> list[dict[str, Any]]:
    """Monitors with both coordinate spaces stated explicitly.

    `width`/`height` from Hyprland are physical pixels while `x`/`y` are a
    logical origin; mixing them is the bug this whole project exists to avoid,
    so both forms are named rather than left to the caller to derive.
    """
    out = []
    for m in hypr.query("monitors"):
        scale = m["scale"]
        out.append(
            {
                "name": m["name"],
                "scale": scale,
                "physical": {"w": m["width"], "h": m["height"]},
                "logical_rect": _rect(
                    m["x"], m["y"], round(m["width"] / scale), round(m["height"] / scale)
                ),
                "focused": m["focused"],
                "active_workspace": m["activeWorkspace"]["name"],
                "disabled": m.get("disabled", False),
            }
        )
    return out


def windows() -> list[dict[str, Any]]:
    out = []
    for c in hypr.query("clients"):
        if not c.get("mapped", True):
            continue
        at, size = c["at"], c["size"]
        out.append(
            {
                "address": c["address"],
                "class": c["class"],
                "title": c["title"],
                "pid": c["pid"],
                "xwayland": c["xwayland"],
                "floating": c["floating"],
                "hidden": c.get("hidden", False),
                "workspace": c["workspace"]["name"],
                "monitor": c["monitor"],
                "logical_rect": _rect(at[0], at[1], size[0], size[1]),
            }
        )
    return out


def focused() -> dict[str, Any] | None:
    active = hypr.query("activewindow")
    if not active or "address" not in active:
        return None
    return {
        "address": active["address"],
        "class": active["class"],
        "title": active["title"],
        "pid": active["pid"],
    }


def cursor() -> dict[str, float]:
    pos = hypr.query("cursorpos")
    return {"x": pos["x"], "y": pos["y"]}


def desktop_bounds(mons: list[dict[str, Any]]) -> dict[str, float]:
    """The logical bounding box of all enabled outputs."""
    rects = [m["logical_rect"] for m in mons if not m["disabled"]]
    if not rects:
        return _rect(0, 0, 0, 0)
    x0 = min(r["x"] for r in rects)
    y0 = min(r["y"] for r in rects)
    x1 = max(r["x"] + r["w"] for r in rects)
    y1 = max(r["y"] + r["h"] for r in rects)
    return _rect(x0, y0, x1 - x0, y1 - y0)


def snapshot() -> dict[str, Any]:
    mons = monitors()
    focus = focused()
    wins = windows()
    for w in wins:
        w["focused"] = focus is not None and w["address"] == focus["address"]
    return {
        "monitors": mons,
        "desktop_logical_bounds": desktop_bounds(mons),
        "windows": wins,
        "focused": focus,
        "cursor_logical": cursor(),
    }
