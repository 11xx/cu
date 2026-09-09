"""The frame contract: the transform, and the refusals that guard it."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from cu import capture, config, exits, frames, png
from cu.cli import _parse_region

MONITORS = [
    {"name": "DP-1", "logical_rect": {"x": 2048, "y": 0, "w": 2560, "h": 1440}},
    {"name": "DP-2", "logical_rect": {"x": 0, "y": 0, "w": 2048, "h": 1152}},
]


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))


def frame(rect, image, **extra):
    return {
        "id": "ftest",
        "logical_rect": rect,
        "image_size": image,
        "captured_at": time.time(),
        **extra,
    }


def test_untouched_frame_maps_one_to_one():
    f = frame({"x": 0, "y": 0, "w": 100, "h": 50}, {"w": 100, "h": 50})
    assert frames.to_logical(f, 30, 20) == (30, 20)


def test_downscaled_frame_scales_back_up():
    """A 2048x1152 output shown at 1920x1080 must still land on the right pixel."""
    f = frame({"x": 0, "y": 0, "w": 2048, "h": 1152}, {"w": 1920, "h": 1080})
    assert frames.to_logical(f, 960, 540) == (1024, 576)


def test_offset_frame_adds_its_origin():
    """A window crop on the second monitor: image origin is not logical origin."""
    f = frame({"x": 2048, "y": 100, "w": 800, "h": 400}, {"w": 400, "h": 200})
    assert frames.to_logical(f, 0, 0) == (2048, 100)
    assert frames.to_logical(f, 400, 200) == (2848, 500)


def test_coordinates_outside_the_image_are_refused():
    f = frame({"x": 0, "y": 0, "w": 100, "h": 50}, {"w": 100, "h": 50})
    with pytest.raises(frames.FrameError):
        frames.to_logical(f, 101, 10)


def test_degenerate_image_is_refused():
    f = frame({"x": 0, "y": 0, "w": 100, "h": 50}, {"w": 0, "h": 50})
    with pytest.raises(frames.FrameError):
        frames.to_logical(f, 0, 0)


WINDOW = {
    "address": "0xdead",
    "class": "zen",
    "logical_rect": {"x": 3, "y": 29, "w": 2042, "h": 1120},
}
LIVE = [{"address": "0xdead", "logical_rect": WINDOW["logical_rect"]}]


def test_fresh_window_frame_is_usable():
    f = frame({"x": 3, "y": 29, "w": 2042, "h": 1120}, {"w": 1, "h": 1}, window=WINDOW)
    assert frames.staleness(f, LIVE, MONITORS) is None


def test_old_frame_is_stale():
    f = frame({"x": 0, "y": 0, "w": 1, "h": 1}, {"w": 1, "h": 1})
    f["captured_at"] = time.time() - 999
    assert "old" in frames.staleness(f, LIVE, MONITORS)


def test_closed_window_makes_the_frame_stale():
    f = frame({"x": 3, "y": 29, "w": 2042, "h": 1120}, {"w": 1, "h": 1}, window=WINDOW)
    assert "gone" in frames.staleness(f, [], MONITORS)


def test_moved_window_makes_the_frame_stale():
    """The image still looks plausible, which is exactly why age alone is not enough."""
    f = frame({"x": 3, "y": 29, "w": 2042, "h": 1120}, {"w": 1, "h": 1}, window=WINDOW)
    moved = [{"address": "0xdead", "logical_rect": {"x": 500, "y": 29, "w": 2042, "h": 1120}}]
    assert "moved" in frames.staleness(f, moved, MONITORS)


def output_frame() -> dict:
    monitor = MONITORS[1]
    return frame(
        monitor["logical_rect"],
        {"w": 1, "h": 1},
        window=None,
        monitor={"name": monitor["name"], "logical_rect": monitor["logical_rect"]},
    )


def test_unchanged_output_geometry_keeps_a_frame_usable():
    assert frames.staleness(output_frame(), [], MONITORS) is None


def test_changed_output_geometry_makes_a_frame_stale():
    moved = [
        MONITORS[0],
        {**MONITORS[1], "logical_rect": {"x": 10, "y": 0, "w": 2048, "h": 1152}},
    ]
    assert "moved" in frames.staleness(output_frame(), [], moved)


def test_vanished_output_makes_a_frame_stale():
    assert "gone" in frames.staleness(output_frame(), [], [MONITORS[0]])


def test_ids_are_monotonic_and_never_reused():
    seen = [frames._next_id() for _ in range(3)]
    assert seen == ["f1", "f2", "f3"]


@pytest.mark.parametrize("bad", ["../other", "f0", "f-1", "1", "f1/extra"])
def test_frame_ids_cannot_escape_the_store(bad):
    with pytest.raises(frames.FrameError, match="invalid frame id"):
        frames.load(bad)


def test_each_capture_gets_a_private_scratch_path():
    with frames.capture_scratch() as first, frames.capture_scratch() as second:
        assert first != second
        assert first.parent == second.parent == frames.store_dir()
    assert not first.exists() and not second.exists()


def test_region_on_one_output_resolves():
    rect = {"x": 100, "y": 100, "w": 400, "h": 200}
    assert capture.sole_monitor(rect, MONITORS)["name"] == "DP-2"


def test_region_spanning_outputs_is_refused():
    """Hyprland would composite it at the larger scale, upscaling the other half."""
    rect = {"x": 2000, "y": 100, "w": 200, "h": 100}
    with pytest.raises(capture.CaptureError, match="spans"):
        capture.sole_monitor(rect, MONITORS)


def test_region_off_screen_is_refused():
    with pytest.raises(capture.CaptureError, match="outside"):
        capture.sole_monitor({"x": 99000, "y": 0, "w": 10, "h": 10}, MONITORS)


def test_clamp_trims_to_the_output():
    rect = {"x": 1900, "y": 0, "w": 400, "h": 100}
    assert capture.clamp(rect, MONITORS[1]["logical_rect"]) == {
        "x": 1900,
        "y": 0,
        "w": 148,
        "h": 100,
    }


def test_clamp_snaps_a_fractional_region_to_whole_pixels():
    """A landmark resolves to fractions; grim only takes integers.

    The frame must record the rectangle grim was actually given, or every
    coordinate mapped back through it inherits the rounding.
    """
    rect = {"x": 597.45, "y": 100.6, "w": 204.3, "h": 51.2}
    snapped = capture.clamp(rect, MONITORS[1]["logical_rect"])
    assert snapped == {"x": 597, "y": 100, "w": 205, "h": 52}
    assert all(float(v).is_integer() for v in snapped.values())


def test_clamp_covers_the_region_it_was_asked_for():
    rect = {"x": 10.7, "y": 20.2, "w": 30.9, "h": 40.1}
    snapped = capture.clamp(rect, MONITORS[1]["logical_rect"])
    assert snapped["x"] <= rect["x"] and snapped["y"] <= rect["y"]
    assert snapped["x"] + snapped["w"] >= rect["x"] + rect["w"]
    assert snapped["y"] + snapped["h"] >= rect["y"] + rect["h"]


def test_clamp_stays_inside_the_output_when_rounding_outward():
    """Rounding out at the far edge must not walk off the monitor."""
    bounds = MONITORS[1]["logical_rect"]
    rect = {"x": 2000.2, "y": 1100.4, "w": 47.9, "h": 51.9}
    snapped = capture.clamp(rect, bounds)
    assert snapped["x"] + snapped["w"] <= bounds["x"] + bounds["w"]
    assert snapped["y"] + snapped["h"] <= bounds["y"] + bounds["h"]


@pytest.mark.parametrize("text", ["100,100 400x200", "100,100,400,200"])
def test_region_syntaxes(text):
    assert _parse_region(text) == {"x": 100, "y": 100, "w": 400, "h": 200}


@pytest.mark.parametrize("text", ["junk", "1,2 3", "1,2 0x5", "1,2 -3x5"])
def test_bad_regions_are_usage_errors(text):
    with pytest.raises(ValueError):
        _parse_region(text)


def _region_shot_world(monkeypatch):
    """A two-output desktop whose captured rectangles are observable."""
    from cu import capture as capture_module
    from cu import cli, observe

    monitors = [{**monitor, "scale": 1.0} for monitor in MONITORS]
    monkeypatch.setattr(
        observe,
        "snapshot",
        lambda: {
            "monitors": monitors,
            "windows": [],
            "focused": None,
            "cursor_logical": {"x": 0, "y": 0},
            "desktop_logical_bounds": {"x": 0, "y": 0, "w": 4608, "h": 1440},
        },
    )
    grabbed = []

    def fake_grab(rect, dest, scale):
        grabbed.append(rect)
        dest.write_bytes(_png(1))

    monkeypatch.setattr(capture_module, "grab_region", fake_grab)
    monkeypatch.setattr(capture_module, "size_of", lambda path: {"w": 800, "h": 600})
    return cli, grabbed


@pytest.mark.parametrize(
    ("argv", "expected", "source"),
    [
        (
            ["--output", "DP-1", "--region", "0,0 900x26"],
            {"x": 2048, "y": 0, "w": 900, "h": 26},
            {"type": "region", "name": "DP-1"},
        ),
        (
            ["--output", "DP-2", "--region", "0,0 900x26"],
            {"x": 0, "y": 0, "w": 900, "h": 26},
            {"type": "region", "name": "DP-2"},
        ),
        (
            ["--region", "2048,0 900x26"],
            {"x": 2048, "y": 0, "w": 900, "h": 26},
            {"type": "region", "name": "DP-1"},
        ),
    ],
)
def test_shot_regions_keep_absolute_frame_rects(monkeypatch, capsys, argv, expected, source):
    cli, grabbed = _region_shot_world(monkeypatch)

    assert cli.main(["shot", "--json", *argv]) == exits.OK
    frame = json.loads(capsys.readouterr().out)
    assert frame["logical_rect"] == expected
    assert frame["source"] == source
    captured_monitor = next(m for m in MONITORS if m["name"] == source["name"])
    assert frame["monitor"]["logical_rect"] == captured_monitor["logical_rect"]
    assert grabbed == [expected]


def test_an_output_relative_region_clamps_to_its_named_output(monkeypatch, capsys):
    cli, grabbed = _region_shot_world(monkeypatch)

    result = cli.main(
        ["shot", "--json", "--output", "DP-1", "--region", "2500,0 200x26"]
    )
    assert result == exits.OK
    frame = json.loads(capsys.readouterr().out)
    assert frame["logical_rect"] == {"x": 4548, "y": 0, "w": 60, "h": 26}
    assert grabbed == [frame["logical_rect"]]


def test_an_output_relative_region_entirely_off_its_output_refuses(monkeypatch, capsys):
    cli, grabbed = _region_shot_world(monkeypatch)

    result = cli.main(["shot", "--output", "DP-1", "--region", "3000,0 900x26"])
    assert result == exits.DEGENERATE_AIM
    assert "empty after clamping" in capsys.readouterr().err
    assert grabbed == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--landmark", "zen/urlbar", "--output", "DP-1"],
        ["--landmark", "zen/urlbar", "--region", "0,0 900x26"],
    ],
)
def test_landmark_still_refuses_output_or_region(monkeypatch, capsys, argv):
    cli, _ = _region_shot_world(monkeypatch)

    assert cli.main(["shot", *argv]) == exits.USAGE
    assert "cannot combine" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--window", "active", "--output", "DP-1"],
        ["--window", "active", "--region", "0,0 900x26"],
    ],
)
def test_window_still_refuses_output_or_region(monkeypatch, capsys, argv):
    cli, _ = _region_shot_world(monkeypatch)

    assert cli.main(["shot", *argv]) == exits.USAGE
    assert "--window cannot combine" in capsys.readouterr().err


DP2_SCALE, DP1_SCALE = 1.25, 1.0


def test_scale_shrinks_an_oversized_output():
    """DP-2's logical 2048 into a 1920 budget."""
    rect = {"x": 0, "y": 0, "w": 2048, "h": 1152}
    assert capture.scale_for(rect, DP2_SCALE, 1920) == pytest.approx(0.9375)


