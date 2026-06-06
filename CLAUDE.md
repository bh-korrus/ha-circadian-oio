# Circadian OIO — Claude Code Project Guide

This file is the orientation document for anyone (human or AI) working on this codebase. Read it before making changes. Update it when architectural decisions change.

## Project overview

This is a Home Assistant custom integration that wraps OIO (Korrus) tunable-white bulbs and exposes each as a **single-axis dimmer** to the rest of the Home Assistant ecosystem (Lovelace, voice, scripts, Pico remotes, agents, etc.). The user sees and interacts with a normal brightness slider; behind the scenes the integration translates "user intent" + "current time of day" into the right pairing of physical brightness and color temperature, then drives the underlying bulb over Matter.

The end goal is HACS distribution under the "Circadian OIO" name. The integration is scoped to OIO/Korrus bulbs only — it filters on the device-registry manufacturer field and refuses to wrap anything else. The reason is scientific integrity (see "The science" below), not branding.

## The science

The render logic encodes a specific worldview about how circadian-aware dimming should behave. Anyone editing the curve or cap logic needs to understand this. The shortest version:

1. **OIO bulbs emit Planckian-matched spectra at every CCT step.** Their tunable-white architecture is specifically engineered so the spectral power distribution at, say, 2030 K is a real blackbody-shaped spectrum with the right α-opic and melanopic content — not three or four narrow primaries metamerically faking the chromaticity. This is what makes a circadian dim curve meaningful on this bulb.

2. **An RGBW bulb at "2030 K" is not the same thing.** Same chromaticity, totally different spectrum, wrong melanopic content. Applying our curve to RGBW would produce a visually similar dim-to-warm experience but defeat the circadian point. This is why the integration is OIO-only by design.

3. **The dim curve follows real incandescent behavior.** Filament temperature drops as voltage drops, and the empirical relationships for tungsten are:
   - L ∝ V^3.4 (lumens scale with voltage to the 3.4 power)
   - CCT ∝ V^0.42 (filament temperature scales with voltage to the 0.42 power)
   - Combining: CCT = CCT₀ × (L/L₀)^(0.42/3.4) = 2700 × (B/100)^0.124 when anchored at 2700 K at full output
   
   This is the curve in `render.curve_cct()`. It gives roughly 2030 K at 10% brightness, 1523 K at 1%, and asymptotes toward candle territory below that.

4. **Below the natural curve floor we extend the warm end down to 800 K** while holding brightness at the bulb minimum. This mimics deep candle-flame regime (~1850 K is candle, ~1000–1500 K is ember-glow) and gives users a usable "very dim, very warm" zone that real incandescents physically reach as the filament cools toward its solid-state failure threshold.

5. **Time-of-day caps reflect a circadian model.** Late at night (9 PM – 5:30 AM) the bulb is capped at 10% brightness and ~2030 K. This is consistent with evidence that low-mEDI exposure in the hours before bed protects melatonin onset and sleep architecture, and that morning-shifted light exposure entrains the circadian phase. Daytime allows up to 6500 K (high-mEDI) to support alertness and entrainment.

6. **Transitions are 30 minutes, before the boundary.** When the allowed range is about to contract (sunset, 9 PM), the cap starts shifting 30 minutes in advance so a bulb left on a high setting is gracefully walked into the new range, rather than jerked. Expansions (5:30 AM, sunrise) are instant — the bulb is already inside the larger range.

7. **Perceptual brightness uses CIE L\*.** Linear lumen steps look fine at the top and feel huge at the bottom because perception is roughly cube-root in luminance. We step the user-visible "intent" in L\* space so each slider increment produces the same perceived change at any level.

