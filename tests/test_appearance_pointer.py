import copy
from typing import ClassVar

import pytest

from cu import actions, config, frames, observe, safety, uinput

POPUP = {"address": "0x2", "class": "portal", "title": "menu", "monitor": "OUT"}


class RecordingWatch:
    instances: ClassVar[list] = []

    def __init__(self, settle_ms):
        self.events = []
        RecordingWatch.instances.append(self)

    def start(self):
        self.events.append("start")
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc_info):
        self.close()

    def report(self, target=None):
        self.events.append("report")
        return {"status": "observed", "settle_ms": 250, "windows": [POPUP]}

    def close(self):
        self.events.append("close")


def _cfg():
    return copy.deepcopy(config.DEFAULTS)


def test_aimed_typing_watches_from_before_its_own_click(monkeypatch):
    RecordingWatch.instances = []
    monkeypatch.setattr(actions.appearance, "Watch", RecordingWatch)
    order = []

    def click(*args, **kwargs):
        order.append(("click", list(RecordingWatch.instances[0].events)))
        return {"window": {"address": "0x1"}}

    received = {}

    def type_into_focus(cfg, text, **kwargs):
        received.update(kwargs)
        return {"action": "type", "appeared": kwargs["watch"].report()}

    monkeypatch.setattr(actions, "_aimed_click", click)
    monkeypatch.setattr(actions, "_type_into_focus", type_into_focus)

    result = actions.type_text(_cfg(), "text", frame_id="f1", at=(1, 1), dry_run=False)

    watch = RecordingWatch.instances[0]
    assert order == [("click", ["start"])]
    assert received["watch"] is watch
    assert watch.events == ["start", "report", "close"]
    assert result["appeared"]["windows"] == [POPUP]


def test_a_scroll_names_the_window_its_wheel_opened(monkeypatch):
    RecordingWatch.instances = []
    monkeypatch.setattr(actions.appearance, "Watch", RecordingWatch)
    window = {
        "address": "0x1", "class": "app", "title": "fixture",
        "logical_rect": {"x": 0, "y": 0, "w": 10, "h": 10},
    }
    cursor = {"x": 5, "y": 5}
    monkeypatch.setattr(actions, "resolve_target", lambda *a: {
        "aimed": None, "frame": None,
        "snapshot": {"cursor_logical": cursor, "windows": [window], "monitors": []},
    })
    monkeypatch.setattr(observe, "window_at", lambda *a: window)
    monkeypatch.setattr(safety, "preflight_pointer", lambda *a, **k: None)
    monkeypatch.setattr(safety, "record", lambda *a: None)
    monkeypatch.setattr(actions, "_live_pointer_target", lambda *a: (window, cursor))
    scrolled = []

    class Device:
        def __init__(self, settle_ms):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            pass

        def scroll(self, dy, dx):
            scrolled.append(list(RecordingWatch.instances[0].events))

    monkeypatch.setattr(uinput, "Device", Device)

    result = actions.scroll(_cfg(), dy=3, dx=0, frame_id=None, at=None, dry_run=False)

    assert scrolled == [["start"]]
    assert RecordingWatch.instances[0].events == ["start", "report", "close"]
    assert result["appeared"]["windows"] == [POPUP]


def test_a_hover_names_the_window_its_dwell_revealed(monkeypatch):
    RecordingWatch.instances = []
    monkeypatch.setattr(actions.appearance, "Watch", RecordingWatch)
    focused = {"address": "0x1", "class": "app", "logical_rect": {"x": 0, "y": 0, "w": 10, "h": 10}}
    snap = {"focused": focused, "cursor_logical": {"x": 5, "y": 5}, "monitors": [],
            "windows": [focused], "layer_surfaces": []}
    monkeypatch.setattr(observe, "snapshot", lambda: copy.deepcopy(snap))
    moved = []

    def move(*args, **kwargs):
        moved.append(list(RecordingWatch.instances[0].events))
        return {"cursor": {"x": 5, "y": 5}, "placement": "native", "delivery": "delivered"}

    monkeypatch.setattr(actions, "_move", move)
    monkeypatch.setattr(actions, "move", lambda *a, **k: pytest.fail("hover opened a second watch"))
    monkeypatch.setattr(frames, "load", lambda frame_id: {"id": frame_id})
    monkeypatch.setattr(safety, "confirm_pointer", lambda *a: None)
    monkeypatch.setattr(actions, "_motion_boundary_guard", lambda *a, **k: None)
    monkeypatch.setattr(safety, "record", lambda *a: None)

    result = actions.hover(_cfg(), "f1", (1, 1), dwell=0, dry_run=False)

    assert moved == [["start"]]
    assert RecordingWatch.instances[0].events == ["start", "report", "close"]
    assert result["appeared"]["windows"] == [POPUP]


def test_a_move_names_the_window_its_crossing_opened_and_logs_it(monkeypatch):
    RecordingWatch.instances = []
    monkeypatch.setattr(actions.appearance, "Watch", RecordingWatch)
    target = {"address": "0x1", "class": "app", "title": "fixture"}
    calls = []

    def inner(cfg, frame_id, at, **kwargs):
        calls.append((list(RecordingWatch.instances[0].events), kwargs["record"]))
        return {"action": "move", "target_window": target, "dry_run": False}

    recorded = []
    monkeypatch.setattr(actions, "_move", inner)
    monkeypatch.setattr(safety, "record", lambda cfg, result: recorded.append(dict(result)))

    result = actions.move(_cfg(), "f1", (1, 1), dry_run=False)

    assert calls == [(["start"], False)]
    assert RecordingWatch.instances[0].events == ["start", "report", "close"]
    assert result["appeared"]["windows"] == [POPUP]
    assert [entry["appeared"]["windows"] for entry in recorded] == [[POPUP]]


def test_a_dry_run_move_opens_no_watch(monkeypatch):
    RecordingWatch.instances = []
    monkeypatch.setattr(actions.appearance, "Watch", RecordingWatch)
    monkeypatch.setattr(actions, "_move", lambda *a, **k: {"action": "move", "dry_run": True})

    result = actions.move(_cfg(), "f1", (1, 1), dry_run=True)

    assert RecordingWatch.instances == []
    assert "appeared" not in result
