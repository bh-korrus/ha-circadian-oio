# Circadian OIO — What it does and how the dimming works

This document describes the Home Assistant integration as currently deployed
(v0.1.4). It explains what the integration is, the worldview behind it, and
every part of the dimming logic in detail. It is written to stand on its own —
you do not need to read the code to follow it.

## 1. The idea in one paragraph

Circadian OIO wraps a Korrus OIO tunable-white bulb and presents it to the rest
of the smart home as an ordinary single-axis dimmer. The user sees and drags one
brightness slider. Behind that slider the integration translates "what the user
wants" plus "what time it is" into the correct pairing of physical brightness and
color temperature, then drives the real bulb over Matter. As the day progresses
the bulb shifts on its own — warmer and dimmer toward night, cooler and brighter
in the day — without anyone touching it. The goal is that one intuitive control
produces lighting that is always appropriate for the time of day and is honest
about its circadian effect.

## 2. Why OIO bulbs specifically

The integration refuses to wrap anything that is not a Korrus/OIO bulb. This is a
scientific constraint, not branding.

- OIO bulbs emit Planckian-matched spectra at every CCT step. Their tunable-white
  architecture is engineered so that the spectrum at, say, 2030 K is a real
  blackbody-shaped distribution with the correct melanopic and alpha-opic
  content — not three or four narrow primaries metamerically faking the color
  point.
- An RGBW bulb set to the same "2030 K" chromaticity has the wrong spectrum and
  the wrong melanopic content. Applying this curve to an RGBW bulb would look
  similar to the eye but defeat the circadian purpose.

Because the dim curve below is only meaningful on a bulb whose spectrum is
physically correct at each step, the integration filters on the device
manufacturer and wraps OIO bulbs only.

## 3. The user-facing model: one slider

Everything the user interacts with is a normal brightness slider on a light
entity named "<bulb> (Circadian)". There is no separate color-temperature
control exposed to the user. The slider value is stored internally as an
"intent" from 0 to 100. Intent is not the bulb's physical brightness; it is the
user's desired position along a single circadian dimming axis. The integration
is responsible for turning intent into the right physical (brightness, CCT) pair
for the current moment.

The raw physical bulb is hidden so the user cannot accidentally drive it
directly and bypass the logic.

## 4. Architecture and data flow

```
User / voice / script / Pico / automation
      |
      v  sets brightness on
light.<bulb>_circadian        <- the only thing anyone sees
      |  stored as "intent" (0-100)
      |  on intent change OR every 60 seconds:
render(intent, now, sunset, is_day, settings) -> (brightness, cct)
      |  calls light.turn_on on
light.<bulb>_raw              <- the real bulb, hidden from the user
      |  Matter
Korrus OIO bulb
```

The render step is a pure function: given the intent, the current time, the next
sunset, whether the sun is up, and the user's settings, it returns a physical
brightness (1–255) and a color temperature in kelvin. It re-runs whenever the
user changes the slider and on a fixed 60-second tick so the bulb tracks time.

## 5. The dimming logic

This is the core. The logic has to answer one question: given an intent from 0
to 100 at a particular moment, what physical brightness and CCT should the bulb
show? It does this in several layers.

### 5.1 The incandescent dim curve

The relationship between brightness and color temperature follows the physics of
a real incandescent filament cooling as you dim it. For tungsten the empirical
relationships are:

- Luminous output scales with voltage as L is proportional to V^3.4
- Filament color temperature scales with voltage as CCT is proportional to V^0.42

Combining these and anchoring at 2700 K at full output gives:

```
CCT = 2700 * (B / 100) ^ (0.42 / 3.4)
    = 2700 * (B / 100) ^ 0.124
```

where B is brightness as a percentage. This single curve is what makes the bulb
"dim to warm" the way a real incandescent does. Reference points:

| Brightness | Curve CCT |
|-----------:|----------:|
| 100%       | 2700 K    |
| 50%        | ~2478 K   |
| 10%        | ~2030 K   |
| 1%         | ~1525 K   |

