# hal0 test harness — internal contributor guide

End-to-end harness for hal0. Drives install → CLI → slot lifecycle →
uninstall on the local host (and optionally on the `hal0-test` LXC via
the existing `scripts/release-test.sh`). Emits one structured JSON
row per scenario; a fail flags one specific surface, not the whole
pipeline.

This is the **contributor doc** — what to run, how it's wired, how to
add coverage. For the catalogue of bugs the harness already found,
see [`FINDINGS.md`](FINDINGS.md).

---

## 1. Where harness fits in PLAN §10

| Tier | Driver                                | Scope                          | Per-commit? |
|------|---------------------------------------|--------------------------------|-------------|
| α    | `pytest tests/ -m "not integration"`  | unit; mocked HTTP + systemd    | yes |
| β    | `pytest tests/slots/test_integration.py -m integration` | real `hal0-slot@.service` + container | yes-ish |
| γ    | `scripts/release-test.sh` over SSH    | NPU + ROCm + Vulkan matrix on `hal0-test` LXC | release ritual |
| **δ** (new) | **`scripts/harness.sh`** | **install + CLI + slot + uninstall on the dev host** | **on demand** |

α covers code paths. β covers one slot lifecycle with a real
container. γ covers the provider matrix on real hardware. **δ
covers the developer's first-five-minutes journey**: does
`install.sh --dev` work, do all 35 CLI subcommands return 0, does
a slot create-load-chat-unload-delete round-trip pass, does the
uninstaller clean up.

The δ tier is the one a contributor runs after touching `installer/`,
`src/hal0/cli/`, or any user-facing surface. It is the fastest way to
catch "I broke `hal0 config validate`" before it ships.

---

## 2. Quick start

```
bash scripts/harness.sh
```

Output is a colourised table + a JSON report at
`tests/harness/reports/harness.json`. Exit code 0 iff no row is
`fail`.

Opt-in flags:

```
HAL0_HARNESS_PROD=1  bash scripts/harness.sh     # also do sudo /opt/hal0 install + uninstall
HAL0_HARNESS_TLS=1   HAL0_HARNESS_PROD=1  bash scripts/harness.sh   # +TLS-default Caddy install (per ADR-0001)
HAL0_HARNESS_KEEP=1  bash scripts/harness.sh     # keep tmp prefix after run for debugging
```

Per-tier scripts are runnable standalone (useful when iterating on
one specific tier — saves the 6 s install round-trip):

```
bash tests/harness/installer-test.sh
bash tests/harness/cli-test.sh        # reads reports/.api-handoff from a prior installer run
bash tests/harness/runtime-test.sh    # same; needs API + binary + toolbox image
bash tests/harness/harness-cleanup.sh # always run last
```

Pretty-print a previous run without re-executing:

```
python3 scripts/harness-report.py tests/harness/reports/harness.json
```

---

## 3. Status vocabulary

Every row reports one of four statuses. Pick the right one — `fail`
is reserved for **defects in hal0**.

| Status     | When to use it |
|------------|----------------|
| `pass`     | The thing worked. |
| `fail`     | A real bug in hal0 — the harness exits non-zero if any row is `fail`. |
| `skip`     | Scenario intentionally not exercised this run (e.g. TLS-default install without `HAL0_HARNESS_TLS=1`). Also for dependent rows after an upstream row already failed. |
| `deferred` | The capability exists but can't be tested in this environment, **and that's not hal0's fault** — releases.hal0.dev DNS, disk space, toolbox image not yet public, etc. Distinct from `skip` so `FINDINGS.md` can list them separately. |

Rule of thumb: if a green CI machine with the right env vars would
make this row pass, it's a `skip`. If the underlying capability isn't
ready yet at all, it's `deferred`.

---

## 4. File layout

