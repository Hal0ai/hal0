# FLM Toolbox Removal + Unified Model Storage — Implementation Plan (2026-06-07)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (review
> checkpoints) — this plan moves ~29 GB of model weights on a **live shared runtime**
> (CT 105, 192.0.2.10). Steps use `- [ ]` for tracking. **No data moves before the
> owner approves this doc.** Migration is strictly **copy → verify → swap** (never `mv`).
>
> **On execution, this file is already at**
> `docs/superpowers/plans/2026-06-07-flm-host-probe-and-unified-model-storage.md`.
> Builds directly on `2026-06-06-model-store-cleanup-hardening.md` (registry.toml →
> server_models.json regen, the `/mnt/ai-models/local/` symlink-into-hub pattern,
> verify-first-by-SHA discipline).

## 1. Problem (what the user saw)

`gemma4-it:e4b` (and in fact **every** FLM/NPU model) is absent from the dashboard
**models page** and the **NPU chat dropdown**, even though its weights are on disk and
`flm check gemma4-it:e4b` passes all 7 files.

### Root cause (diagnosed 2026-06-07, `/diagnose` loop on `GET /api/models` + `/api/capabilities`)

A **compound** fault, neither part being "gemma4-specific":

1. **Stale, wrong-pathed probe.** `hal0.providers.flm._probe_flm_catalog()` runs the
   **docker toolbox** image (`ghcr.io/hal0ai/hal0-toolbox-flm:v1`, **FLM v0.9.42**) with
   `flm list -j`, bind-mounting `HAL0_FLM_MODELS_DIR or _DEFAULT_FLM_MODELS_DIR` =
   **`/var/lib/hal0/flm-models`** — a directory that is **empty** (created May 23, never
   populated). The real weights live at **`/var/lib/hal0/.config/flm/models`** (where the
   **host** `flm` writes, running as `hal0` with `HOME=/var/lib/hal0`). So the bundled
   `model_list.json` makes the models *appear* in the catalog, but every one reports
   **`installed=False`**.
2. **UI hides not-installed models.** `ui/src/dash/model-modals.jsx:436` —
   `if (!model || !model.installed) return null;`. So `installed=False` ⇒ rendered as
   nothing. gemma4 (and all FLM models) vanish.
3. **Version skew compounds it.** Pointing the *toolbox* at the *correct* dir makes it
   emit `[WARNING] Local model version: 0.9.43 > 0.9.42` lines to stdout — which would
   break `_probe_flm_catalog`'s `json.load` and null the whole catalog. The toolbox
   (v0.9.42) has drifted behind the **host serving flm (v0.9.43)** that actually wrote
   the weights.

**Proof the host flm is correct:** `sudo -u hal0 HOME=/var/lib/hal0 flm list -j` reports
`installed=True` for gemma4-it:e4b, gemma3:4b, qwen3-it:4b, embed-gemma:300m — clean JSON,
no warnings (versions match).

### Why the toolbox is the wrong tool at all

FLM **serving is already host-native** — lemond (`/opt/lemonade/lemond`) spawns the host
`/usr/bin/flm serve`, version-pinned via `/opt/lemonade/resources/backend_versions.json`,
exactly like the ROCm/Vulkan llama.cpp runtimes. The docker toolbox is used **only** for
the catalog *probe* and *pulls* — a vestigial helper that drifts in version and points at
the wrong path. Dropping it makes FLM consistent with every other runtime.

## 2. Goals

1. **Drop the FLM docker toolbox entirely.** Probe + pull via **host `/usr/bin/flm`**
   (run as the `hal0` user, `HOME=/var/lib/hal0`). Kills both the path mismatch and the
   version skew; gemma4 + all on-disk FLM models surface as `installed=True`.
2. **Unify model storage** under hal0's existing FHS store, **bucketed by type**:
   `/var/lib/hal0/models/<type>/` (`flm`, `gguf`, `embed`, `rerank`, `stt`, `tts`).
3. **De-pollute `.config`.** Symlink FLM's hardcoded `~/.config/flm/models` →
   `/var/lib/hal0/models/flm` so FLM stops leaking into `~/.config`.
4. **Share-overlay, portable by default.** When `/mnt/ai-models/hal0` exists,
   `/var/lib/hal0/models` is a symlink → `/mnt/ai-models/hal0/models` (weights live on the
   ZFS share). When absent, the local dir is real → default behavior, no config, works out
   of the box.

## 3. Current state (audit, CT 105, 2026-06-07)

