import itertools
import json
import math

import pytest

from cu import actions, hypr, motion


def test_a_batched_reply_parses_each_query_in_order(monkeypatch):
    sent = []
    reply = "\n" + json.dumps({"x": 1, "y": 2}) + "\n\n\n\n" + json.dumps([{"id": 0}]) + "\n"
    monkeypatch.setattr(hypr, "_request", lambda request: sent.append(request) or reply)
    assert hypr.batch(["cursorpos", "monitors"]) == [{"x": 1, "y": 2}, [{"id": 0}]]
    assert sent == ["[[BATCH]]j/cursorpos;j/monitors"]


def test_a_single_query_skips_the_batch_prefix_and_a_non_json_reply_refuses(monkeypatch):
    sent = []
    monkeypatch.setattr(hypr, "_request", lambda request: sent.append(request) or '{"x": 3}')
    assert hypr.query("cursorpos") == {"x": 3}
    assert sent == ["j/cursorpos"]
    monkeypatch.setattr(hypr, "_request", lambda request: "unknown request")
    with pytest.raises(hypr.HyprError, match="non-JSON"):
        hypr.query("cursorpos")


def test_an_eased_route_leaves_from_rest_and_settles_into_its_endpoint():
    route = motion.eased_line((0, 0), (1000, 0), 20)
    xs = [point["x"] for point in route]
    assert xs[0] == 0 and xs[-1] == 1000
    steps = [later - earlier for earlier, later in itertools.pairwise(xs)]
    assert all(step >= 0 for step in steps)
    assert steps[0] < steps[len(steps) // 2] and steps[-1] < steps[len(steps) // 2]


@pytest.mark.parametrize(
    ("requested", "frame", "expected"),
    [
        (30.0, {"monitor": {"name": "A"}}, 30.0),
        (None, {"monitor": {"name": "A"}}, 165.08),
        (None, {"monitor": "B"}, 59.95),
        (None, {}, 165.08),
        (None, {"monitor": {"name": "FAST"}}, actions.MAX_MOVE_HZ),
        (None, {"monitor": {"name": "UNKNOWN-RATE"}}, actions.FALLBACK_MOVE_HZ),
    ],
)
def test_the_default_rate_follows_the_output_the_movement_is_on(requested, frame, expected):
    monitors = [
        {"name": "A", "refresh_rate": 165.08,
         "logical_rect": {"x": 0, "y": 0, "w": 100, "h": 100}},
        {"name": "B", "refresh_rate": 59.95,
         "logical_rect": {"x": 100, "y": 0, "w": 100, "h": 100}},
        {"name": "FAST", "refresh_rate": 1000.0,
         "logical_rect": {"x": 0, "y": 200, "w": 1, "h": 1}},
        {"name": "UNKNOWN-RATE", "logical_rect": {"x": 0, "y": 300, "w": 1, "h": 1}},
    ]
    assert actions._motion_hz(requested, frame, monitors, {"x": 10, "y": 10}) == expected


def test_one_event_never_moves_the_cursor_further_than_the_step_bound():
    gain = actions._PointerGain()
    gain.submitted, gain.observed = 100.0, 40.0
    dx, dy = gain.delta(3000.0, 4000.0)
    assert math.hypot(dx, dy) * 0.4 <= actions.MAX_CURSOR_STEP + 1
    assert dx / dy == pytest.approx(0.75, rel=0.01)
