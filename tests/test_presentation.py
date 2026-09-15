"""Deterministic coverage for the narrow reversible presentation capability."""

from __future__ import annotations

import copy
import json

import pytest

from cu import actions, cli, config, exits, hypr, presentation, safety


class FakeWindowSeam:
    def __init__(self, world, tiled_rect, floating_rect=None):
        self.world = world
        self.tiled_rect = dict(tiled_rect)
        self.floating_rect = dict(floating_rect or tiled_rect)
        self.calls = []

    def toggle_floating(self, address):
        window = self.world["windows"][0]
        self.calls.append(("toggle-floating", address))
        if window["floating"]:
            window["floating"] = False
            window["logical_rect"] = dict(self.tiled_rect)
        else:
            window["floating"] = True
            window["logical_rect"] = dict(self.floating_rect)

    def move(self, address, x, y):
        window = self.world["windows"][0]
        self.calls.append(("move", address, x, y))
        window["logical_rect"]["x"] = x
        window["logical_rect"]["y"] = y

    def maximize(self, address, enabled):
        window = self.world["windows"][0]
        self.calls.append(("maximize", address, enabled))
        if enabled:
            self.unmaximized_rect = dict(window["logical_rect"])
            window["fullscreen"] = 1
            window["logical_rect"] = dict(self.world["monitors"][0]["logical_rect"])
        else:
            window["fullscreen"] = 0
            window["logical_rect"] = dict(self.unmaximized_rect)


def test_maximize_presents_on_its_own_output_and_restore_recovers(presentation_world):
    world, tiled_rect, store, cfg = presentation_world
    seam = FakeWindowSeam(world, tiled_rect)

    result = actions.present(
        cfg,
        world["windows"][0],
        mode="maximize",
        seam=seam,
        store=store,
        snapshot_reader=_reader(world),
        dry_run=False,
    )

    assert result["mode"] == "maximize"
    assert result["after"]["fullscreen"] == 1
    assert result["after"]["logical_rect"] == {"x": 0, "y": 0, "w": 100, "h": 80}
    assert result["steps"] == [{"operation": "maximize", "status": "verified"}]

    restored = actions.restore(
        cfg, result["token"], seam=seam, store=store, snapshot_reader=_reader(world), dry_run=False
    )

    assert restored["mode"] == "maximize"
    assert restored["after"]["fullscreen"] == 0
    assert restored["after"]["logical_rect"] == tiled_rect
    assert restored["steps"] == [{"operation": "unmaximize", "status": "verified"}]
    assert seam.calls == [("maximize", "0x1", True), ("maximize", "0x1", False)]


def test_maximize_refuses_another_output_and_an_already_maximized_window(presentation_world):
    world, tiled_rect, store, cfg = presentation_world
    world["monitors"].append(
        {"name": "SECOND", "scale": 1, "logical_rect": {"x": 100, "y": 0, "w": 100, "h": 80}}
    )
    seam = FakeWindowSeam(world, tiled_rect)
    present = dict(
        mode="maximize", seam=seam, store=store, snapshot_reader=_reader(world), dry_run=False
    )

    with pytest.raises(safety.Refused) as other_output:
        actions.present(cfg, world["windows"][0], output_name="SECOND", **present)
    assert other_output.value.code == exits.PRESENTATION_REFUSED

    world["windows"][0]["fullscreen"] = 1
    with pytest.raises(safety.Refused) as already:
        actions.present(cfg, world["windows"][0], **present)
    assert already.value.code == exits.PRESENTATION_REFUSED
    assert seam.calls == []


def test_a_maximize_the_compositor_ignores_refuses_without_a_token(presentation_world):
    world, tiled_rect, store, cfg = presentation_world

    class IgnoredMaximize(FakeWindowSeam):
        def maximize(self, address, enabled):
            self.calls.append(("maximize", address, enabled))

    seam = IgnoredMaximize(world, tiled_rect)
    with pytest.raises(safety.Refused) as caught:
        actions.present(
            cfg,
            world["windows"][0],
            mode="maximize",
            seam=seam,
            store=store,
            snapshot_reader=_reader(world),
            dry_run=False,
        )
    assert caught.value.code == exits.PRESENTATION_REFUSED
    assert caught.value.details["token_status"] == "invalidated"


