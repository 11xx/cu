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
