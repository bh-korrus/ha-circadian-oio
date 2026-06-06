/*
 * Circadian OIO dashboard card.
 *
 * A small Lovelace card for a "(Circadian)" wrapper light. It shows the current
 * color (derived from the rendered color temperature), the brightness, the
 * circadian period, and what transition is coming next — using the state
 * attributes the integration publishes.
 *
 * Dependency-free vanilla custom element so it can be auto-registered by the
 * integration without bundling a framework. Add to a dashboard with:
 *   type: custom:circadian-oio-card
 *   entity: light.your_bulb_circadian
 */

const PERIOD_LABELS = {
  day: "Daytime",
  sunset_transition: "Sunset transition",
  evening: "Evening",
  pre_night: "Winding down",
  night: "Night",
  morning_ramp: "Morning ramp",
  pre_sunrise: "Pre-sunrise",
};

// Approximate sRGB for a color temperature in kelvin (Tanner Helland's fit).
function cctToRgb(kelvin) {
  const t = Math.max(800, Math.min(40000, kelvin)) / 100;
  let r, g, b;
  if (t <= 66) {
    r = 255;
    g = 99.4708025861 * Math.log(t) - 161.1195681661;
  } else {
    r = 329.698727446 * Math.pow(t - 60, -0.1332047592);
    g = 288.1221695283 * Math.pow(t - 60, -0.0755148492);
  }
  if (t >= 66) {
    b = 255;
  } else if (t <= 19) {
    b = 0;
  } else {
    b = 138.5177312231 * Math.log(t - 10) - 305.0447927307;
  }
  const clamp = (x) => Math.max(0, Math.min(255, Math.round(x)));
  return `rgb(${clamp(r)}, ${clamp(g)}, ${clamp(b)})`;
}

class CircadianOioCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("circadian-oio-card: 'entity' is required");
    }
    this._config = config;
  }

  getCardSize() {
    return 2;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    const hass = this._hass;
    const cfg = this._config;
    if (!hass || !cfg) return;

    const stateObj = hass.states[cfg.entity];
    if (!stateObj) {
      this.innerHTML = `<ha-card><div style="padding:16px">Entity ${cfg.entity} not found</div></ha-card>`;
      return;
    }

    const a = stateObj.attributes;
    const on = stateObj.state === "on";
    const name = cfg.name || a.friendly_name || cfg.entity;
    const cct = a.rendered_color_temp_kelvin || 2700;
    const intent = a.intent != null ? Math.round(a.intent) : null;
    const period = PERIOD_LABELS[a.circadian_period] || a.circadian_period || "—";
    const nextName = PERIOD_LABELS[a.next_transition] || a.next_transition;
    const nextMin = a.minutes_to_next_transition;
    const swatch = on ? cctToRgb(cct) : "#2b2b2b";
    const opacity = on && intent != null ? 0.25 + 0.75 * (intent / 100) : 0.3;

    const nextLine =
      nextName != null && nextMin != null
        ? `Next: ${nextName} in ${nextMin} min`
        : "";

    this.innerHTML = `
      <ha-card>
        <div class="coio-wrap">
          <div class="coio-swatch" style="background:${swatch};opacity:${opacity}"></div>
          <div class="coio-info">
            <div class="coio-name">${name}</div>
            <div class="coio-big">${on ? (intent != null ? intent + "%" : "On") : "Off"}</div>
            <div class="coio-sub">${on ? period : "—"}</div>
            <div class="coio-sub">${on ? `${cct} K` : ""}</div>
            <div class="coio-next">${on ? nextLine : ""}</div>
          </div>
        </div>
      </ha-card>
      <style>
        .coio-wrap { display:flex; align-items:center; gap:16px; padding:16px; cursor:pointer; }
        .coio-swatch { width:64px; height:64px; border-radius:14px; flex:0 0 auto;
          box-shadow: inset 0 0 0 1px rgba(0,0,0,0.1); transition: background .5s, opacity .5s; }
        .coio-info { display:flex; flex-direction:column; }
        .coio-name { font-weight:600; }
        .coio-big { font-size:1.8em; line-height:1.1; }
        .coio-sub { color: var(--secondary-text-color); font-size:0.9em; }
        .coio-next { color: var(--secondary-text-color); font-size:0.85em; margin-top:4px; }
      </style>
    `;

    this.querySelector(".coio-wrap").addEventListener("click", () => {
      this.dispatchEvent(
        new CustomEvent("hass-more-info", {
          detail: { entityId: cfg.entity },
          bubbles: true,
          composed: true,
        })
      );
    });
  }
}

customElements.define("circadian-oio-card", CircadianOioCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "circadian-oio-card",
  name: "Circadian OIO Card",
  description: "Shows the current circadian color, brightness, and what's next.",
});
