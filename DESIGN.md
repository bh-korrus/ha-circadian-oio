# Design notes

This document captures the technical reasoning behind the integration. CLAUDE.md is the orientation doc for working in the codebase; this is the reasoning doc for understanding why the codebase is shaped the way it is.

## The problem statement

OIO bulbs are tunable-white Matter devices with two control axes: brightness and color temperature. The user-facing problem is that Home Assistant exposes both axes independently, which means:

1. Most user interactions (Lovelace sliders, voice, scripts) only adjust brightness — CCT gets stuck at whatever value was last set, often inappropriate for the current time of day.
2. The relationship between brightness and CCT that makes a tunable bulb feel like an incandescent (warm dim, cool bright) is not preserved without explicit per-action coordination.
3. Caps and circadian-appropriate ranges shift through the day, but the bulb has no awareness of this. A 100% / 6500 K state set at 2 PM stays 100% / 6500 K at midnight unless something forces a change.

The integration's job is to collapse these two axes into one user-visible axis ("how bright do I want this") and to keep the underlying bulb honest with respect to time, the incandescent dim curve, and circadian-appropriate caps.

## Why a wrapper, not an automation

Earlier iterations of this work used a stack of Home Assistant automations and YAML templates: per-Pico automations, time-pattern automations to enforce caps, complex Jinja in variables blocks. This approach has three problems:

1. **State is fragmented.** "What does the user want?" lives in implicit bulb state. There's no single source of truth.
2. **HA's variables block has type-coercion bugs.** Storing datetime/bool values in automation variables often stringifies them, leading to silent failures (e.g., `'str object' has no attribute 'hour'`).
3. **It doesn't generalize.** Each new bulb requires N new automations. New OIO bulbs added later don't auto-wrap.

A custom integration solves all three. The user's intent is persisted on the entity itself. Code is real Python with real types. Discovery is automatic.

## Why a Light entity, not a sensor + script

Could write this as a "Circadian Controller" sensor that publishes recommended values, plus scripts that call `light.turn_on`. But then every interaction surface (Pico, voice, Lovelace, agents) needs to know about the sensor — they're not going to spontaneously talk to a circadian_controller.brightness slider.

By presenting the wrapper as a `light.*` entity with `ColorMode.BRIGHTNESS`, every existing surface in Home Assistant just works. Voice assistants understand "set the kitchen light to 50%." Lovelace shows a brightness slider. Scenes can capture and restore state. The Pico integration's `brightness_step_pct` parameter does exactly the right thing. We get all of this for free by being a normal-looking light.

## Why hide the underlying entity

When a bulb is wrapped, we hide the underlying light entity from the user. This is the right call but worth being explicit about.

The wrapper owns both axes (brightness and CCT) on the underlying bulb. If the user can also poke at the raw bulb directly, two failure modes emerge:

1. **User adjusts CCT directly.** The wrapper's next render call (within 60 seconds) overwrites it. Confusing.
2. **User adjusts brightness directly.** The wrapper's intent is now stale relative to actual bulb state. The render call uses intent (old) and overrides the new direct change. Also confusing.

By hiding the underlying entity we make the wrapper the only interface. The few users who genuinely need the raw entity (debugging, edge cases) can unhide it via the entity registry.

## Why time-based re-render

A natural alternative is to render only on user input. This would be simpler but breaks the central promise: a bulb left at 80% in the afternoon will not get dimmer and warmer as the evening approaches without anyone touching it. The whole point of the integration is that the bulb tracks time.

A 60-second tick is a coarse-enough resolution that it's not wasteful (one HA service call per minute per wrapped bulb is cheap) and fine-enough resolution that 30-minute transitions look smooth — 30 update steps over the transition window, with `transition: 50` on each tick call so the bulb fades between steps rather than stepping discretely. Direct user actions are a separate path: they re-render with a short `transition` (1 s) so the bulb tracks the slider instead of lagging it by the full 50-second tick fade.

## Why L\* perceptual brightness