```
scripts/
  harness.sh              # orchestrator — runs all four tiers, merges reports, exit code
  harness-report.py       # pretty-printer for the aggregate JSON
  release-test.sh         # γ tier (pre-existing) — SSH to hal0-test LXC
  release-test-report.py  # γ tier pretty-printer (pre-existing)

tests/harness/
  README.md               # this doc
  FINDINGS.md             # rolling list of bugs the harness has found
  lib/
    common.sh             # shared row writer, log helpers, timer
  installer-test.sh       # δ.1 — install.sh + filesystem + units + idempotency
  cli-test.sh             # δ.2 — every CLI subcommand against live API
  runtime-test.sh         # δ.3 — one real /v1/chat/completions round-trip
  harness-cleanup.sh      # δ.4 — kill API, rm prefix, opt-in prod uninstall
  reports/
    .api-handoff          # ephemeral handoff between tiers (HAL0_API_URL, HAL0_HOME, HAL0_SERVE_PID)
    installer.json        # per-tier reports, hal0.harness-report.v1
    cli.json
    runtime.json
    cleanup.json
    harness.json          # merged aggregate
    cli-<row>.log         # per-row stdout/stderr (useful when investigating fails)
```

Reports + logs are gitignore-worthy but currently not gitignored —
`tests/harness/reports/` is wiped at the top of every `scripts/harness.sh`
run, so the working tree stays clean if you only ever invoke the
orchestrator.

---

## 5. Report schema (hal0.harness-report.v1)

Per-tier file (e.g. `cli.json`):

```json
{
  "_schema": "hal0.harness-report.v1",
  "generated": 1747345332,
  "tier": "cli",
  "host": "hal0-dev",
  "summary": { "total": 20, "pass": 17, "fail": 1, "skip": 0, "deferred": 2 },
  "rows": [
    {
      "name": "cli-version",
      "status": "pass",
      "duration_ms": 153,
      "detail": "hal0 --version"
    },
    ...
  ]
}
```

Aggregate file (`harness.json`):

```json
{
  "_schema": "hal0.harness-report.v1",
  "generated": 1747345332,
  "tiers": [
    {"name": "installer", "status": "ok", "summary": {...}, "report": "tests/harness/reports/installer.json"},
    {"name": "cli",       "status": "ok", "summary": {...}, "report": "tests/harness/reports/cli.json"},
    ...
  ],
  "summary": { "total": 41, ... },
  "rows": [
    { "tier": "installer", "name": "dev-install", "status": "pass", ... },
    ...
  ]
}
```

Row fields are stable — anything downstream (dashboards, CI checks)
can pin to `_schema = "hal0.harness-report.v1"` and read
`summary.fail > 0` as the gate.

The `scripts/release-test.sh` driver uses a parallel schema
`hal0.release-gate-report.v1`; `harness.sh` automatically folds a
non-baseline release-gate report into the aggregate as a fifth tier
named `release-gate`.

---

## 6. Adding a new row

A row is a few lines of bash in the appropriate tier script. The
shared lib at `tests/harness/lib/common.sh` exposes:

```bash
start=$(start_ms)
# ... do the thing ...
add_row "<row-name>" "<status>" "$(since_ms "${start}")" "<detail>"
```

Example — pretend we want to assert `/api/settings` round-trips a
PUT:

```bash
# In cli-test.sh, after the slot/model rows:
log_step "Row: settings-roundtrip"
start=$(start_ms)
ORIG_TIMEOUT=$(curl -fsS "${HAL0_API_URL}/api/settings" | python3 -c \
    "import json,sys; print(json.load(sys.stdin)['dispatcher']['prefetch_timeout_s'])")
if curl -fsS -X PUT "${HAL0_API_URL}/api/settings" \
       -H 'content-type: application/json' \
       -d '{"dispatcher":{"prefetch_timeout_s": 12.0}}' >/dev/null \
   && [[ "$(curl -fsS "${HAL0_API_URL}/api/settings" | python3 -c \
       "import json,sys; print(json.load(sys.stdin)['dispatcher']['prefetch_timeout_s'])")" == "12.0" ]]; then
    add_row "settings-roundtrip" "pass" "$(since_ms "${start}")" \
        "GET → PUT → GET preserves dispatcher.prefetch_timeout_s"
    # Restore.
    curl -fsS -X PUT "${HAL0_API_URL}/api/settings" \
        -H 'content-type: application/json' \
        -d "{\"dispatcher\":{\"prefetch_timeout_s\": ${ORIG_TIMEOUT}}}" >/dev/null
else
    add_row "settings-roundtrip" "fail" "$(since_ms "${start}")" \
        "settings round-trip didn't persist; current=$(...)"
fi
```

Naming conventions:

