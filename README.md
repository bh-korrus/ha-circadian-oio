# Circadian OIO

A Home Assistant integration that wraps OIO (Korrus) circadian bulbs and exposes them as single-axis dimmers. The user only sees a brightness slider; brightness and CCT under the hood are computed from the slider position and the current time of day, following a physically grounded incandescent dim curve and circadian-appropriate caps.

## What this does

Each wrapped OIO bulb appears in Home Assistant as a normal dimmable light. When the user changes the slider, voice command, scene, or Pico remote sets brightness, the integration translates that "intent" (0–100%) into the right combination of physical brightness and color temperature:

- **Day:** intent 100% → 6500 K at full output. Intent slides down through the incandescent curve.
- **30 min before sunset:** CCT cap shifts linearly from 6500 K to 2700 K.
- **Evening:** 2700 K at full output, dimming follows the incandescent curve.
- **30 min before 9 PM:** brightness cap slides from 100% down to 10%, CCT cap follows the curve.
- **9 PM – 5:30 AM:** capped at 10% brightness, ~2030 K. Sliding the slider lower walks CCT down to 800 K while staying at minimum brightness (candle-flame regime).

The bulb's actual output shifts automatically as time passes, even with no user input — so a bulb left at 80% will get warmer and dimmer as the evening progresses, without anyone touching it.

## Why OIO-only

This integration is intentionally scoped to OIO/Korrus bulbs because the incandescent curve and circadian cap logic assume a Planckian (blackbody) emitter at each CCT step. OIO's tunable-white architecture delivers a real Planckian-matched spectrum with appropriate melanopic content at every step on the curve.

Applying this curve to an RGBW bulb would produce a metameric color match at the wrong spectral content — the dim-to-warm experience would look right but the alpha-opic and melanopic values would be wrong, defeating the point. For that reason the integration filters on manufacturer at the device-registry level and only wraps OIO bulbs.

## The curve

Brightness mapping uses the CIE L\* perceptual scale, so each step on the slider produces the same perceived brightness change at any level.

CCT follows the empirical incandescent dim relationship:

```
CCT = 2700 × (B / 100) ^ 0.124
```

derived from the tungsten filament empirical laws L ∝ V^3.4 and CCT ∝ V^0.42. Below the curve's natural floor (~1357 K at 0.39% brightness) the integration extends the warm end down to 800 K at constant minimum brightness, mimicking deep candle-flame dim.

## Installation

### Via HACS (recommended)

1. In HACS → Integrations, click the three-dot menu → Custom repositories
2. Add `https://github.com/bh-korrus/ha-circadian-oio` as type "Integration"
3. Install Circadian OIO
4. Restart Home Assistant
5. Settings → Devices & Services → Add Integration → Circadian OIO

### Manual

1. Copy `custom_components/circadian_oio/` into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Settings → Devices & Services → Add Integration → Circadian OIO

## Configuration

On adding the integration, you'll see a list of OIO bulbs discovered via the device registry (filtered by manufacturer string). Select which ones to wrap. Each wrapped bulb's underlying physical entity is hidden from the UI; the wrapper appears under the same device.

To change the wrapped selection later: Settings → Devices & Services → Circadian OIO → Configure.

## Using the wrapped lights

Wrapped lights behave like any normal dimmable light:

- **Lovelace slider:** drag the brightness slider; both axes update behind the scenes
- **Voice:** "Set the kitchen light to 50%" works as expected
- **Pico / scene / script:** call `light.turn_on` with `brightness` or `brightness_pct`; do not pass `color_temp_kelvin` (it's ignored — the integration owns that axis)
- **Pico raise / lower:** call `light.turn_on` with `brightness_step` or `brightness_step_pct`. The integration handles the rest.

Example Pico mapping (replace device IDs):

```yaml
triggers:
  - trigger: device
    device_id: YOUR_PICO_ID
    domain: lutron_caseta
    type: press
    subtype: raise
actions:
  - action: light.turn_on
    target:
      entity_id: light.your_oio_bulb_circadian
    data:
      brightness_step_pct: 5
```

## Limitations and known issues

- **Manufacturer string:** the integration matches devices whose manufacturer field contains "korrus", "oio", or "ecosense" (case-insensitive). If your bulbs report something different, edit `KORRUS_MANUFACTURER_MATCHES` in `const.py`.
- **Per-bulb time zones:** all wrapped bulbs share the global time-zone schedule. If you want a bedroom bulb to start its late-night ramp earlier than the living room, that's not supported yet.
- **No options for tuning the curve:** the curve and cap values are constants in `const.py`. Future versions will expose these via Options Flow.
- **Brightness floor:** some OIO bulbs may refuse to stay lit at `brightness: 1`. If a bulb turns off when commanded to its lowest level, raise `MIN_BRIGHTNESS` in `const.py` to 2 or 3 and reload.

## Roadmap

- Options flow for curve and cap tuning
- Per-bulb or per-area time-zone overrides
- Sunrise CCT expansion (currently sunrise is an instant cap lift)
- Optional auto-track mode: bulb gently moves toward "ideal for now" intent when idle

## License

MIT