Below about 1% the curve continues to fall toward candle and ember territory,
which is the basis for the extended low end described in 5.5.

### 5.2 Perceptual brightness stepping (CIE L\*)

Human brightness perception is roughly the cube root of luminance, so equal
steps in raw lumens look enormous at the bottom of the range and tiny at the top.
To make each slider increment feel like the same change at any level, the
integration steps brightness in CIE L\* (perceptual lightness) space rather than
in linear luminance.

The L\* conversion is piecewise:

- For relative luminance Y above 0.008856: `L* = 116 * Y^(1/3) - 16`
- At or below that threshold: `L* = 903.3 * Y` (the linear near-black regime)

and its inverse:

- For L\* above 8: `Y = ((L* + 16) / 116)^3`
- At or below 8: `Y = L* / 903.3`

Both branches matter here. Because the integration operates all the way down to
the bulb's minimum output, it spends real time in the linear regime below L\* 8,
so the simple cubic alone would be wrong at the low end. The two branches meet
smoothly at L\* = 8 (Y = 0.008856).

### 5.3 The three phases of the slider

The 0–100 intent axis is divided into three phases. Two boundaries control them:
Phase A occupies intent 0–10, and Phase C (when it exists) occupies the top 10
of intent; Phase B is everything in between.

Phase A — sub-curve color walk (intent 0 to 10).
The bulb is held at its minimum physical brightness (1/255, about 0.39%). Color
temperature walks from the deep warm floor (800 K, see 5.5) up to the curve's
value at minimum brightness (about 1358 K). This is the "very dim, very warm"
zone: the light barely changes brightness here, it changes color, sliding from
ember glow up toward candle.

Phase B — the main brightness ramp (intent 10 to 90, or to 100).
Brightness ramps from the floor up to the current maximum, stepped uniformly in
L\* so it feels even. Color temperature follows the incandescent curve for
whatever brightness the ramp is at, capped by the time-of-day color limit. This
is where most normal dimming happens: as you bring the light up, it gets both
brighter and cooler together, tracking the filament physics.

Phase C — super-curve color walk (intent 90 to 100), only in daytime.
Phase C exists only when the time-of-day cap allows a color temperature cooler
than the curve's natural top (for example, daytime allows up to 6500 K while the
curve tops out at 2700 K at full brightness). When it exists, the bulb is held at
maximum brightness and color temperature walks from the curve top (2700 K) up to
the daytime maximum (6500 K). This is the "full brightness, push it cooler for
daytime alertness" zone. At night, when the cap does not allow anything cooler
than the curve, Phase C disappears and Phase B simply runs to 100.

The phases are continuous at their boundaries: at intent 10 the end of Phase A
and the start of Phase B produce the same brightness and color, and likewise at
the Phase B/C boundary, so there are no visible jumps as you cross between them.

### 5.4 The candle-regime low end (800 K floor)

A real incandescent does not stop at its rated color temperature; as the filament
cools toward its solid-state failure threshold it passes through candle (~1850 K)
and ember-glow (~1000–1500 K) before going dark. The integration mimics this by
extending the warm end down to 800 K while holding brightness at the bulb
minimum. That extension is what Phase A walks through. It gives the user a usable,
physically-motivated "barely lit, very warm" region at the bottom of the slider
that a naive curve would not reach.

### 5.5 Time-of-day caps

On top of the curve sits a set of caps that restrict what the bulb is allowed to
do at a given time. Each applicable zone contributes a candidate maximum
brightness and a candidate maximum color temperature, and the integration takes
the most restrictive of each (the minimum). The zones:

- Daytime, no transition active: up to 100% brightness, and a color cap that
  follows an arc rather than sitting flat. The arc is at the daytime base (4500 K
  by default) at sunrise and again at the start of the sunset transition, and
  rises to the daytime peak (6500 K by default) at solar noon. The shape is a
  square-rooted sine, so most of the day is spent near the peak (a bias toward
  high, alerting color) while the value is always moving — it touches the exact
  peak only momentarily at noon. This keeps daytime light dynamic instead of a
  static 6500 K block. The arc needs both sunrise and sunset to be known; if only
  one is available it falls back to a flat cap at the peak.
