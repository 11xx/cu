"""Bounded local transport for Firefox page evidence and explicit interaction.

The browser owns the helper and its private Unix socket. Requests are gated by
per-document capabilities; responses have no persistent page archive.
"""

from __future__ import annotations

import base64
import binascii
import errno
import json
import math
import os
import re
import secrets
import selectors
import signal
import socket
import stat
import struct
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Self

from . import png

PROTOCOL = "cu-browser-read"
VERSION = 9
MAX_MESSAGE_BYTES = 1 << 20
MAX_RECV_CHUNK_BYTES = 64 << 10
MAX_PENDING_REQUESTS = 64
MAX_CLIENTS = 4
DEFAULT_TIMEOUT = 2.0
MAX_TIMEOUT = 30.0
MAX_SESSION_CHARS = 128
MAX_REQUEST_ID_CHARS = 128
MAX_DOCUMENT_ID_CHARS = 128
MAX_HELPER_ID_CHARS = 128
MAX_SOCKET_PATH_CHARS = 4096
MAX_QUERY_CHARS = 256
MAX_SELECTOR_CHARS = 256
MAX_ROLE_CHARS = 64
MAX_PAGE = 1_000
MAX_PAGE_SIZE = 100
MAX_ELEMENTS = 128
MAX_GRANTS = 16
MAX_CAPTURE_BYTES = 512 * 1024
MAX_CAPTURE_PIXELS = 16 * 1024 * 1024
MAX_ACTION_VALUE_CHARS = 1024
MAX_WAIT_MS = 10_000
MAX_WAIT_POLL_MS = 1_000
MAX_DEBUG_ENTRIES = 100
MAX_ERROR_CODE_CHARS = 64
MAX_ERROR_MESSAGE_CHARS = 256
SOCKET_PREFIX = "helper-"
SOCKET_SUFFIX = ".sock"

READ_OPERATIONS = frozenset({"observe", "query", "wait", "ax-query", "console", "network"})
# Evidence read through an attached Chromium debugger rather than the page collector.
DEBUGGER_READS = frozenset({"ax-query", "console", "network"})
# Real input sent through an attached Chromium debugger rather than page events.
TRUSTED_INPUT = frozenset(
    {"trusted-click", "trusted-hover", "trusted-type", "trusted-key", "trusted-scroll"}
)
TRUSTED_KEYS = frozenset(
    {
        "Enter",
        "Tab",
        "Escape",
        "Backspace",
        "Delete",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "Home",
        "End",
        "PageUp",
        "PageDown",
        "Space",
    }
)
ACTION_OPERATIONS = (
    frozenset({"click", "focus", "fill", "scroll", "hover", "select", "capture", "reload"})
    | TRUSTED_INPUT
)
# A receipt from any other backend does not describe the operation that was sent.
_ACTION_BACKENDS = {"reload": "tabs", **dict.fromkeys(TRUSTED_INPUT, "debugger")}
OPERATIONS = READ_OPERATIONS | ACTION_OPERATIONS
CONTROL_OPERATIONS = frozenset({"grant", "release", "debug-attach", "debug-detach", "tabs"})
CAPABILITIES = frozenset({"read", "interact"})
_OBSERVE_FIELDS = frozenset({"include_values", "limit", "selector", "include_details"})
_CAPTURE_FIELDS = frozenset({"region", "full_page"})
_QUERY_FIELDS = frozenset(
    {
        "search",
        "role",
        "visible",
        "page",
        "page_size",
        "include_values",
        "selector",
        "include_details",
    }
)
_FRAME_HEADER = struct.Struct("=I")
_SOCKET_NAME = f"{SOCKET_PREFIX}[0-9a-f]{{8,32}}{SOCKET_SUFFIX}"


class BrowserReadError(RuntimeError):
    """Base class for local read transport failures."""


class ProtocolError(BrowserReadError):
    """A frame or protocol message cannot be accepted."""


class OversizeMessage(ProtocolError):
    """A message body exceeds the transport budget."""


class StaleHelper(BrowserReadError):
    """A discovered helper socket is absent or no longer serving."""


class BrowserReadTimeout(BrowserReadError):
    """A bounded client operation did not finish before its deadline."""


class BrowserActionUncertain(BrowserReadTimeout):
    """An action may have reached Firefox before its response was lost."""


class MismatchedResponse(ProtocolError):
    """A response did not match the request waiting on the socket."""


class HelperUnavailable(BrowserReadError):
    """The helper connection could not be used."""


class CaptureTransformError(ProtocolError):
    """A browser-region response cannot be safely transformed or published."""

    def __init__(self, code: str, message: str | None = None) -> None:
        if message is None:
            message = code
            code = "capture-transform"
        super().__init__(message)
        self.code = code


def protocol_limits() -> dict[str, int | float]:
    """The limits a caller needs to construct a bounded request."""
    return {
        "max_message_bytes": MAX_MESSAGE_BYTES,
        "max_pending_requests": MAX_PENDING_REQUESTS,
        "max_query_chars": MAX_QUERY_CHARS,
        "max_selector_chars": MAX_SELECTOR_CHARS,
        "max_page": MAX_PAGE,
        "max_page_size": MAX_PAGE_SIZE,
        "max_elements": MAX_ELEMENTS,
        "max_capture_bytes": MAX_CAPTURE_BYTES,
        "max_capture_pixels": MAX_CAPTURE_PIXELS,
        "max_action_value_chars": MAX_ACTION_VALUE_CHARS,
        "max_wait_ms": MAX_WAIT_MS,
        "max_wait_poll_ms": MAX_WAIT_POLL_MS,
        "default_timeout_seconds": DEFAULT_TIMEOUT,
        "max_timeout_seconds": MAX_TIMEOUT,
    }