def test_scale_never_exceeds_native_detail():
    """A small region would fit many times over, but upsampling buys no detail."""
    rect = {"x": 0, "y": 0, "w": 400, "h": 200}
    assert capture.scale_for(rect, DP2_SCALE, 1920) == DP2_SCALE


def test_scale_caps_at_one_on_an_unscaled_output():
    rect = {"x": 0, "y": 0, "w": 800, "h": 600}
    assert capture.scale_for(rect, DP1_SCALE, 1920) == DP1_SCALE


def test_scale_uses_the_long_edge():
    """A tall narrow window is bounded by its height, not its width."""
    rect = {"x": 0, "y": 0, "w": 100, "h": 4000}
    assert capture.scale_for(rect, DP1_SCALE, 1000) == pytest.approx(0.25)


def test_scale_of_empty_region_is_refused():
    with pytest.raises(capture.CaptureError):
        capture.scale_for({"x": 0, "y": 0, "w": 0, "h": 0}, 1.0, 1920)


def test_png_size_reads_the_header(tmp_path):
    png = tmp_path / "x.png"
    png.write_bytes(
        capture.PNG_MAGIC
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (1920).to_bytes(4, "big")
        + (1080).to_bytes(4, "big")
    )
    assert capture.size_of(png) == {"w": 1920, "h": 1080}


