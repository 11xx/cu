"""Read-only evidence export and consumer round-trip coverage."""

from __future__ import annotations

import copy
import hashlib
import struct
import zlib
from pathlib import Path

import pytest
from evidence_consumer import ConsumerError, consume

from cu import cli, evidence, frames, png


def stored(tmp_path, value=1):
    data = png.encode(2, 1, bytes((value, 20, 30, 255)) * 2)
    return stored_bytes(tmp_path, data)


def stored_bytes(tmp_path, data, *, source=None, logical_rect=None):
    with frames.capture_scratch() as scratch:
        scratch.write_bytes(data)
        return frames.new(
            source=source or {"type": "region", "name": "fixture"},
            logical_rect=logical_rect or {"x": 10, "y": 20, "w": 2, "h": 1},
            image_size={"w": 2, "h": 1},
            scratch=scratch,
            encoded_bytes=len(data),
            digest_native=hashlib.sha256(data).hexdigest(),
            monitor={"name": "fixture", "logical_rect": {"x": 10, "y": 20, "w": 2, "h": 1}},
        )


def ancillary_copy(data):
    kind = b"tEXt"
    payload = b"note\0same pixels"
    chunk = (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(payload, zlib.crc32(kind)))
    )
    return data[:-12] + chunk + data[-12:]


def test_export_round_trip_preserves_identity_spaces_and_change(tmp_path):
    first = stored(tmp_path, 1)
    second = stored(tmp_path, 2)
    retained = tmp_path / "retained.png"
    envelope = evidence.export_frame(first, compare=second, retain=retained)
    result = consume(envelope)
    assert result["status"] == "available"
    assert result["digest"] == envelope["source"]["digest"]
    assert result["coverage"] == envelope["coverage"]
    assert result["producer"] == envelope["producer"]
    assert envelope["frame"]["id"] == first["id"]
    assert envelope["coverage"]["coordinate_spaces"]["image"] == "cu-frame-image-pixels"
    assert envelope["derived"][0]["status"] == "changed"
    assert envelope["availability"]["retained"]["path"] == str(retained)
    assert envelope["producer"]["configuration"] == {}
    assert envelope["coverage"]["requested"] == envelope["coverage"]["returned"]
    assert envelope["derived"][0]["input_frames"] == [first["id"], second["id"]]


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


def test_oversized_source_is_read_with_one_bounded_chunk(tmp_path, monkeypatch):
    frame = stored(tmp_path)
    read_sizes = []

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            read_sizes.append(size)
            return b"x" * (evidence.MAX_RETAINED_BYTES + 1)

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: Reader())
    with pytest.raises(evidence.ExportError, match="bounded export byte limit"):
        evidence._read(frame)
    assert read_sizes == [evidence.MAX_RETAINED_BYTES + 1]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("digest_native", "0" * 64, "digest"),
        ("image_size", {"w": 3, "h": 1}, "dimensions"),
    ],
)
def test_descriptor_mismatch_does_not_create_retained_bytes(tmp_path, field, value, message):
    frame = stored(tmp_path)
    broken = copy.deepcopy(frame)
    broken[field] = value
    retain = tmp_path / f"broken-{field}.png"
    with pytest.raises(evidence.ExportError, match=message):
        evidence.export_frame(broken, retain=retain)
    assert not retain.exists()


def test_source_and_coordinate_identity_are_validated_before_retention(tmp_path):
    frame = stored(tmp_path)
    for name, update, message in (
        ("source", {"type": "region"}, "source identity"),
        (
            "coordinate space",
            {"image": "screen-pixels", "logical": evidence.LOGICAL_SPACE},
            "coordinate spaces",
        ),
    ):
        broken = copy.deepcopy(frame)
        if name == "source":
            broken["source"] = update
        else:
            broken["coordinate_spaces"] = update
        retain = tmp_path / f"broken-{name.replace(' ', '-')}.png"
        with pytest.raises(evidence.ExportError, match=message):
            evidence.export_frame(broken, retain=retain)
        assert not retain.exists()


def test_compare_validation_precedes_retention(tmp_path):
    first = stored(tmp_path, 1)
    second = stored(tmp_path, 2)
    second["logical_rect"] = {"x": 10, "y": 20, "w": 3, "h": 1}
    retain = tmp_path / "compare-mismatch.png"
    with pytest.raises(evidence.ExportError, match="coverage"):
        evidence.export_frame(first, compare=second, retain=retain)
    assert not retain.exists()


def test_compare_digest_validation_precedes_retention(tmp_path):
    first = stored(tmp_path, 1)
    second = stored(tmp_path, 2)
    second["digest_native"] = "0" * 64
    retain = tmp_path / "compare-digest-mismatch.png"
    with pytest.raises(evidence.ExportError, match="digest"):
        evidence.export_frame(first, compare=second, retain=retain)
    assert not retain.exists()


def test_compare_source_identity_validation_precedes_retention(tmp_path):
    first = stored(tmp_path, 1)
    second = stored(tmp_path, 2)
    second["source"] = {"type": "region", "name": "other"}
    retain = tmp_path / "compare-source-mismatch.png"
    with pytest.raises(evidence.ExportError, match="source identity"):
        evidence.export_frame(first, compare=second, retain=retain)
    assert not retain.exists()


def test_pixel_change_reports_decoded_pixels_separately_from_byte_hash(tmp_path):
    first_data = png.encode(2, 1, bytes((1, 20, 30, 255)) * 2)
    second_data = ancillary_copy(first_data)
    first = stored_bytes(tmp_path, first_data)
    second = stored_bytes(tmp_path, second_data)
    envelope = evidence.export_frame(first, compare=second)
    derived = envelope["derived"][0]
    assert derived["status"] == "unchanged"
    assert derived["score"] == "decoded-pixel-equality"
    assert derived["decoded_pixel_equal"] is True
    assert derived["byte_hash_equal"] is False


def test_retained_history_remains_verifiable_after_runtime_collection(tmp_path):
    frame = stored(tmp_path)
    retained = tmp_path / "historical.png"
    historical = evidence.export_frame(frame, retain=retained)
    Path(frame["path"]).unlink()

    assert consume(historical)["status"] == "available"
    reference = evidence.export_frame(frame)
    assert reference["status"] == "unavailable"
    assert reference["availability"] == {
        "runtime": "unavailable",
        "retained": None,
        "reason": "runtime PNG is absent; collection time is unknown",
    }
    assert consume(reference)["status"] == "unavailable"


def test_export_never_overwrites_retained_bytes(tmp_path):
    frame = stored(tmp_path)
    retained = tmp_path / "retained.png"
    retained.write_bytes(b"existing")
    with pytest.raises(evidence.ExportError, match="already exists"):
        evidence.export_frame(frame, retain=retained)
    assert retained.read_bytes() == b"existing"


def test_cli_export_is_read_only_and_returns_the_envelope(tmp_path, capsys):
    frame = stored(tmp_path)
    assert cli.main(["export", "--frame", frame["id"], "--json"]) == 0
    result = __import__("json").loads(capsys.readouterr().out)
    assert result["schema"] == "cu-evidence/1"
    assert result["status"] == "ok"
    assert result["invocation_id"]
