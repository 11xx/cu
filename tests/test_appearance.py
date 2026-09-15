import copy
import socket

from cu import actions, appearance, cli, config, hypr, observe, safety


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