def test_non_png_is_refused(tmp_path):
    bad = tmp_path / "x.png"
    bad.write_bytes(b"not a png at all really")
    with pytest.raises(capture.CaptureError, match="not a PNG"):
        capture.size_of(bad)


def test_a_missing_capture_tool_is_a_tool_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(capture.shutil, "which", lambda _name: None)
    with pytest.raises(capture.CaptureToolError, match="not installed"):
        capture.grab_region(
            {"x": 0, "y": 0, "w": 10, "h": 10}, tmp_path / "shot.png", 1.0
        )


def test_a_failed_capture_tool_is_a_tool_failure(monkeypatch, tmp_path):
    class Failed:
        returncode = 1
        stderr = "capture failed"

    monkeypatch.setattr(capture.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(capture.subprocess, "run", lambda *_args, **_kwargs: Failed())
    with pytest.raises(capture.CaptureToolError, match="capture failed"):
        capture.grab_region(
            {"x": 0, "y": 0, "w": 10, "h": 10}, tmp_path / "shot.png", 1.0
        )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (capture.CaptureAimError("bad aim"), exits.DEGENERATE_AIM),
        (capture.CaptureToolError("grim failed"), exits.PRECONDITION_FAILED),
    ],
)
def test_capture_error_kinds_map_to_their_contract(monkeypatch, error, expected):
    from cu import cli

    def fail(_args):
        raise error

    monkeypatch.setattr(cli, "cmd_observe", fail)
    assert cli.main(["observe"]) == expected


