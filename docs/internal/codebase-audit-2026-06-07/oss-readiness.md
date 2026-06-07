# OSS Release Readiness Audit — hal0
> Audited: 2026-06-07 against main (working tree, untagged post-v0.3.2-alpha.1)
> Auditor: B3 agent (read-only)

hal0 has strong OSS bones: Apache-2.0 LICENSE, CONTRIBUTING.md, a clean pyproject.toml, and no committed real secrets.
The blockers are a version-number scatter, a handful of lab-specific hostnames baked into source templates, and the docs/internal/ tree shipping publicly with private-lab IP addresses.

---

## 1. License

| Finding | File | Severity |
|---|---|---|
| LICENSE file present, Apache-2.0, complete | `LICENSE` | OK |
| pyproject.toml `license = { text = "Apache-2.0" }` | `pyproject.toml:15` | OK |
| No SPDX headers on any Python source file | All `src/hal0/**/*.py` | low |
| No `CODE_OF_CONDUCT.md` at repo root | `/` | med |
| No `SECURITY.md` at repo root | `/` | med |

**Notes:**
- SPDX headers (`# SPDX-License-Identifier: Apache-2.0`) are a community expectation for Apache projects but not strictly required — low priority.
- `gateway/bifrost-src/` has its own `CODE_OF_CONDUCT.md`, `SECURITY.md`, and `LICENSE`, but that directory is untracked (gitignored via `.graphifyignore`). The hal0 repo root has none of these.
- No CLA mechanism or contributor agreement exists — intentional per `CONTRIBUTING.md` which says the contribution model is still open, but worth deciding before a public merge window opens.

---

## 2. Secrets and Private Credentials

No real API keys, passwords, or private tokens were found committed. The one pattern that looks like a key is a deliberate placeholder:

```
src/hal0/memory/cognee_wrapper.py:114  _NOOP_LLM_API_KEY = "sk-hal0-noop-v0.2-no-cognify"
```
This is not a real key — it is a sentinel string fed to Cognee to skip its LLM calls. Fine as-is.

---

## 3. Private IPs and Internal Hostnames Committed in Source

These are the actionable items — lab-specific addresses that expose the operator's homelab topology and break out-of-the-box config for any other installer.

### 3a. BLOCKER — `https://hal0.thinmint.dev` hardcoded as default fallback (source shipped to users)

```
src/hal0/agents/hermes_provision.py:2023
    "dashboard_url": os.environ.get("HAL0_DASHBOARD_URL", "https://hal0.thinmint.dev"),
src/hal0/agents/hermes_templates/HERMES.md.j2:40
    - The dashboard at https://hal0.thinmint.dev shows live slot state.
src/hal0/agents/hermes_templates/AGENTS.md.j2:10
    inference platform; the dashboard at https://hal0.thinmint.dev shows live state.
src/hal0/api/agents/_auth.py:61
    "https://hal0.thinmint.dev",   # DEFAULT_ALLOWED_ORIGINS tuple
```

`thinmint.dev` is the author's private LAN domain (Traefik reverse proxy on 10.0.1.200). `_auth.py` ships this as the **default WebSocket origin allowlist** and the Jinja2 templates write it verbatim into every user's agent config files at `hal0 agent provision hermes`. `hermes_provision.py` falls back to it when `HAL0_DASHBOARD_URL` is unset.

**Fix:** Replace default fallback with `http://hal0.local:8080` (or derive from `HAL0_API_BASE_URL`). Update `DEFAULT_ALLOWED_ORIGINS` to exclude `thinmint.dev` (keep `hal0.local` + `localhost:5173` + `127.0.0.1:8080`). Parametrize the Jinja2 templates via the `dashboard_url` variable that `hermes_provision.py` already computes.

### 3b. Med — `10.0.1.*` addresses in scripts and packaging docs (lab-specific, misleading for external users)

| File | Line | Value | Context |
|---|---|---|---|
| `scripts/prototype_ttft/live_probe.py` | 15, 31 | `10.0.1.142:8088` | Default `BASE` env var |
| `scripts/import_haloai_models.py` | 11, 140 | `10.0.1.220:8080` | Default `--haloai` arg; CT 220 decommissioned 2026-05-27 |
| `scripts/release-test.sh` | 30 | `10.0.1.230` | Default `HAL0_TEST_HOST` |
| `scripts/proxmox-ve/README.md` | 64 | `10.0.1.150/24, gw 10.0.1.1` | Static-IP example |
| `packaging/proxmox/hal0-test-template/README.md` | 15 | `10.0.1.1 10.0.1.200` | Nameserver line |
| `packaging/proxmox/hal0-test-template/README.md` | 17 | `--searchdomain thinmint.dev` | Search domain |
| `installer/lib/ui.sh` | 261 | `10.0.1.230:8080` | Comment-only example in `ui_box` docstring |

