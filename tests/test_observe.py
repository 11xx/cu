"""Coordinate normalization, exercised against the real mixed-scale layout.

The fixture is this host: one output at scale 1.0 and one at 1.25, which is the
arrangement that makes physical and logical pixels diverge. A single-scale
fixture would pass while the transform was wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cu import checks, cli, config, exits, observe, safety

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
        "reserved": [0, 26, 0, 0],
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
        "reserved": [0, 0, 0, 0],
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


def test_monitor_names_each_reserved_edge(monitors):
    dp1 = next(m for m in monitors if m["name"] == "DP-1")
    assert dp1["reserved"] == {"left": 0, "top": 26, "right": 0, "bottom": 0}


def test_layer_surfaces_flatten_levels_and_keep_desktop_coordinates(monkeypatch):
    layers = {
        "DP-1": {
            "levels": {
                "0": [],
                "1": [],
                "2": [
                    {
                        "address": "0xbar",
                        "namespace": "bar",
                        "x": 2048,
                        "y": 0,
                        "w": 2560,
                        "h": 26,
                    }
                ],
                "3": [],
            }
        }
    }
    monkeypatch.setattr(observe.hypr, "query", lambda what: layers)
    assert observe.layer_surfaces() == [
        {
            "address": "0xbar",
            "namespace": "bar",
            "monitor": "DP-1",
            "level": 2,
            "logical_rect": {"x": 2048, "y": 0, "w": 2560, "h": 26},
        }
    ]


def test_snapshot_always_carries_layer_surfaces(monkeypatch):
    layers = {"DP-1": {"levels": {"0": [], "1": [], "2": [], "3": []}}}

    def query(what):
        return {
            "monitors": MONITORS,
            "layers": layers,
            "activewindow": {},
            "clients": [],
            "cursorpos": {"x": 0, "y": 0},
        }[what]

    monkeypatch.setattr(observe.hypr, "query", query)
    assert observe.snapshot()["layer_surfaces"] == []


def test_click_rendering_reports_only_geometry_coverage(capsys):
    bar = {
        "address": "0xbar",
        "namespace": "bar",
        "monitor": "DP-1",
        "level": 2,
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 26},
    }
    result = {
        "action": "click",
        "dry_run": True,
        "window": None,
        "layer_surface": observe.layer_surface_at(10, 10, [bar]),
        "at": {"x": 10, "y": 10},
        "logical": {"x": 10, "y": 10},
        "button": "left",
        "count": 1,
    }
    cli._render_action(result)
    assert "[no window; layer 'bar' covers this point]" in capsys.readouterr().out


def test_click_rendering_keeps_no_window_when_nothing_covers(capsys):
    result = {
        "action": "click",
        "dry_run": True,
        "window": None,
        "layer_surface": observe.layer_surface_at(200, 200, []),
        "at": {"x": 200, "y": 200},
        "logical": {"x": 200, "y": 200},
        "button": "left",
        "count": 1,
    }
    cli._render_action(result)
    assert "[no window]" in capsys.readouterr().out


def test_click_rendering_uses_the_highest_covering_layer(capsys):
    bottom = {
        "address": "0xbottom",
        "namespace": "wallpaper",
        "monitor": "DP-1",
        "level": 0,
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100},
    }
    top = {
        "address": "0xtop",
        "namespace": "notification",
        "monitor": "DP-1",
        "level": 3,
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100},
    }
    result = {
        "action": "click",
        "dry_run": True,
        "window": None,
        "layer_surface": observe.layer_surface_at(10, 10, [bottom, top]),
        "at": {"x": 10, "y": 10},
        "logical": {"x": 10, "y": 10},
        "button": "left",
        "count": 1,
    }
    cli._render_action(result)
    output = capsys.readouterr().out
    assert "layer 'notification' covers this point" in output
    assert "wallpaper" not in output


def test_desktop_bounds_span_both_outputs(monitors):
    assert observe.desktop_bounds(monitors) == {"x": 0, "y": 0, "w": 4608, "h": 1440}


def test_desktop_bounds_ignore_disabled(monitors):
    monitors[0]["disabled"] = True
    assert observe.desktop_bounds(monitors)["w"] == 2048


def test_desktop_bounds_with_no_outputs():
    assert observe.desktop_bounds([])["w"] == 0


def test_reachability_reports_intersections_and_full_coverage():
    result = observe.reachability(
        {"x": 10, "y": 20, "w": 30, "h": 40},
        [{"name": "OUT", "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100}}],
    )
    assert result["status"] == "fully-reachable"
    assert result["reachable_area"] == 1200
    assert result["intersections"][0]["output"] == "OUT"
    assert result["gaps"] == []


def test_reachability_separates_edge_excess_from_an_interior_gap():
    outputs = [
        {"name": "LEFT", "logical_rect": {"x": -20.5, "y": 0, "w": 20.5, "h": 100}},
        {"name": "RIGHT", "logical_rect": {"x": 10, "y": 0, "w": 20, "h": 100}},
    ]
    result = observe.reachability({"x": -30, "y": 0, "w": 70, "h": 100}, outputs)
    assert result["status"] == "partially-reachable"
    assert result["edge_excess"]["left"] == pytest.approx(9.5)
    assert result["gap_count"] == 1
    assert result["gaps"][0] == {"x": 0.0, "y": 0, "w": 10.0, "h": 100}


def test_reachability_ignores_disabled_outputs_and_bounds_gap_details():
    outputs = [
        {"name": "OFF", "disabled": True, "logical_rect": {"x": 0, "y": 0, "w": 10, "h": 10}},
        {"name": "ON-A", "logical_rect": {"x": 10, "y": 20, "w": 5, "h": 10}},
        {"name": "ON-B", "logical_rect": {"x": 20, "y": 20, "w": 5, "h": 10}},
    ]
    result = observe.reachability(
        {"x": 0, "y": 0, "w": 30, "h": 30}, outputs, max_gaps=0
    )
    assert [item["output"] for item in result["intersections"]] == ["ON-A", "ON-B"]
    assert result["gap_count"] > 0
    assert result["gaps"] == []
    assert result["truncated"] is True


def test_every_exit_code_documents_a_response():
    codes = {
        v for k, v in vars(exits).items() if k.isupper() and isinstance(v, int)
    }
    assert codes == set(exits.RESPONSE)


def test_every_documented_response_has_a_production_exit_site():
    package = Path(__file__).parents[1] / "src" / "cu"
    source = "\n".join(
        path.read_text()
        for path in package.glob("*.py")
        if path.name not in {"exits.py", "guide.py"}
    )
    reachable = {
        code
        for name, code in vars(exits).items()
        if name.isupper() and isinstance(code, int) and f"exits.{name}" in source
    }
    assert reachable == set(exits.RESPONSE)


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


def test_each_common_preflight_shares_one_fresh_process_snapshot(monkeypatch):
    scans = []
    seen = []

    def fresh_scan(*, fresh=False):
        assert fresh is True
        snapshot = []
        scans.append(snapshot)
        return snapshot

    monkeypatch.setattr(checks, "_processes", fresh_scan)
    monkeypatch.setattr(safety, "check_abort", lambda: None)
    monkeypatch.setattr(safety, "check_denylist", lambda *_args: None)
    monkeypatch.setattr(safety, "check_session", seen.append)
    monkeypatch.setattr(safety, "check_remapper", seen.append)

    safety._common(config.DEFAULTS, None)
    safety._common(config.DEFAULTS, None)

    assert len(scans) == 2
    assert seen[0] is scans[0]
    assert seen[1] is scans[0]
    assert seen[2] is scans[1]
    assert seen[3] is scans[1]


def test_a_long_run_re_reads_before_acting_again(monkeypatch):
    """A replay injects over minutes, so a session locked mid-run must be seen."""
    clock = [0.0]
    scans = _counted_scan(monkeypatch, clock)
    checks.session_unlocked()
    clock[0] = checks.PROCESS_SCAN_TTL + 0.1
    checks.session_unlocked()
    assert len(scans) == 2


# --- every failure reaches the caller as a code -----------------------------


def _error_classes():
    """Every error class the package declares, found rather than listed."""
    import importlib
    import pkgutil

    import cu

    found = {}
    for module in pkgutil.iter_modules(cu.__path__):
        loaded = importlib.import_module(f"cu.{module.name}")
        for name, value in vars(loaded).items():
            declared_here = getattr(value, "__module__", None) == loaded.__name__
            if declared_here and isinstance(value, type) and issubclass(value, Exception):
                found[f"{module.name}.{name}"] = value
    return found


@pytest.mark.parametrize("where", sorted(_error_classes()))
def test_a_declared_error_becomes_an_exit_code(where, monkeypatch, capsys):
    """A traceback leaves the caller with nothing to branch on, and this CLI's
    whole contract is its exit codes."""
    from cu import cli

    error = _error_classes()[where]
    raised = error(1, "boom") if error is cli.Refused else error("boom")

    def explode(_args):
        raise raised

    monkeypatch.setattr(cli, "cmd_observe", explode)
    code = cli.main(["observe"])
    assert code != 0
    assert code in exits.RESPONSE
    assert "boom" in capsys.readouterr().err


# --- a reader that goes away ------------------------------------------------


def test_output_is_flushed_where_a_dead_pipe_can_still_be_handled(monkeypatch):
    """`cu observe | head`. Left to interpreter shutdown, the failed flush has
    nowhere to report but stderr, which is noise the caller did not ask for."""
    import io

    from cu import cli

    class DeadPipe(io.StringIO):
        def flush(self):
            raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(cli.sys, "stdout", DeadPipe())
    with pytest.raises(BrokenPipeError):
        cli._emit({"monitors": []}, True, print)


def test_a_dead_reader_is_success_and_the_last_flush_gets_a_sink(monkeypatch):
    from cu import cli

    redirected = []
    monkeypatch.setattr(cli.os, "dup2", lambda *fds: redirected.append(fds))

    def explode(_args):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(cli, "cmd_observe", explode)
    assert cli.main(["observe"]) == exits.OK
    assert redirected, "the flush python runs on the way out needs somewhere to go"
