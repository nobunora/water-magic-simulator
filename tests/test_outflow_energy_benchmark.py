import numpy as np
import pytest

from scripts.benchmark_outflow_energy import accumulate_interval, outflow_rates


@pytest.mark.parametrize("resolution", [1., .5])
@pytest.mark.parametrize("axis", [0, 1])
def test_uniform_water_has_same_boundary_energy_after_subdivision(resolution, axis):
    shape = (int(3 / resolution),) * 2
    h = np.ones(shape)
    q, power = outflow_rates(h, h * (2 if axis == 1 else 0), h * (2 if axis == 0 else 0), h > 0, resolution)
    assert q[1, 1] == pytest.approx(2)  # 2 m/s × 1m depth × 1m boundary.
    assert power[1, 1] == pytest.approx(4000)  # 0.5 × 1000 × 2 × 2² J/s.
    assert power[1, 1] * 3 == pytest.approx(12000)
    assert np.all((q[:, -1] if axis == 1 else q[-1, :]) == 0)  # No guessed exterior-face flux.


def test_direction_and_obstacles_assign_outflow_to_the_donor():
    h = np.ones((3, 3))
    active = h > 0
    active[1, 0] = False
    q, _ = outflow_rates(h, h * -2, h * 0, active, 1)
    assert q[1, 1] == 0
    assert q[1, 2] == 2
    assert np.all(q[:, 0] == 0)


def test_reused_memory_buffers_match_disk_interval_sum():
    totals = np.zeros((2, 2, 2))
    work = np.zeros((4, 2, 2))
    rates = [(np.full((2, 2), q), np.full((2, 2), p)) for q, p in [(2., 4000.), (3., 9000.), (1., 1000.)]]
    work[:2] = rates[0]
    expected = np.zeros_like(totals)
    totals_address, work_address = totals.ctypes.data, work.ctypes.data
    for previous, current, dt in zip(rates, rates[1:], [2., 3.]):
        expected += (np.asarray(previous) + np.asarray(current)) * (.5 * dt)
        accumulate_interval(totals, work, *current, dt)
    np.testing.assert_array_equal(totals, expected)
    np.testing.assert_array_equal(work[:2], rates[-1])
    assert totals.ctypes.data == totals_address
    assert work.ctypes.data == work_address
    assert totals[0, 0, 0] == 11
    assert totals[1, 0, 0] == 28000
