Verified against `/home/mint/hal0` @ `rework/descar`. Every file:line below is current code. Plan refs: `/home/mint/hal0-rework-plan.md` §21.4 (retrofit + bundle; ~1–2 weeks), §21.2 (gfx-guard diagnosis IDs), §21.1 (host-tuning preflight WARN checks), §21.11 (golden-paths evidence), §13 (metrics tables = evidence source), §23.4 (`§13.3 tables + §21.2 gfx diagnosis ID ─PREREQ→ §21.4 doctor rework evidence`, line 1643), §24.2 W8 (this spec lands in W8; §24.3 row "§21.4 doctor + bundle edit-plan"), §14.1 (auth on bundle ingest is KB-1 scoped, not in this spec).

---

# §21.4 `hal0 doctor` rework + support bundle — implementation spec

Retrofit, not greenfield. The 1,420-line `doctor_commands.py` + 385-line `doctor_verify.py` already implement perms/models/profiles/migrations/toolbox-pull/verify/logs + `preflight.sh` shell-out + the ad-hoc `repair_flm_store`/`repair_tree_group_share` autofixes. This spec retrofits every existing check onto **one** `_diagnosis(id, severity, confidence, evidence[], next_steps[])` dataclass + stable ID taxonomy + `--json` renderer, **adds** `hal0 doctor bundle`, and wires §21.2's gfx-guard + §13's metrics tables as diagnosis sources.

---

## 0. Current state — what's broken and where

### 0.1 `doctor_commands.py` is 1,420 lines of check-by-check code

The file is one Typer sub-app (`doctor_app`, `doctor_commands.py:59`) with five concrete subcommands (`toolbox-pull`, `perms`, `models`, `migrations`, `profiles`) + the `doctor` callback that shells out to `installer/lib/preflight.sh` + a `doctor_verify_cmd` + `doctor_logs`. **Every subcommand renders its own Rich table independently** — there is no shared return type. An operator running `hal0 doctor models` then `hal0 doctor perms` gets two visually-different reports with no common vocabulary for "what's broken."

| Check | file:line | Render path | Dataclass? |
|---|---|---|---|
| `check_hermes_ownership` (Hermes drift) | `doctor_commands.py:527-577` | dict rows → `_render_audit` (`:702-714`) | no — `dict[str,str]` rows |
| `check_tree_group_share` (editable checkout share) | `doctor_commands.py:616-670` | dict rows → `_render_audit` | no |
| `repair_tree_group_share` (`--fix` chgrp/chmod/setgid/git) | `doctor_commands.py:673-699` | inline `console.print` of result | returns `(bool,str)` |
| `flm_store_divergence` (env vs TOML) | `doctor_commands.py:887-907` | inline `console.print` from `doctor_models` (`:1108-1111`) | returns `dict[str,str] \| None` |
| `flm_mount_guard` (`/mnt/...` not mounted) | `doctor_commands.py:922-948` | inline (`:1114-1118`) | returns `dict[str,str] \| None` |
| `flm_store_writability` (uid 1000 writable?) | `doctor_commands.py:951-975` | inline (`:1120-1151`) | returns `dict[str,object] \| None` |
| `repair_flm_store` (chown 1000:hal0 + chmod 2775) | `doctor_commands.py:978-998` | inline (`:1139-1144`) | returns `(bool,str)` |
| registry entries → file existence | `doctor_commands.py:1047-1067` | inline | counts + console prints |
| store/roots agreement + unregistered scan | `doctor_commands.py:1068-1101` | inline | counts + console prints |
| `pending_layout_migration` (v0.1→v0.2 symlink farm) | `doctor_commands.py:1164-1194` | inline in `doctor_migrations` (`:1211-1227`) | returns `(int,int) \| None` |
| `check_slot_profile_refs` (slot → profile name resolves?) | `doctor_commands.py:1240-1272` | `_render_profiles` (`:1343-1359`) | returns `list[dict[str,str]]` |
| `check_profile_images_present` (image pulled?) | `doctor_commands.py:1284-1317` | `_render_profiles` | returns `list[dict[str,str]]` |
| `_local_image_repos` (podman images → set) | `doctor_commands.py:1320-1340` | consumed by `check_profile_images_present` | returns `set[str] \| None` |
| `toolbox-pull` ghcr.io probe (one per image) | `doctor_commands.py:386-433`, rendered at `:489-510` | JSON or table | returns `list[dict[str,Any]]` |

The `doctor` callback (default, no subcommand) shells out to `preflight.sh` (`doctor_commands.py:171-210`) — that's the §21.1 host-tuning WARN checks path (`installer/lib/preflight.sh:7-30` names `preflight_systemd`, `preflight_python`, `preflight_container_runtime`, `preflight_gpu`, `preflight_disk`, `preflight_ports`, `preflight_bootstrap_prereqs`, `preflight_all`). The shell-out preserves the script's exit code verbatim (`doctor_commands.py:209-210`); **no per-check structure ever crosses the boundary** — operator sees coloured lines and a non-zero rc.

### 0.2 `doctor_verify.py` is 385 lines with a different return type again

The `--verify`/`verify` subcommand (rendered report card, hidden flag at `doctor_commands.py:135-141`, first-class at `doctor_commands.py:216-228`) is the WS-K post-setup report card. It composes **live API endpoints**, not the same checks as `doctor_commands.py`:

| Check | file:line | Source endpoint |
|---|---|---|
| `check_api` (anchor critical) | `doctor_verify.py:69-81` | `GET /api/health` |
| `check_dns` (mDNS/.local) | `doctor_verify.py:84-105` | `GET /api/config/urls` |
| `check_runners` (anchor critical) | `doctor_verify.py:108-132` | `GET /api/health/system` → `checks.slot_manager` |
| `check_capabilities` | `doctor_verify.py:135-150` | `GET /api/capabilities` |
| `check_memory` (Hindsight/banks) | `doctor_verify.py:153-163` | `GET /api/memory/engine` |
| `check_openwebui` | `doctor_verify.py:183-188` (via `_service_check` `:166-180`) | `GET /api/services/health` |
| `check_hermes` | `doctor_verify.py:187-188` | `GET /api/services/health` |

`Check` dataclass (`doctor_verify.py:50-63`) is the one near-good shape — `(key, label, status, detail, critical)` — but it's **not** the dataclass the plan calls for (`_diagnosis(id, severity, confidence, evidence[], next_steps[])` per `hal0-rework-plan.md:1418`). Two vocabularies co-exist. Exit codes: `run_verify` returns 2 on critical, 0 else (`doctor_verify.py:367`).

### 0.3 CLI registration

