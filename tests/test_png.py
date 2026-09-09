"""The deliberately small PNG subset used by frame readback."""

from __future__ import annotations

import struct
import zlib

import pytest

from cu import png


def chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(data, zlib.crc32(kind))
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def fixture(
    width: int,
    height: int,
    colour_type: int,
    scanlines: bytes,
    *,
    depth: int = 8,
    interlace: int = 0,
) -> bytes:
    header = struct.pack(
        ">IIBBBBB", width, height, depth, colour_type, 0, 0, interlace
    )
    return b"".join(
        (
            png.SIGNATURE,
            chunk(b"IHDR", header),
            chunk(b"IDAT", zlib.compress(scanlines)),
            chunk(b"IEND", b""),
        )
    )


def filtered_row(kind: int, row: bytes, previous: bytes, channels: int) -> bytes:
    encoded = bytearray()
    for index, value in enumerate(row):
        left = row[index - channels] if index >= channels else 0
        above = previous[index]
        upper_left = previous[index - channels] if index >= channels else 0
        predictors = (0, left, above, (left + above) // 2, 0)
        predictor = predictors[kind]
        if kind == 4:
            estimate = left + above - upper_left
            left_distance = abs(estimate - left)
            above_distance = abs(estimate - above)
            upper_left_distance = abs(estimate - upper_left)
            if left_distance <= above_distance and left_distance <= upper_left_distance:
                predictor = left
            elif above_distance <= upper_left_distance:
                predictor = above
            else:
                predictor = upper_left
        encoded.append((value - predictor) & 0xFF)
    return bytes([kind]) + bytes(encoded)


def test_encode_then_decode_returns_identical_pixels():
    pixels = bytes(
        (
            1, 2, 3, 4,
            10, 20, 30, 40,
            100, 110, 120, 130,
            250, 240, 230, 220,
        )
    )
    assert png.decode(png.encode(2, 2, pixels)) == (2, 2, pixels)


def test_all_five_scanline_filters_decode_correctly():
    rows = [
        bytes((10 + row, 20 + row, 30 + row, 40 + row, 50 + row, 60 + row, 70 + row, 80 + row))
        for row in range(5)
    ]
    previous = bytes(8)
    scanlines = bytearray()
    for kind, row in enumerate(rows):
        scanlines.extend(filtered_row(kind, row, previous, 4))
        previous = row
    expected = b"".join(rows)
    assert png.decode(fixture(2, 5, 6, bytes(scanlines))) == (2, 5, expected)


@pytest.mark.parametrize(
    ("colour_type", "scanline", "expected"),
    [
        (0, b"\0\x19", bytes((25, 25, 25, 255))),
        (2, b"\0\x01\x02\x03", bytes((1, 2, 3, 255))),
        (4, b"\0\x19\x7f", bytes((25, 25, 25, 127))),
    ],
)
def test_supported_non_rgba_types_normalize_to_rgba(colour_type, scanline, expected):
    assert png.decode(fixture(1, 1, colour_type, scanline)) == (1, 1, expected)


@pytest.mark.parametrize(("colour_type", "depth"), [(3, 8), (6, 16)])
def test_unsupported_colour_or_depth_names_both(colour_type, depth):
    data = fixture(1, 1, colour_type, b"\0\0", depth=depth)
    match = f"colour type {colour_type} at bit depth {depth}"
    with pytest.raises(ValueError, match=match):
        png.decode(data)


def test_interlaced_png_refuses_explicitly():
    data = fixture(1, 1, 6, b"\0\0\0\0\0", interlace=1)
    with pytest.raises(ValueError, match="interlaced PNGs are unsupported"):
        png.decode(data)


@pytest.mark.parametrize("colour_type", [0, 2, 4, 6])
@pytest.mark.parametrize("kind", range(5))
def test_native_reconstruction_for_every_format_and_filter(colour_type, kind):
    channels = png.CHANNELS[colour_type]
    rows = [bytes((i * 71 + row * 31) % 256 for i in range(7 * channels)) for row in range(3)]
    encoded = b"".join(filtered_row(kind, row, rows[n - 1] if n else bytes(7 * channels),
                                   channels) for n, row in enumerate(rows))
    raw = b"".join(rows)
    expected = bytearray()
    for i in range(0, len(raw), channels):
        pixel = raw[i:i + channels]
        rgb = pixel[:3] if colour_type in (2, 6) else pixel[:1] * 3
        alpha = pixel[-1] if colour_type in (4, 6) else 255
        expected.extend(rgb + bytes([alpha]))
    assert png.decode(fixture(7, 3, colour_type, encoded)) == (7, 3, bytes(expected))


@pytest.mark.parametrize("scanlines", [b"", b"\0\0", b"\0" * 6, b"\5\0\0\0\0"])
def test_invalid_scanline_shape_or_filter_refuses(scanlines):
    with pytest.raises(ValueError):
        png.decode(fixture(1, 1, 6, scanlines))


def test_crc_is_checked_even_for_ignored_ancillary_chunks():
    data = fixture(1, 1, 6, b"\0\0\0\0\0")
    damaged = chunk(b"tEXt", b"ignored")[:-1] + b"\0"
    with pytest.raises(ValueError, match="CRC"):
        png.decode(data[:33] + damaged + data[33:])


def test_transparency_metadata_does_not_change_raw_channel_contract():
    data = fixture(1, 1, 0, b"\0\x19")
    data = data[:33] + chunk(b"tRNS", b"\0\x19") + data[33:]
    assert png.decode(data) == (1, 1, bytes((25, 25, 25, 255)))


@pytest.mark.parametrize("removed", [1, 10, 12, 20])
def test_truncated_png_refuses(removed):
    with pytest.raises(ValueError):
        png.decode(fixture(1, 1, 6, b"\0\0\0\0\0")[:-removed])


def test_dimensions_bound_memory_before_decompression():
    with pytest.raises(ValueError, match="pixel limit"):
        png.decode(fixture(png.MAX_PIXELS + 1, 1, 6, b"\0"))
