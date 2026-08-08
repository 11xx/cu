"""Waiting on the event stream: parsing, matching, and the prefix trap."""

from __future__ import annotations

import pytest

from cu import wait


def test_event_addresses_normalise_to_the_form_clients_report():
    """Hyprland's event stream drops the `0x` that `hyprctl clients` keeps.

    Left alone the two never compare equal, and a wait runs to its timeout
    while the window it waited for is sitting on screen.
    """
    assert wait.normalise_address("55f605508210") == wait.normalise_address("0x55f605508210")


def test_normalisation_is_case_insensitive():
    assert wait.normalise_address("0xABC") == wait.normalise_address("abc")


def test_every_condition_names_at_least_one_event():
    assert all(events for events in wait.EVENTS.values())


def test_an_unknown_condition_is_a_usage_error():
    with pytest.raises(ValueError):
        wait.until("no-such-condition", timeout=0)


def test_class_and_title_filters_both_have_to_hold():
    window = {"class": "zen", "title": "Inbox — Mail"}
    assert wait._window_matches(window, "zen", "inbox")
    assert not wait._window_matches(window, "zen", "settings")
    assert not wait._window_matches(window, "firefox", "inbox")


def test_no_filter_matches_anything():
    assert wait._window_matches({"class": "zen", "title": "x"}, None, None)


def test_a_window_is_found_by_whichever_argument_carries_its_address():
    windows = [{"address": "0xdeadbeef", "class": "zen", "title": "x"}]
    assert wait._resolve(["11", "deadbeef", "zen"], windows) == windows[0]


def test_resolution_returns_none_when_no_argument_is_an_address():
    windows = [{"address": "0xdeadbeef", "class": "zen", "title": "x"}]
    assert wait._resolve(["11", "Alacritty"], windows) is None


def test_the_subscription_is_opened_before_the_state_is_checked(monkeypatch):
    """Both orders answer the common cases; only one survives the gap.

    An event announced between the snapshot and the subscription reaches
    neither, and the wait then runs to its timeout reporting that the thing
    never happened — while it sits on screen.
    """
    order = []

    class Subscription:
        def close(self):
            pass

    monkeypatch.setattr(wait, "connect", lambda: order.append("subscribe") or Subscription())
    monkeypatch.setattr(wait.observe, "snapshot", lambda: {"windows": []})
    monkeypatch.setattr(
        wait,
        "already_true",
        lambda *a, **k: order.append("snapshot") or {"class": "zen", "title": "a page"},
    )
    result = wait.until("window-open", cls="zen")
    assert result["waited"] is False
    assert order == ["subscribe", "snapshot"]


def test_a_closed_subscription_is_released_even_when_nothing_matches(monkeypatch):
    closed = []

    class Subscription:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(wait, "connect", Subscription)
    monkeypatch.setattr(wait.observe, "snapshot", lambda: {"windows": []})
    monkeypatch.setattr(wait, "already_true", lambda *a, **k: None)
    monkeypatch.setattr(wait, "stream", lambda conn, deadline: iter(()))
    with pytest.raises(wait.WaitTimeout):
        wait.until("window-open", cls="zen", timeout=0.01)
    assert closed


def test_a_workspace_event_is_returned_without_window_resolution(monkeypatch):
    class Subscription:
        def close(self):
            pass

    monkeypatch.setattr(wait, "connect", Subscription)
    monkeypatch.setattr(wait.observe, "snapshot", lambda: {"windows": []})
    monkeypatch.setattr(
        wait,
        "stream",
        lambda _conn, _deadline: iter([("workspacev2", ["4", "web"])]),
    )
    result = wait.until("workspace", timeout=0.01)
    assert result["matched"] == {"id": "4", "workspace": "web"}


def test_a_filtered_close_ignores_an_unrelated_window(monkeypatch):
    class Subscription:
        def close(self):
            pass

    other = {"address": "0xaaa", "class": "Alacritty", "title": "shell"}
    wanted = {"address": "0xbbb", "class": "zen", "title": "page"}
    monkeypatch.setattr(wait, "connect", Subscription)
    monkeypatch.setattr(wait.observe, "snapshot", lambda: {"windows": [other, wanted]})
    monkeypatch.setattr(
        wait,
        "stream",
        lambda _conn, _deadline: iter(
            [("closewindow", ["aaa"]), ("closewindow", ["bbb"])]
        ),
    )
    result = wait.until("window-close", cls="zen", timeout=0.01)
    assert result["matched"] == wanted
