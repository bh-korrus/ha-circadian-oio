"""Constants for the Circadian OIO integration."""
from __future__ import annotations

DOMAIN = "circadian_oio"

# --- Discovery ----------------------------------------------------------------

# Substrings matched (case-insensitive) against device manufacturer.
# Update this list once you confirm the exact Matter VendorName your bulbs
# advertise. Add corporate variants here as needed.
KORRUS_MANUFACTURER_MATCHES = (
    "korrus",
    "ecosense",
    "oio",
)

# --- Configuration keys -------------------------------------------------------

CONF_WRAPPED_DEVICES = "wrapped_devices"

# User-tunable options (stored in entry.options). Each falls back to the
# matching default constant below when unset.
CONF_NIGHT_START = "night_start"            # time string "HH:MM:SS"
CONF_NIGHT_END = "night_end"                # time string "HH:MM:SS"
CONF_TRANSITION_MINUTES = "transition_minutes"
CONF_NIGHT_BRIGHTNESS_PCT = "night_brightness_pct"
CONF_DAY_MAX_CCT = "day_max_cct"

# Runtime key under hass.data[DOMAIN][entry_id]: maps each underlying entity
# we hid to its prior hidden_by value, so it can be restored on unload.
DATA_HIDDEN = "hidden"

# --- Curve / output ranges ----------------------------------------------------

# Incandescent dim curve: CCT = BASE_CCT * (B/100) ** INCANDESCENT_EXP
# Derived from the empirical tungsten relationship L ∝ V^3.4, CCT ∝ V^0.42.
BASE_CCT = 2700
INCANDESCENT_EXP = 0.124

# Hard bulb limits (Matter level cluster is 0-255).
MIN_BRIGHTNESS = 1
MAX_BRIGHTNESS = 255

# CCT range. Extended low end below the natural curve floor lets you reach
# candle-flame and lower while staying at minimum brightness.
MIN_CCT = 800
MAX_CCT_DAY = 6500

# --- Time zones ---------------------------------------------------------------

# All in minutes since local midnight.
LATE_NIGHT_START_MIN = 21 * 60          # 9:00 PM
LATE_NIGHT_END_MIN = 5 * 60 + 30        # 5:30 AM
NINEPM_TRANSITION_LEAD_MIN = 30         # transition starts 30 min before 9 PM
SUNSET_TRANSITION_LEAD_MIN = 30         # transition starts 30 min before sunset

# Same defaults as time strings, for the options form's time pickers.
DEFAULT_NIGHT_START = "21:00:00"
DEFAULT_NIGHT_END = "05:30:00"

# --- Cap levels ---------------------------------------------------------------

LATE_NIGHT_MAX_B_PCT = 10.0             # max brightness during late-night zone
EVENING_MAX_CCT = 2700                  # post-sunset, pre-late-night CCT cap

# --- Behavior tuning ----------------------------------------------------------

# How often the render loop re-evaluates caps and pushes to the underlying
# bulb. One minute is fine for 30-minute transitions; smaller is wasteful.
UPDATE_INTERVAL_SECONDS = 60

# Smoothing applied to the periodic time-of-day re-render (seconds). Roughly
# matches the update interval so the slow drift across cap shifts chains
# together into a continuous fade rather than visible once-a-minute steps.
RENDER_TRANSITION_SECONDS = 50

# Smoothing applied when the user (or a script/voice/Pico) actively changes the
# slider. This must be short or the bulb appears to lag the control by the full
# RENDER_TRANSITION_SECONDS. A small non-zero value keeps the move smooth
# without feeling sluggish.
USER_TRANSITION_SECONDS = 1

# Where in the intent space [0-100] each phase lives. Phase A is the sub-curve
# CCT-only walk below the brightness floor; Phase B is the brightness ramp with
# curve-tracking CCT; Phase C is the optional super-curve CCT-only walk above
# the natural curve top (only active when the current cap allows CCT > 2700K).
PHASE_A_END = 10.0
PHASE_B_END_WITH_C = 90.0
