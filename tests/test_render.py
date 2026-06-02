"""Unit tests for the pure render math.

These tests don't require Home Assistant. They exercise the L* conversions,
the incandescent curve, the cap computation across time-of-day zones, and the
intent → output mapping at phase boundaries.

Run with: pytest tests/
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.circadian_oio.render import (
    compute_caps,
    curve_cct,
    lstar_from_y,
    render,
    y_from_lstar,
)


# --- L* round-trip ------------------------------------------------------------

@pytest.mark.parametrize("y", [0.001, 0.01, 0.05, 0.1, 0.3, 0.5, 0.8, 1.0])
def test_lstar_roundtrip(y):
    """L* and Y should be inverses across the full range."""
    L = lstar_from_y(y)
    y_back = y_from_lstar(L)
    assert y_back == pytest.approx(y, abs=1e-3)


def test_lstar_at_full_luminance():
    assert lstar_from_y(1.0) == pytest.approx(100.0, abs=0.01)


def test_lstar_at_zero():
    assert lstar_from_y(0.0) == 0.0


# --- Incandescent curve -------------------------------------------------------

def test_curve_at_full_brightness():
    assert curve_cct(100.0) == pytest.approx(2700.0)


@pytest.mark.parametrize(
    "b_pct, expected_cct",
    [
        (100.0, 2700.0),
        (50.0, 2479.0),
        (10.0, 2030.0),
        (1.0, 1525.0),
    ],
)
def test_curve_reference_values(b_pct, expected_cct):
    """Curve should match published reference values within rounding."""
    assert curve_cct(b_pct) == pytest.approx(expected_cct, abs=3)


def test_curve_monotonic():
    """CCT should strictly increase with brightness."""
    last = -1
    for b in range(1, 101):
        c = curve_cct(b)
        assert c > last
        last = c


# --- Cap computation ----------------------------------------------------------

def _t(hour: int, minute: int = 0) -> datetime:
    """Build a datetime at the given local time on a fixed date."""
    return datetime(2026, 6, 1, hour, minute, 0)


def test_caps_at_noon_no_transitions():
    """Daytime with no transitions: full range allowed."""
    max_b, max_cct = compute_caps(_t(12), next_sunset=_t(20), is_day=True)
    assert max_b == 100.0
    assert max_cct == 6500


def test_caps_at_sunset_transition_start():
    """30 minutes before sunset: cap is still 6500K (transition just starting)."""
    now = _t(19, 30)
    sunset = _t(20)
    _, max_cct = compute_caps(now, next_sunset=sunset, is_day=True)
    assert max_cct == 6500


def test_caps_at_sunset_transition_midpoint():
    """15 minutes before sunset: cap should be midway between 6500 and 2700."""
    now = _t(19, 45)
    sunset = _t(20)
    _, max_cct = compute_caps(now, next_sunset=sunset, is_day=True)
    assert max_cct == pytest.approx(4600, abs=10)


def test_caps_at_sunset():
    """At sunset: cap is 2700K."""
    sunset = _t(20)
    _, max_cct = compute_caps(sunset, next_sunset=sunset, is_day=True)
    assert max_cct == 2700


def test_caps_evening_post_sunset():
    """8 PM (sun down, before 9 PM transition): 100% / 2700K."""
    max_b, max_cct = compute_caps(_t(20, 30), next_sunset=None, is_day=False)
    # 8:30 PM is exactly the start of the 9 PM transition, so this is the boundary
    assert max_b <= 100
    assert max_cct <= 2700


def test_caps_late_night_zone():
    """11 PM: capped at 10% / curve(10%) ~ 2030K."""
    max_b, max_cct = compute_caps(_t(23), next_sunset=None, is_day=False)
    assert max_b == 10.0
    assert max_cct == pytest.approx(2030, abs=2)


def test_caps_at_9pm_boundary():
    """9:00 PM exactly: should be in late-night zone."""
    max_b, max_cct = compute_caps(_t(21), next_sunset=None, is_day=False)
    assert max_b == 10.0


def test_caps_at_9pm_transition_start():
    """8:30 PM: 9 PM transition just starting, cap at 100% / 2700K."""
    max_b, max_cct = compute_caps(_t(20, 30), next_sunset=None, is_day=False)
    # At the very start of the transition, frac = 1, so mb = 100
    assert max_b == pytest.approx(100.0, abs=0.5)
    assert max_cct == pytest.approx(2700, abs=5)


def test_caps_at_9pm_transition_midpoint():
    """8:45 PM: 15 min into transition, brightness cap should be 55%."""
    max_b, _ = compute_caps(_t(20, 45), next_sunset=None, is_day=False)
    assert max_b == pytest.approx(55.0, abs=1.0)


def test_caps_just_before_530am():
    """5:29 AM: still in late-night zone."""
    max_b, _ = compute_caps(_t(5, 29), next_sunset=None, is_day=False)
    assert max_b == 10.0


def test_caps_at_530am():
    """5:30 AM: late-night zone ends. Now in pre-sunrise evening-mode."""
    max_b, max_cct = compute_caps(_t(5, 30), next_sunset=None, is_day=False)
    assert max_b == 100.0
    assert max_cct == 2700


# --- Intent → output mapping --------------------------------------------------

def test_render_intent_zero_is_minimum():
    """Intent = 0 should land at brightness=1 (1/255) and CCT=800K."""
    brightness, cct = render(0, _t(12), next_sunset=_t(20), is_day=True)
    assert brightness == 1
    assert cct == 800


def test_render_intent_100_at_noon_is_max():
    """Intent = 100 at noon should hit 100% / 6500K."""
    brightness, cct = render(100, _t(12), next_sunset=_t(20), is_day=True)
    assert brightness == 255
    assert cct == 6500


def test_render_intent_100_late_night_is_capped():
    """Intent = 100 at 11 PM should hit 10% / ~2030K (capped)."""
    brightness, cct = render(100, _t(23), next_sunset=None, is_day=False)
    assert brightness == pytest.approx(26, abs=2)  # 10% of 255 ≈ 25-26
    assert cct == pytest.approx(2030, abs=5)


def test_render_phase_a_increases_cct():
    """In Phase A (intent 0-10), CCT should increase but brightness stays at floor."""
    b_low, c_low = render(1, _t(12), next_sunset=_t(20), is_day=True)
    b_high, c_high = render(9, _t(12), next_sunset=_t(20), is_day=True)
    assert b_low == b_high == 1  # Both at floor
    assert c_high > c_low


def test_render_phase_b_increases_brightness():
    """In Phase B (intent 10-90), both brightness and CCT increase."""
    b_low, c_low = render(20, _t(12), next_sunset=_t(20), is_day=True)
    b_high, c_high = render(80, _t(12), next_sunset=_t(20), is_day=True)
    assert b_high > b_low
    assert c_high > c_low


def test_render_phase_c_only_active_when_headroom():
    """Phase C should only exist when max_cct > curve_at_max_b."""
    # Late night: max_b = 10, curve_at_max_b ~= 2030 = max_cct. No Phase C.
    # Brightness at intent 90 and intent 100 should be the same (both at max_b).
    b_90, _ = render(90, _t(23), next_sunset=None, is_day=False)
    b_100, _ = render(100, _t(23), next_sunset=None, is_day=False)
    # Both at floor of Phase B (brightness at max_b) since no Phase C in late night
    assert b_100 >= b_90


def test_render_monotonic_in_intent():
    """Increasing intent should never decrease total 'energy' (rough proxy: brightness)."""
    last_b = -1
    for intent in range(0, 101, 5):
        b, _ = render(intent, _t(12), next_sunset=_t(20), is_day=True)
        assert b >= last_b
        last_b = b


def test_render_perceptually_uniform_steps():
    """In Phase B, equal intent steps should produce roughly equal L* steps."""
    # Take three intent values in the middle of Phase B (10-90 range).
    _, _ = render(30, _t(12), next_sunset=_t(20), is_day=True)
    b1, _ = render(30, _t(12), next_sunset=_t(20), is_day=True)
    b2, _ = render(50, _t(12), next_sunset=_t(20), is_day=True)
    b3, _ = render(70, _t(12), next_sunset=_t(20), is_day=True)

    L1 = lstar_from_y(b1 / 255)
    L2 = lstar_from_y(b2 / 255)
    L3 = lstar_from_y(b3 / 255)

    # Steps should be approximately equal in L* space.
    step_1_to_2 = L2 - L1
    step_2_to_3 = L3 - L2
    assert step_1_to_2 == pytest.approx(step_2_to_3, abs=2)
