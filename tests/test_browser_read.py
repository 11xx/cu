from __future__ import annotations

import io
import os
import stat
import struct
import threading
import time

import pytest

from cu.browser_read import (
    MAX_ELEMENTS,
    MAX_MESSAGE_BYTES,
    BrowserActionUncertain,
    BrowserReadClient,
    BrowserReadTimeout,
    FrameDecoder,
    HelperSocket,
    MismatchedResponse,
    OversizeMessage,
    ProtocolError,
    StaleHelper,
    encode_frame,
    make_request,
    protocol_limits,
    read_frame,
    validate_payload,
    write_frame,
)

PROTOCOL = {"protocol": "cu-browser-read", "version": 3}


def response(request: dict[str, object], *, response_id: str | None = None) -> dict[str, object]:
    return {
        **PROTOCOL,
        "type": "response",
        "id": response_id or str(request["id"]),
        "ok": True,
        "session": request["session"],
        "tab_id": request["tab_id"],
        "document_id": request["document_id"],
        "result": {"kind": "observation", "generation": 0},
    }


def hello(path: str) -> dict[str, object]:
    return {
        **PROTOCOL,
        "type": "hello",
        "helper_id": "helper-test",
        "socket": path,
        "limits": protocol_limits(),
        "grants": [],
    }


def serve_once(helper: HelperSocket, *, response_id: str | None = None, respond: bool = True):
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            connection, _ = helper.listener.accept()
            with connection:
                write_frame(connection, hello(str(helper.path)))
                request = read_frame(connection)
                assert request is not None
                if respond:
                    write_frame(connection, response(request, response_id=response_id))
                else:
                    time.sleep(0.3)
        except BaseException as error:  # surfaced after the client assertion
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    return thread, errors


def test_native_frames_handle_valid_partial_invalid_oversize_and_eof() -> None:
    message = {"protocol": "cu-browser-read", "version": 1, "type": "hello"}
    encoded = encode_frame(message)
    assert read_frame(io.BytesIO(encoded)) == message
    assert read_frame(io.BytesIO(b"")) is None

    decoder = FrameDecoder()
    assert decoder.feed(encoded[:2]) == []
    assert decoder.feed(encoded[2:5]) == []
    assert decoder.feed(encoded[5:]) == [message]
    decoder.finish()

    with pytest.raises(ProtocolError, match="partial frame header"):
        read_frame(io.BytesIO(encoded[:2]))
    with pytest.raises(ProtocolError, match="partial frame"):
        read_frame(io.BytesIO(encoded[:-1]))
    with pytest.raises(ProtocolError, match="valid UTF-8 JSON"):
        read_frame(io.BytesIO(struct.pack("=I", 1) + b"{"))
    with pytest.raises(ProtocolError, match="empty frames"):
        read_frame(io.BytesIO(struct.pack("=I", 0)))
    with pytest.raises(OversizeMessage):
        read_frame(io.BytesIO(struct.pack("=I", MAX_MESSAGE_BYTES + 1)))
    partial = FrameDecoder()
    partial.feed(encoded[:3])
    with pytest.raises(ProtocolError, match="partial frame"):
        partial.finish()