@pytest.fixture
def presentation_world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "log_path", lambda _cfg: tmp_path / "actions.jsonl")
    monkeypatch.setattr(actions.safety, "check_session", lambda: None)
    monitor = {
        "name": "OUT",
        "scale": 1,
        "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 80},
    }
    tiled_rect = {"x": 10, "y": 5, "w": 40, "h": 30}
    window = {
        "address": "0x1",
        "pid": 10,
        "class": "fixture-app",
        "title": "fixture",
        "workspace": "1",
        "monitor": "OUT",
        "floating": False,
        "logical_rect": dict(tiled_rect),
        "visible": True,
        "hidden": False,
        "focused": True,
    }
    world = {"monitors": [monitor], "windows": [window]}
    store = presentation.RuntimeTokenStore(tmp_path / "tokens")
    cfg = copy.deepcopy(config.DEFAULTS)
    return world, tiled_rect, store, cfg


def _reader(world):
    return lambda: copy.deepcopy(world)


def test_hyprland_seam_dispatches_the_operation(monkeypatch):
    calls = []

    def record(code):
        calls.append(code)

    monkeypatch.setattr(hypr, "lua", record)

    seam = presentation.hyprland_seam()
    seam.toggle_floating("0x1")
    seam.move("0x1", 2500, 150)
    seam.maximize("0x1", True)
    seam.maximize("0x1", False)

    assert calls == [
        'hl.dispatch(hl.dsp.window.float({window = "address:0x1"}))',
        'hl.dispatch(hl.dsp.window.move({window = "address:0x1", x = 2500, y = 150}))',
        'hl.dispatch(hl.dsp.window.fullscreen({window = "address:0x1", '
        'mode = "maximized", action = "set"}))',
        'hl.dispatch(hl.dsp.window.fullscreen({window = "address:0x1", '
        'mode = "maximized", action = "unset"}))',
    ]


def test_ineffective_dispatcher_is_a_typed_noop(presentation_world, monkeypatch):
    world, tiled_rect, store, cfg = presentation_world
    calls = []
    monkeypatch.setattr(hypr, "lua", lambda code: calls.append(code) or "userdata")
    seam = presentation.hyprland_seam()

    with pytest.raises(safety.Refused) as caught:
        actions.present(
            cfg,
            world["windows"][0],
            seam=seam,
            store=store,
            snapshot_reader=_reader(world),
            dry_run=False,
        )

    assert caught.value.code == exits.PRESENTATION_REFUSED
    assert world["windows"][0]["floating"] is False
    assert world["windows"][0]["logical_rect"] == tiled_rect
    assert calls == ['hl.dispatch(hl.dsp.window.float({window = "address:0x1"}))']
    assert not list((store._configured_root).glob("*"))


def test_partial_readback_is_typed_and_reports_actual_state(presentation_world):
    world, tiled_rect, store, cfg = presentation_world

    class PartialMoveSeam(FakeWindowSeam):
        def move(self, address, x, y):
            super().move(address, x, y)
            self.world["windows"][0]["logical_rect"]["y"] += 1

    seam = PartialMoveSeam(
        world,
        tiled_rect,
        floating_rect={"x": 9, "y": 4, "w": 42, "h": 32},
    )

    with pytest.raises(safety.Refused) as caught:
        actions.present(
            cfg,
            world["windows"][0],
            seam=seam,
            store=store,
            snapshot_reader=_reader(world),
            dry_run=False,
        )

    assert caught.value.code == exits.PRESENTATION_PARTIAL
    assert caught.value.details["actual"]["logical_rect"]["y"] == 25
    assert caught.value.details["steps"] == [
        {"operation": "toggle-floating", "status": "verified"}
    ]


def _second_output(world):
    world["monitors"].append(
        {"name": "SECOND", "scale": 1, "logical_rect": {"x": 100, "y": 0, "w": 100, "h": 80}}
    )


def test_present_refuses_an_output_the_window_did_not_reach(presentation_world):
    world, tiled_rect, store, cfg = presentation_world
    _second_output(world)
    seam = FakeWindowSeam(world, tiled_rect, floating_rect={"x": 9, "y": 4, "w": 42, "h": 32})

    with pytest.raises(safety.Refused) as caught:
        actions.present(
            cfg,
            world["windows"][0],
            output_name="SECOND",
            seam=seam,
            store=store,
            snapshot_reader=_reader(world),
            dry_run=False,
        )

    assert caught.value.code == exits.PRESENTATION_PARTIAL
    assert caught.value.details["actual"]["monitor"] == "OUT"
    assert caught.value.details["actual"]["logical_rect"]["x"] == 129.0


