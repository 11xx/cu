"""Read-only evidence export and consumer round-trip coverage."""

from __future__ import annotations

import copy

import pytest
from evidence_consumer import ConsumerError, consume

from cu import cli, evidence, frames, png


def stored(tmp_path, value=1):
    data = png.encode(2, 1, bytes((value, 20, 30, 255)) * 2)
    with frames.capture_scratch() as scratch:
        scratch.write_bytes(data)
        return frames.new(
            source={"type": "region", "name": "fixture"},
            logical_rect={"x": 10, "y": 20, "w": 2, "h": 1},
            image_size={"w": 2, "h": 1},
            scratch=scratch,
            encoded_bytes=len(data),
            digest_native=__import__("hashlib").sha256(data).hexdigest(),
            monitor={"name": "fixture", "logical_rect": {"x": 10, "y": 20, "w": 2, "h": 1}},
        )


def test_export_round_trip_preserves_identity_spaces_and_change(tmp_path):
    first = stored(tmp_path, 1)
    second = stored(tmp_path, 2)
    retained = tmp_path / "retained.png"
    envelope = evidence.export_frame(first, compare=second, retain=retained)
    result = consume(envelope)
    assert result["status"] == "available"
    assert envelope["frame"]["id"] == first["id"]
    assert envelope["coverage"]["coordinate_spaces"]["image"] == "cu-frame-image-pixels"
    assert envelope["derived"][0]["status"] == "changed"
    assert envelope["availability"]["retained"]["path"] == str(retained)


def test_missing_runtime_frame_is_unavailable_not_empty(tmp_path):
    frame = stored(tmp_path)
    frames.path_for(frame["id"], ".png").unlink()
    envelope = evidence.export_frame(frame, compare=frame)
    assert envelope["status"] == "unavailable"
    assert envelope["availability"]["runtime"] == "unavailable"
    assert envelope["derived"][0]["status"] == "unavailable"
    assert consume(envelope)["status"] == "unavailable"


def test_export_refuses_digest_and_space_drift(tmp_path):
    frame = stored(tmp_path)
    broken = copy.deepcopy(frame)
    broken["digest_native"] = "0" * 64
    with pytest.raises(evidence.ExportError, match="digest"):
        evidence.export_frame(broken)
    envelope = evidence.export_frame(frame)
    envelope["coverage"]["coordinate_spaces"]["image"] = "screen-pixels"
    with pytest.raises(ConsumerError, match="coordinate space"):
        consume(envelope)


def test_export_never_overwrites_retained_bytes(tmp_path):
    frame = stored(tmp_path)
    retained = tmp_path / "retained.png"
    retained.write_bytes(b"existing")
    with pytest.raises(evidence.ExportError, match="already exists"):
        evidence.export_frame(frame, retain=retained)


def test_cli_export_is_read_only_and_returns_the_envelope(tmp_path, capsys):
    frame = stored(tmp_path)
    assert cli.main(["export", "--frame", frame["id"], "--json"]) == 0
    result = __import__("json").loads(capsys.readouterr().out)
    assert result["schema"] == "cu-evidence/1"
    assert result["status"] == "ok"
    assert result["invocation_id"]