`doctor_app = typer.Typer(...)` (`doctor_commands.py:59`), mounted as `app.add_typer(doctor_app, name="doctor")` (`main.py:68`). One subapp, one default callback, six subcommands (`verify`, `logs`, `toolbox-pull`, `perms`, `models`, `migrations`, `profiles`). No `--json` flag anywhere except `toolbox-pull --json` (`doctor_commands.py:438-442`).

### 0.4 What is **absent** today (the gaps this spec closes)

| Gap | Where it should land | Today |
|---|---|---|
| Stable diagnosis-ID taxonomy (`HAL0-GFX-TARGET-UNSUPPORTED`, `HAL0-ROCM-LIB-MISSING`, `HAL0-MODEL-FILE-MISSING`, `HAL0-HOST-NOT-TUNED`, `HAL0-PERMS-*`) | Every check returns one `_diagnosis` row carrying an ID | No IDs anywhere — only `dict[str,str]` keys |
| `--json` flag for every subcommand | `hal0 doctor perms --json`, `hal0 doctor models --json`, etc. | Only `toolbox-pull --json` exists (`doctor_commands.py:438`) |
| Structured autofix hints | `_diagnosis.next_steps[]` carries `hal0 doctor X --fix` commands + man-page links | Only free-text `console.print` ("Fix: hal0 model rm <id> && hal0 model scan", `doctor_commands.py:1061-1063`) |
| `hal0 doctor bundle` support bundle | New `bundle` subcommand | Absent |
| §21.2 gfx-arch guard emitting `HAL0-GFX-TARGET-UNSUPPORTED` | Plan §21.2(a) explicitly: "Surface pass/fail as doctor diagnosis ID `HAL0-GFX-TARGET-UNSUPPORTED`" (`hal0-rework-plan.md:1399`) | Probe infra exists (`mcp/probes.py:80-130` decodes `gfx_target_version` → `gfxNNNN`); no integration with doctor |
| §21.1 host-tuning WARN checks (gttsize/tuned-adm/amd_iommu) surfaced as `HAL0-HOST-NOT-TUNED` | Plan §21.1: "add WARN-only read-checks to `installer/lib/preflight.sh`" (`hal0-rework-plan.md:1391`) | `preflight.sh` has no `gttsize`/`tuned-adm`/`/proc/cmdline` reads yet |
| §13 metrics tables as evidence source | §21.3 wires `GET /api/stats` over `request_metric`/`slot_sample`; doctor pulls evidence | Tables absent; doctor has no DB query path |
| §21.11 golden-paths evidence | Bundle's manifest section consumes the `config/`-layout validator (§21.11 contract) | Absent |

### 0.5 What is **already wired** (this spec preserves)

- `preflight.sh` exit code preservation (`doctor_commands.py:209-210`) — operators script on it; do not break.
- `doctor_verify` non-blocking policy (`doctor_verify.py:21-23`) — only `critical` flips exit code to 2; warn is advisory. Preserve in JSON output (`critical` rows gate exit 2, `warn` rows stay advisory).
- `repair_flm_store` + `repair_tree_group_share` first-failure-wins contract (`doctor_commands.py:696-698`, `:996-997`) — `_diagnosis.next_steps[]` references these by ID and command name.
- The 700+ TestClient suite (per `hal0-rework-plan.md:1672`) — additive changes only; no signature change to `run_verify`'s callers.
- `_SENSITIVE_RE` redaction (in `api/_redact.py:37-39`, pattern: `SECRET|TOKEN|PASSWORD|PASS|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|SALT`) — bundle reuses it as the redact pass; do not roll a second masker.
- The `activity._redact` helper (`activity/__init__.py:119-134`) is a different shape (recurse-and-mask to `"***"`) — bundle uses `api._redact.redact_config` for config trees, `activity._redact` only for activity/log lines if needed.

---

## 1. Target — one `_diagnosis` dataclass + stable ID taxonomy

### 1.1 The dataclass (lives in `cli/doctor_diagnosis.py`, NEW)

```python
# src/hal0/cli/doctor_diagnosis.py
"""Shared diagnosis return type — the §21.4 retrofit backbone.

Every check returns ``list[Diagnosis]``. Every renderer (rich table, --json,
support bundle) consumes the same shape. The ``id`` is the stable contract;
the human ``label`` is for humans only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["info", "warn", "fail", "critical"]
Confidence = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Evidence:
    """One piece of proof — a probe result, a file stat, an API payload slice.

    ``kind`` lets renderers bucket (e.g. JSON grouping by kind). ``data`` is
    arbitrary JSON-serialisable; renderers MUST NOT echo ``data`` verbatim in
    the rich path (use ``summary``); the --json / bundle paths emit both.
    """
    kind: str                     # "file" | "command" | "endpoint" | "table_row" | "config"
    summary: str                  # human-readable one-liner
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NextStep:
    """One remediation affordance — a command, a doc link, or a manual action.

    ``kind="command"`` rows are runnable by `hal0 doctor fix <id>` (a future
    dispatcher — not in this spec). ``kind="manual"`` rows are operator
    instruction (e.g. "reboot after modprobe change", §21.1).
    """
    kind: Literal["command", "manual", "doc"]
    label: str                    # "run: hal0 doctor models --fix"
    target: str                   # the argv, the doc URL, or the prose body


@dataclass(frozen=True)
class Diagnosis:
    """One check's result. Stable ``id`` is the contract."""
    id: str                       # "HAL0-GFX-TARGET-UNSUPPORTED" — stable across releases
    severity: Severity
    confidence: Confidence
    summary: str                  # one-liner (used by rich path)
    detail: str = ""              # extended description (JSON: full; rich: terse)
    evidence: list[Evidence] = field(default_factory=list)
    next_steps: list[NextStep] = field(default_factory=list)
    fixable: bool = False         # True iff a registered autofix matches one of next_steps


# Roll-up: an overall verdict derived from a list of Diagnoses. Same vocabulary
# as doctor_verify.overall_status today (doctor_verify.py:212-223) — kept.
def overall_verdict(diagnoses: list[Diagnosis]) -> str:
    if any(d.severity == "critical" for d in diagnoses):
        return "critical"
    if any(d.severity in ("fail", "warn") for d in diagnoses):
        return "warn"
    return "ok"
```

### 1.2 Diagnosis-ID taxonomy (the stable contract)

IDs are `<SCOPE>-<DOMAIN>-<SHORT>`, kebab/screaming mix by SCOPE prefix. **Frozen for the spec's lifetime** — once published, a change is a breaking change (covered in §7 risk #1).

