"""Render logic: maps user intent + current time to (brightness, CCT)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .const import (
    BASE_CCT,
    DAY_BASE_CCT,
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
    max_cct_day: int = MAX_CCT_DAY        # arc peak at solar noon
    day_base_cct: int = DAY_BASE_CCT      # arc shoulders (sunrise / pre-sunset)
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


def _arc_shape(t: float) -> float:
    """Smooth 0 -> 1 -> 0 bump on [0, 1], peaking at 0.5. The square root lifts
    the shoulders so most of the day is spent close to the peak (the requested
    bias toward the high end), while the slope is zero only at the single peak —
    so the value is always moving and never sits on a flat plateau."""
    t = max(0.0, min(1.0, t))
    return math.sqrt(math.sin(math.pi * t))


def _in_morning_transition(time_min: int, s: RenderSettings) -> bool:
    """The brightness ramp just after night ends (symmetric to the pre-night
    ramp): the cap eases back up instead of jumping from the night cap to 100%."""
    if s.transition_lead_min <= 0:
        return False
    return (
        s.late_night_end_min
        <= time_min
        < s.late_night_end_min + s.transition_lead_min
    )


def compute_caps(
    now: datetime,
    next_sunset: Optional[datetime],
    is_day: bool,
    settings: RenderSettings = DEFAULT_SETTINGS,
    next_sunrise: Optional[datetime] = None,
) -> tuple[float, int]:
    """Return (max_brightness_pct, max_cct) for the given moment.

    Takes the most restrictive cap across overlapping zones (late-night,
    pre-night transition, sunset transition, evening, morning brightness ramp,
    pre-sunrise CCT ramp, day).
    """
    s = settings
    time_min = now.hour * 60 + now.minute

    candidates_b: list[float] = [100.0]
    candidates_cct: list[float] = [float(s.max_cct_day)]

    in_late_night = _in_late_night(time_min, s)
    in_ninepm = _in_ninepm_transition(time_min, s)
    in_morning = _in_morning_transition(time_min, s)

    # Pre-sunrise CCT ramp: in the lead minutes before sunrise (sun still down),
    # slide the color cap up from the evening cap toward the daytime max so the
    # light cools into the morning instead of snapping cool at sunrise.
    in_presunrise = False
    presunrise_cct: float | None = None
    if not is_day and next_sunrise is not None and s.transition_lead_min > 0:
        mins_to_sunrise = (next_sunrise - now).total_seconds() / 60.0
        if 0 <= mins_to_sunrise <= s.transition_lead_min:
            in_presunrise = True
            frac = mins_to_sunrise / s.transition_lead_min  # 1 at start, 0 at sunrise
            # Warm (evening) at the start, up to the day arc's base by sunrise.
            presunrise_cct = s.day_base_cct - frac * (s.day_base_cct - EVENING_MAX_CCT)

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

    # Morning brightness ramp (just after night ends): cap eases from the night
    # cap back up to 100% over the lead. Brightness only — the CCT cooling is
    # handled by the pre-sunrise ramp below.
    if in_morning:
        frac = (time_min - s.late_night_end_min) / s.transition_lead_min
        mb = s.late_night_max_b_pct + frac * (100.0 - s.late_night_max_b_pct)
        candidates_b.append(mb)

    # Daytime CCT arc: between sunrise and the start of the sunset transition,
    # the color cap rises from the arc base at sunrise to the arc peak at solar
    # noon and back, always moving. Needs both sun events; with only a partial
    # set it falls back to the flat max_cct_day ceiling already in candidates.
    if is_day and next_sunset is not None and next_sunrise is not None:
        today_sunrise = next_sunrise - timedelta(days=1)
        arc_end = next_sunset - timedelta(minutes=s.transition_lead_min)
        span = (arc_end - today_sunrise).total_seconds()
        if span > 0:
            t = (now - today_sunrise).total_seconds() / span
            candidates_cct.append(
                s.day_base_cct + (s.max_cct_day - s.day_base_cct) * _arc_shape(t)
            )

    # Sunset transition (CCT cap slides the arc base → evening cap over the lead,
    # so it is still at the evening cap exactly at sunset).
    if is_day and next_sunset is not None and s.transition_lead_min > 0:
        mins_to_sunset = (next_sunset - now).total_seconds() / 60.0
        if 0 <= mins_to_sunset <= s.transition_lead_min:
            frac = mins_to_sunset / s.transition_lead_min
            candidates_cct.append(
                EVENING_MAX_CCT + frac * (s.day_base_cct - EVENING_MAX_CCT)
            )

    # Pre-sunrise CCT ramp candidate (replaces the flat evening cap while active).
    if in_presunrise and presunrise_cct is not None:
        candidates_cct.append(presunrise_cct)

    # Evening: sun is down and we're not in another color zone. Skipped during
    # the pre-sunrise ramp so the color cap can rise above the evening value.
    if not is_day and not in_late_night and not in_ninepm and not in_presunrise:
        candidates_cct.append(float(EVENING_MAX_CCT))

    return min(candidates_b), int(round(min(candidates_cct)))


def current_period(
    now: datetime,
    next_sunset: Optional[datetime],
    is_day: bool,
    settings: RenderSettings = DEFAULT_SETTINGS,
    next_sunrise: Optional[datetime] = None,
) -> str:
    """Classify the moment into a human-readable circadian period.

    One of: night, pre_night, morning_ramp, sunset_transition, pre_sunrise,
    day, evening. Used for the wrapper's state attributes.
    """
    s = settings
    time_min = now.hour * 60 + now.minute

    if _in_late_night(time_min, s):
        return "night"
    if _in_ninepm_transition(time_min, s):
        return "pre_night"
    if _in_morning_transition(time_min, s):
        return "morning_ramp"
    if is_day:
        if next_sunset is not None and s.transition_lead_min > 0:
            mins = (next_sunset - now).total_seconds() / 60.0
            if 0 <= mins <= s.transition_lead_min:
                return "sunset_transition"
        return "day"
    if next_sunrise is not None and s.transition_lead_min > 0:
        mins = (next_sunrise - now).total_seconds() / 60.0
        if 0 <= mins <= s.transition_lead_min:
            return "pre_sunrise"
    return "evening"


# --- Intent → output mapping --------------------------------------------------

def render(
    intent: float,
    now: datetime,
    next_sunset: Optional[datetime],
    is_day: bool,
    settings: RenderSettings = DEFAULT_SETTINGS,
    next_sunrise: Optional[datetime] = None,
) -> tuple[int, int]:
    """Compute underlying bulb (brightness 1–255, cct K) from user intent."""
    intent = max(0.0, min(100.0, intent))

    min_brightness = settings.min_brightness
    min_cct = settings.min_cct

    max_b_pct, max_cct = compute_caps(now, next_sunset, is_day, settings, next_sunrise)
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
