"""Render logic: maps user intent + current time to (brightness, CCT)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .const import (
    BASE_CCT,
    EVENING_MAX_CCT,
    INCANDESCENT_EXP,
    LATE_NIGHT_END_MIN,
    LATE_NIGHT_MAX_B_PCT,
    LATE_NIGHT_START_MIN,
    MAX_BRIGHTNESS,
    MAX_CCT_DAY,
    MIN_BRIGHTNESS,
    MIN_CCT,
    NINEPM_TRANSITION_LEAD_MIN,
    PHASE_A_END,
    PHASE_B_END_WITH_C,
    SUNSET_TRANSITION_LEAD_MIN,
)


# --- Color math ---------------------------------------------------------------

def lstar_from_y(y: float) -> float:
    """CIE L* from relative luminance Y (0–1)."""
    if y <= 0:
        return 0.0
    if y > 0.008856:
        return 116.0 * (y ** (1.0 / 3.0)) - 16.0
    return 903.3 * y


def y_from_lstar(L: float) -> float:
    """Relative luminance Y (0–1) from CIE L*."""
    if L > 8.0:
        return ((L + 16.0) / 116.0) ** 3
    return L / 903.3


def curve_cct(b_pct: float) -> float:
    """Incandescent CCT for a given brightness percentage."""
    if b_pct <= 0:
        return MIN_CCT
    return BASE_CCT * ((b_pct / 100.0) ** INCANDESCENT_EXP)


# --- Time-based caps ----------------------------------------------------------

def _in_late_night(time_min: int) -> bool:
    return time_min >= LATE_NIGHT_START_MIN or time_min < LATE_NIGHT_END_MIN


def _in_ninepm_transition(time_min: int) -> bool:
    return (
        LATE_NIGHT_START_MIN - NINEPM_TRANSITION_LEAD_MIN
        <= time_min
        < LATE_NIGHT_START_MIN
    )


def compute_caps(
    now: datetime,
    next_sunset: Optional[datetime],
    is_day: bool,
) -> tuple[float, int]:
    """Return (max_brightness_pct, max_cct) for the given moment.

    Takes the most restrictive cap across overlapping zones (late-night,
    9 PM transition, sunset transition, evening, day).
    """
    time_min = now.hour * 60 + now.minute

    candidates_b: list[float] = [100.0]
    candidates_cct: list[float] = [float(MAX_CCT_DAY)]

    in_late_night = _in_late_night(time_min)
    in_ninepm = _in_ninepm_transition(time_min)

    # Late-night zone: 9 PM – 5:30 AM
    if in_late_night:
        candidates_b.append(LATE_NIGHT_MAX_B_PCT)
        candidates_cct.append(curve_cct(LATE_NIGHT_MAX_B_PCT))

    # 9 PM transition (linear into late-night cap)
    if in_ninepm:
        frac = (LATE_NIGHT_START_MIN - time_min) / NINEPM_TRANSITION_LEAD_MIN
        mb = LATE_NIGHT_MAX_B_PCT + frac * (100.0 - LATE_NIGHT_MAX_B_PCT)
        candidates_b.append(mb)
        candidates_cct.append(curve_cct(mb))

    # Sunset transition (CCT cap slides 6500 → 2700 over 30 min)
    if is_day and next_sunset is not None:
        mins_to_sunset = (next_sunset - now).total_seconds() / 60.0
        if 0 <= mins_to_sunset <= SUNSET_TRANSITION_LEAD_MIN:
            frac = mins_to_sunset / SUNSET_TRANSITION_LEAD_MIN
            candidates_cct.append(
                EVENING_MAX_CCT + frac * (MAX_CCT_DAY - EVENING_MAX_CCT)
            )

    # Evening: sun is down but we're not yet in 9 PM transition or late night
    if not is_day and not in_late_night and not in_ninepm:
        candidates_cct.append(float(EVENING_MAX_CCT))

    return min(candidates_b), int(round(min(candidates_cct)))


# --- Intent → output mapping --------------------------------------------------

def render(
    intent: float,
    now: datetime,
    next_sunset: Optional[datetime],
    is_day: bool,
) -> tuple[int, int]:
    """Compute underlying bulb (brightness 1–255, cct K) from user intent."""
    intent = max(0.0, min(100.0, intent))

    max_b_pct, max_cct = compute_caps(now, next_sunset, is_day)
    floor_b_pct = (MIN_BRIGHTNESS / MAX_BRIGHTNESS) * 100.0
    curve_at_floor = curve_cct(floor_b_pct)
    curve_at_max = curve_cct(max_b_pct)

    # Phase C only exists when there's headroom above the natural curve top.
    has_phase_c = max_cct > curve_at_max + 50
    phase_b_end = PHASE_B_END_WITH_C if has_phase_c else 100.0

    if intent < PHASE_A_END:
        # Phase A: at floor brightness, walk CCT from MIN_CCT to curve_at_floor
        frac = intent / PHASE_A_END
        b_pct = floor_b_pct
        cct = MIN_CCT + frac * (curve_at_floor - MIN_CCT)
    elif has_phase_c and intent >= phase_b_end:
        # Phase C: at max brightness, walk CCT from curve_at_max to max_cct
        frac = (intent - phase_b_end) / (100.0 - phase_b_end)
        b_pct = max_b_pct
        cct = curve_at_max + frac * (max_cct - curve_at_max)
    else:
        # Phase B: L*-uniform brightness ramp, CCT follows curve (capped)
        frac = (intent - PHASE_A_END) / (phase_b_end - PHASE_A_END)
        L_floor = lstar_from_y(floor_b_pct / 100.0)
        L_max = lstar_from_y(max_b_pct / 100.0)
        L = L_floor + frac * (L_max - L_floor)
        b_pct = y_from_lstar(L) * 100.0
        cct = min(curve_cct(b_pct), max_cct)

    brightness = max(MIN_BRIGHTNESS, min(MAX_BRIGHTNESS, round(b_pct * 2.55)))
    cct_int = max(MIN_CCT, min(MAX_CCT_DAY, int(round(cct))))
    return brightness, cct_int