`scripts/prototype_ttft/live_probe.py` and `scripts/import_haloai_models.py` both fallback to private IPs — any external contributor running these scripts without the env var set will get a confusing connection error.  `import_haloai_models.py` also references CT 220 which is decommissioned.

**Fix:** Change defaults to `localhost` or `127.0.0.1`. Replace `thinmint.dev` searchdomain with `hal0.local` in the template README. The `import_haloai_models.py` script should probably be updated or archived.

### 3c. Low — Mock/demo UI files with `halo-strix.local` hostname

```
ui/src/api/mock.ts:124      hostname: d.host?.name ?? 'halo-strix.local',
ui/src/api/mock.ts:216      origins: ['http://halo-strix.local:8081', 'http://localhost:5174']
ui/src/dash/mcp-data.jsx:5  const MCP_HOST_BASE = "https://halo-strix.local";
```

`mcp-data.jsx` is unconditionally imported in `ui/src/main.tsx:69` — it is **not** guarded by `VITE_MOCK_LEMONADE`. `MCP_HOST_BASE` ends up in every production bundle as a JSX constant and is used to construct MCP server URLs shown in the dashboard. `halo-strix.local` is a device-specific mDNS name from the author's lab.

**Fix:** `mcp-data.jsx` mock data should derive `MCP_HOST_BASE` from `window.location.origin` or a config constant rather than a hardcoded lab hostname. The mock-only fallback in `mock.ts` is lower risk (only used under `VITE_MOCK_LEMONADE=1`).

### 3d. Low — `docs/internal/archive/` and other internal docs committed with private IPs

The `docs/internal/` tree is **not gitignored** and is fully committed. It contains:
- `docs/internal/archive/SESSION_HANDOFF_2026-05-22.md:22` — `10.0.1.142`
- `docs/internal/archive/lemonade-spike-2-findings-2026-05-22.md:5` — `root@10.0.1.142`
- `docs/internal/archive/dashboard-v2-implementation-plan-2026-05-23.md:19` — `10.0.1.142`
- `docs/internal/install-test-harness.md:53` — `10.0.1.193`
- `docs/internal/install-test-harness.md:78` — `10.0.1.1 10.0.1.200`
- `docs/internal/primary-model-eval-2026-05-22.md:5` — `10.0.1.142`

These docs don't pose a security risk (they're dev notes referencing a LAN host) but they are confusing to external contributors and expose lab network topology. `.gitignore` already excludes `docs/handoff-*.md` — the broader `docs/internal/` set should either be gitignored or sanitised before a public launch.

---

## 4. README Quality and Quickstart Accuracy

| Finding | Location | Severity |
|---|---|---|
| README version badge says "v0.2.0" but pyproject.toml is `0.3.2-alpha.1` | `README.md:32` | high |
| `docs/v0.2-upgrade.md` linked from README does not exist | `README.md:36` | med |
| CONTRIBUTING.md says "hal0 is at **v0.2.0**" — stale | `CONTRIBUTING.md:3` | med |
| manifest.json `version` field says `0.1.0-alpha.1` vs pyproject `0.3.2-alpha.1` | `manifest.json` (top-level `version`) | med |

The README quickstart (`curl | bash`) is otherwise accurate. The development quickstart (`pip install -e ".[dev]"` → `npm install`) matches reality.

---

## 5. Packaging Metadata

`pyproject.toml` is well-formed and complete:
- `[project.urls]` has Homepage and Repository.
- `license`, `authors`, `keywords`, `classifiers` all set.
- Console scripts `hal0` and `hal0-agent` are declared.
- `[tool.hatch.build.targets.wheel]` correctly points at `src/hal0`.
- Version is derived from `importlib.metadata` at runtime — single source of truth.

**Issues:**
- `pyproject.toml` Repository URL is `https://github.com/hal0ai/hal0` (lowercase org). The README and install scripts use `Hal0ai/hal0` (mixed-case). GitHub URLs are case-insensitive but the inconsistency is cosmetically confusing.
- No `Documentation` URL in `[project.urls]` — minor.
- `mcp==1.27.0` is pinned exactly (not `>=`). This creates friction for users who have other MCP tools that pin differently. An alpha/beta qualifier on a tight pin is common but worth noting.
- `cognee==1.0.7` is similarly exact-pinned. The `pyproject.toml` comment explicitly justifies this, so it is intentional.

---

## 6. .gitignore Gaps

```
.gitignore:93  docs/handoff-*.md      # only handoff files excluded
```

**Gaps:**

