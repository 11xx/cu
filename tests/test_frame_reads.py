"""Cheap reads and derived images from pixels already captured in a frame."""

from __future__ import annotations

import json

import pytest

from cu import cli, exits, frames, png

RECT = {"x": 100, "y": 50, "w": 20, "h": 10}
WINDOW = {
    "address": "0xdead",
    "class": "zen",
    "title": "a page",
    "logical_rect": RECT,
}


def stored_frame(width: int, height: int, pixels: bytes) -> dict:
    with frames.capture_scratch() as scratch:
        scratch.write_bytes(png.encode(width, height, pixels))
        return frames.new(
            source={"type": "window", "name": "zen"},
            logical_rect=RECT,
            image_size={"w": width, "h": height},
            scratch=scratch,
            profile="openai",
            window=WINDOW,
            visual_tokens=999,
        )


def test_pixel_reports_exact_rgba_colour_and_preserves_alpha(capsys):
    pixels = bytes((1, 2, 3, 4, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120))
    frame = stored_frame(2, 2, pixels)

    assert cli.main(["pixel", "--frame", frame["id"], "--at", "1,0", "--json"]) == exits.OK
    payload = json.loads(capsys.readouterr().out)
    assert {key: payload[key] for key in ("r", "g", "b", "a", "hex")} == {
        "r": 10,
        "g": 20,
        "b": 30,
        "a": 40,
        "hex": "#0A141E28",
    }


def test_pixel_refuses_an_out_of_bounds_coordinate(capsys):
    frame = stored_frame(2, 2, bytes(2 * 2 * 4))

    assert cli.main(["pixel", "--frame", frame["id"], "--at", "2,2"]) == exits.USAGE
    assert "outside frame" in capsys.readouterr().err


def test_pixel_radius_uses_channel_medians_despite_one_outlier(capsys):
    ordinary = bytes((10, 20, 30, 40))
    pixels = ordinary * 4 + bytes((255, 0, 255, 0)) + ordinary * 4
    frame = stored_frame(3, 3, pixels)

    result = cli.main(
        ["pixel", "--frame", frame["id"], "--at", "1,1", "--radius", "1", "--json"]
    )
    assert result == exits.OK
    payload = json.loads(capsys.readouterr().out)
    assert tuple(payload[channel] for channel in ("r", "g", "b", "a")) == (10, 20, 30, 40)


def test_magnify_preserves_frame_meaning_and_uses_nearest_neighbour(capsys):
    pixels = bytes((1, 2, 3, 4, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120))
    source = stored_frame(2, 2, pixels)
    source["captured_at"] = 1234.5
    frames.save(source)

    assert cli.main(
        ["magnify", "--frame", source["id"], "--scale", "3", "--json"]
    ) == exits.OK
    derived = json.loads(capsys.readouterr().out)
    width, height, enlarged = png.decode(frames.path_for(derived["id"], ".png").read_bytes())

    assert (width, height) == (6, 6)
    assert derived["image_size"] == {"w": 6, "h": 6}
    assert derived["captured_at"] == source["captured_at"]
    assert derived["logical_rect"] == source["logical_rect"]
    assert derived["window"] == source["window"]
    assert derived["derived_from"] == source["id"]
    assert derived["visual_tokens"] != source["visual_tokens"]
    first_block = [
        enlarged[(y * width + x) * 4 : (y * width + x + 1) * 4]
        for y in range(3)
        for x in range(3)
    ]
    assert first_block == [pixels[:4]] * 9
    assert frames.to_logical(derived, 3, 3) == frames.to_logical(source, 1, 1)


def test_magnify_refuses_a_non_integer_scale():
    frame = stored_frame(1, 1, bytes(4))
    with pytest.raises(SystemExit) as raised:
        cli.main(["magnify", "--frame", frame["id"], "--scale", "2.5"])
    assert raised.value.code == exits.USAGE


@pytest.mark.parametrize("scale", [0, 1])
def test_magnify_refuses_a_scale_below_two(capsys, scale):
    frame = stored_frame(1, 1, bytes(4))

    assert cli.main(
        ["magnify", "--frame", frame["id"], "--scale", str(scale)]
    ) == exits.USAGE
    assert "at least 2" in capsys.readouterr().err


def test_magnify_refuses_an_output_past_the_long_edge_limit(capsys):
    frame = stored_frame(2, 1, bytes(2 * 4))
    scale = cli.MAX_MAGNIFIED_LONG_EDGE // 2 + 1

    assert cli.main(
        ["magnify", "--frame", frame["id"], "--scale", str(scale)]
    ) == exits.USAGE
    assert f"limit is {cli.MAX_MAGNIFIED_LONG_EDGE}px" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["pixel", "--frame", "f1", "--at", "0,0"],
        ["magnify", "--frame", "f1", "--scale", "2"],
    ],
)
def test_frame_read_commands_never_take_the_desktop_lock(argv):
    assert cli.build_parser().parse_args(argv).injects is False
