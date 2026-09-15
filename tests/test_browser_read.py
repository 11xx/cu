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
    make_control,
    make_request,
    protocol_limits,
    read_frame,
    validate_payload,
    write_frame,
)

PROTOCOL = {"protocol": "cu-browser-read", "version": 9}


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


def test_select_payload_requires_one_bounded_option_identity():
    assert validate_payload("select", {"ref": "e1", "value": "two"}) == {
        "ref": "e1",
        "value": "two",
    }
    assert validate_payload("select", {"ref": "e1", "label": "Two"}) == {
        "ref": "e1",
        "label": "Two",
    }
    for payload in (
        {"ref": "e1"},
        {"ref": "e1", "value": "two", "label": "Two"},
    ):
        with pytest.raises(ProtocolError, match="exactly one of value or label"):
            validate_payload("select", payload)


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


def test_tabs_control_lists_every_tab_and_takes_titles_only_on_request():
    assert make_control("tabs")["include_titles"] is False
    assert make_control("tabs", include_titles=True)["include_titles"] is True
    with pytest.raises(ProtocolError, match="no tab_id"):
        make_control("tabs", tab_id=7)
    with pytest.raises(ProtocolError, match="no include_titles"):
        make_control("grant", include_titles=True)
    with pytest.raises(ProtocolError, match="no capabilities"):
        make_control("tabs", capabilities=["read"])


def test_full_page_capture_is_exclusive_with_a_region():
    region = {"x": 0, "y": 0, "width": 1, "height": 1, "space": "css-viewport"}
    assert validate_payload("capture", {"full_page": True}) == {"full_page": True}
    with pytest.raises(ProtocolError, match="must be true"):
        validate_payload("capture", {"full_page": False})
    with pytest.raises(ProtocolError, match="not both"):
        validate_payload("capture", {"full_page": True, "region": region})


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


RELOAD_GRANT = {
    "session": "session-1",
    "tab_id": 7,
    "window_id": 3,
    "document_id": "document-1",
    "generation": 2,
    "active": True,
    "hidden": False,
    "capabilities": ["read", "interact"],
}


def reload_receipt(**changes: object) -> dict[str, object]:
    return {
        "kind": "action",
        "action": "reload",
        "backend": "tabs",
        "document_id": "document-1",
        "generation": 2,
        "dispatch": "api-resolved",
        "verification": "unverified",
        "effect": "not-observed",
        "revoked_grant": {**RELOAD_GRANT, "reason": "reload"},
        "new_document_grant": "none",
        **changes,
    }


def serve_reload(helper: HelperSocket, receipt: dict[str, object] | None):
    """Revoke before replying, then reply with `receipt` or drop the connection."""
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            connection, _ = helper.listener.accept()
            with connection:
                write_frame(connection, {**hello(str(helper.path)), "grants": [RELOAD_GRANT]})
                request = read_frame(connection)
                assert request is not None and request["operation"] == "reload"
                write_frame(
                    connection,
                    {
                        **PROTOCOL,
                        "type": "revoke",
                        "session": "session-1",
                        "tab_id": 7,
                        "document_id": "document-1",
                        "reason": "reload",
                    },
                )
                if receipt is not None:
                    write_frame(connection, {**response(request), "result": receipt})
        except BaseException as error:  # surfaced after the client assertion
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    return thread, errors


def reload_action(client: BrowserReadClient) -> dict[str, object]:
    return client.action(
        "reload",
        session="session-1",
        tab_id=7,
        document_id="document-1",
        generation=2,
        payload={},
        timeout=0.5,
    )


def test_reload_payload_names_no_element_and_requires_generation():
    assert validate_payload("reload", {}) == {}
    with pytest.raises(ProtocolError, match="unsupported message field ref"):
        validate_payload("reload", {"ref": "e1"})
    with pytest.raises(ProtocolError, match="passive read"):
        make_request("reload", session="session-1", tab_id=7, document_id="document-1")


def test_session_controls_bind_or_release_without_page_identity():
    grant = make_control("grant", tab_id=7, capabilities=["read", "interact"], cursor=False)
    assert grant["tab_id"] == 7
    assert grant["capabilities"] == ["read", "interact"]
    assert grant["cursor"] is False
    assert make_control("grant")["capabilities"] == ["read"]
    release = make_control("release")
    assert release["tab_id"] is None
    assert "capabilities" not in release and "cursor" not in release
    with pytest.raises(ProtocolError, match="session control"):
        make_control("transfer")
    with pytest.raises(ProtocolError, match="release takes no"):
        make_control("release", capabilities=["read"])
    with pytest.raises(ProtocolError):
        make_control("grant", tab_id=0)


