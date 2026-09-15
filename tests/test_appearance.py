import copy
import socket

import pytest

from cu import actions, appearance, cli, config, exits, hypr, observe, safety


def test_a_key_receipt_names_the_dialog_its_keystroke_opened(monkeypatch, capsys):
    window = {
        **_window("0x1", cls="firefox", title="Debugging"),
        "focused": True,
        "visible": True,
        "hidden": False,
        "pid": 1,
    }
    monkeypatch.setattr(
        observe, "snapshot", lambda: {"focused": window, "windows": [window], "monitors": []}
    )
    monkeypatch.setattr(safety, "preflight_keyboard", lambda *args: None)
    monkeypatch.setattr(safety, "record", lambda *args: None)
    sent = []
    monkeypatch.setattr(
        actions, "send_chords", lambda cfg, chords, backend, before_send: sent.append(chords)
    )
    popup = _window("0xabc", cls="portal", title="Cannot open file")

    class ReportingWatch:
        def __init__(self, settle_ms):
            self.settle_ms = settle_ms

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            pass

        def report(self, target=None):
            assert sent, "the report is read only after the keystroke is sent"
            windows = [appearance._describe(popup, target)]
            return {"status": "observed", "settle_ms": self.settle_ms, "windows": windows}

    monkeypatch.setattr(actions.appearance, "Watch", ReportingWatch)
    result = actions.key(copy.deepcopy(config.DEFAULTS), action=None, chord="return", dry_run=False)

    assert result["appeared"]["windows"][0]["title"] == "Cannot open file"
    cli._notice_appeared(result)
    assert "Cannot open file" in capsys.readouterr().err


def _window(address, cls="app", title="t", rect=None, reachability="fully-reachable"):
    return {
        "address": address,
        "class": cls,
        "title": title,
        "floating": True,
        "workspace": "2",
        "monitor": "DP-1",
        "logical_rect": rect or {"x": 0, "y": 0, "w": 10, "h": 10},
        "reachability": {"status": reachability},
    }


def test_a_window_opened_by_the_action_is_named_with_its_relation_to_the_target():
    ours, theirs = socket.socketpair()
    target = _window("0x1", rect={"x": 0, "y": 0, "w": 100, "h": 100})
    popup = _window("0xabc", cls="portal", title="Cannot open file",
                    rect={"x": 50, "y": 50, "w": 400, "h": 200},
                    reachability="partially-reachable")
    snapshots = [{"windows": [target]}, {"windows": [target, popup]}]
    watch = appearance.Watch(80, connect=lambda: ours, snapshot=lambda: snapshots.pop(0))
    with watch:
        theirs.sendall(
            b"activewindow>>portal,Cannot open file\n"
            b"openwindow>>abc,2,portal,Cannot open file\n"
        )
        report = watch.report(target=target)
    theirs.close()
    assert report["status"] == "observed"
    assert [w["address"] for w in report["windows"]] == ["0xabc"]
    named = report["windows"][0]
    assert named["over_target"] is True and named["reachability"] == "partially-reachable"
    line = appearance.notice({"appeared": report})
    assert "Cannot open file" in line and "over the target" in line
    assert "(partially-reachable)" in line


def test_nothing_new_reports_an_empty_observation_and_no_notice():
    ours, theirs = socket.socketpair()
    target = _window("0x1")
    watch = appearance.Watch(30, connect=lambda: ours, snapshot=lambda: {"windows": [target]})
    with watch:
        report = watch.report(target=target)
    theirs.close()
    assert report == {"status": "observed", "settle_ms": 30, "windows": []}
    assert appearance.notice({"appeared": report}) is None


def test_disabled_and_unavailable_say_which_silence_it_is():
    def refuse():
        raise hypr.HyprError("the Hyprland event socket is unavailable")

    with appearance.Watch(0, connect=refuse, snapshot=dict) as watch:
        assert watch.report()["status"] == "disabled"
    with appearance.Watch(50, connect=refuse, snapshot=dict) as watch:
        report = watch.report()
    assert report["status"] == "unavailable" and "event socket" in report["reason"]


REAL_WATCH = appearance.Watch


def _popup_watch(settle_ms, theirs_holder, popup, base=None):
    """A real watch over a socketpair whose second snapshot shows the popup."""
    ours, theirs = socket.socketpair()
    theirs_holder.append(theirs)
    base = base or []
    snapshots = [{"windows": base}, {"windows": [*base, popup]}]
    return REAL_WATCH(settle_ms, connect=lambda: ours, snapshot=lambda: snapshots.pop(0))


def test_a_refusal_leaving_a_watch_names_the_window_that_answered():
    held = []
    popup = _window("0xabc", cls="portal", title="Cannot open file")
    with pytest.raises(safety.Refused) as caught, _popup_watch(30, held, popup):
        held[0].sendall(b"openwindow>>abc,2,portal,Cannot open file\n")
        raise safety.Refused(exits.FOCUS_CHANGED, "focus moved to a new window")
    held[0].close()
    appeared = caught.value.details["appeared"]
    assert appeared["status"] == "observed"
    assert [w["title"] for w in appeared["windows"]] == ["Cannot open file"]


def test_a_watch_reads_once_and_leaves_plain_errors_alone():
    held = []
    popup = _window("0xabc")
    watch = _popup_watch(30, held, popup)
    with pytest.raises(safety.Refused) as caught, watch:
        watch.report()
        raise safety.Refused(exits.FOCUS_CHANGED, "after the report", details={"why": "x"})
    assert caught.value.details == {"why": "x"}
    held[0].close()

    plain = appearance.Watch(30, connect=lambda: socket.socketpair()[0], snapshot=lambda: {})
    with pytest.raises(ValueError), plain:
        raise ValueError("not an interlock")


def test_the_cli_names_an_appeared_window_on_a_refusal(capsys):
    popup = appearance._describe(_window("0xabc", title="Cannot open file"), None)

    def refused():
        raise safety.Refused(
            exits.FOCUS_CHANGED,
            "focus moved",
            details={"appeared": {"status": "observed", "settle_ms": 250, "windows": [popup]}},
        )

    assert cli._safe_call(refused, as_json=False) == exits.FOCUS_CHANGED
    err = capsys.readouterr().err
    assert "Cannot open file" in err and "cu: refused: focus moved" in err


def test_a_move_refused_by_its_own_popup_names_it(monkeypatch):
    held = []
    popup = _window("0xabc", cls="menu", title="submenu")
    monkeypatch.setattr(
        actions.appearance, "Watch", lambda settle_ms: _popup_watch(settle_ms, held, popup)
    )

    def refuse_mid_move(*_args, **_kwargs):
        held[0].sendall(b"openwindow>>abc,2,menu,submenu\n")
        raise safety.Refused(exits.FOCUS_CHANGED, "expected the fixture to hold focus")

    monkeypatch.setattr(actions, "_move", refuse_mid_move)
    with pytest.raises(safety.Refused) as caught:
        actions.move(copy.deepcopy(config.DEFAULTS), "f1", (1, 1), dry_run=False)
    held[0].close()
    assert [w["title"] for w in caught.value.details["appeared"]["windows"]] == ["submenu"]