| ID | Severity | Source check | Evidence |
|---|---|---|---|
| `HAL0-HOST-NOT-TUNED` | warn (always; per §21.1) | preflight.sh `preflight_host_tuned` (NEW, §21.1 wires `gttsize`/`amd_iommu`/`tuned-adm`/`vm.swappiness` reads) | `/sys/module/amdgpu/parameters/*` + `/proc/cmdline` + `sysctl vm.swappiness` + `tuned-adm active` |
| `HAL0-GFX-TARGET-UNSUPPORTED` | fail | §21.2(a) startup probe (uses `mcp.probes.gfx_target_version` decoder at `mcp/probes.py:80-130`); WARMING→failed transition (per `hal0-rework-plan.md:1399`) | llama-server `system_info` HIP archs vs `RUNNER_IMAGES[runner].required_hip_archs` |
| `HAL0-ROCM-LIB-MISSING` | fail | §21.2 startup probe (`rocm-smi --showall` capture missing lib path) | `rocminfo` + `rocm-smi` stdout |
| `HAL0-MODEL-FILE-MISSING` | fail | `doctor_models` registry-entries-→-file-existence (`doctor_commands.py:1055`) | `{model_id, path}` rows |
| `HAL0-MODEL-UNREGISTERED` | warn | `doctor_models` unregistered scan (`doctor_commands.py:1080-1100`) | `{file_path}` rows |
| `HAL0-MODEL-STORE-MISSING` | fail | `doctor_models` store-dir-absent (`doctor_commands.py:1091-1092`) | `{store_path}` |
| `HAL0-MODEL-FLM-STORE-DIVERGED` | warn | `flm_store_divergence` (`doctor_commands.py:887-907`) | `{env_val, toml_val}` |
| `HAL0-MODEL-FLM-STORE-UNMOUNTED` | warn | `flm_mount_guard` (`doctor_commands.py:922-948`) | `{store_path, deepest_mountpoint}` |
| `HAL0-MODEL-FLM-STORE-NOT-WRITABLE` | fail | `flm_store_writability` (`doctor_commands.py:951-975`) | `{uid, mode}` |
| `HAL0-MIGRATION-PENDING` | warn | `doctor_migrations` (`doctor_commands.py:1211-1227`) | `{create_count, overwrite_count}` |
| `HAL0-PROFILE-REF-DANGLES` | fail | `check_slot_profile_refs` (`doctor_commands.py:1240-1272`) | `{slot, missing_profile}` rows |
| `HAL0-PROFILE-IMAGE-MISSING` | warn | `check_profile_images_present` (`doctor_commands.py:1284-1317`) | `{profile, used_by, image}` rows |
| `HAL0-PERMS-HERMES-DRIFT` | fail | `check_hermes_ownership` (`doctor_commands.py:527-577`) | `{path, label, status}` rows |
| `HAL0-PERMS-TREE-NOT-SHARED` | fail | `check_tree_group_share` (`doctor_commands.py:616-670`) | per-row `drift` |
| `HAL0-PERMS-PATH-OWNERSHIP-DRIFT` | fail | `perms_mod.audit_rows` (`doctor_commands.py:792-796`) | per-row drift |
| `HAL0-TOOLBOX-IMAGE-UNREACHABLE` | fail | `_probe_one` (`doctor_commands.py:386-433`) | `{image, error}` |
| `HAL0-TOOLBOX-IMAGE-DIGEST-DRIFT` | warn | `matches_pin=False` row | `{image, pinned_digest, actual_digest}` |
| `HAL0-API-UNREACHABLE` | critical | `check_api` (`doctor_verify.py:69-81`) — anchors `doctor_verify` | `health.json` |
| `HAL0-RUNNERS-NONE-HEALTHY` | critical | `check_runners` (`doctor_verify.py:108-132`) | `{healthy, errored}` |
| `HAL0-DNS-LOCAL-UNRESOLVED` | warn | `check_dns` (`doctor_verify.py:84-105`) | `{host}` |
| `HAL0-CAPABILITIES-NONE` | warn | `check_capabilities` (`doctor_verify.py:135-150`) | `{selections}` |
| `HAL0-MEMORY-ENGINE-UNREACHABLE` | warn | `check_memory` (`doctor_verify.py:153-163`) | `{engine, reachable}` |
| `HAL0-OPENWEBUI-DOWN` | warn | `check_openwebui` (`doctor_verify.py:183-188`) | `{detail}` |
| `HAL0-HERMES-DOWN` | warn | `check_hermes` (`doctor_verify.py:187-188`) | `{detail}` |

**Always-info diagnostics** (emit but never fail): `HAL0-DOCTOR-OK` (no problems), `HAL0-DOCTOR-SKIPPED` (check deliberately skipped — e.g. podman absent → `_local_image_repos` returns `None`).

### 1.3 `doctor_verify.Check` (existing, `doctor_verify.py:50-63`) → `Diagnosis` adapter

A single shim translates `Check` → `Diagnosis` so `run_verify` can keep its callers and the JSON renderer can walk a flat list:

```python
# In doctor_verify.py (refactor, additive):
def to_diagnosis(c: Check) -> Diagnosis:
    """Map a Check row to a Diagnosis row. ID mapping is the spec contract (§1.2)."""
    id_map = {
        "api": "HAL0-API-UNREACHABLE",
        "dns": "HAL0-DNS-LOCAL-UNRESOLVED",
        "runners": "HAL0-RUNNERS-NONE-HEALTHY",
        "capabilities": "HAL0-CAPABILITIES-NONE",
        "memory": "HAL0-MEMORY-ENGINE-UNREACHABLE",
        "openwebui": "HAL0-OPENWEBUI-DOWN",
        "hermes": "HAL0-HERMES-DOWN",
    }
    sev = "critical" if (c.status == _FAIL and c.critical) else ("warn" if c.status == _WARN else "fail" if c.status == _FAIL else "info")
    return Diagnosis(
        id=id_map[c.key],
        severity=sev,
        confidence="high",                  # the verify checks are unconditional probes
        summary=c.label,
        detail=c.detail,
        evidence=[Evidence(kind="endpoint", summary=c.detail)],
        next_steps=[],
    )
```

`run_verify` (`doctor_verify.py:350-368`) gains a `json_output: bool = False` parameter that emits `json.dumps([d.to_dict() for d in diagnoses])` and a `bundle_output: Path | None` that emits the bundle (§4).

---

## 2. Per-check retrofit (the actual edit surface)

Each row of §0.1's table gains a one-line wrapper that converts the existing helper's `dict[str,str]` / `(bool,str)` return into a `Diagnosis`. **Helpers stay.** Renderers change.

### 2.1 `doctor perms` (Hermes + tree-share + path-ownership)

