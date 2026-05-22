# Contributing to hal0

hal0 is licensed [Apache 2.0](./LICENSE). hal0 is in **v0.1.0-alpha**;
the contribution model is still being decided (see
[`PLAN.md`](./PLAN.md) §16). External PRs aren't being merged yet;
please open issues for discussion.

When the model opens up, the shape will be:

- One PR per feature; small, reviewable diffs
- Run `make lint test` before pushing
- Update `PLAN.md` if your change moves the v0.1 scope
- Slot/dispatcher/provider changes require both unit and integration
  tests (Tier-1 reliability is non-negotiable)
- UI changes need Playwright coverage for any new critical path

## Test tiers

hal0's test strategy (PLAN §10) is three tiers, each with a different
cadence and a different runtime ceiling. Every PR runs the unit + the
integration tier; the release-gate tier is `hal0-test` LXC territory and
is the last gate before a tagged release.

| Tier | What it does | Where it runs | When | Local cmd |
|---|---|---|---|---|
| α  Unit | `pytest`, mocked systemd/docker/HTTP | any host, no daemons | every commit / PR | `make test` |
| β  Integration | Real `hal0-slot@.service` + Vulkan-CPU toolbox + tiny GGUF; load → chat → swap → unload + SSE state stream | GitHub Actions runner (`integration.yml`) **and** any host with systemd + the template installed | every PR; required for merge | `make test-integration` |
| γ  Release-gate | Full matrix — Vulkan, ROCm, NPU/FLM, STT/TTS smokes, OpenWebUI proxy, updater round-trip | `hal0-test` LXC (10.0.1.230) over SSH | per release candidate, not per-commit | `make release-test` |

### α — unit (`make test`)

```sh
make test            # runs pytest with `-m "not integration"`
```

Pure pytest. No systemd, no docker, no network. ~3 s on the dev VM.
The 425+ baseline tests live under `tests/` and shouldn't grow much
slower than that — integration-flavoured cases must be marked
`@pytest.mark.integration` so they're excluded by default.

### β — integration (`make test-integration`)

Exercises the real systemd template unit and a real container. Needs
root (the template lands in `/etc/systemd/system/`) and Docker.

Locally:

```sh
sudo bash installer/install.sh --no-start    # writes hal0-slot@.service
make test-integration
```

In CI: `.github/workflows/integration.yml` does the install on the
runner, builds `hal0-toolbox-vulkan` from `packaging/toolbox/vulkan.Dockerfile`,
caches `Qwen/Qwen2.5-0.5B-Instruct-GGUF`, and runs three gated cases
in `tests/slots/test_integration.py`:

1. `test_end_to_end_load_serve_unload` — full create → load → unload → delete
2. `test_state_transitions_visible_via_stream` — SSE state stream sees `starting → warming → ready`
3. `test_full_state_machine_round_trip_via_stream` — full round-trip incl. `unloading → offline`

Wall-clock budget: ≤12 minutes (PLAN §10, Integration β). Toolbox
image build is layer-cached via GHA so cold-cache runs are ~10 min,
hot-cache ~4.

### γ — release-gate (`make release-test`)

SSHes into the hal0-test LXC and walks a matrix of seven rows:
**vulkan, rocm, flm (NPU), moonshine (STT), kokoro (TTS), updater,
openwebui**. Each row produces a structured record; the full report
lands in `tests/release-gate-report.json`.

```sh
# Set HAL0_TEST_SSH_KEY to whatever key authorises you on your test host
# (defaults to ~/.ssh/id_ed25519; override via env or make var).
make release-test

# Override host / key:
make release-test HAL0_TEST_HOST=10.0.1.231 HAL0_TEST_SSH_KEY=~/.ssh/my-test-key

# Pretty-print the most recent report:
make release-test-report
```

Row status is one of `pass | fail | skip | deferred`:

- **skip** — a required image isn't pinned in `manifest.json` yet
  (Team A territory). Non-blocking.
- **deferred** — a cross-team dependency isn't merged yet (e.g.
  Team D's updater CLI). Non-blocking, but flagged in the report.
- **fail** — exits the script non-zero; blocks the release.

The hal0-test LXC is shared with other agents; `make release-test`
uses a per-run prefix (`ci-h-<job-id>` in CI, `ci-h-local-<pid>` from
a developer machine) and tears every slot it created down on exit,
even on failure.

### Pre-tag check

`scripts/release-check.sh` is the ritual you run **before** cutting a
tag. It walks the per-release gate:

- backend tests green
- UI build clean
- toolbox images present in `manifest.json` with non-empty digests
- `release-test` last run within 24 h and all-pass
- git working tree clean, tag doesn't yet exist, `pyproject.toml`
  version matches the proposed tag

If any of these fail, fix and re-run before `git tag`.

## E2E tests

The `ui/tests/e2e/` Playwright suite covers the seven critical paths
from PLAN §10 (E2E γ) — FirstRun wizard, slot lifecycle, model
management, settings + restart banner, logs SSE tail, hardware probe,
update banner. All seven run on every PR via
`.github/workflows/playwright.yml` in <8 minutes against mocked
backends.

```bash
cd ui
npm install                  # one-time, picks up @playwright/test
npm run test:e2e:install     # downloads Chromium (~150 MB, one-time)

npm run test:e2e             # full suite, headless
npm run test:e2e:ui          # Playwright UI mode (local dev)
npx playwright test firstrun # one spec only
```

### Mock vs live backend

Default mode mocks every `/api/*` endpoint via `page.route` — no
backend required. To exercise the real API against the `hal0-test`
LXC (or any live install):

```bash
HAL0_E2E_LIVE=1 npm run test:e2e
```

The `hal0-test` LXC at `10.0.1.230` is the standing target for
release-gate runs. The Vite dev server's `vite.config.js` proxy
already forwards `/api/*` + `/v1/*` to that host, so live mode just
needs the env var. Live-mode adjusts test timeouts (180s per spec,
30min wall-clock) to fit real model pulls and slot warm-up.

### Adding a spec

1. Drop a new file in `ui/tests/e2e/specs/`. Import `test`, `expect`,
   `json` from `../fixtures/apiMock` and the SSE helpers from
   `../fixtures/sseHarness` if you need an event stream.
2. Override the routes you care about by calling `page.route(...)`
   inside the test body — these take precedence over the fixture's
   defaults (Playwright matches routes in reverse-registration order).
3. Keep each spec <90s. If you need a `data-testid`, document the
   addition in the PR description so the lid on UI churn stays low.

## Reasoning tools

Some chunks of hal0 have a *teaching prototype* checked in alongside
the production code — a tiny TUI you can drive by keystroke to feel
out the data model before changing it. These aren't tests; they're
debugger-replacements for design questions.

- `scripts/prototype_ttft/` — TTFT + KV-cache aggregation model that
  feeds the dashboard's per-slot tiles and fleet-avg throughput card.
  See `docs/internal/metrics-prototype.md`.

  ```sh
  ssh -t hal0 'cd /opt/hal0 && make proto-ttft'        # logic TUI
  ssh    hal0 'cd /opt/hal0 && make proto-ttft-live'   # client-side validator
  ```

If you're tweaking the rule that decides "should this slot count
toward the fleet average?", start in the TUI.

