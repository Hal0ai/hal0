# Contributing to hal0

hal0 is licensed [Apache 2.0](./LICENSE). hal0 is at **v0.8.2b3** — the
container-runtime line (one podman container per slot). The contribution model is still
being decided.
<!-- TODO(human): flip the next line to open the external-PR merge window
     when ready (audit Q6.3 / #630). Timing is the maintainer's call. -->
External PRs aren't being merged yet; please open issues for discussion.

When the model opens up, the shape will be:

- **Sign off every commit** — `git commit -s` (Developer Certificate of
  Origin; see [below](#developer-certificate-of-origin-dco))
- One PR per feature; small, reviewable diffs
- Run `make lint test` before pushing
- Update the maintainer planning doc (`docs/.devdocs/PLAN.md`, local-only) if your change moves the scope
- Slot/dispatcher/provider changes require both unit and integration
  tests (Tier-1 reliability is non-negotiable)
- UI changes need Playwright coverage for any new critical path

## Anti-scar rules

hal0's architecture is sound, but rapid iteration leaves *scars*: dead
abstractions, duplicate sources of truth, oversized modules, divergent
implementations, stale docs, and shims that outlive their purpose. These
rules keep new work from adding scars. They apply to every PR — CI
enforces the ones marked **(gated)**.

1. **One owner per fact.** Every fact — a config value, a slot roster, a
   model-store path, a port claim — has exactly one authoritative home.
   Any other place that needs it derives a view; it never keeps a second
   copy to sync. If you find yourself writing the same truth twice, make
   one the owner and the other a projection.

2. **Delete before refactoring.** If code is unused, remove it — don't
   restructure around it. A smaller surface is worth more than a tidier
   dead one. Land the deletion first, then build on the clearing.

3. **No speculative generality.** One adapter does not justify a public
   seam. Introduce an interface, a provider ABC, or a plugin point only
   when a *second* working implementation ships — not in anticipation of
   one.

4. **Shims are temporary and expire (gated).** Any intentional
   compatibility shim carries a `# HAL0-SUNSET: v<major>.<minor>[.<patch>]`
   marker naming the release by which it must be gone. `make check-sunset`
   (`scripts/check_sunset.py`, run in CI) fails once the project version
   reaches that sunset, forcing removal. Do not add a compat path without
   a sunset.

5. **The scar baseline only goes down (gated).** The count of scar
   markers (`removed in #`, `DEPRECATED`, `legacy`, `backward-compat`,
   `compat shim`) in `src/` is frozen in `scripts/scar_baseline.txt`. CI
   fails if the live count exceeds it. A de-scar PR *lowers* the baseline
   (`python scripts/check_sunset.py --update-baseline`, then commit the
   drop); new work must not raise it — a genuinely new shim carries a
   `HAL0-SUNSET` marker instead (rule 4).

6. **Deep modules, small interfaces.** Splitting a big file is only the
   first step. Each resulting module must expose a narrow, intent-oriented
   interface that hides substantial behavior — moving code into more files
   without shrinking the interface is not a fix.

7. **TOML for human config, SQLite for machine state.** Operator-authored
   configuration stays in TOML (`/etc/hal0/*.toml`). Machine-owned runtime
   state and model metadata belong in SQLite. Don't store hand-edited
   config in a database or machine state in a TOML file.

8. **Deny-by-default exposure.** New routes are classified in the one
   exposure table; anything unclassified defaults to admin-only.
   Non-loopback binding requires authentication, and the OPEN set must not
   grow. Never widen exposure as a side effect of another change.

9. **No ghost-doc citations.** A decision is either recorded in a real,
   tracked document or explained inline where it takes effect. Do not cite
   a doc, ADR, or design note that does not exist in the tree. hal0 keeps
   no separate ADR tree: rationale lives next to the code or in
   [`ARCHITECTURE.md`](./ARCHITECTURE.md). Touched documentation must match
   the code it describes.

10. **Test behavior, not framework internals.** Probe routes and effects
    through the public surface; never assert on `app.routes` shape or other
    framework-internal structures that shift between dependency versions.
    CI installs from the lockfile (`uv sync --frozen`) so CI and local do
    not skew. This class bit twice: a floating FastAPI gave CI lazy
    `_IncludedRouter` wrappers (zero `app.routes`) while local synced eager
    routes — sinking a route-collision test and a golden-path assertion that
    were CI-only. Assert on behavior, pin the environment.

11. **Find the owner before adding a parallel.** Before you add a test, a
    migration, a config table, or a module for some fact, grep for an
    existing owner. A duplicate that later drifts is a scar (see rule 1). A
    board row once dispatched a route-collision test that already existed;
    the duplicate failed CI and had to be removed. Migration numbers are
    allocated once, at dispatch, on the board — two files at one version is a
    broken migrate.

## Developer Certificate of Origin (DCO)

hal0 uses the [Developer Certificate of Origin](https://developercertificate.org/)
(DCO) — **not** a CLA. The DCO is a lightweight per-commit attestation
that you wrote the patch or otherwise have the right to submit it under
the project's Apache-2.0 license.

Sign off each commit by adding a `Signed-off-by` trailer with your real
name and email:

```
Signed-off-by: Jane Developer <jane@example.com>
```

`git commit -s` adds this automatically (configure `user.name` /
`user.email` first). The full text you are certifying is at
<https://developercertificate.org/>.

> A DCO status check may be enabled on PRs once the external-PR merge
> window opens; until then, sign-offs are documented but not gated.

## Test tiers

hal0's test strategy is three tiers, each with a different
cadence and a different runtime ceiling. Every PR runs the unit + the
integration tier; the release-gate tier is `hal0-test` LXC territory and
is the last gate before a tagged release.

| Tier | What it does | Where it runs | When | Local cmd |
|---|---|---|---|---|
| α  Unit | `pytest`, mocked systemd/HTTP/Lemonade client | any host, no daemons | every commit / PR | `make test` |
| β  Integration | Real `hal0-lemonade.service` + tiny GGUF; load → chat → swap → unload + slot state via `/v1/health` | GitHub Actions runner (`integration.yml`) **and** any host with systemd + Lemonade installed | every PR; required for merge | `make test-integration` |
| γ  Release-gate | Full matrix — Lemonade `llamacpp` (Vulkan + ROCm + CPU), `flm:npu` trio, `whisper.cpp`, `kokoro:cpu`, `sd-cpp`, OpenWebUI proxy, updater round-trip | `hal0-test` LXC over SSH | per release candidate, not per-commit | `make release-test` |

### α — unit (`make test`)

```sh
make test            # runs pytest with `-m "not integration"`
```

Pure pytest. No systemd, no docker, no network. ~3 s on the dev VM.
The 425+ baseline tests live under `tests/` and shouldn't grow much
slower than that — integration-flavoured cases must be marked
`@pytest.mark.integration` so they're excluded by default.

### β — integration (`make test-integration`)

Exercises the real `hal0-lemonade.service` daemon. Needs root (units
land in `/etc/systemd/system/`) and the Lemonade prerequisites
(installed by `installer/install.sh`).

Locally:

```sh
sudo bash installer/install.sh --no-start    # writes hal0-lemonade.service + config.json
make test-integration
```

In CI: `.github/workflows/integration.yml` does the install on the
runner, caches `Qwen/Qwen2.5-0.5B-Instruct-GGUF`, and runs the gated
cases in `tests/slots/test_integration.py`:

1. `test_end_to_end_load_serve_unload` — full slot register → load → unload → delete via `/v1/load` + `/v1/unload`
2. `test_state_transitions_visible_via_stream` — slot state stream sees `starting → warming → ready` (from `/v1/health` polling + Lemonade `/logs/stream` events)
3. `test_full_state_machine_round_trip_via_stream` — full round-trip incl. `unloading → offline`

Wall-clock budget: ≤12 minutes (Integration β). Lemonade's
embeddable tarball is layer-cached via GHA so cold-cache runs are
~10 min, hot-cache ~4.

### γ — release-gate (`make release-test`)

SSHes into the hal0-test LXC and walks a matrix of seven rows:
**llamacpp-vulkan, llamacpp-rocm, flm (chat + trio asr/embed),
whispercpp (STT), tts (TTS), sd-cpp (image), updater,
openwebui**. Each row produces a structured record; the full report
lands in `tests/release-gate-report.json`.

```sh
# Set HAL0_TEST_SSH_KEY to whatever key authorises you on your test host
# (defaults to ~/.ssh/id_ed25519; override via env or make var).
make release-test

# Override host / key:
make release-test HAL0_TEST_HOST=192.0.2.10 HAL0_TEST_SSH_KEY=~/.ssh/my-test-key

# Pretty-print the most recent report:
make release-test-report
```

Row status is one of `pass | fail | skip | deferred`:

- **skip** — a required dependency isn't pinned in `manifest.json` yet
  (e.g. a new FastFlowLM `.deb` version waiting on a smoke). Non-blocking.
- **deferred** — a cross-team dependency isn't merged yet. Non-blocking, but flagged in the report.
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
- Lemonade embeddable tarball + FastFlowLM `.deb` pinned in `manifest.json` with non-empty sha256s
- `release-test` last run within 24 h and all-pass
- git working tree clean, tag doesn't yet exist, `pyproject.toml`
  version matches the proposed tag

If any of these fail, fix and re-run before `git tag`.

## E2E tests

The `ui/tests/e2e/` Playwright suite covers the seven critical paths
(E2E γ) — FirstRun wizard, slot lifecycle, model
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

The `hal0-test` LXC is the standing target for
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
  The prototype's own `README.md` in that directory documents the data
  model it exercises.

  ```sh
  ssh -t hal0 'cd /opt/hal0 && make proto-ttft'        # logic TUI
  ssh    hal0 'cd /opt/hal0 && make proto-ttft-live'   # client-side validator
  ```

If you're tweaking the rule that decides "should this slot count
toward the fleet average?", start in the TUI.

## Stable-patch triage

Release-candidate bands change by channel count and aren't the right
lens for triaging *one* patch. Use this decision tree for every PR
that lands on `main` (channel-count-independent per D8).
Each question is yes/no; route at the first "yes" or fall through to
**decline**.

1. **Does it fix a crash, data loss, or security issue?**
   → **Backport** to the current `stable` band. Requires the §14.1
   surfaces checklist in `.github/PULL_REQUEST_TEMPLATE.md` to be
   re-checked on the backport branch.
2. **Is it low-risk and localized** (one component, no API/schema
   change, no new dependency)?
   → **Next release.** Lands on the next `beta` → `stable` promotion.
3. **Is the same fix already on `main` and unreleased?**
   → **Next release.** Close as duplicate of the mainline commit; no
   backport branch.
4. **Does it need a release note** (user-visible behaviour change,
   new surface, deprecation, versioned schema bump)?
   → **Next release, noted.** Add a bullet to `CHANGELOG.md` and link
   it from the PR description's "Rollback" line.
5. None of the above.
   → **Decline.** Politely close with the reason; offer a follow-up
   issue if the work still has value outside the stable band.

The first question wins. A security fix on an unsupported channel is
still a backport; a cosmetic tweak is never a backport.

## Hardware support class

hal0 classifies the hardware it can drive on a slot, separate from
the **bench** (A/B/C) and **test tier** (α/β/γ) — both already
overloaded. We call it **support class** and it is the verdict
`evaluate_model_fit` in `src/hal0/model_fit.py` returns for a
`(model, slot_type, device, profile)` tuple. The three outcomes are
`allowed`, `degraded`, and `blocked`; a support-class table just maps
each outcome to the operator-visible promise.

| Class    | Outcome `evaluate_model_fit` | Operator promise |
|----------|------------------------------|------------------|
| supported | `allowed`                    | Runs as configured; no caveats in the UI or logs. |
| partial   | `degraded`                   | Runs but with a reason chip (e.g. GPU/CPU mismatch); flagged before launch. |
| blocked   | `blocked`                    | Refused at registration with a stable reason code; not a slot. |

Don't relabel the bench tiers or test tiers as "support class" — the
naming is intentional and the triage tree above assumes it.