def test_collect_drops_only_the_expired(monkeypatch):
    store = frames.store_dir()
    keep = store / "f1.json"
    keep.write_text("{}")
    (store / "f1.png").write_text("x")
    old = store / "f2.json"
    old.write_text("{}")
    (store / "f2.png").write_text("x")
    ancient = time.time() - frames.GC_MAX_AGE - 60
    import os

    os.utime(old, (ancient, ancient))

    assert frames.collect() == 1
    assert keep.exists() and not old.exists()
    assert not (store / "f2.png").exists()


SHOT_WINDOW = {
    "address": "0xdead",
    "class": "zen",
    "title": "a page",
    "focused": True,
    "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600},
}


def _shot_world(monkeypatch, tmp_path, pixels: bytes):
    """A desktop with one window on one output, and a grim that writes `pixels`."""
    from cu import capture as capture_module
    from cu import cli, observe

    monkeypatch.setattr(
        observe,
        "snapshot",
        lambda: {
            "monitors": [{**MONITORS[1], "scale": 1.0}],
            "windows": [SHOT_WINDOW],
            "focused": SHOT_WINDOW,
            "cursor_logical": {"x": 0, "y": 0},
            "desktop_logical_bounds": MONITORS[1]["logical_rect"],
        },
    )

    def fake_grab(rect, dest, scale):
        dest.write_bytes(pixels)

    monkeypatch.setattr(capture_module, "grab_region", fake_grab)
    monkeypatch.setattr(capture_module, "size_of", lambda path: {"w": 800, "h": 600})
    monkeypatch.setattr(observe, "windows", lambda _monitors: [SHOT_WINDOW])
    monkeypatch.setattr(observe, "cursor", lambda: {"x": 0, "y": 0})
    return cli


