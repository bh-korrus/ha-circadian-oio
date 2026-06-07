"""Regression guards for the HA-facing plumbing in light.py.

These don't spin up Home Assistant — the wrapper entity subclasses LightEntity,
which can't be imported under the lightweight stubs in conftest. Instead they
assert on the source so a known footgun can't quietly come back.

The one that matters: _apply() once called datetime.now(tz=self.hass.config.time_zone).
hass.config.time_zone is an IANA string, not a tzinfo, so that raised TypeError
on every render and the integration could never drive a bulb. The fix is to use
homeassistant.util.dt.now(), which returns a tz-aware local datetime.
"""
from __future__ import annotations

from pathlib import Path

LIGHT_SRC = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "circadian_oio"
    / "light.py"
).read_text()


def test_apply_does_not_build_datetime_from_tz_string():
    """datetime.now(tz=hass.config.time_zone) is the bug; it must not return."""
    assert "datetime.now(tz=" not in LIGHT_SRC
    assert "config.time_zone" not in LIGHT_SRC


def test_apply_uses_dt_util_now():
    """The render path must source 'now' from HA's tz-aware helper."""
    assert "dt_util.now()" in LIGHT_SRC


def test_translations_mirror_strings():
    """Custom integrations load flow text from translations/en.json, not
    strings.json. The two must exist and match, or the menu renders blank."""
    import json

    base = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "circadian_oio"
    )
    strings = json.loads((base / "strings.json").read_text())
    en = json.loads((base / "translations" / "en.json").read_text())
    assert en == strings, "translations/en.json must mirror strings.json"
    # The options menu labels must be present (the missing-labels bug).
    menu = en["options"]["step"]["init"]["menu_options"]
    assert set(menu) == {"bulbs", "defaults", "overrides"}
