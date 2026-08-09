"""The small subset of PNG needed to inspect and derive cu frames."""

from __future__ import annotations

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    distances = (
        abs(estimate - left),
        abs(estimate - above),
        abs(estimate - upper_left),
    )
    return (left, above, upper_left)[distances.index(min(distances))]


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(data, zlib.crc32(kind))
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def decode(data: bytes) -> tuple[int, int, bytes]:
    """Decode one supported PNG into width, height, and RGBA bytes."""
    if not data.startswith(SIGNATURE):
        raise ValueError("not a PNG")

    offset = len(SIGNATURE)
    header = None
    compressed = bytearray()
    saw_end = False
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("truncated PNG chunk")
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("truncated PNG chunk")
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(payload, zlib.crc32(kind)) != expected_crc:
            name = kind.decode("ascii", errors="replace")
            raise ValueError(f"PNG {name} chunk has an invalid CRC")
        if header is None and kind != b"IHDR":
            raise ValueError("PNG IHDR must be the first chunk")
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise ValueError("PNG has an invalid IHDR")
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            if length:
                raise ValueError("PNG has an invalid IEND")
            saw_end = True
            break
        offset = end

    if header is None:
        raise ValueError("PNG has no IHDR")
    width, height, depth, colour_type, compression, filtering, interlace = header
    if depth != 8 or colour_type not in CHANNELS:
        raise ValueError(
            f"unsupported PNG colour type {colour_type} at bit depth {depth}"
        )
    if interlace != 0:
        raise ValueError("interlaced PNGs are unsupported")
    if compression != 0 or filtering != 0:
        raise ValueError("unsupported PNG compression or filter method")
    if width == 0 or height == 0:
        raise ValueError("PNG dimensions must be positive")
    if not compressed or not saw_end:
        raise ValueError("PNG is missing IDAT or IEND")

    try:
        filtered = zlib.decompress(compressed)
    except zlib.error as exc:
        raise ValueError(f"cannot decompress PNG pixels: {exc}") from exc
    channels = CHANNELS[colour_type]
    stride = width * channels
    if len(filtered) != height * (stride + 1):
        raise ValueError("PNG scanline data has the wrong size")

    previous = bytearray(stride)
    decoded = bytearray()
    for row_number in range(height):
        start = row_number * (stride + 1)
        filter_type = filtered[start]
        if filter_type > 4:
            raise ValueError(f"unsupported PNG filter type {filter_type}")
        row = bytearray(filtered[start + 1 : start + 1 + stride])
        for index, value in enumerate(row):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            predictor = (0, left, above, (left + above) // 2, 0)[filter_type]
            if filter_type == 4:
                predictor = _paeth(left, above, upper_left)
            row[index] = (value + predictor) & 0xFF
        decoded.extend(row)
        previous = row

    if colour_type == 6:
        return width, height, bytes(decoded)
    rgba = bytearray()
    for index in range(0, len(decoded), channels):
        pixel = decoded[index : index + channels]
        if colour_type == 2:
            rgba.extend((*pixel, 255))
        elif colour_type == 0:
            rgba.extend((pixel[0], pixel[0], pixel[0], 255))
        else:
            rgba.extend((pixel[0], pixel[0], pixel[0], pixel[1]))
    return width, height, bytes(rgba)


def encode(width: int, height: int, rgba: bytes) -> bytes:
    """Encode 8-bit RGBA pixels with one filter-0 scanline per row."""
    if width <= 0 or height <= 0:
        raise ValueError("PNG dimensions must be positive")
    if len(rgba) != width * height * 4:
        raise ValueError("RGBA pixel data has the wrong size")
    stride = width * 4
    scanlines = b"".join(
        b"\0" + rgba[start : start + stride]
        for start in range(0, len(rgba), stride)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"".join(
        (
            SIGNATURE,
            _chunk(b"IHDR", header),
            _chunk(b"IDAT", zlib.compress(scanlines)),
            _chunk(b"IEND", b""),
        )
    )