If a future change to the curve, cap structure, or transition behavior is being considered, validate it against these principles. The brand positioning is "alignment with the mEDI / α-opic framework, dynamic spectral sensitivity, rejection of CCT-as-circadian-proxy, no blue-light-hazard framing for circadian claims." Don't drift from that.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ User / Pico / Voice / Script / Agent                         │
│   ↓ writes brightness to                                     │
│ light.<bulb>_circadian   ← what everyone sees                │
│   ↓ stored as "intent" (0-100) inside CircadianOIOLight     │
│   ↓ on intent change OR 60s time tick:                      │
│ render(intent, now, sunset, is_day) → (brightness, cct)     │
│   ↓ calls light.turn_on on                                   │
│ light.<bulb>_raw   ← physical entity, hidden from user      │
│   ↓ Matter                                                   │
│ Korrus OIO bulb                                              │
└──────────────────────────────────────────────────────────────┘
```

Three layers:

- **Discovery + config flow** (`config_flow.py`) — scans device registry for OIO bulbs, lets user select which to wrap.
- **Wrapper entity** (`light.py`) — `CircadianOIOLight` class. Inherits from `LightEntity` + `RestoreEntity`. Persists intent across restarts. Subscribes to a 1-minute time tick that re-renders so the bulb shifts visibly as time passes even with no user input.
- **Render math** (`render.py`) — pure functions: L\* conversions, incandescent curve, cap computation, intent-to-output mapping. No HA dependencies inside `render()` itself; it takes raw inputs and returns raw outputs. This is deliberate to keep it unit-testable.

## File map

```
ha-circadian-oio/
├── CLAUDE.md                                    ← you are here
├── README.md                                    ← user-facing install/usage docs
├── DESIGN.md                                    ← deeper architecture & science doc
├── DIMMING_LOGIC.md                             ← standalone explainer of the dimming logic
├── PUBLISHING.md                                ← HACS default + brands submission guide
├── LICENSE                                      ← MIT
├── hacs.json                                    ← HACS metadata
├── pytest.ini                                   ← pytest config (asyncio mode, testpaths)
├── requirements-dev.txt                         ← pytest etc. for the fast render tests
├── requirements-test.txt                        ← PHACC for the full HA integration tests
├── .gitignore
├── .github/
│   └── workflows/
│       └── ci.yml                               ← tests (py3.12) + hassfest + HACS validation
├── custom_components/
│   └── circadian_oio/
│       ├── __init__.py                          ← config entry setup/teardown, un-hide on unload
│       ├── manifest.json                        ← HA integration manifest
│       ├── const.py                             ← constants: curves, caps, time zones
│       ├── config_flow.py                       ← discovery + setup + options UI
│       ├── light.py                             ← CircadianOIOLight entity
│       ├── render.py                            ← pure math: caps, curve, intent mapping, settings
│       ├── strings.json                         ← config flow UI text
│       ├── brand/                               ← icon.png, icon@2x.png, logo.png (Korrus "o" ring)
│       └── www/
│           └── circadian-oio-card.js            ← dashboard card, auto-registered by __init__
└── tests/
    ├── __init__.py
    ├── conftest.py                              ← stubs HA when absent; PHACC fixtures when present
    ├── test_render.py                           ← unit tests for pure render math (no HA)
    ├── test_light_plumbing.py                   ← source-level regression guards (no HA)
    ├── test_config_flow.py                      ← config + options flow against real HA (PHACC)
    └── test_light.py                            ← wrapper entity lifecycle against real HA (PHACC)