- `check_hermes_ownership` (`doctor_commands.py:527-577`) → wrapped by new `_diagnose_hermes_ownership()`: each `drift` row → one `Diagnosis(id=HAL0-PERMS-HERMES-DRIFT, severity=fail, ...)`. `absent` rows → emit nothing (or `info` `HAL0-DOCTOR-SKIPPED`).
- `check_tree_group_share` (`doctor_commands.py:616-670`) → `_diagnose_tree_group_share()`: each `drift` row → `HAL0-PERMS-TREE-NOT-SHARED`. `next_steps[0]` = `NextStep(kind="command", label="sudo hal0 doctor perms --fix", target="hal0 doctor perms --fix")`.
- `perms_mod.audit_rows` (`doctor_commands.py:792-796`) → `_diagnose_path_ownership()`: same pattern, `HAL0-PERMS-PATH-OWNERSHIP-DRIFT`.
- `--fix` flag (`:719-723`) untouched — still invokes `repair_tree_group_share` (`:673-699`) and `perms_mod.commit` (`:837`). After `--fix`, re-run the diagnosis and emit a `Diagnosis(id=HAL0-PERMS-TREE-NOT-SHARED, severity=info, ...)` per fixed row (proves the repair; consumer can compare `before`/`after`).

### 2.2 `doctor models` (registry + store/roots + FLM)

- Registry-entries-→-file-existence (`doctor_commands.py:1047-1067`) → `_diagnose_registry_files()`: each missing file → `HAL0-MODEL-FILE-MISSING`. `next_steps[0]` = `NextStep(kind="command", label="hal0 model rm <id> && hal0 model scan", target=...)`.
- Unregistered-files-in-store (`:1080-1100`) → `HAL0-MODEL-UNREGISTERED`.
- Store-dir-absent (`:1091-1092`) → `HAL0-MODEL-STORE-MISSING`.
- `flm_store_divergence` (`doctor_commands.py:887-907`) → `HAL0-MODEL-FLM-STORE-DIVERGED`. `fixable=False` (operator decision: unset env var OR align TOML).
- `flm_mount_guard` (`doctor_commands.py:922-948`) → `HAL0-MODEL-FLM-STORE-UNMOUNTED`. `next_steps[0]` = manual: "reorder systemd unit to add `RequiresMountsFor=`".
- `flm_store_writability` (`doctor_commands.py:951-975`) → `HAL0-MODEL-FLM-STORE-NOT-WRITABLE`. `fixable=True` iff `--fix`. `next_steps[0]` = `hal0 doctor models --fix`.

### 2.3 `doctor migrations`

- `pending_layout_migration` (`doctor_commands.py:1164-1194`) → `HAL0-MIGRATION-PENDING` (always warn; the migration is operator-initiated). `next_steps[0]` = `hal0 migrate model-layout --apply`.

### 2.4 `doctor profiles`

- `check_slot_profile_refs` (`doctor_commands.py:1240-1272`) → `HAL0-PROFILE-REF-DANGLES` per `drift` row. `next_steps[0]` = `hal0 slot edit <slot> --profile <name>`.
- `check_profile_images_present` (`doctor_commands.py:1284-1317`) → `HAL0-PROFILE-IMAGE-MISSING` per warn row. `next_steps[0]` = `podman pull <image>`.

### 2.5 `doctor toolbox-pull`

- `_probe_one` rows (`doctor_commands.py:386-433`) → `HAL0-TOOLBOX-IMAGE-UNREACHABLE` (ok=False) or `HAL0-TOOLBOX-IMAGE-DIGEST-DRIFT` (matches_pin=False). `evidence[0]` = the probe's `data` dict (already JSON-safe).

### 2.6 `doctor verify` (live API composition)

- `run_verify` (`doctor_verify.py:350-368`) calls `to_diagnosis(c)` per `Check`. Output is a list of `Diagnosis`s; rich render unchanged (`render_report` `:261-282`), `--json` emits `{"verdict", "diagnoses": [...]}`. Existing exit-code contract preserved (`:367`).

---

## 3. New subcommand: `hal0 doctor bundle` (support bundle)

The plan calls for "redact KEY/TOKEN/Bearer from config dumps, emit a command-status TSV, layout system/config/diagnostics/logs/manifest, include `rocm-smi --showall` + `rocminfo` captures" (`hal0-rework-plan.md:1420`).

### 3.1 Layout

```
hal0-doctor-bundle-<hostname>-<UTC-ts>/
├── manifest.json            # generated first — every other section's metadata
├── commands.tsv             # command, exit_code, stdout_bytes, stderr_bytes, ts (one row per probe)
├── system/
│   ├── uname.txt
│   ├── os-release.txt
│   ├── cmdline.txt          # /proc/cmdline (for amd_iommu + boot params)
│   ├── tuned.txt            # tuned-adm active
│   ├── amdgpu-params.txt    # /sys/module/amdgpu/parameters/{gttsize,ppfeaturemask,gpu_recovery,pages_limit,page_pool_size}
│   ├── sysctl.txt           # vm.swappiness, vm.vfs_cache_pressure
│   ├── rocminfo.txt         # `rocminfo` (captured)
│   ├── rocm-smi.txt         # `rocm-smi --showall` (captured)
│   ├── rocm-smi-version.txt
│   ├── cpuinfo.txt
│   ├── meminfo.txt
│   ├── df.txt               # df -h / df -i
│   ├── systemctl-hal0.txt   # systemctl list-units hal0-*
│   ├── podman-images.txt    # `podman images --format json`
│   └── network.txt          # `ss -tlnp` (listening ports)
├── config/
│   ├── hal0.toml            # REDACTED via api._redact.redact_config
│   ├── api.env              # REDACTED (every KEY/TOKEN/Bearer line)
│   ├── slots/               # each slot TOML, REDACTED
│   ├── profiles.toml
│   ├── capabilities.toml
│   └── manifest.json        # raw, no redaction (no secrets)
├── diagnostics/
│   ├── doctor.json          # output of `hal0 doctor --json` (the full Diagnosis list)
│   ├── verify.json          # `hal0 doctor verify --json`
│   ├── toolbox-pull.json    # `hal0 doctor toolbox-pull --json`
│   ├── models.json          # GET /api/models
│   ├── slots.json           # GET /api/slots
│   ├── hardware.json        # GET /api/hardware
│   ├── stats.json           # GET /api/stats (§21.3 — sequence with §13)
│   └── system-info.json     # GET /api/system-info (§21.3)
├── logs/
│   ├── hal0-api.log         # `journalctl -u hal0-api -n 1000 --no-pager`
│   ├── hal0-slot-*.log      # one per slot, -n 500 each
│   └── hal0-agent.log       # Hermes (provision logs)
└── doctor-summary.txt       # human-readable report-card (the rich path, for eyeballs)
```