def _png(colour: int) -> bytes:
    return capture.PNG_MAGIC + b"\0" * 8 + (800).to_bytes(4, "big") + (600).to_bytes(
        4, "big"
    ) + bytes([colour])


def _args(**overrides):
    import argparse

    return argparse.Namespace(
        json=True, window=None, output=None, region=None, landmark=None,
        context=None, profile="claude", if_changed=True, after=None, watch=None,
        poll_ms=100, quiet_ms=200, timeout=10.0, **overrides
    )


def test_if_changed_reuses_the_frame_whose_pixels_match(monkeypatch, tmp_path, capsys):
    cli = _shot_world(monkeypatch, tmp_path, _png(1))
    cli.cmd_shot(_args())
    first = json.loads(capsys.readouterr().out)

    cli.cmd_shot(_args())
    second = json.loads(capsys.readouterr().out)
    assert second["changed"] is False
    assert second["id"] == first["id"]


def test_a_reused_frame_is_dated_by_the_capture_that_confirmed_it(
    monkeypatch, tmp_path, capsys
):
    """The pixels were just re-read and matched, which is what staleness stands in
    for. Keeping the original timestamp would refuse a frame proven current."""
    cli = _shot_world(monkeypatch, tmp_path, _png(1))
    cli.cmd_shot(_args())
    first = json.loads(capsys.readouterr().out)

    aged = frames.load(first["id"])
    aged["captured_at"] -= 3600
    frames.save(aged)

    cli.cmd_shot(_args())
    reused = json.loads(capsys.readouterr().out)
    assert frames.age(reused) < 5
    assert frames.staleness(
        frames.load(first["id"]), [SHOT_WINDOW], [{**MONITORS[1], "scale": 1.0}]
    ) is None


def test_if_changed_re_reads_the_cursor_only_when_reusing(
    monkeypatch, tmp_path, capsys
):
    cli = _shot_world(monkeypatch, tmp_path, _png(1))
    cli.cmd_shot(_args())
    capsys.readouterr()
    reads = []
    monkeypatch.setattr(
        cli.observe,
        "cursor",
        lambda: reads.append(True) or {"x": 31, "y": 47},
    )

    cli.cmd_shot(_args())
    reused = json.loads(capsys.readouterr().out)
    assert reused["cursor_logical"] == {"x": 31, "y": 47}
    assert reads == [True]


@pytest.mark.parametrize("moved", [False, True])
def test_window_geometry_is_rechecked_after_capture(
    monkeypatch, tmp_path, capsys, moved
):
    cli = _shot_world(monkeypatch, tmp_path, _png(1))
    live = SHOT_WINDOW
    if moved:
        live = {
            **SHOT_WINDOW,
            "logical_rect": {**SHOT_WINDOW["logical_rect"], "x": 25},
        }
    queries = []
    monkeypatch.setattr(
        cli.observe,
        "windows",
        lambda monitors: queries.append(monitors) or [live],
    )

    result = cli.main(["shot", "--json", "--for", "claude"])
    assert result == (exits.STALE_FRAME if moved else exits.OK)
    assert len(queries) == 1
    if moved:
        assert "during capture" in capsys.readouterr().err


def test_changed_pixels_produce_a_new_frame(monkeypatch, tmp_path, capsys):
    cli = _shot_world(monkeypatch, tmp_path, _png(1))
    cli.cmd_shot(_args())
    first = json.loads(capsys.readouterr().out)

    _shot_world(monkeypatch, tmp_path, _png(2))
    cli.cmd_shot(_args())
    second = json.loads(capsys.readouterr().out)
    assert second["changed"] is True
    assert second["id"] != first["id"]


def _watch_png(*colours: int) -> bytes:
    rgba = b"".join(bytes((colour, colour, colour, 255)) for colour in colours)
    return png.encode(len(colours), 1, rgba)