CIE L\* (lightness) is the standard model of perceptually uniform lightness, defined to approximate the human visual system's nonlinear response to luminance. Equal steps in L\* feel like equal steps in brightness regardless of the underlying luminance level.

The alternative — linear stepping in luminance — looks fine at the top end (50% → 45% is barely noticeable) but feels enormous at the bottom (5% → 1% is dramatic). L\* fixes this. From the user's perspective, dragging the slider from 100 to 90 and dragging it from 20 to 10 produce the same perceived change.

The formula has a piecewise definition: cubic for L\* ≥ 8 and linear for L\* < 8. The transition is smooth (continuous in value and derivative). Below the linear regime threshold, the perceived-to-luminance relationship flattens out because near-threshold vision is governed by different mechanisms. Both branches are implemented in `render.lstar_from_y` / `y_from_lstar`.

## The incandescent dim curve

The relationship `CCT = 2700 × (B/100)^0.124` is derived as follows. For a tungsten filament:

- Lumens scale as approximately V^3.4 (electrical voltage to luminous output)
- Filament temperature scales as approximately V^0.42

Where V is the voltage across the filament. Eliminating V:

- B / B₀ = (V / V₀)^3.4
- CCT / CCT₀ = (V / V₀)^0.42
- (V / V₀) = (B / B₀)^(1/3.4)
- CCT / CCT₀ = ((B / B₀)^(1/3.4))^0.42 = (B / B₀)^(0.42/3.4) = (B / B₀)^0.1235...

Anchored at 2700 K at full output, this gives the curve. Sample values:

| B (%) | CCT (K) |
|-------|---------|
| 100   | 2700    |
| 50    | 2479    |
| 10    | 2030    |
| 1     | 1523    |
| 0.39  | 1357    |
| 0.1   | 1227    |

The empirical exponents are valid near rated operation but get progressively less reliable as you extrapolate toward very dim levels. At 1% brightness the curve says 1523 K; a more careful Planckian / luminous-efficacy calculation gives closer to 1700 K. We use the simpler empirical formula because the exact value at the deep end is less important than continuity with the rest of the curve. If we ever care about precise low-end behavior, swap in the Planckian-derived version.

## The extended low end (below 800 K)

Below the natural curve floor — that is, below the CCT the curve gives at the bulb's minimum brightness — we extend the warm end further down to 800 K while holding brightness at minimum. This is a deliberate departure from real-incandescent physics (a real incandescent filament can't sustain such low filament temperatures while emitting useful light) and is included because users want a "very warm, very dim" mode that maps emotionally to candle / firelight.

The transition between the brightness-ramp regime (Phase B) and the CCT-only regime (Phase A) is at the bulb's brightness floor. Phase A walks CCT from the curve's value at floor brightness (~1357 K) down to 800 K while brightness stays at minimum.

## Cap zones and transitions

The day is divided into zones with different (max_b, max_cct) pairs:

| Zone                  | Time                     | max_b | max_cct |
|-----------------------|--------------------------|-------|---------|
| Day                   | sunrise → 30 min before sunset | 100%  | 6500 K  |
| Sunset transition     | 30 min before sunset → sunset | 100%  | linear 6500 → 2700 |
| Evening               | sunset → 8:30 PM         | 100%  | 2700 K  |
| 9 PM transition       | 8:30 PM → 9 PM           | linear 100 → 10% | linear 2700 → 2030 |
| Late night            | 9 PM → 5:30 AM           | 10%   | 2030 K  |
| Morning (no transition yet) | 5:30 AM → sunrise        | 100%  | 2700 K  |

Two design choices worth noting:

1. **Contractions get lead time, expansions are instant.** The 9 PM and sunset transitions start 30 minutes before the boundary because the cap is shrinking and we need to walk the bulb gracefully into the new range. The 5:30 AM and sunrise transitions are instant because the cap is growing; the bulb is already in the (now larger) allowed range.