- Late night (default 9:00 PM to 5:30 AM): brightness capped at 10% and color
  capped at the curve value for 10% (about 2030 K). This is the low-melanopic
  protective window before and during sleep.
- Evening (sun down, but before the pre-night transition and late night): color
  capped at 2700 K, brightness still allowed to 100%. The light can be bright but
  not cool once the sun is down.
- Pre-night transition (the 30 minutes before late-night start by default): the
  brightness cap slides linearly from 100% down to the 10% night cap, and the
  color cap follows the curve for that sliding brightness. See 5.6.
- Sunset transition (the 30 minutes before sunset, while the sun is still up):
  the color cap slides from the daytime maximum (6500 K) down to 2700 K, so the
  light cools off ahead of sunset rather than snapping warm at the moment the sun
  sets.

The most-restrictive-cap rule is deliberate. Zones overlap — for example, a
summer sunset at 8:45 PM coincides with the start of the 9 PM pre-night
transition — and rather than encoding precedence rules, the integration collects
every applicable candidate and takes the minimum. This is robust to overlap and
makes adding new zones simple.

The caps feed back into the phase logic from 5.3: the "current maximum
brightness" that Phase B ramps to and the "maximum color temperature" that Phase
B is capped at and Phase C walks to are exactly these computed caps. So at night
the same intent that would give a bright cool light at noon instead gives a dim
warm light, because the caps have contracted the available range.

### 5.6 Transitions: walk down before the boundary

When the allowed range is about to contract — at sunset, and at the start of the
late-night window — the cap does not snap. It starts moving 30 minutes ahead of
the boundary and slides to its new value over that window. The reason is that a
bulb left on a high setting needs to be gracefully walked into the smaller range
rather than jerked down at the instant the boundary arrives.

- The pre-night transition slides the brightness cap from 100% to 10% (and color
  along the curve) over the 30 minutes before night starts.
- The sunset transition slides the color cap from the daytime base (4500 K) down
  to 2700 K over the 30 minutes before sunset. It begins exactly where the
  daytime arc's evening shoulder ends, so the handoff is seamless, and it still
  reaches 2700 K precisely at sunset.

Morning is the mirror image, also ramped rather than snapped:

