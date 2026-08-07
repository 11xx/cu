"""Coordinate normalization, exercised against the real mixed-scale layout.

The fixture is this host: one output at scale 1.0 and one at 1.25, which is the
arrangement that makes physical and logical pixels diverge. A single-scale
fixture would pass while the transform was wrong.
"""

from __future__ import annotations

import pytest

from cu import checks, exits, observe

MONITORS = [
    {
        "name": "DP-1",
        "width": 2560,
        "height": 1440,
        "x": 2048,
        "y": 0,
        "scale": 1.0,
        "focused": False,
        "activeWorkspace": {"name": "3"},
    },
    {
        "name": "DP-2",
        "width": 2560,
        "height": 1440,
        "x": 0,
        "y": 0,
        "scale": 1.25,
        "focused": True,
        "activeWorkspace": {"name": "4"},
    },
]


@pytest.fixture
def monitors(monkeypatch):
    monkeypatch.setattr(observe.hypr, "query", lambda what: MONITORS)
    return observe.monitors()


def test_scaled_output_reports_both_spaces(monitors):
    dp2 = next(m for m in monitors if m["name"] == "DP-2")
    assert dp2["physical"] == {"w": 2560, "h": 1440}
    assert dp2["logical_rect"] == {"x": 0, "y": 0, "w": 2048, "h": 1152}


def test_unscaled_output_keeps_physical_size(monitors):
    dp1 = next(m for m in monitors if m["name"] == "DP-1")
    assert dp1["logical_rect"]["w"] == dp1["physical"]["w"] == 2560


def test_desktop_bounds_span_both_outputs(monitors):
    assert observe.desktop_bounds(monitors) == {"x": 0, "y": 0, "w": 4608, "h": 1440}


def test_desktop_bounds_ignore_disabled(monitors):
    monitors[0]["disabled"] = True
    assert observe.desktop_bounds(monitors)["w"] == 2048


def test_desktop_bounds_with_no_outputs():
    assert observe.desktop_bounds([])["w"] == 0


def test_every_exit_code_documents_a_response():
    codes = {
        v for k, v in vars(exits).items() if k.isupper() and isinstance(v, int)
    }
    assert codes == set(exits.RESPONSE)


def test_abort_is_not_retryable():
    """An agent that retries past an abort defeats the interlock."""
    assert exits.ABORTED_BY_USER not in exits.RETRYABLE
    assert exits.DENIED_BY_POLICY not in exits.RETRYABLE
    assert exits.STALE_FRAME in exits.RETRYABLE


@pytest.mark.parametrize(
    "statuses, expected",
    [
        (["ok", "ok"], "ok"),
        (["ok", "warn"], "warn"),
        (["ok", "warn", "fail"], "fail"),
        ([], "ok"),
    ],
)
def test_worst_status_wins(statuses, expected):
    assert checks.worst([{"status": s} for s in statuses]) == expected


def _procs(monkeypatch, entries):
    monkeypatch.setattr(checks, "_processes", lambda: entries)


def test_unfiltered_xremap_fails(monkeypatch):
    """Unfiltered, xremap grabs virtual keyboards and rewrites injected keys."""
    _procs(monkeypatch, [(42, ["/usr/bin/xremap", "/home/u/.config/xremap/x.yaml"])])
    assert checks.xremap_filter()["status"] == checks.FAIL


def test_device_filtered_xremap_passes(monkeypatch):
    _procs(
        monkeypatch,
        [(42, ["/usr/bin/xremap", "x.yaml", "--device", "SINO WEALTH USB KEYBOARD"])],
    )
    result = checks.xremap_filter()
    assert result["status"] == checks.OK
    assert "SINO WEALTH USB KEYBOARD" in result["detail"]


def test_one_unfiltered_instance_among_several_still_fails(monkeypatch):
    _procs(
        monkeypatch,
        [
            (1, ["/usr/bin/xremap", "a.yaml", "--device", "Keyboard A"]),
            (2, ["/usr/bin/xremap", "b.yaml"]),
        ],
    )
    assert checks.xremap_filter()["status"] == checks.FAIL


def test_absent_xremap_is_fine(monkeypatch):
    _procs(monkeypatch, [(1, ["/usr/bin/something-else"])])
    assert checks.xremap_filter()["status"] == checks.OK


def test_lock_screen_blocks_injection(monkeypatch):
    _procs(monkeypatch, [(7, ["/usr/bin/hyprlock"])])
    assert checks.session_unlocked()["status"] == checks.FAIL


def _counted_scan(monkeypatch, clock):
    """Replace the /proc walk with a counter, and hand the module a fake clock."""
    scans = []
    monkeypatch.setattr(checks, "_process_scan", [], raising=False)
    monkeypatch.setattr(checks.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        checks.Path, "iterdir", lambda self: (scans.append(1), iter(()))[1]
    )
    return scans


def test_one_scan_serves_both_guards_in_a_single_injection(monkeypatch):
    """The remapper filter and the lock check are meant to describe one instant."""
    clock = [0.0]
    scans = _counted_scan(monkeypatch, clock)
    checks.xremap_filter()
    checks.session_unlocked()
    assert len(scans) == 1


def test_a_long_run_re_reads_before_acting_again(monkeypatch):
    """A replay injects over minutes, so a session locked mid-run must be seen."""
    clock = [0.0]
    scans = _counted_scan(monkeypatch, clock)
    checks.session_unlocked()
    clock[0] = checks.PROCESS_SCAN_TTL + 0.1
    checks.session_unlocked()
    assert len(scans) == 2