```

Everything HA-aware (entity lifecycle, services, registry access) lives in `light.py` and `config_flow.py`. The math is isolated in `render.py` and can be tested without spinning up HA.

## Key design decisions

These are choices we made deliberately. If something seems wrong, check here before "fixing" it.

1. **Single config entry, multi-bulb.** One integration setup that wraps N bulbs, rather than one entry per bulb. Simpler UX, easier to add/remove bulbs via the Options flow.

2. **Hide the underlying entity, and restore it on teardown.** When a bulb is wrapped, we set `hidden_by=RegistryEntryHider.INTEGRATION` on the underlying light entity so the user only sees the wrapper. Don't change this — exposing both creates a footgun where users adjust the raw entity and bypass the circadian logic. We record each entity's prior `hidden_by` in `hass.data[DOMAIN][entry_id]["hidden"]` (the `DATA_HIDDEN` key) and `async_unload_entry` restores it. Without this, removing a bulb from the wrap or uninstalling the integration would leave the user's real bulbs hidden forever. A prior USER hide is preserved on restore; anything else is cleared. Because the Options flow reloads the entry, removed bulbs are un-hidden on unload and simply not re-hidden on the following setup.

   **Never select our own wrapper as the underlying.** The wrapper sets its `device_info` to the wrapped bulb's device, so the wrapper entity lives on that same device. When `async_setup_entry` lists the device's `light.` entities to find the one to wrap, it MUST exclude entities whose `platform == DOMAIN`. Otherwise, on any reload, a wrapper whose entity_id sorts before the raw bulb's gets picked as its own underlying — the integration hides the wrapper and the wrapper drives itself, which both breaks control and creates a turn_on feedback loop. This was the v0.1.3 self-wrap bug; `test_does_not_wrap_its_own_wrapper_entity` guards it. The wrapper also self-heals in `async_added_to_hass`: if its own entity is INTEGRATION-hidden, it un-hides itself, so setups corrupted by the old bug recover on update.

3. **Pure render function with no HA imports.** `render.render()` takes `intent, now, next_sunset, is_day` and returns `(brightness, cct)`. This makes it trivial to test and easy to iterate on without restarting HA. Keep it this way; if you find yourself wanting to import `hass` inside `render.py`, refactor instead.

4. **Caps use `min()` of all applicable zones.** Multiple transitions can overlap (e.g., summer sunset at 8:45 PM coincides with the start of the 9 PM transition). Rather than precedence rules, we collect all applicable cap candidates and take the minimum. This is robust to overlaps and makes adding new zones easy.

5. **L\* in `[8, 100]` uses the cubic formula; below 8 uses the linear regime.** Both branches are in `lstar_from_y` / `y_from_lstar`. The transition is smooth at L\* = 8 (Y = 0.008856). Don't simplify to just the cubic — at our extended low end we're operating in the linear region.

6. **Two transition times: long for the tick, short for user actions.** The once-a-minute time-of-day re-render uses `RENDER_TRANSITION_SECONDS` (50 s), which roughly matches the tick interval so the slow drift across cap shifts chains into a continuous fade rather than visible once-a-minute steps. A direct user/script/voice/Pico change uses `USER_TRANSITION_SECONDS` (1 s) so the bulb tracks the control instead of crawling in over ~50 s. `_apply()` takes the transition as an argument; `async_turn_on` and the startup restore pass the short value, `_handle_tick` passes the long one. Don't collapse these back into a single value — applying the long transition to user actions is exactly the "bulb lags the slider by a minute" bug. If you change the tick interval, keep `RENDER_TRANSITION_SECONDS` near it.

7. **Manufacturer filter is case-insensitive substring match.** Matter VendorName fields can vary ("Korrus", "Korrus Inc.", "Ecosense / Korrus"). The substring approach is forgiving. Strings live in `const.KORRUS_MANUFACTURER_MATCHES` and are easy to extend.

8. **The schedule and bulb floor are user-tunable; the curve is not.** The Options flow exposes seven knobs — night start time, night end time, transition duration, night brightness cap, daytime max CCT, minimum brightness, and minimum CCT — stored in `entry.options`. The first five are behavior; the last two are per-bulb hardware floors (raise minimum brightness if the bulb cuts out at the bottom; raise minimum CCT to the warmest the bulb can actually render). The curve shape itself (exponent, `BASE_CCT`, the L\* phase split, the evening CCT cap) stays hard-coded in `const.py`; those define the circadian behavior and aren't safe to hand to arbitrary users. The options form's description warns that lifting the night cap or cooling the night color weakens the effect. If you add more knobs, keep schedule/intensity tunable and leave the curve math fixed.

   Mechanically: tunables flow through a frozen `render.RenderSettings` dataclass whose fields default to the `const.py` values, so `render(...)`/`compute_caps(...)` with no `settings` argument reproduce the original hard-coded behavior (this keeps the render math pure and every existing test valid). `light.py` builds a `RenderSettings` from `entry.options` via `_settings_from_options()` and passes it to each entity; an options change reloads the entry, so entities pick up new settings. Time strings ("HH:MM:SS") are parsed to minutes-since-midnight in `light.py` before reaching the pure layer. A `transition_lead_min` of 0 is valid (instant cap changes) and is guarded against division-by-zero in `compute_caps`.

## Development workflow

### Running tests

The suite has two tiers that run from the same `pytest tests/` command. Which tier executes depends on what's installed; `tests/conftest.py` detects whether Home Assistant is importable and adapts.

Fast tier — pure render math, no Home Assistant:

```bash
pip install -r requirements-dev.txt
pytest tests/
```

When HA is absent, conftest stubs the HA modules with MagicMock so `test_render.py` and `test_light_plumbing.py` run on a bare interpreter (Python 3.9+ is fine here). The two integration test modules skip themselves via `pytest.importorskip`.

Full tier — drives the entity layer and config flow against a real Home Assistant:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -r requirements-test.txt
pytest tests/
```

`requirements-test.txt` pins `pytest-homeassistant-custom-component` (PHACC), which pins an exact HA version and needs Python 3.12+. With HA present, conftest loads the PHACC plugin instead of stubbing, and `test_config_flow.py` / `test_light.py` run for real — config flow discovery and abort paths, the options-flow unwrap, the wrapper hiding/un-hiding the underlying light, and `_apply()` actually driving the underlying bulb with a (brightness, color_temp_kelvin) pair. That last one is the regression guard for the `dt_util.now()` bug: if `_apply()` raised, no downstream service call would be recorded.

CI (`.github/workflows/ci.yml`) runs the full tier on Python 3.12 plus `hassfest` and HACS validation on every push and PR. Bumping the pinned HA version means bumping the PHACC pin in `requirements-test.txt`.

