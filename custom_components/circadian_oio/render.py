"""Render logic: maps user intent + current time to (brightness, CCT)."""
from __future__ import annotations

from dataclasses import dataclass
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
)


# --- Tunable settings ---------------------------------------------------------

@dataclass(frozen=True)
class RenderSettings:
    """User-tunable inputs to the render math.

    All fields default to the module constants, so render(...) with no settings
    reproduces the hard-coded behavior. light.py builds one of these from the
    config entry's options and passes it in. Kept here (not in light.py) so the
    render math stays pure and unit-testable.
    """

    late_night_start_min: int = LATE_NIGHT_START_MIN
    late_night_end_min: int = LATE_NIGHT_END_MIN
    transition_lead_min: int = NINEPM_TRANSITION_LEAD_MIN
    late_night_max_b_pct: float = LATE_NIGHT_MAX_B_PCT
    max_cct_day: int = MAX_CCT_DAY
    # Bulb floor. Raise min_brightness if the bulb switches off at the bottom of
    # the range; raise min_cct to the warmest color the bulb can actually render.
    min_brightness: int = MIN_BRIGHTNESS
    min_cct: int = MIN_CCT


DEFAULT_SETTINGS = RenderSettings()


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

def _in_late_night(time_min: int, s: RenderSettings) -> bool:
    """Late-night wraps midnight, so the test is start..24h OR 0..end."""
    if s.late_night_start_min <= s.late_night_end_min:
        return s.late_night_start_min <= time_min < s.late_night_end_min
    return time_min >= s.late_night_start_min or time_min < s.late_night_end_min


def _in_ninepm_transition(time_min: int, s: RenderSettings) -> bool:
    if s.transition_lead_min <= 0:
        return False
    return (
        s.late_night_start_min - s.transition_lead_min
        <= time_min
        < s.late_night_start_min
    )


def compute_caps(
    now: datetime,
    next_sunset: Optional[datetime],
    is_day: bool,
    settings: RenderSettings = DEFAULT_SETTINGS,
) -> tuple[float, int]:
    """Return (max_brightness_pct, max_cct) for the given moment.

    Takes the most restrictive cap across overlapping zones (late-night,
    pre-night transition, sunset transition, evening, day).
    """
    s = settings
    time_min = now.hour * 60 + now.minute

    candidates_b: list[float] = [100.0]
    candidates_cct: list[float] = [float(s.max_cct_day)]

    in_late_night = _in_late_night(time_min, s)
    in_ninepm = _in_ninepm_transition(time_min, s)

    # Late-night zone.
    if in_late_night:
        candidates_b.append(s.late_night_max_b_pct)
        candidates_cct.append(curve_cct(s.late_night_max_b_pct))

    # Pre-night transition (linear into the late-night cap).
    if in_ninepm:
        frac = (s.late_night_start_min - time_min) / s.transition_lead_min
        mb = s.late_night_max_b_pct + frac * (100.0 - s.late_night_max_b_pct)
        candidates_b.append(mb)
        candidates_cct.append(curve_cct(mb))

    # Sunset transition (CCT cap slides max_cct_day → evening cap over the lead).
    if is_day and next_sunset is not None and s.transition_lead_min > 0:
        mins_to_sunset = (next_sunset - now).total_seconds() / 60.0
        if 0 <= mins_to_sunset <= s.transition_lead_min:
            frac = mins_to_sunset / s.transition_lead_min
            candidates_cct.append(
                EVENING_MAX_CCT + frac * (s.max_cct_day - EVENING_MAX_CCT)
            )

    # Evening: sun is down but we're not yet in the pre-night transition or
    # late night.
    if not is_day and not in_late_night and not in_ninepm:
        candidates_cct.append(float(EVENING_MAX_CCT))

    return min(candidates_b), int(round(min(candidates_cct)))


# --- Intent → output mapping --------------------------------------------------

def render(
    intent: float,
    now: datetime,
    next_sunset: Optional[datetime],
    is_day: bool,
    settings: RenderSettings = DEFAULT_SETTINGS,
) -> tuple[int, int]:
    """Compute underlying bulb (brightness 1–255, cct K) from user intent."""
    intent = max(0.0, min(100.0, intent))

    min_brightness = settings.min_brightness
    min_cct = settings.min_cct

    max_b_pct, max_cct = compute_caps(now, next_sunset, is_day, settings)
    floor_b_pct = (min_brightness / MAX_BRIGHTNESS) * 100.0
    curve_at_floor = curve_cct(floor_b_pct)
    curve_at_max = curve_cct(max_b_pct)

    # Phase C only exists when there's headroom above the natural curve top.
    has_phase_c = max_cct > curve_at_max + 50
    phase_b_end = PHASE_B_END_WITH_C if has_phase_c else 100.0

    if intent < PHASE_A_END:
        # Phase A: at floor brightness, walk CCT from min_cct to curve_at_floor
        frac = intent / PHASE_A_END
        b_pct = floor_b_pct
        cct = min_cct + frac * (curve_at_floor - min_cct)
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

    brightness = max(min_brightness, min(MAX_BRIGHTNESS, round(b_pct * 2.55)))
    cct_int = max(min_cct, min(settings.max_cct_day, int(round(cct))))
    return brightness, cct_int
