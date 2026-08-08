"""The frame contract: the transform, and the refusals that guard it."""

from __future__ import annotations

import json
import time

import pytest

from cu import capture, frames
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
    assert frames.staleness(f, LIVE) is None


def test_old_frame_is_stale():
    f = frame({"x": 0, "y": 0, "w": 1, "h": 1}, {"w": 1, "h": 1})
    f["captured_at"] = time.time() - 999
    assert "old" in frames.staleness(f, LIVE)


def test_closed_window_makes_the_frame_stale():
    f = frame({"x": 3, "y": 29, "w": 2042, "h": 1120}, {"w": 1, "h": 1}, window=WINDOW)
    assert "gone" in frames.staleness(f, [])


def test_moved_window_makes_the_frame_stale():
    """The image still looks plausible, which is exactly why age alone is not enough."""
    f = frame({"x": 3, "y": 29, "w": 2042, "h": 1120}, {"w": 1, "h": 1}, window=WINDOW)
    moved = [{"address": "0xdead", "logical_rect": {"x": 500, "y": 29, "w": 2042, "h": 1120}}]
    assert "moved" in frames.staleness(f, moved)


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
    return cli


def _png(colour: int) -> bytes:
    return capture.PNG_MAGIC + b"\0" * 8 + (800).to_bytes(4, "big") + (600).to_bytes(
        4, "big"
    ) + bytes([colour])


def _args(**overrides):
    import argparse

    return argparse.Namespace(
        json=True, window=None, output=None, region=None, landmark=None,
        context=None, profile="claude", if_changed=True, **overrides
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
    assert frames.staleness(frames.load(first["id"]), [SHOT_WINDOW]) is None


def test_changed_pixels_produce_a_new_frame(monkeypatch, tmp_path, capsys):
    cli = _shot_world(monkeypatch, tmp_path, _png(1))
    cli.cmd_shot(_args())
    first = json.loads(capsys.readouterr().out)

    _shot_world(monkeypatch, tmp_path, _png(2))
    cli.cmd_shot(_args())
    second = json.loads(capsys.readouterr().out)
    assert second["changed"] is True
    assert second["id"] != first["id"]