Add render tests to `test_render.py` whenever you change `render.py`; add entity/flow tests to `test_light.py` / `test_config_flow.py` whenever you change `light.py` or `config_flow.py`.

### Testing in a real HA instance

1. Copy `custom_components/circadian_oio/` into your HA `config/custom_components/`
2. Restart HA
3. Settings → Devices & Services → Add Integration → "Circadian OIO"
4. Enable debug logging for the integration:
   ```yaml
   # configuration.yaml
   logger:
     default: warning
     logs:
       custom_components.circadian_oio: debug
   ```
5. Watch the log for `rendered intent=X.X -> brightness=Y, cct=ZK` lines as time passes or you adjust the wrapper.

### Iterating on the render function

Because `render.py` has no HA imports, you can iterate in a Python REPL:

```python
from datetime import datetime
from custom_components.circadian_oio.render import render

# Mid-evening, sun down, 80% intent
now = datetime(2026, 6, 1, 20, 0, 0)
brightness, cct = render(intent=80, now=now, next_sunset=None, is_day=False)
print(f"{brightness}/255, {cct}K")
```

This is the fastest iteration loop. Once render looks right, restart HA and let it run.

## Open work / known issues

These are the things we know need attention. Update as items are addressed.

- **Verify the manufacturer string.** The integration assumes OIO bulbs report manufacturer containing "korrus", "oio", or "ecosense". Confirm with a real bulb in Settings → Devices and update `const.KORRUS_MANUFACTURER_MATCHES` if needed.

- **Per-bulb floors are global-overridable but per-area scheduling is still manual.** Per-bulb overrides now exist (Options → Per-bulb overrides), so a bedroom can dim earlier than the living room. There is still no area-level grouping that sets several bulbs at once — you override them one at a time. Area-based config in the Options flow would be the next step.

- **HACS default-list submission + brand logo.** Brand assets exist in `custom_components/circadian_oio/brand/` (the electric-blue Korrus "o" ring as icon, the wordmark as logo), so HACS validation passes and the HACS store shows the icon — `ignore: brands` has been removed from CI. Still outstanding: the home-assistant/brands PR (so the icon shows in core HA's integration UI) and the hacs/default PR (so it's installable by name). See `PUBLISHING.md`; both are ready to submit using the prepared assets.

- **The dashboard card is not test-covered.** `www/circadian-oio-card.js` is browser JS auto-registered by `__init__._register_card` (best-effort, non-fatal). The render/attributes it consumes are tested, but the card rendering itself is only syntax-checked. A real HA frontend would confirm it.

## Conventions

- **Python 3.11+** to match HA's current minimum.
- **Type hints everywhere.** Even in private methods.
- **`from __future__ import annotations`** at the top of every Python file.
- **`async` for anything that touches HA APIs.** Never block the event loop.
- **No new pip dependencies** without strong justification. The `requirements` field in `manifest.json` is empty and should stay that way; everything we need is in HA core.
- **Constants in `const.py`**, not magic numbers in `light.py` or `render.py`.
- **Logging at DEBUG for routine work**, INFO for state changes the user might care about, WARNING for misconfigurations, ERROR for failures.

## For AI assistants working on this codebase

If you (Claude Code, another LLM, or any tool) are making changes:

1. **Read this file first.** It encodes the rationale behind decisions that look arbitrary in code.
2. **Don't generalize this integration to non-OIO bulbs.** That's a feature, not a bug. See "The science" above.
3. **Don't expose curve tuning to users without designing the UX.** A user accidentally setting the late-night cap to 100% defeats the integration's purpose.
4. **Render must remain a pure function.** If you need HA state inside the render path, pass it as an argument from `light.py`.
5. **Run the unit tests** before suggesting a change is complete: `pytest tests/`.
6. **Update this file** when you make architectural changes, add new design decisions, or close out items from "Open work."
7. **Match Ben's writing style.** Direct, no flattery, plain-text, no markdown bolding inside prose. Em-dash use is fine; emoji is not. This applies to commit messages, PR descriptions, and code comments.

## References

- Home Assistant developer docs: https://developers.home-assistant.io/
- LightEntity API: https://developers.home-assistant.io/docs/core/entity/light/
- Config flow: https://developers.home-assistant.io/docs/config_entries_config_flow_handler/
- Device registry: https://developers.home-assistant.io/docs/device_registry_index/
- HACS publishing: https://hacs.xyz/docs/publish/start
- Matter Light Cluster: https://csa-iot.org/all-solutions/matter/
- CIE 15:2018 colorimetry standards (for L\* and chromaticity math)
- CIE S 026:2018 α-opic action spectra (for the melanopic / circadian framing)
