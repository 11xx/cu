"""Read-only browser/compositor association feasibility probe."""

from __future__ import annotations

from typing import Any


def associate(browser: dict[str, Any], compositor_windows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a mapping verdict only when a stable shared identity is present."""
    bridge = browser.get("compositor_window_id")
    if not isinstance(bridge, str) or not bridge:
        return {"status": "unprojectable", "reason": "browser identity has no compositor bridge"}
    candidates = [
        window for window in compositor_windows if window.get("address") == bridge
    ]
    if len(candidates) != 1:
        return {
            "status": "ambiguous" if candidates else "unprojectable",
            "reason": "shared compositor identity is not unique",
            "candidates": len(candidates),
        }
    viewport = browser.get("viewport")
    rect = candidates[0].get("logical_rect")
    if not isinstance(viewport, dict) or not isinstance(rect, dict):
        return {"status": "unprojectable", "reason": "viewport or compositor geometry is missing"}
    return {
        "status": "supported",
        "window_address": bridge,
        "viewport": viewport,
        "logical_rect": rect,
        "invalidation": ["document_id", "generation", "window_address", "geometry"],
    }


def evidence(
    browser: dict[str, Any],
    compositor_windows: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a bounded evidence envelope without title, URL, or page content."""
    return {
        "schema": "cu-browser-frame-association/1",
        "browser": {
            key: browser.get(key)
            for key in ("tab_id", "document_id", "generation", "viewport")
        },
        "compositor": {
            "windows": [
                {key: window.get(key) for key in ("address", "class", "logical_rect", "monitor")}
                for window in compositor_windows
            ],
            "outputs": [
                {key: output.get(key) for key in ("name", "logical_rect", "scale", "disabled")}
                for output in outputs
            ],
        },
        "association": associate(browser, compositor_windows),
    }
