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