2. **9 PM transition follows the dim curve.** During the 8:30–9 PM window, max_b drops linearly from 100% to 10%, but max_cct is computed via the incandescent curve at the current max_b, not linearly between 2700 and 2030. This keeps the cap boundary on the curve, so a bulb being walked down by the cap follows the same path as a bulb being dimmed manually.

## The intent → output mapping

The user's brightness slider value (0–100) maps to one of three phases of the output curve, with phase boundaries at intent = 10 and intent = 90 (or 100 if no Phase C):

- **Phase A** (intent 0–10): brightness at floor, CCT walks from 800 K to ~1357 K (the curve at floor brightness). This is the "candle to deep ember" regime.
- **Phase B** (intent 10–90 or 10–100): brightness ramps from floor to current max_b in L\*-uniform steps. CCT follows the incandescent curve, clamped to current max_cct. This is the main dimming regime.
- **Phase C** (intent 90–100, only when max_cct > curve_at_max_b): brightness at max_b, CCT walks from ~2700 K up to current max_cct. This is the "blue noon" regime; it only exists during the daytime when there's headroom above the curve top.

The split point at 90% allocates 80% of the slider range to the brightness ramp (Phase B), which is where most user interaction happens, and 10% each to the extreme ends. This feels about right in practice — getting the bulb into a "candle for reading in bed" state requires deliberately moving the slider all the way down, not just nudging it.

## Why caps use `min()` of candidates

Multiple cap zones can overlap. In summer, sunset might fall at 8:45 PM, which is during the 9 PM transition window (8:30 PM – 9 PM). At 8:30 PM the sunset-transition cap on CCT might say 4600 K (halfway through its slide from 6500 to 2700), but the 9 PM transition cap on CCT says 2700 K (just entering its slide from 2700 to 2030).

We resolve overlapping caps by collecting all applicable candidate values and taking the minimum (most restrictive). This is correct because every cap is an upper bound; the actual ceiling is the lowest of all upper bounds. It also makes adding new zones trivial: just add another candidate to the list under the right condition.

## Persistence model

User intent is the only state that needs to survive HA restarts. It's stored via the standard `RestoreEntity` mechanism — on entity load, we read the last brightness value, convert to intent, and resume. The underlying bulb's state isn't persisted by us; whatever state it was in before the restart will be re-rendered on the first tick or first user input.

There's a small race on startup: the wrapper restores at one moment, the underlying entity restores at another, and there might be a few-second window where the wrapper's "is_on=True, intent=80" is not yet reflected on the underlying bulb. The first 60-second tick or any user action closes this. Not worth fixing.

## What this integration does not do

- **It does not adjust based on individual sleep timing.** The 9 PM / 5:30 AM boundaries are global. A user who sleeps at midnight gets late-night light starting at 9 PM, three hours earlier than ideal for them. A "personal circadian phase" mode that takes a chronotype or a wake-time would be a useful addition.
- **It does not consider activity.** Watching a movie at 8 PM probably wants different light than reading. There's no notion of mode or scene awareness.
- **It does not consider the actual sun.** It uses HA's sun.sun entity for sunrise/sunset times but doesn't track actual outdoor light levels or cloud cover. A lux sensor could plausibly inform the cap, but it's not in the current model.
- **It does not handle non-circadian use cases.** Doing photography? Want to match daylight at midnight? You'd need to bypass the wrapper. We hide the underlying entity by default, so this requires un-hiding in the entity registry. Deliberate friction.

## Where this integration is going

In rough order:

1. Make it work reliably on real OIO bulbs. Confirm manufacturer string. Find the actual brightness floor. Hit known edge cases (overlapping transitions, DST boundaries, sun-never-sets / sun-never-rises latitudes).
2. Publish to HACS as a custom repository.
3. Add tests for cap boundaries and transitions.
4. Add sunrise CCT ramp and 5:30 AM brightness ramp.
5. Add per-area time zone overrides (bedroom dims earlier than living room).
6. Add personal circadian phase (chronotype-based shift of the global schedule).
7. Submit to HACS default repository list.
8. Consider scenes-with-circadian-context (e.g., "movie mode" that ignores the brightness cap but keeps the CCT cap).