**Manifest** (`manifest.json`):
```json
{
  "schema_version": 1,
  "generated_at_utc": "2026-07-18T14:23:01Z",
  "hal0_version": "<hal0.__version__>",
  "hostname": "<socket.gethostname()>",
  "installer_root": "<cfg_paths.usr_lib()>",
  "hal0_home": "<cfg_paths.hal0_home>",
  "sections": ["manifest.json", "commands.tsv", "system/", "config/", "diagnostics/", "logs/"],
  "redaction_applied": ["config/hal0.toml", "config/api.env", "config/slots/*"],
  "redaction_policy": "SECRET|TOKEN|PASSWORD|PASS|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|SALT (api._redact._SENSITIVE_RE)",
  "command_count": 23,
  "diagnosis_summary": {"critical": 0, "fail": 2, "warn": 5, "info": 16}
}
```

**Redaction**: reuse `hal0.api._redact.redact_config` (`api/_redact.py:67-93`) — the existing canonical masker triggered by key-name regex `SECRET|TOKEN|PASSWORD|PASS|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|SALT` (`:37-39`). For `api.env` (one key=value per line), parse → dict → redact → serialise back; values get the `***REDACTED***` sentinel but the line is preserved so the operator sees the key set. **JWTs / Bearer tokens** in arbitrary text fields (logs, comments) get a regex pass too: `re.sub(r"Bearer\s+\S+", "Bearer ***REDACTED***", text)` and `re.sub(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", "***JWT***", text)` (JWT signature heuristic).

**Commands TSV** (`commands.tsv`):
```
command	exit_code	stdout_bytes	stderr_bytes	ts_utc	duration_ms
"uname -a"	0	92	0	2026-07-18T14:23:01.012Z	45
"rocminfo"	0	8412	0	2026-07-18T14:23:01.456Z	443
...
```
TSV, header row, double-quote any field containing tab/quote/newline. `exit_code=-1` if the command was not found on PATH (e.g. `rocminfo` missing on a non-AMD box — see `rocminfo.txt` then written with `# command not found: rocminfo` as the only line).

### 3.2 Implementation (skeleton)

```python
# src/hal0/cli/doctor_bundle.py  (NEW)
"""hal0 doctor bundle — the §21.4 support-bundle generator."""
from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import typer

from hal0.api._redact import redact_config

_BEARER_RE = re.compile(r"Bearer\s+\S+")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")

_PROBES: tuple[tuple[str, tuple[str, ...], Path], ...] = (
    # (label, argv, output_path_relative)
    ("uname", ("uname", "-a"), Path("system/uname.txt")),
    ("os-release", ("cat", "/etc/os-release"), Path("system/os-release.txt")),
    ("cmdline", ("cat", "/proc/cmdline"), Path("system/cmdline.txt")),
    ("tuned", ("tuned-adm", "active"), Path("system/tuned.txt")),
    ("rocminfo", ("rocminfo",), Path("system/rocminfo.txt")),
    ("rocm-smi-showall", ("rocm-smi", "--showall"), Path("system/rocm-smi.txt")),
    # ...etc
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _redact_text(text: str) -> str:
    return _BEARER_RE.sub("Bearer ***REDACTED***", _JWT_RE.sub("***JWT***", text))


def _run_one(label: str, argv: tuple[str, ...], dest: Path, *,
             timeout: float = 10.0) -> tuple[int, int, int]:
    """Run argv, write stdout to dest. Returns (exit_code, stdout_bytes, duration_ms)."""
    ts0 = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"# command not found: {argv[0]}\n")
        return -1, 0, int((time.monotonic() - ts0) * 1000)
    except subprocess.TimeoutExpired:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"# command timed out after {timeout}s: {' '.join(argv)}\n")
        return -2, 0, int((time.monotonic() - ts0) * 1000)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_redact_text(proc.stdout))
    return proc.returncode, len(proc.stdout.encode("utf-8")), int((time.monotonic() - ts0) * 1000)


def build_bundle(out: Path) -> Path:
    """Write the bundle under ``out`` and return its path."""
    out.mkdir(parents=True, exist_ok=True)
    commands_tsv: list[list[str]] = [["command", "exit_code", "stdout_bytes", "stderr_bytes", "ts_utc", "duration_ms"]]
    for label, argv, rel in _PROBES:
        rc, nbytes, dur = _run_one(label, argv, out / rel)
        commands_tsv.append([label, str(rc), str(nbytes), "0", _now_iso(), str(dur)])
    # ...write config/, diagnostics/, logs/ sections per §3.1...
    # manifest last.
    return out
```

`hal0 doctor bundle` registers as a Typer subcommand (`doctor_commands.py:236-310` area; new `bundle` function). Default output: `$PWD/hal0-doctor-bundle-<host>-<UTC-ts>/`. Flags: `--out PATH`, `--no-rocm-smi` (skip `rocm-smi --showall` capture if the operator wants a fast bundle), `--include-logs=auto|yes|no`.

### 3.3 What bundle does **not** do (scope out)

- No upload to a remote endpoint. The bundle is operator-curated — operator `tar czf` and ship.
- No redaction of `manifest.json` (the manifest itself contains no secrets; `hal0_version` + paths only).
- No interactive prompts; `--force` skips any optional section. Default = full bundle.
- No `hal0 doctor fix <id>` dispatcher in this spec — referenced by `next_steps[]` only. Follow-on lane.

---

## 4. Renderer (`--json` everywhere)

### 4.1 `Diagnosis.to_dict()` (the JSON shape)

```python
def to_dict(self) -> dict[str, Any]:
    return {
        "id": self.id,
        "severity": self.severity,
        "confidence": self.confidence,
        "summary": self.summary,
        "detail": self.detail,
        "fixable": self.fixable,
        "evidence": [{"kind": e.kind, "summary": e.summary, "data": e.data} for e in self.evidence],
        "next_steps": [{"kind": s.kind, "label": s.label, "target": s.target} for s in self.next_steps],
    }
```

### 4.2 `--json` flag on every subcommand

Add `json_output: bool = typer.Option(False, "--json", help="Emit stable JSON instead of the human table.")` to each subcommand in `doctor_commands.py`. On `--json`:
- Rich render path is skipped.
- `json.dumps([d.to_dict() for d in diagnoses], indent=2, sort_keys=False)` printed to stdout.
- Exit code follows the `overall_verdict` (critical → 2, fail/warn → 1, ok → 0). The existing per-command exit-code contracts (e.g. `toolbox-pull` exit 1 = at least one image unreachable, `doctor_commands.py:509-510`) get a translation table per subcommand (§6 verification table).

### 4.3 Backward-compat (do **not** break)