def _watch_world(monkeypatch, sequence):
    from cu import capture as capture_module
    from cu import cli, observe

    reference_png = sequence[0]
    width, height, _ = png.decode(reference_png)
    rect = {"x": 10, "y": 20, "w": width, "h": height}
    with frames.capture_scratch() as scratch:
        scratch.write_bytes(reference_png)
        reference = frames.new(
            source={"type": "region", "name": "DP-2"},
            logical_rect=rect,
            image_size={"w": width, "h": height},
            scratch=scratch,
            native_size={"w": width, "h": height},
            digest_native=capture.digest(scratch),
            monitor={"name": "DP-2", "scale": 1.0, "logical_rect": rect},
            profile="claude",
            capture_scale=1.0,
            visual_tokens=1,
            cursor_logical={"x": 0, "y": 0},
            window=None,
        )
    monkeypatch.setattr(
        observe,
        "snapshot",
        lambda: {
            "monitors": [{"name": "DP-2", "scale": 1.0, "logical_rect": rect}],
            "windows": [],
            "focused": None,
            "cursor_logical": {"x": 7, "y": 8},
            "desktop_logical_bounds": rect,
        },
    )
    probes = iter(sequence[1:])
    last = sequence[-1]

    def fake_grab(_rect, dest, _scale):
        nonlocal last
        last = next(probes, last)
        dest.write_bytes(last)

    monkeypatch.setattr(capture_module, "grab_region", fake_grab)
    monkeypatch.setattr(observe, "cursor", lambda: {"x": 7, "y": 8})
    clock = [0.0]
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cli.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    return cli, reference


def _watch_argv(reference, *extra):
    return [
        "shot",
        "--json",
        "--after",
        reference["id"],
        "--watch",
        "0,0 1x1",
        "--poll-ms",
        "1",
        "--quiet-ms",
        "2",
        "--timeout",
        "0.01",
        *extra,
    ]


def test_after_returns_on_the_first_different_probe(monkeypatch, capsys):
    original = _watch_png(1)
    changed = _watch_png(2)
    cli, reference = _watch_world(monkeypatch, [original, changed])

    argv = _watch_argv(reference, "--quiet-ms", "0")
    assert cli.main(argv) == exits.OK
    result = json.loads(capsys.readouterr().out)
    assert result["wait"]["probes"] == 1
    assert Path(result["path"]).read_bytes() == changed


def test_after_returns_the_probe_that_settled(monkeypatch, capsys):
    original = _watch_png(1)
    changed = _watch_png(2)
    cli, reference = _watch_world(
        monkeypatch, [original, original, original, changed, changed, changed]
    )

    assert cli.main(_watch_argv(reference)) == exits.OK
    result = json.loads(capsys.readouterr().out)
    assert result["wait"]["probes"] == 5
    assert Path(result["path"]).read_bytes() == changed


def test_after_times_out_without_creating_a_frame(monkeypatch, capsys):
    original = _watch_png(1)
    cli, reference = _watch_world(monkeypatch, [original])

    argv = _watch_argv(reference, "--timeout", "0.003")
    assert cli.main(argv) == exits.TIMED_OUT
    captured = capsys.readouterr()
    assert "0.003s and 3 probes" in captured.err
    assert list(frames.store_dir().glob("f*.json")) == [
        frames.path_for(reference["id"], ".json")
    ]


def test_after_ignores_changes_outside_the_watch_box(monkeypatch, capsys):
    original = _watch_png(1, 1)
    outside_changed = _watch_png(1, 2)
    cli, reference = _watch_world(
        monkeypatch, [original, outside_changed, outside_changed]
    )

    argv = _watch_argv(reference, "--timeout", "0.003")
    assert cli.main(argv) == exits.TIMED_OUT
    assert "3 probes" in capsys.readouterr().err


def test_after_refuses_a_probe_with_a_different_image_size(monkeypatch, capsys):
    original = _watch_png(1)
    cli, reference = _watch_world(monkeypatch, [original, _watch_png(2, 2)])

    assert cli.main(_watch_argv(reference)) == exits.STALE_FRAME
    assert "capture conditions changed" in capsys.readouterr().err
    assert len(list(frames.store_dir().glob("f*.json"))) == 1


@pytest.mark.parametrize(
    "target",
    [
        ["--window", "active"],
        ["--output", "DP-2"],
        ["--region", "0,0 1x1"],
        ["--landmark", "zen/urlbar"],
    ],
)
def test_after_refuses_another_capture_target(monkeypatch, capsys, target):
    original = _watch_png(1)
    cli, reference = _watch_world(monkeypatch, [original])

    assert cli.main([*_watch_argv(reference), *target]) == exits.USAGE
    assert "reference frame already names the region" in capsys.readouterr().err