- Row name: `<scope>-<action>`, kebab-case, ≤ 32 chars. Examples:
  `dev-install`, `cli-slot-create`, `runtime-chat-roundtrip`.
- Detail: ≤ 80 chars after `pass`, longer for `fail` / `deferred`
  (those carry diagnostic context). Cite `file:line` when blaming a
  specific code path.

Two convenience wrappers in `cli-test.sh` show the common shapes:

```bash
run_row "<name>" 0       "<pass-detail>" -- "${HAL0_BIN}" <cmd...>
run_row "<name>" nonzero "<pass-detail>" -- "${HAL0_BIN}" <cmd that should error>
```

`run_row` handles timing, stdout/stderr capture (under
`reports/cli-<name>.log`), and emits the row. Use it for any
subcommand whose contract is just "exits 0" or "exits non-zero".

For rows that need richer post-condition checks (file presence,
parsing output, comparing to a snapshot), write the row inline like
the settings example above — `run_row` is sugar, not the only way.

---

## 7. Adding a new tier

Tiers are bash scripts under `tests/harness/`. Template:

```bash
#!/usr/bin/env bash
# tests/harness/<tier>-test.sh
set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

REPORT="${SCRIPT_DIR}/reports/<tier>.json"
harness_init "<tier>" "${REPORT}"

# Pick up handoff from installer-test.sh if you need a live API.
HANDOFF="${SCRIPT_DIR}/reports/.api-handoff"
[[ -r "${HANDOFF}" ]] && source "${HANDOFF}"

# ... rows ...

harness_write_report || true
exit 0
```

Then add it to `scripts/harness.sh` in the run order. Tiers that
need the live API must run **after** `installer-test.sh` and
**before** `harness-cleanup.sh`.

---

## 8. The handoff file

`installer-test.sh` writes
`tests/harness/reports/.api-handoff` after the `dev-api-up` row
succeeds:

```
HAL0_API_URL=http://127.0.0.1:18080
HAL0_HOME=/home/halo/dev/hal0/.harness/install-12345
HAL0_SERVE_PID=12346
```

`cli-test.sh` and `runtime-test.sh` `source` this file at the top so
they pick up the live API without spawning their own. `harness-cleanup.sh`
reads it last to kill the server and rm the prefix, then deletes the
file so a follow-up run starts clean.

If you run a tier standalone after the orchestrator has already
cleaned up, the handoff is gone — you'll need to recreate it by
hand or re-run `installer-test.sh` first.

---

## 9. Opt-in env flags

| Flag | Effect | Used by |
|------|--------|---------|
| `HAL0_HARNESS_PROD=1` | Enable rows that mutate `/etc`, `/var/lib`, `/usr/lib` via real `sudo bash installer/install.sh` and `installer/uninstall.sh`. | `installer-test.sh:prod-no-start`, `harness-cleanup.sh:prod-uninstall` |
| `HAL0_HARNESS_TLS=1` | Enable the TLS-default install (installs Caddy + renders the Caddyfile per [ADR-0001](../../docs/adr/0001-collapse-edge-auth-into-fastapi.md); renamed from `HAL0_HARNESS_AUTH` when Caddy stopped doing edge auth). Implies PROD=1. | `installer-test.sh:tls-default` |
| `HAL0_HARNESS_KEEP=1` | When running `installer-test.sh` standalone, keep the tmp prefix after exit. No effect through the orchestrator (orchestrator always cleans). | `installer-test.sh` |
| `HAL0_HARNESS_API_PORT=<n>` | Port `hal0 serve` binds during the install test (default 18080). | `installer-test.sh:dev-api-up` |
| `HAL0_DOCTOR_PORTS="<p1> <p2>..."` | Space-separated TCP ports `hal0 doctor`'s port-collision check probes. Defaults to `"18080 13001"` inside `cli-test.sh` so the row doesn't trip on a co-resident prod install bound to the canonical `8080 3001`. Override to match your dev install if non-default. | `cli-test.sh:cli-doctor` |

The orchestrator passes the environment through unchanged, so set
these in front of `bash scripts/harness.sh`.

---

## 10. Troubleshooting

### "tier dies mid-run"