| Existing subcommand | Today exit codes | After retrofit |
|---|---|---|
| `doctor` (preflight shell-out) | preserves `preflight.sh` rc | **unchanged** — `preflight.sh` integration is out of this spec's scope; existing redaction-free stdout preserved |
| `doctor verify` | 0/2 (critical) | unchanged for the rich path; `--json` adds a second shape |
| `doctor logs` | 0/1 | unchanged |
| `doctor toolbox-pull` | 0/1/2 (per `:509-510`) | 0/1/2/0-by-`--json`; new exit 3 = `--json` parse error (no JSON shape change) |
| `doctor perms` | 0/1 (per `:863-866`) | 0/1 unchanged; `--json` emits the same 0/1 boundary |
| `doctor models` | 0/1 (per `:1158`) | unchanged |
| `doctor migrations` | 0/1 (per `:1214,1227`) | unchanged |
| `doctor profiles` | 0/1 (per `:1419-1420`) | unchanged |
| `doctor bundle` (NEW) | n/a | 0 = ok; 1 = some probe commands failed (non-fatal, included in TSV); 2 = could not write bundle (permission/disk) |

---

## 5. Cross-lane coordination

### 5.1 §21.2 gfx-guard → `HAL0-GFX-TARGET-UNSUPPORTED`

Per `hal0-rework-plan.md:1399`, the §21.2(a) startup probe "Surface[s] pass/fail as doctor diagnosis ID `HAL0-GFX-TARGET-UNSUPPORTED`". This spec owns the **ID string + the diagnosis schema**; the probe itself lands in §21.2's lane (`runners/` widening, `mcp/probes.py:80-130` gfx decoder is the seam). The contract:

- §21.2 emits, on WARMING→failed transition, one `Diagnosis(id="HAL0-GFX-TARGET-UNSUPPORTED", severity="fail", confidence="high", summary="<runner> reported HIP arch <X>, expected <Y>", evidence=[Evidence(kind="command", summary="llama-server /system_info", data={"reported_archs": ["gfx1151"], "required_archs": ["gfx1151", "gfx1200"]})], next_steps=[NextStep(kind="doc", label="see §21.2 gfx-guard", target="hal0-rework-plan.md#21.2")])`.
- §21.2's lane MUST import `Diagnosis` from `hal0.cli.doctor_diagnosis` (the NEW module from §1.1). To avoid a circular import (slot manager imports from `cli/`?), the dataclass moves to **`hal0.diagnostics` (`src/hal0/diagnostics.py`, NEW, tiny module — pure stdlib + dataclasses)**. Re-export from `cli/doctor_diagnosis.py` for the doctor paths.
- Doctor's `bundle` section reads the most recent gfx-guard diagnosis from the slot state (`GET /api/slots` → each `slot.last_diagnosis` field, added in §21.2). The probe writes the diagnosis there; doctor only reads it.

### 5.2 §21.1 preflight WARN checks → `HAL0-HOST-NOT-TUNED`

Per `hal0-rework-plan.md:1391`, §21.1 adds `preflight_host_tuned` to `installer/lib/preflight.sh`. The shell function:

```bash
# Reads /sys/module/amdgpu/parameters/{gttsize,ppfeaturemask,gpu_recovery} and
# ttm pages_limit/page_pool_size (derived from gttsize), /proc/cmdline (amd_iommu),
# tuned-adm active, sysctl vm.swappiness/vfs_cache_pressure. Emits one WARN line
# per failed check + a final aggregate:
#   "host not tuned for Strix Halo, expected +X% inference — see hal0-rework-plan §21.1"
# Exit 0 always (WARN-only per spec); operator can run --json to parse.
preflight_host_tuned() {
    local drift=0
    local gttsize; gttsize=$(cat /sys/module/amdgpu/parameters/gttsize 2>/dev/null || echo 0)
    [ "$gttsize" -lt 120000 ] && { _log warn "amdgpu gttsize=$gttsize (< 120000)"; drift=1; }
    grep -q 'amd_iommu=off' /proc/cmdline || { _log warn "amd_iommu=off missing from /proc/cmdline"; drift=1; }
    tuned-adm active 2>/dev/null | grep -q accelerator-performance || { _log warn "tuned-adm profile != accelerator-performance"; drift=1; }
    [ "$(sysctl -n vm.swappiness 2>/dev/null)" -gt 10 ] && { _log warn "vm.swappiness > 10"; drift=1; }
    [ $drift -eq 1 ] && _log warn "host not tuned for Strix Halo — see hal0-rework-plan §21.1"
    return 0   # WARN-only, per spec
}
```

The doctor retrofit wraps `preflight.sh`'s stdout into one `Diagnosis(id=HAL0-HOST-NOT-TUNED, severity=warn)` per WARN line, **without** changing `preflight.sh`'s exit code. The shell-out (`doctor_commands.py:196-210`) captures stdout into a buffer, post-processes the `_log warn "host not tuned"` sentinel line → emit diagnosis.

### 5.3 §13 metrics tables → bundle's `diagnostics/stats.json`

Per `hal0-rework-plan.md:1643` (`§13.3 tables + §21.2 gfx diagnosis ID ─PREREQ→ §21.4 doctor rework evidence`), the bundle's `diagnostics/stats.json` (`§3.1` row 8) reads `GET /api/stats`. That endpoint is §21.3 work (`hal0-rework-plan.md:1408-1410`); when §21.3 is merged, the bundle can fetch it. Until then, the bundle writes `{"_unavailable": "stats endpoint merges with §21.3"}`. This is the only forward-dep in the bundle beyond §21.2's gfx ID.

### 5.4 §21.11 golden-paths → bundle's manifest

Per `hal0-rework-plan.md:1465`, §21.11 owns the "canonical on-disk layout (config/models/cache/logs)" contract. The bundle's `manifest.json` lists each expected path + presence (e.g. `{"path": "/etc/hal0/hal0.toml", "present": true, "redacted": true}`). When §21.11 lands, the manifest gains a `golden_paths` array; before §21.11 lands, the bundle only writes the `sections` array + `redaction_applied`.

### 5.5 §14.1 / KB-1 auth — out of scope

The bundle writes locally; no upload. Auth on `/api/diagnostics/bundle` (a hypothetical future ingest route) is KB-1's lane (`hal0-rework-plan.md:1667-1680`). This spec does **not** add a route.

### 5.6 Build DAG (§23.4 line 1643 + §24.2 W8)

```
S8 db/ foundation (ML-1, FIRST)
  ├─ §13.3 request_metric / slot_sample / slot_event    ← §13.7 "after ML-1"
  │     ├─ §21.3 /api/stats (W6)                          
  │     └─ §21.4 doctor rework (W8) — evidence source
  └─ §21.2 gfx diagnosis ID (W5)
        └─ §21.4 doctor rework (W8) — emits/reads HAL0-GFX-TARGET-UNSUPPORTED

§21.1 preflight (W5 / W6 host-tuning block)  ← can land before §21.4; doctor reads preflight.sh output
§21.11 golden-paths (W8)                      ← rides alongside §21.4 (same W8 wave)
```