def test_socket_permissions_and_cleanup_are_per_helper(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    first = HelperSocket.create()
    second = HelperSocket.create()
    try:
        assert first.path != second.path
        assert stat.S_IMODE(os.stat(first.path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(first.path.parent).st_mode) == 0o700
        first.close()
        assert not first.path.exists()
        assert second.path.exists()
    finally:
        first.close()
        second.close()
    assert not second.path.exists()


def test_client_round_trip_uses_bounded_request_and_matching_id() -> None:
    helper = HelperSocket.create()
    thread, errors = serve_once(helper)
    try:
        with BrowserReadClient.connect(helper.path, timeout=0.5) as client:
            result = client.observe(session="session-1", tab_id=7, document_id="document-1")
            assert result["ok"] is True
            assert result["result"] == {"kind": "observation", "generation": 0}
    finally:
        thread.join(timeout=1)
        helper.close()
    assert errors == []


def test_client_rejects_mismatched_response_id() -> None:
    helper = HelperSocket.create()
    thread, errors = serve_once(helper, response_id="different-request")
    try:
        with (
            BrowserReadClient.connect(helper.path, timeout=0.5) as client,
            pytest.raises(MismatchedResponse),
        ):
            client.observe(session="session-1", tab_id=7, document_id="document-1")
    finally:
        thread.join(timeout=1)
        helper.close()
    assert errors == []


def test_client_timeout_closes_a_silent_helper() -> None:
    helper = HelperSocket.create()
    thread, errors = serve_once(helper, respond=False)
    try:
        client = BrowserReadClient.connect(helper.path, timeout=0.5)
        with pytest.raises(BrowserReadTimeout):
            client.observe(session="session-1", tab_id=7, document_id="document-1", timeout=0.05)
        assert client._closed is True
    finally:
        thread.join(timeout=1)
        helper.close()
    assert errors == []


def test_stale_helper_and_unknown_operation_are_refused() -> None:
    helper = HelperSocket.create()
    path = helper.path
    helper.close()
    with pytest.raises(StaleHelper):
        BrowserReadClient.connect(path, timeout=0.1)
    with pytest.raises(ProtocolError, match="passive read"):
        make_request("click", session="session-1", tab_id=7, document_id="document-1")


def test_request_and_frame_budgets_are_explicit() -> None:
    assert MAX_ELEMENTS == 128
    assert protocol_limits()["max_message_bytes"] == MAX_MESSAGE_BYTES
    with pytest.raises(ProtocolError, match="at most 256"):
        make_request(
            "query",
            session="session-1",
            tab_id=7,
            document_id="document-1",
            payload={"search": "x" * 257},
        )
    with pytest.raises(OversizeMessage):
        encode_frame({"data": "x" * MAX_MESSAGE_BYTES})


def test_eof_after_partial_decoder_frame_is_not_silent() -> None:
    message = encode_frame({"protocol": "cu-browser-read", "version": 1, "type": "hello"})
    decoder = FrameDecoder()
    decoder.feed(message[:-1])
    with pytest.raises(ProtocolError, match="partial frame"):
        decoder.finish()


def test_stream_header_can_arrive_one_byte_at_a_time():
    class Fragmented(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 1))

    message = {"test": "fragmented"}
    assert read_frame(Fragmented(encode_frame(message))) == message


def test_discovery_without_extension_is_empty(tmp_path, monkeypatch):
    from cu.browser_read import discover_socket_paths

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert discover_socket_paths() == []


def test_socket_collision_does_not_unlink_another_helper(monkeypatch):
    import cu.browser_read as module

    monkeypatch.setattr(module.secrets, "token_hex", lambda size: "a" * (size * 2))
    with HelperSocket.create() as first:
        with pytest.raises(module.HelperUnavailable):
            HelperSocket.create()
        assert first.path.exists()


def test_action_protocol_requires_generation_and_bounds_structured_reads():
    with pytest.raises(ProtocolError, match="passive read"):
        make_request(
            "click", session="session-1", tab_id=7, document_id="document-1", payload={"ref": "e1"}
        )
    request = make_request(
        "click",
        session="session-1",
        tab_id=7,
        document_id="document-1",
        generation=3,
        payload={"ref": "e1"},
    )
    assert request["operation"] == "click"
    assert (
        validate_payload(
            "observe", {"selector": ".result-card", "include_details": True, "limit": 2}
        )["selector"]
        == ".result-card"
    )
    with pytest.raises(ProtocolError, match="selector"):
        validate_payload("query", {"selector": "x" * 257})


def test_capture_region_is_explicitly_css_viewport_geometry():
    assert validate_payload(
        "capture",
        {"region": {"x": 1, "y": 2, "width": 30, "height": 40, "space": "css-viewport"}},
    )["region"] == {
        "x": 1.0,
        "y": 2.0,
        "width": 30.0,
        "height": 40.0,
        "space": "css-viewport",
    }
    with pytest.raises(ProtocolError, match="css-viewport"):
        validate_payload(
            "capture",
            {"region": {"x": 0, "y": 0, "width": 1, "height": 1, "space": "image-pixels"}},
        )


def test_action_timeout_reports_uncertain_effect():
    helper = HelperSocket.create()
    thread, errors = serve_once(helper, respond=False)
    try:
        client = BrowserReadClient.connect(helper.path, timeout=0.5)
        with pytest.raises(BrowserActionUncertain, match="partially applied"):
            client.action(
                "click",
                session="session-1",
                tab_id=7,
                document_id="document-1",
                generation=0,
                payload={"ref": "e1"},
                timeout=0.05,
            )
        assert client._closed is True
    finally:
        thread.join(timeout=1)
        helper.close()
    assert errors == []