def test_after_requires_an_explicit_watch_box(monkeypatch, capsys):
    original = _watch_png(1)
    cli, reference = _watch_world(monkeypatch, [original])

    assert cli.main(["shot", "--after", reference["id"]]) == exits.USAGE
    assert "ticking clock" in capsys.readouterr().err


def test_after_refuses_a_watch_box_outside_the_reference(monkeypatch, capsys):
    original = _watch_png(1)
    cli, reference = _watch_world(monkeypatch, [original])

    argv = _watch_argv(reference)
    argv[argv.index("0,0 1x1")] = "1,0 1x1"
    assert cli.main(argv) == exits.USAGE
    assert "outside the reference image" in capsys.readouterr().err


def test_after_frame_has_a_fresh_capture_time(monkeypatch, capsys):
    original = _watch_png(1)
    changed = _watch_png(2)
    cli, reference = _watch_world(monkeypatch, [original, changed])
    reference["captured_at"] = 1234.5
    frames.save(reference)

    argv = _watch_argv(reference, "--quiet-ms", "0")
    assert cli.main(argv) == exits.OK
    result = json.loads(capsys.readouterr().out)
    assert result["captured_at"] > reference["captured_at"]
    assert result["logical_rect"] == reference["logical_rect"]
    assert result["source"] == reference["source"]


def test_after_re_reads_the_cursor_only_when_promoting(monkeypatch, capsys):
    original = _watch_png(1)
    changed = _watch_png(2)
    cli, reference = _watch_world(monkeypatch, [original, original, changed])
    reads = []
    monkeypatch.setattr(
        cli.observe,
        "cursor",
        lambda: reads.append(True) or {"x": 53, "y": 59},
    )

    argv = _watch_argv(reference, "--quiet-ms", "0")
    assert cli.main(argv) == exits.OK
    result = json.loads(capsys.readouterr().out)
    assert result["wait"]["probes"] == 2
    assert result["cursor_logical"] == {"x": 53, "y": 59}
    assert reads == [True]


# --- content identity ----------------------------------------------------


