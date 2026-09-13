"""Caller-visible native hover: entry, dwell, pixel evidence, and timeout."""

from __future__ import annotations

import json

from cu import actions, capture, checks, cli, frames, observe, png


def _world(monkeypatch, sequence):
    window = {
        "address": "0x1",
        "class": "fixture",
        "title": "hover fixture",
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100},
        "visible": True,
        "hidden": False,
        "focus_order": 0,
    }
    cursor = [{"x": 0.0, "y": 0.0}]
    monitor = {"name": "OUT", "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100}}
    snap = {
        "monitors": [monitor],
        "windows": [window],
        "focused": window,
        "cursor_logical": cursor[0],
        "layer_surfaces": [],
    }
    reference_png = sequence[0]
    with frames.capture_scratch() as scratch:
        scratch.write_bytes(reference_png)
        reference = frames.new(
            source={"type": "window", "name": "fixture"},
            logical_rect=window["logical_rect"],
            image_size={"w": 100, "h": 100},
            scratch=scratch,
            monitor=monitor,
            window={key: window[key] for key in ("address", "class", "title", "logical_rect")},
            profile="generic",
        )
    monkeypatch.setattr(actions.frames, "load", lambda _frame_id: reference)
    monkeypatch.setattr(cli.frames, "load", lambda _frame_id: reference)
    monkeypatch.setattr(observe, "snapshot", lambda: {**snap, "cursor_logical": cursor[0]})
    monkeypatch.setattr(observe, "focused", lambda: window)
    monkeypatch.setattr(observe, "windows", lambda _monitors: [window])
    monkeypatch.setattr(observe, "monitors", lambda: [monitor])
    monkeypatch.setattr(observe, "cursor", lambda: cursor[0])
    monkeypatch.setattr(observe, "layer_surfaces", lambda: [])
    monkeypatch.setattr(checks, "_processes", lambda **_kwargs: [])
    monkeypatch.setattr(capture, "size_of", lambda _path: {"w": 100, "h": 100})
    outputs = iter(sequence[1:])

    def grab(_rect, destination, _scale):
        destination.write_bytes(next(outputs, sequence[-1]))

    monkeypatch.setattr(capture, "grab_region", grab)
    return reference, cursor, window


def _png(value):
    return png.encode(100, 100, bytes((value, value, value, 255)) * 10000)


def _device(monkeypatch, cursor):
    class Device:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def move_relative(self, dx, dy):
            cursor[0] = {"x": cursor[0]["x"] + dx, "y": cursor[0]["y"] + dy}

    monkeypatch.setattr(actions.uinput, "Device", Device)
    monkeypatch.setattr(actions.time, "sleep", lambda _seconds: None)


def test_hover_cli_returns_changed_pixel_evidence_without_reveal_claim(monkeypatch, capsys):
    reference, cursor, _window = _world(monkeypatch, [_png(0), _png(0), _png(1)])
    _device(monkeypatch, cursor)

    assert cli.main([
        "hover", "--frame", reference["id"], "--at", "10,10", "--duration", "0.1",
        "--hz", "10", "--move-timeout", "1", "--dwell", "0", "--watch", "full",
        "--poll-ms", "1", "--quiet-ms", "0", "--timeout", "0.1", "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "observed"
    assert result["condition"]["status"] == "changed"
    assert result["reveal"]["status"] == "unknown"
    assert result["observation"]["changed"] is True
    assert result["entry"]["transport"] == "uinput-relative-v1"


def test_hover_timeout_keeps_application_meaning_unknown(monkeypatch, capsys):
    reference, cursor, _window = _world(monkeypatch, [_png(0), _png(0), _png(0)])
    _device(monkeypatch, cursor)

    assert cli.main([
        "hover", "--frame", reference["id"], "--at", "10,10", "--duration", "0.1",
        "--hz", "10", "--move-timeout", "1", "--dwell", "0", "--watch", "full",
        "--poll-ms", "1", "--quiet-ms", "0", "--timeout", "0.003", "--json",
    ]) == 24
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "timed-out"
    assert result["condition"]["status"] == "timed-out"
    assert result["observation"] is None
    assert result["reveal"]["status"] == "unknown"


def test_hover_dwell_only_returns_a_frame_without_reveal_success(monkeypatch, capsys):
    reference, cursor, _window = _world(monkeypatch, [_png(0), _png(1)])
    _device(monkeypatch, cursor)

    assert cli.main([
        "hover", "--frame", reference["id"], "--at", "10,10", "--duration", "0.1",
        "--hz", "10", "--move-timeout", "1", "--dwell", "0", "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["condition"]["status"] == "not-requested"
    assert result["observation"] is not None
    assert result["reveal"]["status"] == "unknown"
