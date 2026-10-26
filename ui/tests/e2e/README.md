# hal0 dashboard E2E (Playwright)

Specs live in `specs/`, shared fixtures in `fixtures/` (`apiMock` seeds the
`/api/*` + `/v1/*` stubs). Run the suite from `ui/`:

```bash
npx playwright test                 # full suite (mock data, Vite dev server)
npx playwright test warm-color      # one spec by name substring
HAL0_E2E_LIVE=1 npx playwright test # live mode: proxy to real hal0-api :8080
```

The suite asserts **DOM and computed style**, not committed image snapshots —
there are no PNG baselines to update. A visual contract is pinned by reading
the value the browser resolves (e.g. `getComputedStyle(el).color`) and
asserting it.

## Running in the Playwright container

When the host has no browsers installed, run the suite in Microsoft's Playwright
image:

- The image tag must match `@playwright/test` in `ui/package.json`:
  `mcr.microsoft.com/playwright:v<version>-noble`. A mismatched browser build
  fails to launch.
- Run as the host user with a writable home, so the container neither writes
  root-owned files into `ui/` nor fails on a read-only `HOME`:
  `-u $(id -u):$(id -g) -e HOME=/tmp`.
- Mount `ui/` and run `npx playwright test --reporter=line` from it.

```bash
docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD":/work -w /work \
  mcr.microsoft.com/playwright:v1.64.1-noble \
  npx playwright test --reporter=line
```

Run it from `ui/`; the tag above matches `@playwright/test` 1.64.1, so change
both together.

## Retinting the unified warm color (`warm-color-tones.spec.ts`)

Every warming-state indicator (slot dots, infer/NPU epills, connections dot)
renders the single `--warn` token from `src/dashboard.css`; the overhaul-era
`--warming` alias points at it. To change the warm color:

1. Edit `--warn` (and, if you also want to adjust the glow, `--warming-glow`)
   in `src/dashboard.css`.
2. Update the `WARM_RGB` constant at the top of
   `specs/warm-color-tones.spec.ts` to the new `rgb(...)` value.
3. `npx playwright test warm-color` — a green run confirms every warming site
   still resolves to the one token (a stray hex or a divergent `--warming`
   re-fork fails here).