- The morning brightness ramp eases the brightness cap from the night cap (10%)
  back up to 100% over the transition window just after night ends, instead of
  jumping at 5:30 AM. (Brightness only — color cooling is the sunrise ramp's job.)
- The pre-sunrise CCT ramp slides the color cap from 2700 K up to the daytime
  base (4500 K) over the window before sunrise, handing off to the daytime arc at
  sunrise, so the light cools into the morning rather than snapping cool the
  instant the sun crosses the horizon.

The protective night window always wins where they overlap: if sunrise falls
inside the late-night window, the most-restrictive-cap rule keeps the bulb dim
and warm until night ends, regardless of the sunrise ramp.

### 5.7 Output clamping

After the phase logic produces a brightness percentage and a color temperature,
the brightness is converted to the Matter 0–255 scale and clamped to the bulb's
limits (the configured minimum brightness, default 1, up to 255), and the color
temperature is clamped between the configured minimum CCT (default 800 K) and the
configured daytime maximum. The bulb is then
commanded with that brightness, that color temperature, and a transition time.

## 6. Continuous re-rendering (the time drift)

The integration does not only render when the user moves the slider. It also
re-renders on a fixed 60-second tick. On each tick it recomputes the caps for the
new time and pushes the result to the bulb. This is what makes a bulb left
untouched in the afternoon get visibly dimmer and warmer as the evening arrives:
the intent stays the same, but the caps contract minute by minute, so the
rendered output drifts. A 60-second cadence is cheap (one command per minute per
bulb) and fine-grained enough that the 30-minute transitions look smooth — about
30 update steps across each transition window.

The tick is a backstop, not the primary path. The wrapper also subscribes to the
underlying bulb's state, so it reacts the instant the bulb is switched on or off
by any route — a Pico remote, a light group, a scene, voice — and renders
immediately rather than waiting up to a minute for the next tick. The tick still
runs so the color keeps drifting while the bulb sits on, and it re-syncs on/off
state as a safety net, but a press no longer waits on the poll.

## 7. Transition timing: snappy for the user, smooth for the drift

Every command to the bulb carries a fade time. The integration uses two different
ones depending on what triggered the render:

- A direct user action (slider, voice, script, Pico) is applied instantly (zero
  transition), so a deliberate press is honored immediately, matching turn-off.
- The 60-second time-of-day tick fades over 50 seconds, roughly matching the tick
  interval, so consecutive ticks chain into one continuous fade rather than
  visible once-a-minute steps.

Using the long fade for user actions was an early mistake — it made the bulb
appear to lag the control — which is why the two paths are distinct, and why
deliberate inputs now use no transition at all.

## 8. What the user can tune

The curve itself, the perceptual stepping, the candle floor, and the evening
color cap are fixed; they define the circadian behavior and are not exposed.
The schedule and intensity are user-tunable through the integration's settings:

- Night start time (default 9:00 PM) — when the dim/warm cap begins.
- Night end time (default 5:30 AM) — when it lifts.
- Transition duration (default 30 minutes) — how long the pre-night and
  pre-sunset slides take. A value of 0 means instant cap changes.
- Night brightness cap (default 10%) — the maximum brightness during the night
  window.
- Daytime peak color temperature (default 6500 K) — the coolest the light gets,
  reached at solar noon at the top of the daytime arc.
- Daytime base color temperature (default 4500 K) — the arc's shoulders at
  sunrise and at the start of the sunset transition. Lower it for a warmer start
  and end to the day; the arc still peaks at the daytime peak value at noon.
- Minimum brightness (default 1 of 255) — the lowest level the bulb is ever
  commanded to. Raise this if the bulb switches off at the bottom of the range
  instead of staying dimly lit.
- Minimum color temperature (default 800 K) — the warmest color the bulb is
  asked to produce at the very bottom of the slider. Raise it to the warmest the
  bulb can actually render if 800 K is below its capability.

The settings UI warns that raising the night cap or cooling the night color
weakens the circadian effect. The two floor settings are per-bulb hardware
limits, not behavior choices — they exist so the curve adapts to what a given
bulb can physically do, rather than commanding a level it cannot reach.

## 9. Worked examples

Intent 80 at noon (full range, daytime). Caps allow 100% and 6500 K. Intent 80 is
in Phase B, so brightness sits high on the L\*-uniform ramp and color follows the
curve for that brightness — a bright, fairly cool white. Push the slider to 100
and Phase C takes over: brightness pins at 100% and color walks up to 6500 K.

Intent 80 at 11 PM (late night). Caps clamp to 10% and about 2030 K. The same
intent of 80 now lands near the top of the contracted range: roughly 10%
brightness at about 2030 K — a dim, warm light. The user did not change anything;
the time of day did.

Intent 5 at any time. This is in Phase A: the bulb is at its minimum brightness
and the color is walking through the candle/ember region, somewhere between 800 K
and about 1358 K. Barely lit, very warm.

8:45 PM in summer with the sun setting at 8:50. The pre-night transition (started
8:30) is sliding the brightness cap down toward 10%, and the sunset transition is
sliding the color cap toward 2700 K. The integration takes the most restrictive
of both. The bulb is being walked down on both axes at once, smoothly, ahead of
the 9 PM boundary.

## 10. What it deliberately does not do

- It does not wrap non-OIO bulbs. The curve is only meaningful on a bulb with
  physically correct spectra at each step.
- It does not expose color temperature as a separate user control. The whole
  point is one axis.
- It does not let the user reshape the curve, only the schedule and intensity.
- It does not treat color temperature as a circadian proxy. Both brightness and
  the bulb's real Planckian color are controlled together, which is what makes
  the melanopic claim honest.

---

This document reflects the deployed behavior as of v0.1.4. If the curve, caps,
or transition logic change, update this file alongside the code.
