"""The small subset of PNG needed to inspect and derive cu frames."""

from __future__ import annotations

import io
import struct
import zlib

from PIL import Image

MAX_PIXELS = 64 * 1024 * 1024

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


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

    if width * height > MAX_PIXELS:
        raise ValueError(f"PNG exceeds the {MAX_PIXELS} pixel limit")
    stride = width * CHANNELS[colour_type]
    expected = height * (stride + 1)
    try:
        decoder = zlib.decompressobj()
        filtered = decoder.decompress(compressed, expected + 1)
    except zlib.error as exc:
        raise ValueError(f"cannot decompress PNG pixels: {exc}") from exc
    if len(filtered) != expected:
        raise ValueError("PNG scanline data has the wrong size")
    if not decoder.eof:
        raise ValueError("cannot decompress PNG pixels: incomplete stream")
    for filter_type in filtered[::stride + 1]:
        if filter_type > 4:
            raise ValueError(f"unsupported PNG filter type {filter_type}")
    del filtered

    # Only pixel-bearing chunks reach Pillow. Ancillary colour profiles and
    # tRNS do not alter this API's raw channels or introduce metadata decoding.
    canonical = b"".join((
        SIGNATURE,
        _chunk(b"IHDR", struct.pack(">IIBBBBB", *header)),
        _chunk(b"IDAT", bytes(compressed)),
        _chunk(b"IEND", b""),
    ))
    try:
        with Image.open(io.BytesIO(canonical)) as image:
            return width, height, image.convert("RGBA").tobytes()
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot decode PNG pixels: {exc}") from exc


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
