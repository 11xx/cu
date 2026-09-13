"""Association probe refuses guessed title/URL matches."""

from __future__ import annotations

from browser_frame_probe import associate, evidence


def browser_identity(**overrides):
    return {
        "tab_id": 7,
        "document_id": "document-1",
        "generation": 2,
        "viewport": {"width": 800, "height": 600, "space": "css-viewport"},
        **overrides,
    }


def window(address):
    return {
        "address": address,
        "class": "firefox",
        "logical_rect": {"x": 0, "y": 0, "w": 1000, "h": 800},
    }


def test_same_title_and_url_without_a_shared_identity_is_unprojectable():
    result = associate(
        browser_identity(title="same", url="https://example.test"),
        [window("0x1"), window("0x2")],
    )
    assert result["status"] == "unprojectable"


def test_unique_shared_identity_is_the_smallest_supported_domain():
    result = associate(browser_identity(compositor_window_id="0x1"), [window("0x1")])
    assert result["status"] == "supported"
    assert result["invalidation"] == ["document_id", "generation", "window_address", "geometry"]


def test_multiple_shared_candidates_are_ambiguous():
    result = associate(browser_identity(compositor_window_id="0x1"), [window("0x1"), window("0x1")])
    assert result["status"] == "ambiguous"


def test_evidence_envelope_omits_title_and_url():
    result = evidence(
        browser_identity(compositor_window_id="0x1", title="same", url="https://example.test"),
        [window("0x1")],
        [{"name": "OUT", "logical_rect": {"x": 0, "y": 0, "w": 1000, "h": 800}, "scale": 1}],
    )
    encoded = str(result)
    assert result["schema"] == "cu-browser-frame-association/1"
    assert result["association"]["status"] == "supported"
    assert "same" not in encoded and "example.test" not in encoded