def test_present_accepts_an_output_the_window_reached(presentation_world):
    world, tiled_rect, store, cfg = presentation_world
    _second_output(world)

    class OutputFollowingSeam(FakeWindowSeam):
        def move(self, address, x, y):
            super().move(address, x, y)
            self.world["windows"][0]["monitor"] = "SECOND" if x >= 100 else "OUT"

    seam = OutputFollowingSeam(world, tiled_rect, floating_rect={"x": 9, "y": 4, "w": 42, "h": 32})

    result = actions.present(
        cfg,
        world["windows"][0],
        output_name="SECOND",
        seam=seam,
        store=store,
        snapshot_reader=_reader(world),
        dry_run=False,
    )

    assert result["after"]["monitor"] == "SECOND"
    assert result["output"]["name"] == "SECOND"


def test_present_records_verified_before_and_after_and_restore_recovers(
    presentation_world,
):
    world, tiled_rect, store, cfg = presentation_world
    seam = FakeWindowSeam(
        world,
        tiled_rect,
        floating_rect={"x": 9, "y": 4, "w": 42, "h": 32},
    )

    result = actions.present(
        cfg,
        world["windows"][0],
        why="read the fixture",
        seam=seam,
        store=store,
        snapshot_reader=_reader(world),
        dry_run=False,
    )

    assert result["why"] == "read the fixture"
    assert result["before"]["floating"] is False
    assert result["after"]["floating"] is True
    assert result["after"]["logical_rect"]["x"] == 29.0
    assert result["changed"] is True
    assert [step["operation"] for step in result["steps"]] == [
        "toggle-floating",
        "move",
    ]
    token = result["token"]
    assert token and (store._configured_root / token).is_file()

    restored = actions.restore(
        cfg,
        token,
        why="finish the fixture",
        seam=seam,
        store=store,
        snapshot_reader=_reader(world),
        dry_run=False,
    )

    assert restored["why"] == "finish the fixture"
    assert restored["after"]["floating"] is False
    assert restored["after"]["logical_rect"] == tiled_rect
    assert restored["steps"] == [{"operation": "toggle-floating", "status": "verified"}]
    assert not (store._configured_root / token).exists()


def test_restore_refuses_user_change_without_overwriting_it(presentation_world):
    world, tiled_rect, store, cfg = presentation_world
    seam = FakeWindowSeam(world, tiled_rect, floating_rect={"x": 9, "y": 4, "w": 42, "h": 32})
    result = actions.present(
        cfg,
        world["windows"][0],
        seam=seam,
        store=store,
        snapshot_reader=_reader(world),
        dry_run=False,
    )
    world["windows"][0]["logical_rect"]["x"] = 7
    calls_before_restore = list(seam.calls)

    with pytest.raises(safety.Refused) as caught:
        actions.restore(
            cfg,
            result["token"],
            seam=seam,
            store=store,
            snapshot_reader=_reader(world),
            dry_run=False,
        )

    assert caught.value.code == exits.PRESENTATION_CHANGED
    assert world["windows"][0]["logical_rect"]["x"] == 7
    assert seam.calls == calls_before_restore
    assert caught.value.details["actual"]["logical_rect"]["x"] == 7
    assert not (store._configured_root / result["token"]).exists()


def test_restore_dry_run_rechecks_window_safety(presentation_world, monkeypatch):
    world, tiled_rect, store, cfg = presentation_world
    seam = FakeWindowSeam(world, tiled_rect, floating_rect={"x": 9, "y": 4, "w": 42, "h": 32})
    result = actions.present(
        cfg,
        world["windows"][0],
        seam=seam,
        store=store,
        snapshot_reader=_reader(world),
        dry_run=False,
    )
    calls = []

    def refuse(_cfg, _window):
        calls.append(True)
        raise safety.Refused(exits.DENIED_BY_POLICY, "window is denied")

    monkeypatch.setattr(actions.safety, "check_denylist", refuse)
    with pytest.raises(safety.Refused) as caught:
        actions.restore(
            cfg,
            result["token"],
            seam=seam,
            store=store,
            snapshot_reader=_reader(world),
            dry_run=True,
        )

    assert caught.value.code == exits.DENIED_BY_POLICY
    assert calls == [True]
    assert seam.calls == [("toggle-floating", "0x1"), ("move", "0x1", 29.0, 24.0)]