def test_client_control_tracks_the_grant_the_helper_announces():
    helper = HelperSocket.create()
    grant = {
        "session": "session-1",
        "tab_id": 7,
        "window_id": 1,
        "document_id": "document-1",
        "generation": 0,
        "active": False,
        "hidden": False,
        "capabilities": ["read", "interact"],
    }
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            connection, _ = helper.listener.accept()
            with connection:
                write_frame(connection, hello(str(helper.path)))
                control = read_frame(connection)
                assert control is not None
                assert (control["type"], control["operation"], control["tab_id"]) == (
                    "control",
                    "grant",
                    7,
                )
                write_frame(connection, {**PROTOCOL, "type": "grant", "grant": grant})
                write_frame(
                    connection,
                    {
                        **PROTOCOL,
                        "type": "response",
                        "id": control["id"],
                        "ok": True,
                        "session": grant["session"],
                        "tab_id": grant["tab_id"],
                        "document_id": grant["document_id"],
                        "result": {"grant": grant},
                    },
                )
        except BaseException as error:  # surfaced after the client assertion
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        with BrowserReadClient.connect(helper.path, timeout=0.5) as client:
            result = client.control("grant", tab_id=7, capabilities=["read", "interact"])
            assert result["result"]["grant"] == grant
            assert client.metadata["grants"] == [grant]
    finally:
        thread.join(timeout=1)
        helper.close()
    assert errors == []


def test_debugger_evidence_payloads_and_controls_are_bounded():
    assert validate_payload("console", {}) == {"limit": 100}
    assert validate_payload("network", {"limit": 5}) == {"limit": 5}
    with pytest.raises(ProtocolError):
        validate_payload("network", {"limit": 101})
    with pytest.raises(ProtocolError, match="unsupported message field since"):
        validate_payload("console", {"since": 1})
    assert validate_payload("ax-query", {"role": "button"}) == {
        "role": "button",
        "name": None,
        "include_values": False,
        "limit": MAX_ELEMENTS,
    }
    for operation in ("debug-attach", "debug-detach"):
        assert make_control(operation)["tab_id"] is None
        with pytest.raises(ProtocolError, match=f"{operation} takes no"):
            make_control(operation, capabilities=["read"])


def test_debugger_input_payloads_are_bounded_and_need_a_generation():
    assert validate_payload("trusted-click", {"ref": "e1"}) == {"ref": "e1"}
    assert validate_payload("trusted-type", {"ref": "e2", "text": "hi"}) == {
        "ref": "e2",
        "text": "hi",
    }
    assert validate_payload("trusted-key", {"key": "Enter"}) == {"key": "Enter"}
    with pytest.raises(ProtocolError, match="named key"):
        validate_payload("trusted-key", {"key": "F5"})
    assert validate_payload("trusted-scroll", {"dx": 0, "dy": 300}) == {"dx": 0, "dy": 300}
    with pytest.raises(ProtocolError, match="passive read"):
        make_request(
            "trusted-click", session="s", tab_id=7, document_id="d", payload={"ref": "e1"}
        )


def test_reload_receipt_names_the_revoked_grant_and_tracks_the_revocation():
    helper = HelperSocket.create()
    thread, errors = serve_reload(helper, reload_receipt())
    try:
        with BrowserReadClient.connect(helper.path, timeout=0.5) as client:
            result = reload_action(client)
            assert result["result"]["new_document_grant"] == "none"
            assert client.metadata["grants"] == []
    finally:
        thread.join(timeout=1)
        helper.close()
    assert errors == []


@pytest.mark.parametrize(
    "receipt",
    [
        reload_receipt(new_document_grant="granted"),
        reload_receipt(revoked_grant={**RELOAD_GRANT, "document_id": "other-document"}),
        reload_receipt(backend="dom"),
        None,
    ],
    ids=["regrants", "wrong-grant", "dom-backend", "lost-receipt"],
)
def test_reload_without_a_trustworthy_receipt_is_uncertain(receipt):
    helper = HelperSocket.create()
    thread, errors = serve_reload(helper, receipt)
    try:
        client = BrowserReadClient.connect(helper.path, timeout=0.5)
        with pytest.raises(BrowserActionUncertain):
            reload_action(client)
        assert client._closed is True
    finally:
        thread.join(timeout=1)
        helper.close()
    assert errors == []