def _window_frame(title="page A", address="0x1", cls="helium"):
    return {
        "id": "f60",
        "window": {"address": address, "class": cls, "title": title,
                   "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600}},
        "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600},
        "image_size": {"w": 800, "h": 600},
        "captured_at": time.time(),
    }


def _live(title="page A", address="0x1", cls="helium"):
    return [{"address": address, "class": cls, "title": title,
             "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600}}]


def test_the_same_window_showing_the_same_thing_has_not_drifted():
    assert frames.content_drift(_window_frame(), _live()) is None


def test_a_tab_that_navigated_is_reported_with_both_identities():
    why = frames.content_drift(_window_frame("YouTube - Helium"), _live("Instagram - Helium"))
    assert "YouTube - Helium" in why and "Instagram - Helium" in why


def test_geometry_alone_cannot_see_a_navigation():
    """The window did not move, so staleness is silent — which is the whole
    reason content identity is a separate question."""
    frame = _window_frame("YouTube - Helium")
    assert frames.staleness(frame, _live("Instagram - Helium"), []) is None


def test_a_class_whose_title_drifts_can_be_exempted():
    frame = _window_frame("inbox (3)", cls="thunderbird")
    live = _live("inbox (4)", cls="thunderbird")
    assert frames.content_drift(frame, live, ["Thunderbird"]) is None


def test_an_output_frame_depicts_no_window_and_asserts_nothing():
    assert frames.content_drift({"id": "f1", "monitor": {"name": "DP-2"}}, _live()) is None


def test_a_better_identity_wins_over_the_title():
    """A browser adapter that can name the document should be believed over a
    title, which a page controls and can leave unchanged across a navigation."""
    frame = _window_frame()
    frame["window"]["content_id"] = "https://a.example/one"
    live = _live()
    live[0]["content_id"] = "https://a.example/two"
    assert "one" in frames.content_drift(frame, live)


def test_a_frame_backed_action_refuses_before_injecting(monkeypatch):
    from cu import exits, safety

    cfg = config.load()
    frame = _window_frame("YouTube - Helium")
    with pytest.raises(safety.Refused) as caught:
        safety.check_frame(cfg, frame, _live("Instagram - Helium"), [])
    assert caught.value.code == exits.CONTENT_CHANGED


def test_the_interlock_can_be_turned_off_wholesale(monkeypatch):
    from cu import safety

    cfg = config.load()
    cfg["safety"]["frame_content_identity"] = False
    safety.check_frame(cfg, _window_frame("YouTube - Helium"), _live("Instagram - Helium"), [])


def test_a_reused_frame_re_reads_the_identity_with_the_pixels(monkeypatch, tmp_path):
    """Identical pixels prove the picture is current, so the identity it
    depicts is re-read with it — otherwise a frame just re-proven against the
    screen is refused as depicting content that changed."""
    from cu import cli

    captured = {"address": "0x1", "class": "zen", "title": "page A",
                "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600}}
    live = [{"address": "0x1", "class": "zen", "title": "page B",
             "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600}}]
    assert cli._current_window(captured, live)["title"] == "page B"


def test_a_window_that_vanished_keeps_what_the_capture_recorded():
    """Staleness answers a missing window; inventing a current identity for
    one that is gone would be worse than reporting what was seen."""
    from cu import cli

    captured = {"address": "0x1", "class": "zen", "title": "page A",
                "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600}}
    assert cli._current_window(captured, [])["title"] == "page A"


def test_a_refreshed_identity_keeps_the_geometry_the_pixels_depict():
    """The image depicts the rectangle the capture was aimed at. Adopting a
    newer geometry would let a window that moved during the capture pass the
    staleness check it should fail."""
    from cu import cli

    captured = {"address": "0x1", "class": "zen", "title": "page A",
                "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600},
                "content_id": "https://a.example/one"}
    live = [{"address": "0x1", "class": "zen", "title": "page B",
             "logical_rect": {"x": 40, "y": 40, "w": 800, "h": 600}}]
    refreshed = cli._current_window(captured, live)
    assert refreshed["logical_rect"] == captured["logical_rect"]
    assert refreshed["title"] == "page B"
    # A stronger identity the capture recorded survives the refresh.
    assert refreshed["content_id"] == "https://a.example/one"


def test_a_capture_keeps_the_strongest_identity_the_observer_gave_it():
    """A frame that fell back to the title would be checked by the weaker of
    the two identities for the rest of its life."""
    live = {"address": "0x1", "class": "zen", "title": "Dashboard",
            "content_id": "https://a.example/one",
            "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600}}
    frame = {"id": "f1", "window": {**live}}
    assert frames.content_identity(frame) == "https://a.example/one"
    # And a document swap the title hides is then caught.
    moved = [{**live, "content_id": "https://a.example/two"}]
    assert frames.content_drift(frame, moved)


def test_a_refreshed_capture_takes_the_current_document_identity():
    """Keeping the older one would label a current picture with what the
    window used to show, and every action through it would be refused."""
    from cu import cli

    captured = {"address": "0x1", "class": "zen", "title": "Dashboard",
                "content_id": "https://a.example/one",
                "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600}}
    live = [{"address": "0x1", "class": "zen", "title": "Dashboard",
             "content_id": "https://a.example/two",
             "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600}}]
    assert cli._current_window(captured, live)["content_id"] == "https://a.example/two"


def test_a_drifting_title_exemption_does_not_excuse_a_changed_document():
    """The exemption is about titles that move on their own; a document
    identity changes only when the document does."""
    rect = {"x": 0, "y": 0, "w": 800, "h": 600}
    frame = {"id": "f1", "window": {"address": "0x1", "class": "zen", "title": "Mail",
                                    "content_id": "url:A", "logical_rect": rect}}
    live = [{"address": "0x1", "class": "zen", "title": "Mail (3)",
             "content_id": "url:B", "logical_rect": rect}]
    assert frames.content_drift(frame, live, ["zen"])
    # The same exemption still absorbs a title that moved on its own.
    same_document = [{**live[0], "content_id": "url:A"}]
    assert frames.content_drift(frame, same_document, ["zen"]) is None
