import json

import pytest

from cu import actions, browser, cli, config, exits, observe, safety, status
from cu.safety import Refused


def _window(cls="thorium-browser", **extra):
    return {"address": "0x1", "class": cls, "title": "page", "fullscreen": 0, **extra}


@pytest.fixture
def focused(monkeypatch):
    """A focus that succeeds, with every arrangement call recorded."""
    calls = {"present": [], "attached": []}
    target = _window()
    monkeypatch.setattr(safety, "check_abort", lambda: None)
    monkeypatch.setattr(safety, "check_session", lambda: None)
    monkeypatch.setattr(safety, "check_denylist", lambda *a: None)
    monkeypatch.setattr(safety, "record", lambda *a: None)
    monkeypatch.setattr(safety, "confirm_focus", lambda *a: None)
    monkeypatch.setattr(actions.hypr, "lua", lambda _code: "")
    monkeypatch.setattr(observe, "focused", lambda: target)
    monkeypatch.setattr(status, "current_session", lambda: "s1")
    monkeypatch.setattr(
        browser,
        "for_window_class",
        lambda cls, browsers=None: {"binary": "thorium-browser"} if "browser" in cls else None,
    )

    def present(cfg, window, *, mode, why, dry_run):
        calls["present"].append((window["address"], mode))
        return {"token": "p-" + "a" * 32}

    monkeypatch.setattr(actions, "present", present)
    monkeypatch.setattr(
        status, "attach_presentation",
        lambda token, session: calls["attached"].append((token, session)) or True,
    )
    return calls


def test_focusing_a_browser_in_a_session_maximizes_it_for_the_session(focused):
    result = actions.focus(config.DEFAULTS, _window(), dry_run=False)
    assert result["presentation"]["status"] == "maximized"
    assert result["presentation"]["restore"] == "session end"
    assert focused["present"] == [("0x1", "maximize")]
    assert focused["attached"] == [("p-" + "a" * 32, "s1")]


@pytest.mark.parametrize(
    ("change", "status_name"),
    [
        ("no-session", "not-requested"),
        ("never", "not-requested"),
        ("terminal", "not-requested"),
        ("fullscreen", "not-needed"),
    ],
)
def test_focus_leaves_the_arrangement_alone_when_the_default_does_not_apply(
    focused, monkeypatch, change, status_name
):
    cfg = json.loads(json.dumps(config.DEFAULTS))
    window = _window()
    if change == "no-session":
        monkeypatch.setattr(status, "current_session", lambda: None)
    elif change == "never":
        cfg["presentation"]["maximize_on_focus"] = "never"
    elif change == "terminal":
        window = _window(cls="kitty")
    else:
        window = _window(fullscreen=1)
    result = actions.focus(cfg, window, dry_run=False)
    assert result["presentation"]["status"] == status_name
    assert focused["present"] == [] and focused["attached"] == []


def test_no_maximize_and_dry_run_never_arrange(focused):
    assert "presentation" not in actions.focus(
        config.DEFAULTS, _window(), dry_run=False, maximize=False
    )
    assert "presentation" not in actions.focus(config.DEFAULTS, _window(), dry_run=True)
    assert focused["present"] == []


def test_a_refused_maximize_is_reported_beside_a_successful_focus(focused, monkeypatch):
    def refuse(*_a, **_k):
        raise Refused(exits.PRESENTATION_REFUSED, "compositor refused")

    monkeypatch.setattr(actions, "present", refuse)
    result = actions.focus(config.DEFAULTS, _window(), dry_run=False)
    assert result["action"] == "focus"
    assert result["presentation"] == {
        "status": "refused", "code": exits.PRESENTATION_REFUSED, "reason": "compositor refused",
    }
    assert focused["attached"] == []


def test_a_session_keeps_the_presentations_it_is_handed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv(status.SESSION_ENV, raising=False)
    session = status.begin("maximize probe")["id"]
    assert status.attach_presentation("p-1", session)
    assert status.attach_presentation("p-2", session)
    assert not status.attach_presentation("p-3", "missing")
    assert status.presentations(session) == (session, ["p-1", "p-2"])
    assert status.end(session)["was_open"]
    assert status.presentations(session) == (session, [])


def test_session_end_reports_another_agents_reservation_without_restoring(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(status, "presentations", lambda session: ("s1", ["p-one"]))
    monkeypatch.setattr(
        status, "end", lambda key: {"ended": key, "was_open": True, "remaining": 1}
    )

    def reserved(key):
        raise Refused(exits.DESKTOP_RESERVED, "session s2 (other) has reserved the desktop")

    monkeypatch.setattr(status, "check_exclusive", reserved)
    monkeypatch.setattr(actions, "restore", lambda *a, **k: pytest.fail("restored anyway"))
    assert cli.main(["session", "end", "--session", "s1", "--json"]) == exits.OK
    record = json.loads(capsys.readouterr().out)
    assert record["restored"] == [
        {"token": "p-one", "status": "refused", "code": exits.DESKTOP_RESERVED,
         "reason": "session s2 (other) has reserved the desktop"},
    ]


def test_session_end_restores_newest_first_and_reports_refusals(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(status, "check_exclusive", lambda key: None)
    ended = []
    monkeypatch.setattr(status, "presentations", lambda session: ("s1", ["p-old", "p-new"]))
    monkeypatch.setattr(
        status, "end",
        lambda key: ended.append(key) or {"ended": key, "was_open": True, "remaining": 0},
    )
    restored = []

    def restore(cfg, token, *, why, dry_run):
        restored.append(token)
        if token == "p-old":
            raise Refused(exits.PRESENTATION_CHANGED, "window changed after presentation")
        return {"action": "restore"}

    monkeypatch.setattr(actions, "restore", restore)
    assert cli.main(["session", "end", "--session", "s1", "--json"]) == exits.OK
    record = json.loads(capsys.readouterr().out)
    assert restored == ["p-new", "p-old"]
    assert ended == ["s1"]
    assert record["restored"] == [
        {"token": "p-new", "status": "restored"},
        {"token": "p-old", "status": "refused", "code": exits.PRESENTATION_CHANGED,
         "reason": "window changed after presentation"},
    ]


def test_config_refuses_an_unknown_focus_presentation_policy(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[presentation]\nmaximize_on_focus = "always"\n')
    with pytest.raises(config.ConfigError, match="maximize_on_focus"):
        config.load(path)