W8 sequence (per `hal0-rework-plan.md:1717`):
1. Land §21.2 gfx-guard's diagnosis-emit seam in `runners/` (W5 prerequisite).
2. Land §13.3 metrics tables (W6 prerequisite) + §21.3 `/api/stats` (W6).
3. **This spec** (W8): new `cli/doctor_diagnosis.py` + `diagnostics.py` module + retrofit + `bundle` subcommand.
4. §21.11 golden-paths (W8, parallel lane — UI collision class).
5. §21.9 /v1/messages + realtime + §21.12 client docs + §21.14 chat REPL (W8, separate lanes).

---

## 6. Edit plan (files + order)

One PR per step. Each green-pushed. Total ~6 PRs (1,420-line file is the bulk — keep edits surgical).

### PR 1 — `diagnostics.py` + `doctor_diagnosis.py` (pure new modules)

- New `src/hal0/diagnostics.py` — `Evidence`, `NextStep`, `Diagnosis`, `Severity`, `Confidence`, `overall_verdict` (per §1.1). Pure stdlib. **No imports from `hal0.cli`** — sits below the CLI layer so `runners/` (§21.2) can use it without circulars.
- New `src/hal0/cli/doctor_diagnosis.py` — re-exports `from hal0.diagnostics import *` + adds `to_diagnosis(c: Check) -> Diagnosis` (per §1.3) + `render_json(diagnoses) -> str`.
- Tests: `tests/cli/test_diagnosis.py` — dataclass frozen, JSON round-trip, ID taxonomy stability (snapshot test on the §1.2 table).

### PR 2 — Retrofit `doctor perms` + `doctor models`

- `doctor_commands.py` Hermes / tree-share / path-ownership / FLM helpers wrapped by `_diagnose_*` (per §2.1 + §2.2). The existing `dict[str,str]` row helpers stay (legacy callers); new functions wrap them.
- `--json` flag added to `perms` and `models`.
- Existing tests pass; new tests in `tests/cli/test_doctor_perms_json.py`, `test_doctor_models_json.py` — diagnosis IDs stable, exit codes preserved.

### PR 3 — Retrofit `doctor migrations` + `doctor profiles` + `doctor toolbox-pull`

- Per §2.3 / §2.4 / §2.5. `--json` flags added. `_probe_one` rows already JSON-serialisable (`doctor_commands.py:404-413`).
- Tests: `tests/cli/test_doctor_migrations_json.py`, `test_doctor_profiles_json.py`, `test_doctor_toolbox_pull_json.py` — diagnosis ID stability, exit-code preservation.

### PR 4 — Retrofit `doctor verify`

- `doctor_verify.Check` stays; `run_verify` gains `json_output: bool = False` parameter; `to_diagnosis` mapping per §1.3.
- `render_report` (rich path) untouched.
- `doctor_verify_cmd` (`:216-228`) gains `--json` flag passthrough.
- Tests: `tests/cli/test_doctor_verify_json.py` — output shape, exit code on critical.

### PR 5 — New `doctor bundle` subcommand

- New `src/hal0/cli/doctor_bundle.py` per §3.2.
- `doctor_commands.py` registers the new subcommand via `app.command("bundle")(doctor_bundle_cmd)`.
- `_redact_text` per §3.1 (Bearer/JWT pass) + reuse of `api._redact.redact_config` for TOML trees.
- Tests: `tests/cli/test_doctor_bundle.py` — layout (§3.1) created, manifest matches, `commands.tsv` row count, redaction in `config/hal0.toml`, JSON files present. Use `tmp_path` fixture; mock `subprocess.run` + `api_get` (existing pattern in `tests/cli/test_doctor_profiles.py`).

### PR 6 — §21.2/§21.1 emission seams

- Add `hal0/runners/gfx_guard.py` (NEW) — startup probe that emits `Diagnosis(id="HAL0-GFX-TARGET-UNSUPPORTED", ...)` on mismatch. Reuses `mcp.probes.gfx_target_version` decoder (`mcp/probes.py:80-130`). Stores into `Slot.last_diagnosis` (new field).
- `doctor bundle`'s `diagnostics/verify.json` includes the most-recent gfx-guard diagnosis per slot.
- `installer/lib/preflight.sh` adds `preflight_host_tuned` per §5.2.
- `doctor_commands.py:171-210` post-processes `preflight.sh` stdout to emit `HAL0-HOST-NOT-TUNED` diagnosis.
- Tests: `tests/runners/test_gfx_guard.py` (mock llama-server `/system_info`); `tests/installer/test_preflight_host_tuned.sh` (bash test in `tests/installer/`).

> **Cross-wave rider (§24.2)**: §21.2 lands its diagnosis-emit seam in W5 (`runners/` widening). PR 6 of this spec depends on that seam. If §21.2 lands first, this PR is small; if not, the gfx-guard tests here use a stub that the §21.2 lane replaces. Either order is shippable.

---

## 7. Risks + mitigations