A tier script exit code != 0 means the *script itself* (not a row)
crashed — usually a `set -e` trip in glue code, not an assertion
failure. Check `reports/.api-handoff` exists if the installer tier
got through `dev-api-up`. If not, scroll up for the `dev-install`
log path printed in the row detail.

### "everything's a skip"

The toolbox image probably can't be pulled (ghcr.io unauthorised,
see FINDINGS §8) and `runtime-test.sh` short-circuits all its
provider rows. CLI and installer tiers don't depend on the image.

### "cleanup leaves stuff behind"

`harness-cleanup.sh:dev-manual-cleanup` only removes paths under the
ephemeral `${HAL0_HOME}` (the `.harness/install-<pid>` dir). If you
ran with `HAL0_HARNESS_PROD=1`, the prod uninstaller may have left
the data dirs in place (intentionally — `--keep-data`). Check
`/etc/hal0`, `/var/lib/hal0`.

### "wanted to test against the real /etc/hal0"

That's prod mode: `HAL0_HARNESS_PROD=1 bash scripts/harness.sh`. The
installer harness will do a real `sudo bash installer/install.sh
--no-start` and assert units exist but aren't started.

### "want to run against hal0-test LXC instead"

The δ harness is local-only. For Strix Halo / NPU / ROCm, use
`bash scripts/release-test.sh` (γ tier). `scripts/harness.sh`
automatically folds the result of `tests/release-gate-report.json`
into the aggregate report if it's recent.

### "results changed between runs and I want to diff"

The orchestrator clobbers `reports/*.json` at the top of each run.
To diff:

```
cp tests/harness/reports/harness.json /tmp/before.json
# … make changes …
bash scripts/harness.sh
diff <(python3 -c "import json; [print(r['name'], r['status']) for r in json.load(open('/tmp/before.json'))['rows']]") \
     <(python3 -c "import json; [print(r['name'], r['status']) for r in json.load(open('tests/harness/reports/harness.json'))['rows']]")
```

---

## 11. CI integration (not yet wired)

A GitHub Actions job that runs the δ tier on every PR would look
like (rough sketch, not committed):

```yaml
- name: hal0 harness (δ)
  run: bash scripts/harness.sh
- name: Upload harness report
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: harness-report
    path: tests/harness/reports/harness.json
```

Runners need:
- Python 3.11+ (matches the package's own floor)
- `bash`, `curl`, `python3` (everything else the install script needs
  is checked by `installer/lib/preflight.sh`)
- A writable home dir for the ephemeral prefix
- ≥ 1 GB free disk (the harness's own footprint is small; the
  installer's preflight wants 20 GB but the dev path doesn't enforce
  it as hard)

Don't enable `HAL0_HARNESS_PROD=1` in CI unless the runner is
disposable — it really does `sudo` and writes to real `/etc`.

---

## 12. Relationship to existing tests

The harness is **additive**. It doesn't replace anything under
`tests/{api,auth,cli,config,...}` — those keep providing fast
unit-level coverage that this harness can't (and shouldn't) duplicate.

What it adds:

- Realistic install/uninstall round-trip (no unit-level equivalent).
- CLI subcommands exercised against a *real* server, not mocked
  httpx. Catches "we changed the route prefix" bugs that pure unit
  tests miss because they assert against the FastAPI app directly.
- Cross-cutting assertions like "after install, slot create + delete
  via the CLI both succeed in the same run."

What it does **not** add:

- Provider matrix coverage — that's γ's job.
- UI E2E — Playwright in `ui/tests/e2e/` owns that.
- Unit-level error paths — those belong in pytest.

When picking where a new test lives, the rule:

- **Pure assertion about a function or class** → pytest under `tests/`.
- **Asserts the operator-visible flow** (install, run a CLI, hit the
  API end-to-end) **and runs locally** → δ harness here.
- **Asserts a specific hardware backend (NPU, ROCm, FLM)** → γ tier
  (`scripts/release-test.sh`).
- **Asserts a UI interaction** → Playwright γ.

---

## 13. Schema bump policy

`hal0.harness-report.v1`. Bump to `.v2` when any of:

- A field's type changes (e.g. `duration_ms` becomes a float).
- A new required field is added (e.g. `correlation_id`).
- A tier's `status` values expand (we currently use ok / missing).

Additive optional fields don't require a bump.