| Thing | Where | Notes |
|---|---|---|
| FLM NPU weights | `/var/lib/hal0/.config/flm/models/` (**29 GB**, local) | `Gemma4-E4B-IT-NPU2`, `Gemma3-4B-NPU2`, `Qwen3-…`, etc. Real dir, not symlinked. |
| hal0 `models_dir()` | `/var/lib/hal0/models/` (**79 MB**, local) | FHS state store; lightly used today. |
| GGUF (named) | `/mnt/ai-models/<name>/` | `chadrock-35b-ace-saber`, `qwopus3.6-27b-v2`, `qwen3.6-35b-crown-halo`, `gemma-4-12b-it`, … (root:hal0, clean names). |
| GGUF (HF cache) | `/mnt/ai-models/huggingface/hub/` | lemond checkpoints (Hermes-4-14B, nomic-embed, …). |
| `local/` slugs | `/mnt/ai-models/local/` | symlink-into-hub pattern (per the 2026-06-06 cleanup doc). |
| Wrong probe default | `/var/lib/hal0/flm-models/` (**empty**) | the bug's bind-mount source. Delete after migration. |
| lemond config | `/var/lib/hal0/lemonade/config.json` | `extra_models_dir: /mnt/ai-models`, `models_dir: auto`. |
| Free space | share **405 GB** free (681 G ZFS), local root **316 GB** free | ample for copy-then-verify. |

Probe/UI/registry machinery (locked from source):
- `src/hal0/providers/flm.py` — `_probe_flm_catalog()` (docker `list -j`),
  `flm_pull_command()` (docker `pull`), `flm_served_models()` (caches probe;
  `installed = bool(entry["installed"])`), `reset_flm_catalog_cache()`, constants
  `_DEFAULT_FLM_IMAGE`, `_DEFAULT_FLM_MODELS_DIR`, `_IMAGE_FLM_ROOT`, `_DEFAULT_FLM_ROOT`.
- `src/hal0/registry/pull.py` — `run_flm_pull()` calls `flm_pull_command()`, spawns argv via
  `asyncio.create_subprocess_exec`, polls dir-size for progress, SIGTERM to cancel, resets
  the FLM catalog cache on success.
- `src/hal0/config/paths.py` — `models_dir()` = `/var/lib/hal0/models` (HAL0_HOME-aware).
- `ui/src/dash/model-modals.jsx:436` — the `installed` render-guard.
- `hal0-api.service` runs as **`User=root`** (so it can drop to `hal0` for flm).

## 4. Target architecture (3 layers)