1. **Diagnosis-ID contract is a public promise.** Once published, renaming `HAL0-GFX-TARGET-UNSUPPORTED` breaks every consumer (operator scripts, KB articles, the §21.11 golden-paths manifest). Mitigation: PR 1's snapshot test pins the §1.2 table; any change requires an explicit `bump diagnosis_id schema_version` commit + a CHANGELOG note. IDs follow the `HAL0-<SCOPE>-<DOMAIN>-<SHORT>` shape — never invent a new prefix.
2. **`preflight.sh` WARN-line parsing is brittle.** A future edit to the script's `_log warn` format silently breaks `HAL0-HOST-NOT-TUNED` emission. Mitigation: `preflight.sh` exposes a `--json` flag (alongside today's plain text) that emits one JSON object per check; the doctor wraps that path. The plain-text path remains for `hal0 doctor` (no API) — both stay in sync via the same source-of-truth shell function.
3. **`api._redact._SENSITIVE_RE` over-redacts.** The existing pattern (`api/_redact.py:37-39`) is deliberately conservative (`TOKEN` matches `TOKENIZER_ID`). Mitigation: the bundle's `manifest.json: redacted_keys_count` field tells operators how many keys were masked, so a future false-positive is traceable. No expansion of the regex in this spec.
4. **Bundle size.** A full bundle can be 50–200 MB (logs + `rocminfo` + slot TOMLs). Mitigation: `--no-logs` and `--no-rocm-smi` flags (per §3.2); default includes them; operators on constrained disks use the flags. Auto-pruning of `logs/hal0-slot-*.log` to the most recent N=500 lines (same as `doctor_logs` `:243`).
5. **Exit-code translation across subcommands.** Each subcommand today has its own contract (`toolbox-pull` 0/1/2; `verify` 0/2). Mitigation: §4.3 table pins every subcommand's new exit codes. A single §6 verification test (`tests/cli/test_doctor_exit_codes.py`) walks every subcommand with `--json` and asserts exit-code stability.
6. **`Diagnosis` import layering.** §21.2's `runners/` needs the dataclass; today's `runners/` doesn't import from `cli/`. Mitigation: §1.1 places `Diagnosis` in `hal0.diagnostics` (NEW, under `src/hal0/` root) — no `cli/` import. `cli/doctor_diagnosis.py` re-exports. Verified by `tests/diagnostics/test_layering.py` (forbidden-import check: `hal0.diagnostics` may not import from `hal0.cli`).
7. **`run_verify` callers.** Today `doctor_commands.py:168-170` and `:226-228` call `run_verify(console=...)`. After PR 4, both pass through. Mitigation: `run_verify` signature stays `(console: Console | None = None, base: str | None = None, json_output: bool = False)` — additive, no caller breaks.
8. **`doctor bundle` runs while hal0-api is down.** Each `GET /api/*` is best-effort: `api_get` already tolerates 5xx/connection-refused (`cli/_shared.py`); bundle writes `{"_unavailable": "<url>"}` instead of failing the whole bundle. The TSV's `exit_code` for the probe command captures the real outcome.
9. **PR 6's gfx-guard seam ordering.** If §21.2 hasn't merged by the time PR 6 ships, the new `Slot.last_diagnosis` field doesn't exist. Mitigation: PR 6 includes the field add (`slot.last_diagnosis: Diagnosis | None = None` on the `Slot` dataclass — found at `manager.py:239-283` per `spec-p3-slots.final.md`'s §0.1) as a SHIM that's a no-op until §21.2 fills it. The §21.2 lane replaces the shim's writer with the real probe — no further changes to PR 6.

---

## 8. Files referenced

### Modified
- `/home/mint/hal0/src/hal0/cli/doctor_commands.py` (1,420 lines — adds `--json` to 5 subcommands; wraps each helper with `_diagnose_*`; new `bundle` registration; PRs 2/3/5)
- `/home/mint/hal0/src/hal0/cli/doctor_verify.py` (385 lines — `run_verify` gains `json_output`; `to_diagnosis` adapter; PR 4)
- `/home/mint/hal0/src/hal0/cli/main.py` (no change — `doctor_app` is mounted at `:68`; new subcommand registers via `doctor_commands.py`)
- `/home/mint/hal0/installer/lib/preflight.sh` (adds `preflight_host_tuned` per §5.2; PR 6)
- `/home/mint/hal0/src/hal0/slots/manager.py` (`Slot.last_diagnosis` shim field; PR 6 — depends on P3-slots decomposition per `spec-p3-slots.final.md`)
- `/home/mint/hal0/src/hal0/mcp/probes.py` (no change — `gfx_target_version` decoder at `:80-130` is the seam §21.2 imports from)

### New
- `/home/mint/hal0/src/hal0/diagnostics.py` (the `Diagnosis` + `Evidence` + `NextStep` dataclasses, layering-pure)
- `/home/mint/hal0/src/hal0/cli/doctor_diagnosis.py` (re-exports + `to_diagnosis` adapter + JSON renderer)
- `/home/mint/hal0/src/hal0/cli/doctor_bundle.py` (the support-bundle generator; `_run_one`, `_redact_text`, `build_bundle`)
- `/home/mint/hal0/src/hal0/runners/gfx_guard.py` (startup probe, §21.2(a) seam — emits `HAL0-GFX-TARGET-UNSUPPORTED`)

### Tests (new)
- `/home/mint/hal0/tests/cli/test_diagnosis.py` (dataclass frozen + JSON round-trip + ID-taxonomy snapshot)
- `/home/mint/hal0/tests/cli/test_doctor_perms_json.py`
- `/home/mint/hal0/tests/cli/test_doctor_models_json.py`
- `/home/mint/hal0/tests/cli/test_doctor_migrations_json.py`
- `/home/mint/hal0/tests/cli/test_doctor_profiles_json.py`
- `/home/mint/hal0/tests/cli/test_doctor_toolbox_pull_json.py`
- `/home/mint/hal0/tests/cli/test_doctor_verify_json.py`
- `/home/mint/hal0/tests/cli/test_doctor_bundle.py` (layout + redaction + manifest)
- `/home/mint/hal0/tests/cli/test_doctor_exit_codes.py` (every subcommand × `--json` × expected rc)
- `/home/mint/hal0/tests/diagnostics/test_layering.py` (`hal0.diagnostics` does NOT import `hal0.cli`)
- `/home/mint/hal0/tests/runners/test_gfx_guard.py` (mock llama-server `/system_info`, asserts diagnosis on mismatch)
- `/home/mint/hal0/tests/installer/test_preflight_host_tuned.sh` (bash test of the new preflight function)

### Tests (regression — must stay green)
- `/home/mint/hal0/tests/cli/test_doctor.py`, `test_doctor_models.py`, `test_doctor_perms.py`, `test_doctor_profiles.py`, `test_doctor_verify.py` — all existing, no edits required; the retrofit is additive (existing row helpers stay; `_diagnose_*` wraps them).

### Plan + companion refs
- `/home/mint/hal0-rework-plan.md` (§21.4 lines 1416-1420 — spec source; §21.2 line 1399 — gfx ID contract; §21.1 line 1391 — preflight WARN; §21.11 line 1465 — golden-paths; §13 lines 732-800 — metrics tables; §23.4 line 1643 — DAG; §24.2 line 1717 — W8; §24.3 line 1731 — spec-authoring backlog)
- Companion specs: `/home/mint/hal0-specs/spec-p3-slots.final.md` (manager.py decomposition gates PR 6's `Slot.last_diagnosis` add); `/home/mint/hal0-specs/spec-p3-schema.final.md` (§7.1a flag resolution context for §21.2); `/home/mint/hal0-specs/spec-ml-runner-flags.final.md` (§7.1a flags + §21.2 widens)
- Reused code: `/home/mint/hal0/src/hal0/api/_redact.py:67-93` (`redact_config`); `/home/mint/hal0/src/hal0/api/_redact.py:37-39` (`_SENSITIVE_RE`); `/home/mint/hal0/src/hal0/mcp/probes.py:80-130` (`gfx_target_version` decoder); `/home/mint/hal0/src/hal0/slots/capacity.py:351-368` (`CapacitySnapshot.free_vram_mb` — §21.10 elevates this; bundle's `system/meminfo.txt` is the boot-strap)