def test_floating_presentation_restores_by_move(presentation_world):
    world, tiled_rect, store, cfg = presentation_world
    original = {"x": 70, "y": 60, "w": 20, "h": 15}
    world["windows"][0]["floating"] = True
    world["windows"][0]["logical_rect"] = dict(original)
    seam = FakeWindowSeam(world, tiled_rect)

    result = actions.present(
        cfg,
        world["windows"][0],
        seam=seam,
        store=store,
        snapshot_reader=_reader(world),
        dry_run=False,
    )
    assert [step["operation"] for step in result["steps"]] == ["move"]
    assert world["windows"][0]["logical_rect"] != original

    actions.restore(
        cfg,
        result["token"],
        seam=seam,
        store=store,
        snapshot_reader=_reader(world),
        dry_run=False,
    )
    assert world["windows"][0]["floating"] is True
    assert world["windows"][0]["logical_rect"] == original


def test_partly_off_output_remains_extent_and_reachability_fact(presentation_world):
    world, tiled_rect, store, cfg = presentation_world
    world["monitors"][0]["logical_rect"] = {"x": 0, "y": 0, "w": 30, "h": 20}
    window = world["windows"][0]
    window["floating"] = True
    window["logical_rect"] = {"x": 70, "y": 60, "w": 40, "h": 30}
    seam = FakeWindowSeam(world, tiled_rect)

    result = actions.present(
        cfg,
        window,
        output_name="OUT",
        seam=seam,
        store=store,
        snapshot_reader=_reader(world),
        dry_run=False,
    )

    assert result["after"]["logical_rect"] == {"x": 0.0, "y": 0.0, "w": 40, "h": 30}
    assert result["after"]["extent"]["status"] == "partially-outside"
    assert result["after"]["reachability"]["status"] == "partially-reachable"
    assert result["after"]["extent"]["edge_excess"]["right"] == 10
    assert result["after"]["reachability"]["edge_excess"]["right"] == 10


def test_stale_identity_refuses_and_invalidates_token(presentation_world):
    world, tiled_rect, store, cfg = presentation_world
    seam = FakeWindowSeam(world, tiled_rect, floating_rect={"x": 9, "y": 4, "w": 42, "h": 32})
    result = actions.present(
        cfg,
        world["windows"][0],
        seam=seam,
        store=store,
        snapshot_reader=_reader(world),
        dry_run=False,
    )
    world["windows"][0]["address"] = "0x2"

    with pytest.raises(safety.Refused) as caught:
        actions.restore(
            cfg,
            result["token"],
            seam=seam,
            store=store,
            snapshot_reader=_reader(world),
            dry_run=False,
        )

    assert caught.value.code == exits.PRESENTATION_STALE
    assert caught.value.details["actual"] is None
    assert not (store._configured_root / result["token"]).exists()


def test_no_request_capture_remains_observational(presentation_world, monkeypatch):
    world, _tiled_rect, _store, _cfg = presentation_world
    dispatches = []
    monkeypatch.setattr(actions.hypr, "lua", dispatches.append)
    before = copy.deepcopy(world["windows"])

    assert world["windows"] == before
    assert dispatches == []


def test_present_receipt_is_json_serializable(presentation_world):
    world, tiled_rect, store, cfg = presentation_world
    seam = FakeWindowSeam(world, tiled_rect, floating_rect={"x": 9, "y": 4, "w": 42, "h": 32})
    result = actions.present(
        cfg,
        world["windows"][0],
        seam=seam,
        store=store,
        snapshot_reader=_reader(world),
        dry_run=False,
    )

    json.dumps(result)


def test_cli_exposes_present_and_restore_receipts(presentation_world, monkeypatch, capsys):
    world, tiled_rect, store, cfg = presentation_world
    seam = FakeWindowSeam(world, tiled_rect, floating_rect={"x": 9, "y": 4, "w": 42, "h": 32})
    monkeypatch.setattr(cli.observe, "snapshot", _reader(world))
    monkeypatch.setattr(cli.config, "load", lambda: cfg)
    monkeypatch.setattr(cli.actions.presentation, "hyprland_seam", lambda: seam)
    monkeypatch.setattr(cli.actions.presentation, "RuntimeTokenStore", lambda: store)

    assert cli.main(["present", "active", "--why", "inspect", "--json"]) == exits.OK
    presented = json.loads(capsys.readouterr().out)
    assert presented["action"] == "present"
    assert presented["why"] == "inspect"

    assert cli.main(["restore", presented["token"], "--why", "done", "--json"]) == exits.OK
    restored = json.loads(capsys.readouterr().out)
    assert restored["action"] == "restore"
    assert restored["why"] == "done"