**Layer 1 — canonical, type-bucketed store** (hal0's existing FHS `models_dir()`):
```
/var/lib/hal0/models/
├── flm/      # FLM NPU dirs:  Gemma4-E4B-IT-NPU2/, Gemma3-4B-NPU2/, …
├── gguf/     # llama.cpp:     chadrock-35b-ace-saber/, qwopus3.6-27b-v2/, …
├── embed/    # nomic, embed-gemma
├── rerank/   # jina
├── stt/      # whisper
└── tts/      # kokoro / voices
```
No share → this is a real dir → default behavior. **No new path convention** — extends
`models_dir()` the codebase already resolves.

**Layer 2 — runtime-expected paths symlink *into* Layer 1** (so each engine finds models
where it hardcodes them, zero code change to the engines):
- `~/.config/flm/models` → `/var/lib/hal0/models/flm`  *(FLM hardcodes this; de-pollutes `.config`)*
- lemond `extra_models_dir` → the canonical root (or per-type as lemond requires)

**Layer 3 — share overlay** (only when present):
- `/var/lib/hal0/models` → `/mnt/ai-models/hal0/models`
- Weights physically on ZFS; hal0 finds them at the default path transparently.

**How the probe/pull fix composes:** host `flm` reads/writes `~/.config/flm/models`
→ (Layer 2 symlink) `/var/lib/hal0/models/flm` → (Layer 3 symlink) the share. `installed`
reflects reality; gemma4 appears; pulls land on the share. One coherent path.

## 5. PART A — Code change: host-flm probe + pull (no toolbox)

> Develop in a worktree on hal0-dev, PR, deploy to `/opt/hal0`, restart `hal0-api`.
> This part is **independent of the migration** and can ship first (low risk).

- [ ] **A1. Add host-flm identity helpers to `providers/flm.py`.**
  - `_HOST_FLM_BIN = os.environ.get("HAL0_FLM_BIN", "/usr/bin/flm")`
  - `_HOST_FLM_HOME = os.environ.get("HAL0_FLM_HOME", "/var/lib/hal0")`
  - `_HOST_FLM_USER = os.environ.get("HAL0_FLM_USER", "hal0")`
  - `_HOST_FLM_MODELS_DIR = f"{_HOST_FLM_HOME}/.config/flm/models"` (replaces the empty
    `_DEFAULT_FLM_MODELS_DIR`).
  - `flm_host_spawn_kwargs() -> dict`: returns `{"env": {**os.environ, "HOME": _HOST_FLM_HOME}}`
    plus `user`/`group=_HOST_FLM_USER` **only when `os.geteuid()==0`** and the user resolves
    (`pwd.getpwnam`), so dev/test runs as the model owner don't try to drop privilege.
- [ ] **A2. Rewrite `_probe_flm_catalog()`** to `subprocess.run([_HOST_FLM_BIN, "list", "-j"],
  …, **flm_host_spawn_kwargs())`. Parse defensively with a `_extract_json_object()` helper
  (`text.find("{")` then `json.JSONDecoder().raw_decode`) so any stray preamble line can't
  null the catalog. Keep the `returncode != 0 → None` and `models not list → None` guards.
- [ ] **A3. Rewrite `flm_pull_command(tag)`** to return
  `([_HOST_FLM_BIN, "pull", tag], _HOST_FLM_MODELS_DIR)`.
- [ ] **A4. Update `registry/pull.py::run_flm_pull`** to pass `**flm_host_spawn_kwargs()`
  into `asyncio.create_subprocess_exec` (import the helper). SIGTERM-cancel still works
  (now hits the flm process directly). Dir-size progress polling unchanged (`host_models_dir`
  now the real path). Cache-reset-on-success unchanged.
- [ ] **A5. Remove the toolbox-only constants/paths** (`_DEFAULT_FLM_IMAGE`,
  `_DEFAULT_FLM_MODELS_DIR`, image-ref/`container_spec` plumbing **iff** unused by serving —
  confirm `FLMProvider.container_spec`/`start_cmd` aren't on a live path first; lemond serves
  via host flm, but verify no hal0-managed slot still renders the toolbox `ContainerSpec`
  before deleting). Update `tests/providers/test_flm.py` (currently mocks `docker` /
  `container_spec` — replace with host-flm subprocess mocks).
- [ ] **A6. Regression test** (`tests/providers/test_flm.py`): given a fake `flm list -j`
  payload with mixed `installed` flags + a leading `[WARNING]` line, assert
  `flm_served_models()` returns the models with correct `installed`, and that the spawn uses
  `_HOST_FLM_BIN` + `HOME` + (when root) `user=hal0`. Add a `flm_pull_command` shape test.
- [ ] **A7. Deploy + verify:** restart `hal0-api`; `reset_flm_catalog_cache` via restart;
  `GET /api/capabilities` shows FLM models `downloaded=True`; gemma4-it:e4b appears in the
  NPU picker; `GET /api/models` (after selecting it as the npu slot model, per §7) lists it.

## 6. PART B — Migration (copy → verify → swap; NEVER mv)

> Tier-3 bulk data op on a live runtime. Back up pointers first; verify by SHA / `flm check`
> before deleting any source. Do FLM first (the actual bug + clean win); GGUF reorg
> (§6.4) is a **separate, later, optional** phase because it touches the working `primary`.

- [ ] **B0. Pre-flight.** `wip hal0 claim`; snapshot `lemonade/config.json`,
  `registry/registry.toml`, `server_models.json` (`.bak-storage-2026-06-07`). Record
  baseline: `flm list -j` installed set; `/api/slots` healthy; `du -sh` sources.
- [ ] **B1. Create the share tree.** `mkdir -p /mnt/ai-models/hal0/models/{flm,gguf,embed,rerank,stt,tts}`
  (owner `hal0:hal0`, mode 0775). *(No-share hosts skip B1–B3; Layer-1 local dir is the default.)*
- [ ] **B2. Copy FLM weights to share.** `rsync -aH --info=progress2
  /var/lib/hal0/.config/flm/models/ /mnt/ai-models/hal0/models/flm/`. ~29 GB; double-stored
  temporarily (405 GB free).
- [ ] **B3. Verify the share copy.** For each model: `sudo -u hal0 HOME=<tmp pointing at
  share> flm check <tag>` **or** per-file `sha256sum` diff source↔dest. **Gate:** every model
  green before any swap.
- [ ] **B4. Swap FLM into the canonical store + symlink.**
  - With share: `ln -s /mnt/ai-models/hal0/models /var/lib/hal0/models` is wrong if
    `models/` already exists — instead: move existing local `models/*` into the share's
    matching buckets (copy+verify), then replace `/var/lib/hal0/models` dir with a symlink →
    `/mnt/ai-models/hal0/models` **(Layer 3)**.
  - Replace `/var/lib/hal0/.config/flm/models` (real dir) with symlink →
    `/var/lib/hal0/models/flm` **(Layer 2)**. Since `models` → share, FLM now reads/writes
    the share.
  - **Order matters:** create Layer-3 symlink first, confirm `/var/lib/hal0/models/flm/`
    lists the weights through it, then create Layer-2 symlink.
- [ ] **B5. Smoke FLM end-to-end.** `sudo -u hal0 HOME=/var/lib/hal0 flm list -j` →
  `installed=True` (now reading via symlinks→share); load gemma3-4b + gemma4-it:e4b through
  lemond; confirm NPU serve works off the share-backed path.
- [ ] **B6. Delete sources only after B5 green.** Remove the 29 GB original
  `/.config/flm/models` backup copy and the empty `/var/lib/hal0/flm-models`.
- [ ] **B7. (LATER / OPTIONAL) GGUF consolidation.** Move `/mnt/ai-models/<name>/` +
  HF-hub GGUF into `/mnt/ai-models/hal0/models/gguf/` (and embed/rerank/stt/tts buckets),
  update lemond `extra_models_dir` + `registry.toml` pointers, `hal0 capabilities sync` to
  regenerate `server_models.json`. **Touches the live `primary` (chadrock-35b)** — schedule
  deliberately, one model at a time, copy+verify+swap, with a slot reload after each.

## 7. PART C — Default-path code (make Layer-1 the documented default)

- [ ] **C1.** Confirm `models_dir()` = `/var/lib/hal0/models` is the single source for
  hal0-managed pulls (`registry/pull.py` `run_pull` install path). Add the `<type>` bucket
  convention (helper `models_dir(type: str) -> Path` or document the subdir layout) so new
  pulls land in the right bucket.
- [ ] **C2.** Point FLM's canonical at the bucket: the §5 `_HOST_FLM_MODELS_DIR` already
  resolves to `~/.config/flm/models` → (symlink) `models/flm`. Optionally add
  `HAL0_FLM_MODELS_DIR` override doc for hosts that can't symlink.
- [ ] **C3.** Installer/first-run: create `/var/lib/hal0/models/<type>/` skeleton and, when
  `/mnt/ai-models/hal0` is detected, set up the Layer-3 symlink automatically (so fresh
  installs get the overlay without manual steps).

## 8. Rollback

- **Part A:** revert the PR, redeploy, restart `hal0-api` (probe falls back to the toolbox).
- **Part B:** sources are retained until B6; to roll back, delete the Layer-2/3 symlinks and
  restore the real dirs from the retained copies. lemond/registry pointers restored from the
  `.bak-storage-2026-06-07` snapshots.

## 9. Verification (definition of done)

- [ ] `GET /api/capabilities` → FLM models `downloaded=True`.
- [ ] gemma4-it:e4b selectable in the NPU chat dropdown; appears on the models page once set
  as the npu slot model.
- [ ] `flm list -j` (host) and the probe agree on `installed`.
- [ ] All dashboard slots healthy after restarts (`primary`, `npu`, `embed`, …).
- [ ] FLM weights physically on `/mnt/ai-models/hal0/models/flm`; `~/.config/flm/models`
  and `/var/lib/hal0/models` are symlinks; `/var/lib/hal0/flm-models` gone.
- [ ] `pytest tests/providers/test_flm.py` green (host-flm mocks).
- [ ] No-share path still works: with `/mnt/ai-models/hal0` absent, Layer-1 local dir is the
  real store and FLM probe/pull/serve all function.

## 10. Open questions / risks

- **Q: Does `FLMProvider.container_spec`/`start_cmd` feed any *live* serving path?** Audit
  says lemond serves via host flm, but confirm no hal0-managed slot renders the toolbox
  `ContainerSpec` before deleting it in A5. If one does, that serving path also moves to
  host flm (in-scope: "drop the toolbox entirely").
- **Q: lemond `models_dir: auto` + `extra_models_dir`** interaction with the new buckets —
  confirm lemond still resolves GGUF after §6.4 reorg (defer until that phase).
- **Risk: NPU single-tenancy** is unaffected by storage (orthogonal), but note the parallel
  open item — the FLM *trio* (`--asr 1 --embed 1`) fails to load on gemma4-e4b
  ("Alloc hw resource failed"); chat-only loads fine. Tracked separately; owner is bringing
  the trio up carefully after gemma4 chat is wired.
- **Risk: torn weights** — mitigated by copy→verify→swap and retaining sources through B6.
