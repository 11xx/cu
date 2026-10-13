import math
from itertools import pairwise

import pytest

from cu.absolute_motion import AXIS_STEPS, Layout, Output, plan
from cu.motion import PathError


def test_axis_quantization_uses_layout_origin_and_pixel_centers():
    layout = Layout((Output(-1920, -200, 1920, 1080), Output(0, 0, 2560, 1440)))
    left, top, width, height = layout.bounds
    for x, y in ((-1920, -200), (-1, 879), (0, 0), (2559, 1439), (300, 300)):
        ax, ay = layout.encode(x, y)
        assert 0 <= ax < AXIS_STEPS and 0 <= ay < AXIS_STEPS
        decoded_x = left + (ax + 0.5) / AXIS_STEPS * width - 0.5
        decoded_y = top + (ay + 0.5) / AXIS_STEPS * height - 0.5
        assert abs(decoded_x - x) <= width / (2 * AXIS_STEPS)
        assert abs(decoded_y - y) <= height / (2 * AXIS_STEPS)


@pytest.mark.parametrize("point", [(100, 100), (-1, 0), (201, 0), (0, math.nan)])
def test_axis_encoding_refuses_gaps_edges_and_nonfinite_points(point):
    layout = Layout((Output(0, 0, 100, 100), Output(101, 0, 100, 100)))
    with pytest.raises(PathError):
        layout.encode(*point)


def test_entire_route_refuses_a_gap_even_between_widely_spaced_samples():
    layout = Layout((Output(0, 0, 100, 100), Output(101, 0, 100, 100)))
    with pytest.raises(PathError, match="gap"):
        plan(layout, (0, 10), (150, 10), duration=0.001, refresh_hz=60)


def test_adjacent_outputs_cover_a_route_without_a_gap():
    layout = Layout((Output(0, 0, 100, 100), Output(100, 0, 100, 100)))
    assert plan(layout, (0, 10), (199, 10), duration=0.001, refresh_hz=60)[-1].x == 199


def test_refresh_multiple_keeps_deadlines_and_exact_endpoint():
    layout = Layout((Output(0, 0, 1920, 1080),))
    points = plan(layout, (10, 20), (1000, 500), duration=0.08, refresh_hz=165.08)
    assert len(points) == math.ceil(0.08 * 165.08 * 3) + 1
    assert points[0].at == 0
    assert points[-1].at == 0.08
    assert (points[-1].x, points[-1].y) == (1000, 500)
    for first, second in pairwise(points[:-1]):
        assert second.at - first.at == pytest.approx(1 / (165.08 * 3))
    assert all(first.at < second.at for first, second in pairwise(points))
    assert all(first.x <= second.x for first, second in pairwise(points))


def test_easing_changes_positions_without_changing_deadlines():
    layout = Layout((Output(0, 0, 1000, 1000),))
    linear = plan(layout, (0, 0), (900, 900), duration=1, refresh_hz=60, eased=False)
    eased = plan(layout, (0, 0), (900, 900), duration=1, refresh_hz=60)
    assert [point.at for point in linear] == [point.at for point in eased]
    assert eased[1].x < linear[1].x
    assert eased[-2].x > linear[-2].x
    assert eased[-1] == linear[-1]


def test_duplicate_positions_keep_frame_and_event_deadlines():
    layout = Layout((Output(0, 0, 1000, 1000),))
    points = plan(layout, (1, 1), (1, 1), duration=0.01, refresh_hz=100, multiple=3)
    assert len(points) == 4
    assert len({(point.abs_x, point.abs_y) for point in points}) == 1
    assert [point.at for point in points] == [0, 1 / 300, 2 / 300, 0.01]


@pytest.mark.parametrize("overrides", [
    {"duration": 0}, {"duration": math.nan}, {"duration": 11},
    {"refresh_hz": 0}, {"refresh_hz": math.inf}, {"refresh_hz": 1001},
    {"multiple": True}, {"multiple": 0}, {"multiple": 9}, {"eased": "yes"},
    {"duration": 10, "refresh_hz": 1000, "multiple": 8},
])
def test_planner_refuses_invalid_rates_and_sample_budgets(overrides):
    layout = Layout((Output(0, 0, 100, 100),))
    arguments = {"duration": 0.08, "refresh_hz": 60, **overrides}
    with pytest.raises(PathError):
        plan(layout, (1, 1), (50, 50), **arguments)


@pytest.mark.parametrize("factory", [
    lambda: Layout(()), lambda: Layout([Output(0, 0, 100, 100)]),
    lambda: Output(0, 0, 0, 1), lambda: Output(math.nan, 0, 1, 1),
    lambda: Output(1e308, 0, 1e308, 1),
])
def test_invalid_output_geometry_refuses(factory):
    with pytest.raises(PathError):
        factory()
