# Publishing Circadian OIO to the HACS default list

Goal: let people install the integration by name in HACS instead of pasting the
repo URL, with a proper icon. This takes two PRs to Home Assistant projects, in
order. The first (brands) is a hard prerequisite for the second (HACS default).

## Where we already stand

Our repo side is ready:

- Public repo with tagged releases (v0.1.x).
- `hacs.json` with a name, `homeassistant` minimum version, and `render_readme`.
- `manifest.json` with `version`, `documentation`, `issue_tracker`, `codeowners`.
- A README, and repo description + topics set on GitHub.
- CI runs the test suite, `hassfest`, and HACS validation on every push. HACS
  validation currently passes with `ignore: brands` because the brand assets are
  not yet in the Home Assistant brands repo (step 1 below). Remove that ignore
  once step 1 is merged.

Brand assets are now in the repo at `custom_components/circadian_oio/brand/`
(`icon.png` 256x256, `icon@2x.png` 512x512 — the electric-blue "o" ring; and
`logo.png` — the trimmed wordmark). HACS checks this local `brand/` folder
before the central brands repo, so HACS validation passes without the external
PR, and the icon shows in the HACS store. The `ignore: brands` line has been
removed from CI.

The home-assistant/brands PR below is still worth doing: the icon shown in core
Home Assistant's Settings → Devices & Services comes from that central repo, not
the local folder. The same files in `brand/` are ready to submit.

## Step 1 — Add brand assets to home-assistant/brands

HACS (and Home Assistant) pull integration icons from the central
`home-assistant/brands` repository, keyed by domain. Our domain is
`circadian_oio`.

Prepare two PNGs (transparent background, square, trimmed of padding):

- `icon.png` — 256x256 (a 512x512 `icon@2x.png` is also accepted/encouraged)
- `logo.png` — wordmark, max 256 px on the shortest side (optional but nice)

Place them in the brands repo under the custom-integration path:

```
custom_integrations/circadian_oio/icon.png
custom_integrations/circadian_oio/logo.png
```

Then:

The prepared files are in this repo at `custom_components/circadian_oio/brand/`
(icon.png, icon@2x.png, logo.png) — copy them straight across.

```bash
gh repo fork home-assistant/brands --clone
cd brands
mkdir -p custom_integrations/circadian_oio
# copy icon.png / icon@2x.png / logo.png from ha-circadian-oio/custom_components/circadian_oio/brand/
git checkout -b add-circadian-oio
git add custom_integrations/circadian_oio
git commit -m "Add Circadian OIO brand assets"
git push -u origin add-circadian-oio
gh pr create --repo home-assistant/brands --fill
```

Their CI checks image dimensions and that the domain matches a real
integration. Once merged, the icon shows up in HACS and HA.

After it merges, drop the `ignore: brands` line from `.github/workflows/ci.yml`
so HACS validation runs the brands check for real.

## Step 2 — Submit to the HACS default list

Add the repo to `hacs/default` so it's searchable in HACS by name.

```bash
gh repo fork hacs/default --clone
cd default
# add the line "bh-korrus/ha-circadian-oio" to the `integration` file,
# keeping the file alphabetically sorted
git checkout -b add-circadian-oio
git add integration
git commit -m "Add bh-korrus/ha-circadian-oio"
git push -u origin add-circadian-oio
gh pr create --repo hacs/default --fill
```

The HACS bot validates the repo automatically (it must pass the same checks the
`hacs/action` job already runs, including brands — hence step 1 first). Reviews
can take a while; until merged, users add the repo as a custom repository.

## Notes

- Both PRs are outward-facing contributions to third-party repos and get human
  review there. Nothing here submits them automatically.
- Keep the version in `manifest.json` bumped and a matching GitHub release cut
  for each change; HACS surfaces releases as the installable versions.