def _json_bytes(message: Mapping[str, Any]) -> bytes:
    if not isinstance(message, Mapping):
        raise ProtocolError("protocol messages must be JSON objects")
    try:
        encoded = json.dumps(
            dict(message), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ProtocolError("message is not valid JSON") from exc
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise OversizeMessage(f"message exceeds {MAX_MESSAGE_BYTES} bytes")
    return encoded


def encode_frame(message: Mapping[str, Any]) -> bytes:
    """Encode one native-messaging frame using the host's native byte order."""
    body = _json_bytes(message)
    return _FRAME_HEADER.pack(len(body)) + body


def _decode_body(body: bytes) -> dict[str, Any]:
    try:
        message = json.loads(
            body.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ProtocolError("message body is not valid UTF-8 JSON") from exc
    if not isinstance(message, dict):
        raise ProtocolError("protocol messages must be JSON objects")
    return message


def _read_once(stream: BinaryIO | socket.socket, size: int, deadline: float | None = None) -> bytes:
    if isinstance(stream, socket.socket):
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("frame deadline exceeded")
            stream.settimeout(remaining)
        return stream.recv(size)
    data = stream.read(size)
    return b"" if data is None else data


def _read_exact(
    stream: BinaryIO | socket.socket, size: int, deadline: float | None = None
) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = _read_once(stream, remaining, deadline)
        if not chunk:
            raise ProtocolError("unexpected EOF in a partial frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: BinaryIO | socket.socket) -> dict[str, Any] | None:
    """Read one bounded frame, returning ``None`` only at a clean frame EOF."""
    timeout = stream.gettimeout() if isinstance(stream, socket.socket) else None
    deadline = time.monotonic() + timeout if timeout is not None else None
    header = _read_once(stream, 1, deadline)
    if not header:
        return None
    try:
        header += _read_exact(stream, _FRAME_HEADER.size - 1, deadline)
    except ProtocolError as exc:
        raise ProtocolError("unexpected EOF in a partial frame header") from exc
    (length,) = _FRAME_HEADER.unpack(header)
    if length == 0:
        raise ProtocolError("empty frames are not valid messages")
    if length > MAX_MESSAGE_BYTES:
        raise OversizeMessage(f"message exceeds {MAX_MESSAGE_BYTES} bytes")
    return _decode_body(_read_exact(stream, length, deadline))


def _write_all(stream: BinaryIO | socket.socket, data: bytes) -> None:
    if hasattr(stream, "sendall"):
        stream.sendall(data)  # type: ignore[union-attr]
        return
    offset = 0
    while offset < len(data):
        written = stream.write(data[offset:])
        if written is None:
            written = len(data) - offset
        if written <= 0:
            raise ProtocolError("stream refused a frame write")
        offset += written
    flush = getattr(stream, "flush", None)
    if flush is not None:
        flush()


def write_frame(stream: BinaryIO | socket.socket, message: Mapping[str, Any]) -> None:
    """Write one complete bounded frame to a native or Unix stream."""
    _write_all(stream, encode_frame(message))


class FrameDecoder:
    """Incrementally decode bounded frames without retaining an open-ended body."""

    def __init__(self, max_bytes: int = MAX_MESSAGE_BYTES) -> None:
        if max_bytes <= 0 or max_bytes > MAX_MESSAGE_BYTES:
            raise ValueError("max_bytes is outside the protocol budget")
        self.max_bytes = max_bytes
        self._buffer = bytearray()

    def feed(self, chunk: bytes | bytearray | memoryview) -> list[dict[str, Any]]:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("frame input must be bytes")
        if len(chunk) > MAX_RECV_CHUNK_BYTES:
            raise OversizeMessage("input chunk exceeds the receive budget")
        self._buffer.extend(chunk)
        messages: list[dict[str, Any]] = []
        while len(self._buffer) >= _FRAME_HEADER.size:
            (length,) = _FRAME_HEADER.unpack(self._buffer[: _FRAME_HEADER.size])
            if length == 0:
                raise ProtocolError("empty frames are not valid messages")
            if length > self.max_bytes:
                raise OversizeMessage(f"message exceeds {self.max_bytes} bytes")
            end = _FRAME_HEADER.size + length
            if len(self._buffer) < end:
                break
            body = bytes(self._buffer[_FRAME_HEADER.size : end])
            del self._buffer[:end]
            messages.append(_decode_body(body))
        if len(self._buffer) > self.max_bytes + _FRAME_HEADER.size:
            raise ProtocolError("frame decoder retained more than one bounded frame")
        return messages

    def finish(self) -> None:
        if self._buffer:
            raise ProtocolError("unexpected EOF in a partial frame")


def _bounded_string(value: Any, name: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value) or len(value) > maximum:
        qualifier = "non-empty " if not empty else ""
        raise ProtocolError(f"{name} must be a {qualifier}string of at most {maximum} characters")
    return value


def _bounded_int(value: Any, name: str, maximum: int, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise ProtocolError(f"{name} must be an integer from {minimum} through {maximum}")
    return value


def _bounded_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ProtocolError(f"{name} must be a boolean")
    return value


def _bounded_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolError(f"{name} must be a finite number")
    return float(value)


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolError(f"{name} must be an object")
    return dict(value)


def _exact_fields(message: Mapping[str, Any], required: set[str], allowed: set[str]) -> None:
    keys = set(message)
    missing = required - keys
    if missing:
        raise ProtocolError(f"message is missing {sorted(missing)[0]}")
    unknown = keys - allowed
    if unknown:
        raise ProtocolError(f"unsupported message field {sorted(unknown)[0]}")


def _validate_envelope(message: Mapping[str, Any], message_type: str) -> None:
    if (
        message.get("protocol") != PROTOCOL
        or type(message.get("version")) is not int
        or message["version"] != VERSION
    ):
        raise ProtocolError("unsupported protocol version")
    if message.get("type") != message_type:
        raise ProtocolError(f"expected {message_type} message")


def _capabilities(value: Any, default: tuple[str, ...] = ("read",)) -> list[str]:
    if value is None:
        return list(default)
    if (
        not isinstance(value, list)
        or not value
        or len(value) > len(CAPABILITIES)
        or any(not isinstance(item, str) or item not in CAPABILITIES for item in value)
        or len(set(value)) != len(value)
        or "read" not in value
    ):
        raise ProtocolError("capabilities must include read and contain only read and interact")
    return list(value)


def _bounded_signed_int(value: Any, name: str, maximum: int) -> int:
    return _bounded_int(value, name, maximum, minimum=-maximum)


def _selector(value: Any) -> str | None:
    if value is None:
        return None
    return _bounded_string(value, "selector", MAX_SELECTOR_CHARS)


def _ref(value: Any) -> str:
    return _bounded_string(value, "ref", 64)


def _capture_region(value: Any) -> dict[str, Any]:
    region = _object(value, "capture region")
    fields = {"x", "y", "width", "height", "space"}
    _exact_fields(region, fields, fields)
    result = {
        "x": _bounded_number(region["x"], "capture region x"),
        "y": _bounded_number(region["y"], "capture region y"),
        "width": _bounded_number(region["width"], "capture region width"),
        "height": _bounded_number(region["height"], "capture region height"),
        "space": _bounded_string(region["space"], "capture region space", 32),
    }
    if result["width"] <= 0 or result["height"] <= 0:
        raise ProtocolError("capture region width and height must be positive")
    if result["space"] != "css-viewport":
        raise ProtocolError("capture region space must be css-viewport")
    return result


def capture_payload(region: Mapping[str, Any] | None, full_page: bool) -> dict[str, Any]:
    if region is not None and full_page:
        raise ValueError("a capture takes a region or the full page, not both")
    if full_page:
        return {"full_page": True}
    return {} if region is None else {"region": dict(region)}


def validate_payload(operation: str, payload: Any) -> dict[str, Any]:
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise ProtocolError("operation is not a supported passive read or browser action")
    payload_object = _object({} if payload is None else payload, "payload")
    if operation == "capture":
        _exact_fields(payload_object, set(), set(_CAPTURE_FIELDS))
        if "full_page" in payload_object:
            if "region" in payload_object:
                raise ProtocolError("capture takes a region or the full page, not both")
            if payload_object["full_page"] is not True:
                raise ProtocolError("capture full_page must be true when present")
            return {"full_page": True}
        return (
            {"region": _capture_region(payload_object["region"])}
            if "region" in payload_object
            else {}
        )
    if operation == "reload":
        _exact_fields(payload_object, set(), set())
        return {}
    if operation in {"click", "focus", "hover", "trusted-click", "trusted-hover"}:
        _exact_fields(payload_object, {"ref"}, {"ref"})
        return {"ref": _ref(payload_object["ref"])}
    if operation == "trusted-type":
        _exact_fields(payload_object, {"ref", "text"}, {"ref", "text"})
        return {
            "ref": _ref(payload_object["ref"]),
            "text": _bounded_string(payload_object["text"], "text", MAX_ACTION_VALUE_CHARS),
        }
    if operation == "trusted-key":
        _exact_fields(payload_object, {"key"}, {"key"})
        key = _bounded_string(payload_object["key"], "key", 16)
        if key not in TRUSTED_KEYS:
            raise ProtocolError("key is not a supported named key")
        return {"key": key}
    if operation == "trusted-scroll":
        _exact_fields(payload_object, {"dx", "dy"}, {"ref", "dx", "dy"})
        return {
            **({"ref": _ref(payload_object["ref"])} if "ref" in payload_object else {}),
            "dx": _bounded_signed_int(payload_object["dx"], "dx", 10000),
            "dy": _bounded_signed_int(payload_object["dy"], "dy", 10000),
        }
    if operation == "select":
        _exact_fields(payload_object, {"ref"}, {"ref", "value", "label"})
        has_value = "value" in payload_object
        has_label = "label" in payload_object
        if has_value == has_label:
            raise ProtocolError("select requires exactly one of value or label")
        option_field = "value" if has_value else "label"
        return {
            "ref": _ref(payload_object["ref"]),
            option_field: _bounded_string(
                payload_object[option_field], option_field, MAX_ACTION_VALUE_CHARS, empty=True
            ),
        }
    if operation == "fill":
        _exact_fields(payload_object, {"ref", "value"}, {"ref", "value"})
        return {
            "ref": _ref(payload_object["ref"]),
            "value": _bounded_string(
                payload_object["value"], "value", MAX_ACTION_VALUE_CHARS, empty=True
            ),
        }
    if operation == "scroll":
        _exact_fields(payload_object, {"target", "dx", "dy"}, {"target", "ref", "dx", "dy"})
        target = _bounded_string(payload_object["target"], "target", 8)
        if target not in {"document", "element"}:
            raise ProtocolError("target must be document or element")
        if target == "element" and "ref" not in payload_object:
            raise ProtocolError("element scroll requires ref")
        if target == "document" and "ref" in payload_object:
            raise ProtocolError("document scroll does not accept ref")
        return {
            "target": target,
            **({"ref": _ref(payload_object["ref"])} if target == "element" else {}),
            "dx": _bounded_signed_int(payload_object.get("dx", 0), "dx", 10000),
            "dy": _bounded_signed_int(payload_object.get("dy", 0), "dy", 10000),
        }
    if operation in {"console", "network"}:
        _exact_fields(payload_object, set(), {"limit"})
        return {
            "limit": _bounded_int(
                payload_object.get("limit", MAX_DEBUG_ENTRIES),
                "limit",
                MAX_DEBUG_ENTRIES,
                minimum=1,
            )
        }
    if operation == "ax-query":
        _exact_fields(payload_object, set(), {"role", "name", "include_values", "limit"})
        role = payload_object.get("role")
        name = payload_object.get("name")
        return {
            "role": None if role is None else _bounded_string(role, "role", MAX_ROLE_CHARS),
            "name": None if name is None else _bounded_string(name, "name", MAX_QUERY_CHARS),
            "include_values": _bounded_bool(
                payload_object.get("include_values", False), "include_values"
            ),
            "limit": _bounded_int(
                payload_object.get("limit", MAX_ELEMENTS), "limit", MAX_ELEMENTS, minimum=1
            ),
        }
    if operation == "wait":
        allowed = {"ref", "selector", "state", "text", "value", "timeout_ms", "poll_ms"}
        _exact_fields(payload_object, {"state"}, allowed)
        has_ref = "ref" in payload_object
        selector = _selector(payload_object.get("selector"))
        if has_ref == bool(selector):
            raise ProtocolError("wait requires exactly one of ref or selector")
        state = _bounded_string(payload_object["state"], "state", 16)
        if state not in {
            "present",
            "visible",
            "enabled",
            "text",
            "value",
            "playing",
            "paused",
            "ended",
        }:
            raise ProtocolError("wait state is unsupported")
        if state in {"text", "value"} and state not in payload_object:
            raise ProtocolError(f"{state} wait requires {state}")
        if state in {"playing", "paused", "ended"} and not has_ref:
            raise ProtocolError("media waits require ref")
        result: dict[str, Any] = (
            {"ref": _ref(payload_object["ref"])} if has_ref else {"selector": selector}
        )
        result["state"] = state
        if "text" in payload_object:
            result["text"] = _bounded_string(payload_object["text"], "text", 512, empty=True)
        if "value" in payload_object:
            result["value"] = _bounded_string(payload_object["value"], "value", 512, empty=True)
        result["timeout_ms"] = _bounded_int(
            payload_object.get("timeout_ms", MAX_WAIT_MS), "timeout_ms", MAX_WAIT_MS, minimum=1
        )
        result["poll_ms"] = _bounded_int(
            payload_object.get("poll_ms", 50), "poll_ms", MAX_WAIT_POLL_MS, minimum=10
        )
        return result

    allowed = _OBSERVE_FIELDS if operation == "observe" else _QUERY_FIELDS
    unknown = set(payload_object) - allowed
    if unknown:
        raise ProtocolError(f"unsupported {operation} field {sorted(unknown)[0]}")
    include_values = payload_object.get("include_values", False)
    _bounded_bool(include_values, "include_values")
    include_details = payload_object.get("include_details", False)
    _bounded_bool(include_details, "include_details")
    selector = _selector(payload_object.get("selector"))
    if operation == "observe":
        return {
            "include_values": include_values,
            "include_details": include_details,
            "selector": selector,
            "limit": _bounded_int(
                payload_object.get("limit", MAX_ELEMENTS), "limit", MAX_ELEMENTS, minimum=1
            ),
        }
    search = payload_object.get("search", "")
    _bounded_string(search, "search", MAX_QUERY_CHARS, empty=True)
    role = payload_object.get("role")
    if role is not None:
        _bounded_string(role, "role", MAX_ROLE_CHARS)
    visible = payload_object.get("visible")
    if visible is not None:
        _bounded_bool(visible, "visible")
    return {
        "search": search,
        "role": role,
        "visible": visible,
        "page": _bounded_int(payload_object.get("page", 0), "page", MAX_PAGE),
        "page_size": _bounded_int(
            payload_object.get("page_size", MAX_PAGE_SIZE), "page_size", MAX_PAGE_SIZE, minimum=1
        ),
        "include_values": include_values,
        "include_details": include_details,
        "selector": selector,
    }


def _capture_viewport(viewport: Any) -> tuple[float, float]:
    try:
        value = _object(viewport, "capture viewport")
        width = _bounded_number(value.get("width"), "capture viewport width")
        height = _bounded_number(value.get("height"), "capture viewport height")
    except ProtocolError as exc:
        raise CaptureTransformError(
            "capture-scale-mismatch", "capture viewport geometry is unsupported"
        ) from exc
    if width <= 0 or height <= 0 or value.get("space") != "css-viewport":
        raise CaptureTransformError(
            "capture-scale-mismatch", "capture viewport geometry is unsupported"
        )
    device_pixel_ratio = value.get("device_pixel_ratio")
    if device_pixel_ratio is not None:
        try:
            ratio = _bounded_number(device_pixel_ratio, "capture device pixel ratio")
        except ProtocolError as exc:
            raise CaptureTransformError(
                "capture-scale-mismatch", "capture device pixel ratio is unsupported"
            ) from exc
        if ratio <= 0:
            raise CaptureTransformError(
                "capture-scale-mismatch", "capture device pixel ratio is unsupported"
            )
    return width, height


def _capture_scale(viewport: Any, width: int, height: int) -> tuple[float, float]:
    viewport_width, viewport_height = _capture_viewport(viewport)
    scale_x = width / viewport_width
    scale_y = height / viewport_height
    tolerance = 1e-6 * max(1.0, abs(scale_x), abs(scale_y))
    if (
        not math.isfinite(scale_x)
        or not math.isfinite(scale_y)
        or scale_x <= 0
        or scale_y <= 0
        or abs(scale_x - scale_y) > tolerance
    ):
        raise CaptureTransformError(
            "capture-scale-mismatch", "capture image and CSS viewport scales do not match"
        )
    return scale_x, scale_y


def _capture_pixel_box(
    region: Mapping[str, Any],
    viewport: Any,
    width: int,
    height: int,
    scale_x: float,
    scale_y: float,
) -> dict[str, int]:
    viewport_width, viewport_height = _capture_viewport(viewport)
    right_css = region["x"] + region["width"]
    bottom_css = region["y"] + region["height"]
    epsilon = 1e-9 * max(1.0, viewport_width, viewport_height)
    if (
        region["x"] < -epsilon
        or region["y"] < -epsilon
        or right_css > viewport_width + epsilon
        or bottom_css > viewport_height + epsilon
    ):
        raise CaptureTransformError(
            "unsupported-geometry", "capture region is outside the CSS viewport"
        )
    left = max(0, math.floor(region["x"] * scale_x + 1e-9))
    top = max(0, math.floor(region["y"] * scale_y + 1e-9))
    right = min(width, math.ceil(right_css * scale_x - 1e-9))
    bottom = min(height, math.ceil(bottom_css * scale_y - 1e-9))
    if left >= right or top >= bottom:
        raise CaptureTransformError(
            "unsupported-geometry", "capture region does not cover an image pixel"
        )
    pixels = (right - left) * (bottom - top)
    if pixels > MAX_CAPTURE_PIXELS:
        raise CaptureTransformError(
            "capture-budget", "cropped browser capture exceeds the pixel budget"
        )
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _capture_returned_coverage(
    box: Mapping[str, int], scale_x: float, scale_y: float
) -> dict[str, float | str]:
    return {
        "x": box["x"] / scale_x,
        "y": box["y"] / scale_y,
        "width": box["width"] / scale_x,
        "height": box["height"] / scale_y,
        "space": "css-viewport",
    }


def normalize_capture_response(
    response: Mapping[str, Any], request: Mapping[str, Any]
) -> dict[str, Any]:
    """Crop one validated browser PNG while retaining its source identity."""
    if request.get("operation") != "capture" or not response.get("ok"):
        return dict(response)
    payload = _object(request.get("payload"), "capture request payload")
    if "region" not in payload:
        return dict(response)
    region = _capture_region(payload["region"])
    body = _object(response.get("result"), "capture result")
    if body.get("document_id") != request.get("document_id"):
        raise CaptureTransformError("stale-document", "browser capture document identity changed")
    if body.get("tab_id") != request.get("tab_id"):
        raise CaptureTransformError(
            "capture-identity-mismatch", "browser capture tab identity changed"
        )
    if body.get("generation") != request.get("generation"):
        raise CaptureTransformError(
            "stale-generation", "browser capture generation changed before cropping"
        )
    existing_coverage = body.get("coverage")
    if isinstance(existing_coverage, Mapping):
        if existing_coverage.get("requested") == region and "source_pixels" in body:
            return {**response, "result": body}
        raise CaptureTransformError(
            "capture-identity-mismatch", "browser capture coverage did not match its request"
        )
    encoded = body.get("png_base64")
    if not isinstance(encoded, str):
        raise CaptureTransformError("capture-format", "browser capture did not contain PNG bytes")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise CaptureTransformError("capture-format", "browser capture PNG is invalid") from exc
    if len(data) > MAX_CAPTURE_BYTES:
        raise CaptureTransformError("capture-budget", "browser capture exceeds the byte budget")
    try:
        source_width, source_height, rgba = png.decode(data)
    except ValueError as exc:
        raise CaptureTransformError("capture-format", "browser capture PNG is invalid") from exc
    if source_width * source_height > MAX_CAPTURE_PIXELS:
        raise CaptureTransformError("capture-budget", "browser capture exceeds the pixel budget")
    source_pixels = {
        "width": source_width,
        "height": source_height,
        "space": "image-pixels",
    }
    if body.get("pixels") != source_pixels:
        raise CaptureTransformError(
            "capture-format", "browser capture dimensions do not match metadata"
        )
    scale_x, scale_y = _capture_scale(body.get("viewport"), source_width, source_height)
    box = _capture_pixel_box(
        region,
        body["viewport"],
        source_width,
        source_height,
        scale_x,
        scale_y,
    )
    row_bytes = source_width * 4
    left = box["x"] * 4
    right = (box["x"] + box["width"]) * 4
    cropped_rgba = b"".join(
        rgba[row * row_bytes + left : row * row_bytes + right]
        for row in range(box["y"], box["y"] + box["height"])
    )
    cropped = png.encode(box["width"], box["height"], cropped_rgba)
    if len(cropped) > MAX_CAPTURE_BYTES:
        raise CaptureTransformError(
            "capture-budget", "cropped browser capture exceeds the byte budget"
        )
    returned = _capture_returned_coverage(box, scale_x, scale_y)
    transformed = {
        **body,
        "png_base64": base64.b64encode(cropped).decode("ascii"),
        "bytes": len(cropped),
        "source_bytes": len(data),
        "source_pixels": source_pixels,
        "pixels": {"width": box["width"], "height": box["height"], "space": "image-pixels"},
        "capture_scale": {
            "x": scale_x,
            "y": scale_y,
            "space": "css-viewport-to-image-pixels",
        },
        "coverage": {
            "requested": region,
            "returned": returned,
            "image_pixels": {**box, "space": "image-pixels"},
        },
    }
    return {**response, "result": transformed}


def _revoked_by_reload(body: Mapping[str, Any], request: Mapping[str, Any]) -> bool:
    """A reload receipt names the grant it ended and confers none on the new document."""
    revoked = body.get("revoked_grant")
    return (
        isinstance(revoked, Mapping)
        and all(
            revoked.get(field) == request[field] for field in ("session", "tab_id", "document_id")
        )
        and body.get("new_document_grant") == "none"
    )


def validate_request(message: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a versioned browser read or action request."""
    request = _object(message, "request")
    allowed = {
        "protocol",
        "version",
        "type",
        "id",
        "operation",
        "session",
        "tab_id",
        "document_id",
        "generation",
        "capabilities",
        "payload",
    }
    _exact_fields(
        request,
        {
            "protocol",
            "version",
            "type",
            "id",
            "operation",
            "session",
            "tab_id",
            "document_id",
            "generation",
            "payload",
        },
        allowed,
    )
    _validate_envelope(request, "request")
    operation = request["operation"]
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise ProtocolError("operation is not a supported passive read or browser action")
    generation = request["generation"]
    if generation is not None:
        _bounded_int(generation, "generation", 2**53 - 1)
    if operation in ACTION_OPERATIONS and generation is None:
        raise ProtocolError(
            "browser actions are not passive read operations without an observed generation"
        )
    return {
        "protocol": PROTOCOL,
        "version": VERSION,
        "type": "request",
        "id": _bounded_string(request["id"], "id", MAX_REQUEST_ID_CHARS),
        "operation": operation,
        "session": _bounded_string(request["session"], "session", MAX_SESSION_CHARS),
        "tab_id": _bounded_int(request["tab_id"], "tab_id", 2**31 - 1, minimum=1),
        "document_id": _bounded_string(
            request["document_id"], "document_id", MAX_DOCUMENT_ID_CHARS
        ),
        "generation": generation,
        "capabilities": _capabilities(request.get("capabilities")),
        "payload": validate_payload(operation, request["payload"]),
    }


def make_request(
    operation: str,
    *,
    session: str,
    tab_id: int,
    document_id: str,
    generation: int | None = None,
    payload: Mapping[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build a request after applying the same allowlist used by the host."""
    return validate_request(
        {
            "protocol": PROTOCOL,
            "version": VERSION,
            "type": "request",
            "id": request_id or uuid.uuid4().hex,
            "operation": operation,
            "session": session,
            "tab_id": tab_id,
            "document_id": document_id,
            "generation": generation,
            "payload": dict(payload or {}),
        }
    )


def validate_control(message: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a session control that binds or releases a tab without a browser click."""
    control = _object(message, "control")
    _exact_fields(
        control,
        {"protocol", "version", "type", "id", "operation", "tab_id"},
        {
            "protocol",
            "version",
            "type",
            "id",
            "operation",
            "tab_id",
            "capabilities",
            "cursor",
            "include_titles",
        },
    )
    _validate_envelope(control, "control")
    operation = control["operation"]
    if not isinstance(operation, str) or operation not in CONTROL_OPERATIONS:
        raise ProtocolError("operation is not a supported session control")
    tab_id = control["tab_id"]
    result: dict[str, Any] = {
        "protocol": PROTOCOL,
        "version": VERSION,
        "type": "control",
        "id": _bounded_string(control["id"], "id", MAX_REQUEST_ID_CHARS),
        "operation": operation,
        "tab_id": None if tab_id is None else _bounded_int(tab_id, "tab_id", 2**31 - 1, minimum=1),
    }
    if operation != "tabs" and "include_titles" in control:
        raise ProtocolError(f"{operation} takes no include_titles")
    if operation == "tabs":
        if result["tab_id"] is not None:
            raise ProtocolError("tabs lists every tab and takes no tab_id")
        result["include_titles"] = _bounded_bool(
            control.get("include_titles", False), "include_titles"
        )
    if operation != "grant":
        if "capabilities" in control or "cursor" in control:
            raise ProtocolError(f"{operation} takes no capabilities or cursor")
        return result
    result["capabilities"] = _capabilities(control.get("capabilities"))
    if "cursor" in control:
        result["cursor"] = _bounded_bool(control["cursor"], "cursor")
    return result


def make_control(
    operation: str,
    *,
    tab_id: int | None = None,
    capabilities: list[str] | None = None,
    cursor: bool | None = None,
    include_titles: bool | None = None,
) -> dict[str, Any]:
    """Build a session control after applying the same allowlist used by the host."""
    control: dict[str, Any] = {
        "protocol": PROTOCOL,
        "version": VERSION,
        "type": "control",
        "id": uuid.uuid4().hex,
        "operation": operation,
        "tab_id": tab_id,
    }
    if capabilities is not None:
        control["capabilities"] = capabilities
    if cursor is not None:
        control["cursor"] = cursor
    if include_titles is not None:
        control["include_titles"] = include_titles
    return validate_control(control)


def _validate_grant(grant: Mapping[str, Any]) -> dict[str, Any]:
    value = _object(grant, "grant")
    allowed = {
        "session",
        "tab_id",
        "window_id",
        "document_id",
        "generation",
        "capabilities",
        "active",
        "hidden",
        "debugger",
    }
    _exact_fields(
        value,
        {"session", "tab_id", "window_id", "document_id", "generation", "active", "hidden"},
        allowed,
    )
    session = _bounded_string(value["session"], "grant session", MAX_SESSION_CHARS)
    tab_id = _bounded_int(value["tab_id"], "grant tab_id", 2**31 - 1, minimum=1)
    window_id = _bounded_int(value["window_id"], "grant window_id", 2**31 - 1, minimum=1)
    document_id = _bounded_string(value["document_id"], "grant document_id", MAX_DOCUMENT_ID_CHARS)
    generation = _bounded_int(value["generation"], "grant generation", 2**53 - 1)
    capabilities = _capabilities(value.get("capabilities"))
    active = _bounded_bool(value["active"], "grant active")
    hidden = _bounded_bool(value["hidden"], "grant hidden")
    return {
        "session": session,
        "tab_id": tab_id,
        "window_id": window_id,
        "document_id": document_id,
        "generation": generation,
        "capabilities": capabilities,
        "active": active,
        "hidden": hidden,
        **(
            {"debugger": _bounded_bool(value["debugger"], "grant debugger")}
            if "debugger" in value
            else {}
        ),
    }


def _validate_grants(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_GRANTS:
        raise ProtocolError(f"grants must contain at most {MAX_GRANTS} entries")
    grants = [_validate_grant(grant) for grant in value]
    keys = [(grant["session"], grant["tab_id"]) for grant in grants]
    if len(keys) != len(set(keys)):
        raise ProtocolError("grants contain a duplicate session and tab")
    return grants


def _validate_response(message: Mapping[str, Any], *, identity: bool = True) -> dict[str, Any]:
    response = _object(message, "response")
    allowed = {
        "protocol",
        "version",
        "type",
        "id",
        "session",
        "tab_id",
        "document_id",
        "ok",
        "result",
        "error",
    }
    _exact_fields(response, {"protocol", "version", "type", "id", "ok"}, allowed)
    _validate_envelope(response, "response")
    _bounded_string(response["id"], "response id", MAX_REQUEST_ID_CHARS)
    ok = _bounded_bool(response["ok"], "response ok")
    if "session" in response:
        _bounded_string(response["session"], "response session", MAX_SESSION_CHARS)
    if "tab_id" in response:
        _bounded_int(response["tab_id"], "response tab_id", 2**31 - 1, minimum=1)
    if "document_id" in response:
        _bounded_string(response["document_id"], "response document_id", MAX_DOCUMENT_ID_CHARS)
    if ok:
        if identity:
            _exact_fields(response, {"session", "tab_id", "document_id"}, allowed)
        if "result" not in response or not isinstance(response["result"], Mapping):
            raise ProtocolError("successful responses require a result object")
    else:
        error = _object(response.get("error"), "response error")
        if set(error) - {"code", "state", "message"}:
            raise ProtocolError("response error contains an unsupported field")
        _bounded_string(error.get("code"), "error code", MAX_ERROR_CODE_CHARS)
        _bounded_string(error.get("state", "unsupported"), "error state", MAX_ERROR_CODE_CHARS)
        _bounded_string(
            error.get("message", "request refused"), "error message", MAX_ERROR_MESSAGE_CHARS
        )
    return response


def _error_response(request_id: str | None, code: str, message: str) -> dict[str, Any]:
    response: dict[str, Any] = {
        "protocol": PROTOCOL,
        "version": VERSION,
        "type": "response",
        "ok": False,
        "error": {"code": code, "state": "unsupported", "message": message},
    }
    if request_id is not None:
        response["id"] = request_id
    return response


def _safe_request_id(message: Mapping[str, Any]) -> str | None:
    value = message.get("id")
    if isinstance(value, str) and value and len(value) <= MAX_REQUEST_ID_CHARS:
        return value
    return None


def _helper_socket_directory(*, create: bool = False) -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise BrowserReadError("XDG_RUNTIME_DIR is required for browser read transport")
    root = Path(runtime)
    if not root.is_absolute():
        raise BrowserReadError("XDG_RUNTIME_DIR must be an absolute path")
    directory = root / "cu" / "browser"
    if create:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    return directory


def _socket_path(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path)
    directory = _helper_socket_directory()
    if not candidate.is_absolute() or candidate.parent != directory:
        raise StaleHelper("helper socket is outside the browser runtime directory")
    if not re.fullmatch(_SOCKET_NAME, candidate.name):
        raise StaleHelper("helper socket name is not recognized")
    if len(str(candidate)) > MAX_SOCKET_PATH_CHARS:
        raise StaleHelper("helper socket path is too long")
    return candidate


def _owned_socket_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        return False
    return stat.S_IMODE(info.st_mode) == 0o600


def discover_socket_paths() -> list[Path]:
    """List candidate helper sockets without removing stale entries."""
    directory = _helper_socket_directory()
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return []
    paths = [
        entry
        for entry in entries
        if re.fullmatch(_SOCKET_NAME, entry.name) and _owned_socket_file(entry)
    ]
    return sorted(paths, key=lambda path: path.name)


class HelperSocket:
    """A helper listener whose cleanup is limited to its own inode and path."""

    def __init__(self, listener: socket.socket, path: Path, identity: tuple[int, int]) -> None:
        self.listener = listener
        self.path = path
        self._identity = identity
        self._closed = False

    @classmethod
    def create(cls) -> HelperSocket:
        directory = _helper_socket_directory(create=True)
        available = 107 - len(os.fsencode(str(directory))) - 1
        hex_chars = min(32, available - len(SOCKET_PREFIX) - len(SOCKET_SUFFIX))
        if hex_chars < 8:
            raise BrowserReadError("XDG_RUNTIME_DIR leaves no usable Unix socket path")
        for _ in range(8):
            token = secrets.token_hex((hex_chars + 1) // 2)[:hex_chars]
            path = directory / f"{SOCKET_PREFIX}{token}{SOCKET_SUFFIX}"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            bound = False
            try:
                listener.bind(str(path))
                bound = True
                listener.listen(MAX_CLIENTS)
                os.chmod(path, 0o600)
                info = path.stat()
                return cls(listener, path, (info.st_dev, info.st_ino))
            except OSError as exc:
                listener.close()
                if bound:
                    path.unlink(missing_ok=True)
                if exc.errno != errno.EADDRINUSE:
                    raise
        raise HelperUnavailable("could not allocate a unique helper socket")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.listener.close()
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return
        if (info.st_dev, info.st_ino) == self._identity:
            with suppress(FileNotFoundError):
                self.path.unlink()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()


class BrowserReadClient:
    """A bounded synchronous client for one browser-owned helper socket."""

    def __init__(self, connection: socket.socket, path: Path, metadata: dict[str, Any]) -> None:
        self._connection = connection
        self.socket_path = path
        self.metadata = metadata
        self._lock = threading.Lock()
        self._closed = False
        self._timeout = DEFAULT_TIMEOUT

    @classmethod
    def connect(
        cls, path: str | os.PathLike[str], timeout: float = DEFAULT_TIMEOUT
    ) -> BrowserReadClient:
        deadline = _deadline(timeout)
        candidate = _socket_path(path)
        if not _owned_socket_file(candidate):
            raise StaleHelper(f"helper socket is stale: {candidate}")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        try:
            connection.connect(str(candidate))
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            hello = read_frame(connection)
            if hello is None:
                raise StaleHelper("helper closed before its hello message")
            metadata = _validate_local_hello(hello)
        except TimeoutError as exc:
            connection.close()
            raise BrowserReadTimeout("helper hello exceeded the client deadline") from exc
        except (OSError, ProtocolError) as exc:
            connection.close()
            if isinstance(exc, ProtocolError):
                raise
            raise StaleHelper(f"helper socket is not serving: {candidate}") from exc
        client = cls(connection, candidate, metadata)
        client._timeout = timeout
        connection.settimeout(timeout)
        return client

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with suppress(OSError):
            self._connection.shutdown(socket.SHUT_RDWR)
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _event(self, message: Mapping[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "hello":
            self.metadata = _validate_local_hello(message)
        elif message_type == "grant":
            grant = _validate_grant(message.get("grant"))
            grants = [
                item
                for item in self.metadata["grants"]
                if (item["session"], item["tab_id"]) != (grant["session"], grant["tab_id"])
            ]
            self.metadata = {**self.metadata, "grants": [*grants, grant]}
        elif message_type == "revoke":
            session = _bounded_string(message.get("session"), "revoke session", MAX_SESSION_CHARS)
            tab_id = _bounded_int(message.get("tab_id"), "revoke tab_id", 2**31 - 1, minimum=1)
            grants = [
                item
                for item in self.metadata["grants"]
                if (item["session"], item["tab_id"]) != (session, tab_id)
            ]
            self.metadata = {**self.metadata, "grants": grants}
        else:
            raise ProtocolError("unexpected helper event")

    def request(
        self,
        operation: str,
        *,
        session: str,
        tab_id: int,
        document_id: str,
        generation: int | None = None,
        payload: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        request = make_request(
            operation,
            session=session,
            tab_id=tab_id,
            document_id=document_id,
            generation=generation,
            payload=payload,
        )
        wait = self._timeout if timeout is None else timeout
        deadline = _deadline(wait)
        if not self._lock.acquire(timeout=wait):
            raise BrowserReadTimeout("another request holds this client past the deadline")
        try:
            if self._closed:
                raise HelperUnavailable("browser read client is closed")
            action_sent = False
            try:
                self._connection.settimeout(max(0.001, deadline - time.monotonic()))
                action_sent = request["operation"] in ACTION_OPERATIONS - {"capture"}
                write_frame(self._connection, request)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BrowserReadTimeout("browser read request exceeded its deadline")
                    self._connection.settimeout(remaining)
                    response = read_frame(self._connection)
                    if response is None:
                        raise StaleHelper("browser read helper closed the connection")
                    if response.get("type") in {"hello", "grant", "revoke"}:
                        self._event(response)
                        continue
                    if response.get("type") != "response":
                        raise ProtocolError("helper returned an unsupported message")
                    if response.get("id") != request["id"]:
                        raise MismatchedResponse("helper response id did not match the request")
                    validated = _validate_response(response)
                    if validated["ok"] and any(
                        validated[field] != request[field]
                        for field in ("session", "tab_id", "document_id")
                    ):
                        raise MismatchedResponse(
                            "helper response identity did not match the request"
                        )
                    if action_sent and validated["ok"]:
                        body = validated["result"]
                        result_generation = _bounded_int(
                            body.get("generation"), "generation", 2**53 - 1
                        )
                        if (
                            body.get("kind") != "action"
                            or body.get("action") != operation
                            or body.get("backend") != _ACTION_BACKENDS.get(operation, "dom")
                            or body.get("document_id") != document_id
                            or body.get("verification") not in {"verified", "unverified"}
                            or result_generation < generation
                            or (operation == "reload" and not _revoked_by_reload(body, request))
                            or (
                                "ref" in request["payload"]
                                and body.get("ref") != request["payload"]["ref"]
                            )
                        ):
                            raise ProtocolError("browser action receipt did not match its request")
                    return validated
            except TimeoutError as exc:
                self.close()
                if action_sent:
                    raise BrowserActionUncertain(
                        "browser action may have been partially applied; response timed out"
                    ) from exc
                raise BrowserReadTimeout("browser read request exceeded its deadline") from exc
            except (BrokenPipeError, ConnectionError, OSError) as exc:
                self.close()
                if action_sent:
                    raise BrowserActionUncertain(
                        "browser action may have been partially applied; helper disconnected"
                    ) from exc
                raise HelperUnavailable("browser read helper is unavailable") from exc
            except (BrowserReadTimeout, MismatchedResponse, ProtocolError, StaleHelper) as exc:
                self.close()
                if action_sent:
                    raise BrowserActionUncertain(
                        "browser action may have been partially applied; response was unusable"
                    ) from exc
                raise
        finally:
            self._lock.release()

    def control(
        self,
        operation: str,
        *,
        tab_id: int | None = None,
        capabilities: list[str] | None = None,
        cursor: bool | None = None,
        include_titles: bool | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send one session control: bind, release, attach, detach, or list tabs."""
        control = make_control(
            operation,
            tab_id=tab_id,
            capabilities=capabilities,
            cursor=cursor,
            include_titles=include_titles,
        )
        wait = self._timeout if timeout is None else timeout
        deadline = _deadline(wait)
        if not self._lock.acquire(timeout=wait):
            raise BrowserReadTimeout("another request holds this client past the deadline")
        try:
            if self._closed:
                raise HelperUnavailable("browser read client is closed")
            try:
                self._connection.settimeout(max(0.001, deadline - time.monotonic()))
                write_frame(self._connection, control)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BrowserReadTimeout("session control exceeded its deadline")
                    self._connection.settimeout(remaining)
                    response = read_frame(self._connection)
                    if response is None:
                        raise StaleHelper("browser read helper closed the connection")
                    if response.get("type") in {"hello", "grant", "revoke"}:
                        self._event(response)
                        continue
                    if response.get("type") != "response":
                        raise ProtocolError("helper returned an unsupported message")
                    if response.get("id") != control["id"]:
                        raise MismatchedResponse("helper response id did not match the control")
                    return _validate_response(response, identity=control["operation"] != "tabs")
            except TimeoutError as exc:
                self.close()
                raise BrowserReadTimeout("session control exceeded its deadline") from exc
            except (BrokenPipeError, ConnectionError, OSError) as exc:
                self.close()
                raise HelperUnavailable("browser read helper is unavailable") from exc
            except (BrowserReadTimeout, MismatchedResponse, ProtocolError, StaleHelper):
                self.close()
                raise
        finally:
            self._lock.release()

    def observe(
        self,
        *,
        session: str,
        tab_id: int,
        document_id: str,
        generation: int | None = None,
        include_values: bool = False,
        limit: int = MAX_ELEMENTS,
        selector: str | None = None,
        include_details: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "observe",
            session=session,
            tab_id=tab_id,
            document_id=document_id,
            generation=generation,
            payload={
                "include_values": include_values,
                "limit": limit,
                "selector": selector,
                "include_details": include_details,
            },
            timeout=timeout,
        )

    def query(
        self,
        *,
        session: str,
        tab_id: int,
        document_id: str,
        search: str = "",
        role: str | None = None,
        visible: bool | None = None,
        page: int = 0,
        page_size: int = MAX_PAGE_SIZE,
        generation: int | None = None,
        include_values: bool = False,
        selector: str | None = None,
        include_details: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "query",
            session=session,
            tab_id=tab_id,
            document_id=document_id,
            generation=generation,
            payload={
                "search": search,
                "role": role,
                "visible": visible,
                "page": page,
                "page_size": page_size,
                "include_values": include_values,
                "selector": selector,
                "include_details": include_details,
            },
            timeout=timeout,
        )

    def action(
        self,
        operation: str,
        *,
        session: str,
        tab_id: int,
        document_id: str,
        generation: int,
        payload: Mapping[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if operation not in ACTION_OPERATIONS - {"capture"}:
            raise ValueError("operation is not a browser action")
        return self.request(
            operation,
            session=session,
            tab_id=tab_id,
            document_id=document_id,
            generation=generation,
            payload=payload,
            timeout=timeout,
        )

    def wait(
        self,
        *,
        session: str,
        tab_id: int,
        document_id: str,
        payload: Mapping[str, Any],
        generation: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "wait",
            session=session,
            tab_id=tab_id,
            document_id=document_id,
            generation=generation,
            payload=payload,
            timeout=timeout,
        )

    def capture(
        self,
        *,
        session: str,
        tab_id: int,
        document_id: str,
        generation: int,
        region: Mapping[str, Any] | None = None,
        full_page: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "capture",
            session=session,
            tab_id=tab_id,
            document_id=document_id,
            generation=generation,
            payload=capture_payload(region, full_page),
            timeout=timeout,
        )


def _validate_timeout(timeout: float) -> float:
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        raise ValueError("timeout must be a finite positive number")
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 0 and {MAX_TIMEOUT} seconds")
    return float(timeout)


def _deadline(timeout: float) -> float:
    return time.monotonic() + _validate_timeout(timeout)


def _validate_local_hello(message: Mapping[str, Any]) -> dict[str, Any]:
    value = _object(message, "hello")
    allowed = {"protocol", "version", "type", "helper_id", "socket", "limits", "grants"}
    _exact_fields(value, allowed, allowed)
    _validate_envelope(value, "hello")
    helper_id = _bounded_string(value["helper_id"], "helper_id", MAX_HELPER_ID_CHARS)
    socket_path = _bounded_string(value["socket"], "socket", MAX_SOCKET_PATH_CHARS)
    limits = _object(value["limits"], "limits")
    if limits != protocol_limits():
        raise ProtocolError("helper limits do not match the protocol")
    grants = _validate_grants(value["grants"])
    return {
        "protocol": PROTOCOL,
        "version": VERSION,
        "type": "hello",
        "helper_id": helper_id,
        "socket": socket_path,
        "limits": limits,
        "grants": grants,
    }


def _validate_native_message(message: Mapping[str, Any]) -> dict[str, Any]:
    value = _object(message, "native message")
    message_type = value.get("type")
    if message_type == "hello":
        allowed = {"protocol", "version", "type", "grants"}
        _exact_fields(value, allowed, allowed)
        _validate_envelope(value, "hello")
        return {
            "protocol": PROTOCOL,
            "version": VERSION,
            "type": "hello",
            "grants": _validate_grants(value["grants"]),
        }
    if message_type == "grant":
        allowed = {"protocol", "version", "type", "grant"}
        _exact_fields(value, allowed, allowed)
        _validate_envelope(value, "grant")
        return {
            "protocol": PROTOCOL,
            "version": VERSION,
            "type": "grant",
            "grant": _validate_grant(value["grant"]),
        }
    if message_type == "revoke":
        allowed = {"protocol", "version", "type", "session", "tab_id", "document_id", "reason"}
        _exact_fields(value, {"protocol", "version", "type", "session", "tab_id"}, allowed)
        _validate_envelope(value, "revoke")
        result = {
            "protocol": PROTOCOL,
            "version": VERSION,
            "type": "revoke",
            "session": _bounded_string(value["session"], "revoke session", MAX_SESSION_CHARS),
            "tab_id": _bounded_int(value["tab_id"], "revoke tab_id", 2**31 - 1, minimum=1),
        }
        if "document_id" in value:
            result["document_id"] = _bounded_string(
                value["document_id"], "revoke document_id", MAX_DOCUMENT_ID_CHARS
            )
        if "reason" in value:
            result["reason"] = _bounded_string(
                value["reason"], "revoke reason", MAX_ERROR_CODE_CHARS
            )
        return result
    if message_type == "response":
        return _validate_response(value, identity=False)
    raise ProtocolError("unsupported native message type")


@dataclass
class _Peer:
    connection: socket.socket
    decoder: FrameDecoder


class NativeMessagingHost:
    """The browser-owned stdio helper and its in-memory local broker."""

    def __init__(self, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> None:
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout.buffer
        self.helper: HelperSocket | None = None
        self.grants: dict[tuple[str, int], dict[str, Any]] = {}
        self.peers: list[_Peer] = []
        self.pending: dict[str, tuple[_Peer, dict[str, Any]]] = {}
        self._selector: selectors.BaseSelector | None = None
        self._running = False
        self._native_output = bytearray()

    def _ready(self) -> dict[str, Any]:
        if self.helper is None:
            raise HelperUnavailable("native helper socket has not been created")
        return {
            "protocol": PROTOCOL,
            "version": VERSION,
            "type": "ready",
            "helper_id": self.helper.path.stem,
            "socket": str(self.helper.path),
            "limits": protocol_limits(),
        }

    def _local_hello(self) -> dict[str, Any]:
        if self.helper is None:
            raise HelperUnavailable("native helper socket has not been created")
        return {
            "protocol": PROTOCOL,
            "version": VERSION,
            "type": "hello",
            "helper_id": self.helper.path.stem,
            "socket": str(self.helper.path),
            "limits": protocol_limits(),
            "grants": list(self.grants.values()),
        }

    def _write_local(self, peer: _Peer, message: Mapping[str, Any]) -> None:
        peer.connection.settimeout(0.5)
        try:
            write_frame(peer.connection, message)
        finally:
            peer.connection.setblocking(False)

    def _remove_peer(self, peer: _Peer) -> None:
        if peer in self.peers:
            self.peers.remove(peer)
        if self._selector is not None:
            with suppress(KeyError, ValueError):
                self._selector.unregister(peer.connection)
        for request_id, (owner, request) in list(self.pending.items()):
            if owner is peer:
                del self.pending[request_id]
                if self._running:
                    self._send_native(
                        {
                            "protocol": PROTOCOL,
                            "version": VERSION,
                            "type": "cancel",
                            "id": request_id,
                            "session": request["session"],
                        }
                    )
        peer.connection.close()

    def _broadcast(self, message: Mapping[str, Any]) -> None:
        for peer in list(self.peers):
            try:
                self._write_local(peer, message)
            except OSError:
                self._remove_peer(peer)

    def _send_native(self, message: Mapping[str, Any]) -> None:
        frame = encode_frame(message)
        if len(self._native_output) + len(frame) > MAX_MESSAGE_BYTES + 4:
            raise OversizeMessage("native output queue exceeds the message budget")
        if not self._native_output:
            self._selector.register(self.stdout, selectors.EVENT_WRITE, ("native-output", None))
        self._native_output.extend(frame)

    def _flush_native(self) -> None:
        try:
            written = os.write(self.stdout.fileno(), self._native_output)
        except BlockingIOError:
            return
        del self._native_output[:written]
        if not self._native_output:
            self._selector.unregister(self.stdout)

    def _grant_refusal(self, request: Mapping[str, Any]) -> dict[str, Any] | None:
        grant = self.grants.get((request["session"], request["tab_id"]))
        if grant is None:
            return _error_response(request["id"], "tab-not-granted", "the tab has no active grant")
        if grant["document_id"] != request["document_id"]:
            return _error_response(request["id"], "stale-document", "the document grant is stale")
        if request["operation"] in ACTION_OPERATIONS and "interact" not in grant["capabilities"]:
            return _error_response(
                request["id"], "interaction-not-granted", "the current browser grant is read-only"
            )
        return None

    def _handle_local(self, peer: _Peer, message: Mapping[str, Any]) -> None:
        control = isinstance(message, Mapping) and message.get("type") == "control"
        try:
            request = validate_control(message) if control else validate_request(message)
        except ProtocolError as exc:
            request_id = _safe_request_id(message)
            if request_id is None:
                self._remove_peer(peer)
                return
            try:
                self._write_local(peer, _error_response(request_id, "invalid-request", str(exc)))
            except OSError:
                self._remove_peer(peer)
            return

        request_id = request["id"]
        response = None if control else self._grant_refusal(request)
        if response is None and request_id in self.pending:
            response = _error_response(
                request_id, "duplicate-request-id", "request id is already pending"
            )
        elif response is None and len(self.pending) >= MAX_PENDING_REQUESTS:
            response = _error_response(request_id, "busy", "the helper request budget is full")
        elif response is None:
            self.pending[request_id] = (peer, request)
            try:
                self._send_native(request)
            except OSError:
                del self.pending[request_id]
                self._remove_peer(peer)
            return
        try:
            self._write_local(peer, response)
        except OSError:
            self._remove_peer(peer)

    def _handle_native(self, message: Mapping[str, Any]) -> None:
        value = _validate_native_message(message)
        message_type = value["type"]
        if message_type == "hello":
            self.grants = {(grant["session"], grant["tab_id"]): grant for grant in value["grants"]}
            self._broadcast(self._local_hello())
            return
        if message_type == "grant":
            grant = value["grant"]
            self.grants[(grant["session"], grant["tab_id"])] = grant
            self._broadcast(value)
            return
        if message_type == "revoke":
            key = (value["session"], value["tab_id"])
            self.grants.pop(key, None)
            self._broadcast(value)
            return
        request_id = value["id"]
        pending = self.pending.pop(request_id, None)
        if pending is None:
            return  # The local caller can time out and close before the browser replies.
        peer, request = pending
        # Only a tab listing answers without naming the grant it acts under.
        listing = request.get("type") == "control" and request["operation"] == "tabs"
        if value["ok"] and not listing and not {"session", "tab_id", "document_id"} <= set(value):
            raise ProtocolError("successful responses must name their session, tab and document")
        for field in ("session", "tab_id", "document_id"):
            expected = request.get(field)
            if field in value and expected is not None and value[field] != expected:
                raise MismatchedResponse(f"native response {field} did not match the request")
        if request["operation"] == "capture" and "region" in request["payload"] and value["ok"]:
            try:
                value = normalize_capture_response(value, request)
            except CaptureTransformError as exc:
                value = _error_response(request_id, exc.code, str(exc))
        try:
            self._write_local(peer, value)
        except OSError:
            self._remove_peer(peer)

    def _accept(self, listener: socket.socket) -> None:
        while len(self.peers) < MAX_CLIENTS:
            try:
                connection, _ = listener.accept()
            except BlockingIOError:
                return
            connection.setblocking(False)
            peer = _Peer(connection, FrameDecoder())
            self.peers.append(peer)
            if self._selector is None:
                raise HelperUnavailable("native helper selector is not running")
            self._selector.register(connection, selectors.EVENT_READ, ("local", peer))
            try:
                self._write_local(peer, self._local_hello())
            except OSError:
                self._remove_peer(peer)
        try:
            connection, _ = listener.accept()
        except BlockingIOError:
            return
        connection.close()

    def _read_native(self) -> bool:
        try:
            chunk = os.read(self.stdin.fileno(), MAX_RECV_CHUNK_BYTES)
        except OSError:
            return False
        if not chunk:
            self._native_decoder.finish()
            return False
        messages = self._native_decoder.feed(chunk)
        for message in messages:
            self._handle_native(message)
        return True

    def _read_local(self, peer: _Peer) -> None:
        try:
            chunk = peer.connection.recv(MAX_RECV_CHUNK_BYTES)
            if not chunk:
                self._remove_peer(peer)
                return
            for message in peer.decoder.feed(chunk):
                self._handle_local(peer, message)
        except (OSError, ProtocolError):
            self._remove_peer(peer)

    def run(self) -> int:
        self.helper = HelperSocket.create()
        self._selector = selectors.DefaultSelector()
        self._native_decoder = FrameDecoder()
        self._running = True
        listener = self.helper.listener
        listener.setblocking(False)
        self._selector.register(listener, selectors.EVENT_READ, ("listener", listener))
        self._selector.register(self.stdin, selectors.EVENT_READ, ("native", self.stdin))
        os.set_blocking(self.stdout.fileno(), False)
        # A signal that lands just before the selector blocks would otherwise
        # leave its Python handler waiting for the next unrelated event; the
        # wakeup socket makes every delivered signal an event of its own.
        wakeup_read, wakeup_write = socket.socketpair()
        wakeup_read.setblocking(False)
        wakeup_write.setblocking(False)
        previous_wakeup = signal.set_wakeup_fd(wakeup_write.fileno(), warn_on_full_buffer=False)
        self._selector.register(wakeup_read, selectors.EVENT_READ, ("signal", wakeup_read))
        try:
            self._send_native(self._ready())
            while self._running:
                events = self._selector.select()
                for key, _ in events:
                    kind, value = key.data
                    if kind == "native-output":
                        self._flush_native()
                    elif kind == "listener":
                        self._accept(value)
                    elif kind == "native":
                        if not self._read_native():
                            self._running = False
                            break
                    elif kind == "signal":
                        with suppress(BlockingIOError):
                            value.recv(64)
                    else:
                        self._read_local(value)
        except (BrokenPipeError, ConnectionError, OSError, ProtocolError):
            return 2
        finally:
            signal.set_wakeup_fd(previous_wakeup)
            with suppress(KeyError, ValueError):
                self._selector.unregister(wakeup_read)
            wakeup_read.close()
            wakeup_write.close()
            self._running = False
            for peer in list(self.peers):
                self._remove_peer(peer)
            try:
                self._selector.unregister(self.stdin)
                self._selector.unregister(listener)
            except (KeyError, ValueError):
                pass
            self._selector.close()
            self._selector = None
            self.helper.close()
            self.helper = None
        return 0


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments != ["host"]:
        return 2
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        return NativeMessagingHost().run()
    except (BrowserReadError, OSError):
        return 2


if __name__ == "__main__":
    sys.exit(main())
