"""Pure sustained-motion path and timer-reset probe coverage."""

from __future__ import annotations

import pytest

from cu import motion

BOUNDS = {"x": 0, "y": 0, "w": 100, "h": 100}


def test_line_includes_both_endpoints_and_stays_bounded():
    points = motion.line((10, 20), (90, 80), 4)
    assert points[0] == {"x": 10.0, "y": 20.0}
    assert points[-1] == {"x": 90.0, "y": 80.0}
    motion.validate(points, BOUNDS)


def test_circle_is_closed_and_has_bounded_samples():
    points = motion.circle((50, 50), 20, 1, 12)
    assert points[0] == pytest.approx(points[-1])
    assert len(points) == 13
    motion.validate(points, BOUNDS)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: motion.line((0, 0), (1, 1), 0),
        lambda: motion.line((0, 0), (1, 1), motion.MAX_PATH_POINTS + 1),
        lambda: motion.circle((50, 50), 0, 1, 8),
        lambda: motion.circle((50, 50), 2, 1, 2),
    ],
)
def test_invalid_path_bounds_are_refused(factory):
    with pytest.raises(motion.PathError):
        factory()


def test_validation_checks_the_complete_sampled_route():
    with pytest.raises(motion.PathError, match="safe region"):
        motion.validate(motion.line((10, 10), (110, 10), 4), BOUNDS)


class TimerResetFixture:
    """A tiny model of a control whose reveal expires without pointer motion."""

    def __init__(self, expiry_ticks=2):
        self.expiry_ticks = expiry_ticks
        self.ticks_since_motion = 0
        self.revealed = False

    def native_motion(self):
        self.revealed = True
        self.ticks_since_motion = 0

    def tick(self):
        self.ticks_since_motion += 1
        if self.ticks_since_motion >= self.expiry_ticks:
            self.revealed = False

    def capture(self):
        return {"revealed": self.revealed}


def test_stationary_hover_expires_but_bounded_motion_keeps_capture_visible():
    stationary = TimerResetFixture()
    stationary.native_motion()
    stationary.tick()
    stationary.tick()
    assert stationary.capture() == {"revealed": False}

    sustained = TimerResetFixture()
    for _ in range(4):
        sustained.native_motion()
        sustained.tick()
    assert sustained.capture() == {"revealed": True}