| Gap | Risk |
|---|---|
| `docs/internal/` not excluded | Internal dev notes (private IPs, lab architecture) ship publicly |
| `graphify-out/graph.json`, `graphify-out/GRAPH_REPORT.md`, `graphify-out/manifest.json` not excluded | Tool-generated AST dump ships with the repo; large files that no external user needs |
| `CLAUDE.md` (untracked, tool config) not in .gitignore | Tool-specific config for Claude Code — not harmful but noisy |
| `.graphifyignore` (untracked) not in .gitignore | Tool config, same |
| `gateway/` (untracked) not in .gitignore | Bifrost vendor copy — `graphifyignore` excludes it from the graph but it's not in `.gitignore`, so `git status` is noisy |

---

## 7. Embarrassing / Internal-Only Content

### 7a. `scripts/import_haloai_models.py` — references a decommissioned host
File exists to scrape CT 220 (`haloai`, `10.0.1.220`) which was decommissioned 2026-05-27. The default `--haloai` arg points at a dead host. To an external user this script is confusing and orphaned. Either remove or update the docstring to clarify it is a maintainer-only tool for updating the seed file.

### 7b. `_legacy_toolboxes` block in `manifest.json` with `"digest": "placeholder"`
The top-level comment says this block will be removed after Phase 5. It is still present and the placeholder digests are technically inert (the code reads `toolbox_images`, not `_legacy_toolboxes`), but `"placeholder"` strings in a shipped JSON file look unprofessional.

### 7c. `docs/internal/archive/` — unredacted field notes referencing lab
Multiple files contain `ssh hal0` / `root@10.0.1.142` as literal commands and references to the Strix Halo LXC 105 topology. These are archived spike runbooks, not operator docs, but they ship publicly (see §3d above). Low security impact, high cosmetic impact.

### 7d. `CONTRIBUTING.md` still blocks external PRs
"External PRs aren't being merged yet; please open issues for discussion." — this is appropriate for pre-v1.0 but should be updated when the project opens contributions.

### 7e. `src/hal0/registry/curated.py:378` — casual internal comment
```
# Q4_K_M is already running in production on hal0 LXC.
```
Not a blocker, but looks like a personal dev note in a public-facing curated model registry. Minor polish item.

---

## 8. Checklist Summary

| # | Item | Severity | Status |
|---|---|---|---|
| 1 | LICENSE present (Apache-2.0) | — | ✓ |
| 2 | No committed real secrets/tokens | — | ✓ |
| 3 | `thinmint.dev` as default WS allowlist origin | **high** | BLOCKER |
| 4 | `thinmint.dev` in shipped Jinja2 agent templates | **high** | BLOCKER |
| 5 | `https://hal0.thinmint.dev` as default `dashboard_url` fallback | **high** | BLOCKER |
| 6 | README version says v0.2.0 (actual: v0.3.2-alpha.1) | **high** | Fix before tag |
| 7 | `docs/v0.2-upgrade.md` linked but missing | med | Fix |
| 8 | CONTRIBUTING.md version stale | med | Fix |
| 9 | `manifest.json` version stale (0.1.0-alpha.1) | med | Fix |
| 10 | No `CODE_OF_CONDUCT.md` at repo root | med | Add |
| 11 | No `SECURITY.md` at repo root | med | Add |
| 12 | Private IPs in scripts default args | med | Fix defaults |
| 13 | `docs/internal/` not gitignored | med | Decide: exclude or sanitise |
| 14 | `mcp-data.jsx` uses `halo-strix.local` in production bundle | med | Fix to derive from origin |
| 15 | `graphify-out/*.json` not gitignored | low | Add to .gitignore |
| 16 | `_legacy_toolboxes` placeholder digests in manifest.json | low | Remove block |
| 17 | `import_haloai_models.py` references decommissioned CT 220 | low | Archive/update |
| 18 | No SPDX headers on Python source | low | Nice-to-have |
| 19 | `hal0 LXC` / `hal0-dev` informal comments in source | low | Cosmetic cleanup |

---

## Cross-cutting Seams

- **`_auth.py` → deployment model**: The `DEFAULT_ALLOWED_ORIGINS` list directly couples the WebSocket security boundary to the operator's specific reverse-proxy topology. This should be reconciled with the A-series agents auditing the API auth surface.
- **`hermes_provision.py` → agents subsystem**: The `dashboard_url` default value flows into Jinja2 templates that populate the Hermes agent's system prompt. The agents B-series and the API B-series should both be aware that this default needs a generic fallback.
- **`manifest.json` version → installer + updater**: `manifest.json:version` is read by `src/hal0/updater/updater.py` and the release workflow. The stale `0.1.0-alpha.1` value could cause version checks to behave unexpectedly during an update cycle.
- **`docs/internal/` public shipping → documentation audit**: The D-series agent auditing docs should flag this entire tree as requiring an access decision before public launch.
