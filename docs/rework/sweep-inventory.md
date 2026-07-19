# SWEEP inventory — the overlooked surface (R5 release-prep)

> Produced 2026-07-19 by a 5-way **read-only** discovery fan-out (graphify-first, model-tiered:
> sonnet for reasoning slices, haiku for pattern slices). Every "dead" claim was grep-confirmed
> against src+tests before listing. This is the backlog for the **SWEEP workstream** in
> `handoff-r5-drive2.md §5` — a standing lane that runs in parallel with all phases.
>
> **Verdict: the platform is unusually clean post de-scar (R1–R4).** `ruff --select F401,F811,F841`
> is green; almost every historical "dead code" note in the rework docs is already resolved. The
> real yield is small, high-confidence, and low-risk — exactly what a release wants: a short,
> safe deletion/fix list plus a few *forgotten wire-ups that may be latent bugs*.
>
> **Disposition legend:** DELETE (safe removal, grep-confirmed) · DEPRECATE (stamp `HAL0-SUNSET`,
> remove next release) · IMPLEMENT (real gap) · FIX (prod-reaching filler) · VERIFY (needs a human
> look — could be a bug/forgotten wire-up, not cruft) · KEEP-DOC (intentional; already documented).

---

## 0. Cross-cutting findings (release-cut blockers — surface to user)

- **Version is inconsistent across the repo.** `pyproject` reports **0.9.8**; `ui/package.json` +
  `ui/index.html` report **0.5.0-alpha.1**. The release cut (Phase 5) must reconcile to one real
  version and kill the alpha placeholder. *(sweep-filler + sweep-settings)*
- **Two forgotten wire-ups that smell like latent bugs, not dead code** — send these to the owning
  lane as **VERIFY-then-likely-fix**, not DELETE:
  - `ports/authority.py:375 reconcile_listeners()` — docstring says "surfaces so it shows up in
    `/api/ports`", but `routes/ports.py` only ever calls `list_ports()` → the reconcile pass never
    runs. Either wire it into the ports route or delete the intent. *(sweep-py-dead)*
  - `config/paths.py:400 bundle_chosen_marker()` — references `POST /api/bundles/{name}` +
    `GET /api/bundles/skip`, **neither route exists**. A whole feature is stubbed at the path layer
    with no route/UI. Decide: implement, or delete the orphan marker. *(sweep-py-dead)*

## 1. Dead Python  (~60–90 LOC; ruff-clean otherwise)

| file:line | symbol | evidence | conf | disposition |
|---|---|---|:--:|---|
| slots/manager.py:2589 | `_idle_monitor_loop()` | thin delegate; start/stop call `_reaper` directly, bypass it; 0 callers | H | DELETE |
| agents/manager.py:728 | `_all_known_drivers()` | docstring "test hook" but tests import `BUNDLED_AGENTS` directly; 0 callers | H | DELETE |
| api/routes/updater.py:420 | `_parse_flm_version()` | superseded by `_parse_flm_version_from_image()` (L502); 0 callers | H | DELETE |
| cli/capabilities_commands.py:237 | `dry_run` param | `del dry_run # deprecated`; never read after intake | H | DELETE |
| config/paths.py:400 | `bundle_chosen_marker()` | routes it names don't exist — see §0 | H | VERIFY (feature missing) |
| ports/authority.py:375 | `reconcile_listeners()` | never called — see §0 | H | VERIFY (wire-up bug?) |
| ports/authority.py:346 | `release_port()` | 0 callers; release path may differ | M | VERIFY |
| ports/authority.py:200 | `is_held_by_other()` | 0 callers | M | VERIFY |
| agents/hermes_provision.py:3608 | `_resolve_custom_providers()` | half-wired; hermes-side counterpart absent (~25 LOC) | M | VERIFY |
| omni_router/route_to_chat.py:52 | `_model_of()` | 0 callers; sibling `_system_prompt_of()` IS used | M | VERIFY |
| config/paths.py:378 | `first_run_lock()` | 0 callers; `install/perms.py:233` inlines the same string (dup risk) | M | VERIFY |
| bench/control.py:109 | `pop_next()` | 0 callers; enqueue/dequeue used | M | VERIFY |
| providers/kokoro.py:209, providers/qwen3tts.py:238 | `.health()` | ABC-required override, self-documented dead | H | KEEP-DOC |

## 2. Dead UI  (~20 LOC — 16 dead endpoint constants in `ui/src/api/endpoints.ts`)

FE is unusually clean: **no dead components/hooks/routes** — every apparent orphan was a
`main.tsx` side-effect window-global (64 such imports, by design; the plan's "~47" is stale). The
only rot is unused endpoint constants (each has a live literal duplicating it → 2-sources-of-truth):

| endpoints.ts | consts | disposition |
|---|---|---|
| :377-378 | `comfyNativeQueue`, `comfyNativeHistory` | DELETE or wire `services-card.jsx:118` to them |
| :114 | `statsPower` | DELETE or wire `useStatsPower.ts` |
| :28 | `comfyuiPreview` | DELETE or wire `comfyui-pane.jsx:533` |
| :30, :49-50, :80, :149, :136, :214, :454, :457, :164 | `slotMetrics`, `slotStateStream`, `modelScanCommit`, `agentActivity`, `agentMcpClient`(singular), `agentPersonaUpdate`, `boardProfile`(singular), `boardDiagnostics`, `memoryList` | DELETE (0 refs) |
| :183,185,187,193 | `memoryBankEntities/Entity/Memories/Tags` (sibling `…EntityGraph` IS wired) | DELETE |
| :120,158 | `agentApprovalsStream/List` | KEEP-DOC (comment: future-reserved SSE) |
| :301 | `doctor` | KEEP-DOC (documented API-lane request; route pending Phase 1) |

**KEEP-DOC gated affordances (NOT dead — intentional, reason-stubbed):** `SecurityPage.jsx`
client-key "Set key…" button, login-throttle status row, client-key `state="unknown"` — all await
Phase-1 backend routes (`/api/auth/*` posture). Leave until Phase 1 wires them.

*Deferred:* `mockFixtures.ts` (1103 LOC) not per-key audited — spot-checks clean; a full
mock↔endpoints parity pass belongs to the Phase-1 UI mock-realignment lane.

## 3. Stubs & unfinished  (codebase clean — 1 real gap)

| file:line | stub | disposition |
|---|---|---|
| registry/curated.py:18 | curated STT/TTS FirstRun picks (Moonshine/Kokoro/VibeVoice) blocked on multi-file pull shape | **IMPLEMENT** — FirstRun UX gap; needs multi-file pull mode (`pull.py`) + validator relax |
| install/perms.py:1 | `FIXME(phase4)`: `api.env` 0644 world-readable may carry tokens | TRIAGE → Phase-4 security lane (interim OK: api.env usually empty on release installs) |
| providers/base.py:194, container.py:309, kokoro.py:44, qwen3tts.py:39 | `NotImplementedError` on `image_ref()`/`start_cmd()` | KEEP — intentional (systemd/quadlet owns lifecycle) |
| tests/fixtures/hermes/contracts/memory_provider.py:1 | ABC stub `NotImplementedError` | KEEP — vendored test fixture |

*Note:* the Hermes `reflect` gap named in `handoff-r5-endgame.md §4.5` did **not** surface as a
`NotImplementedError`-style stub here — treat it as a Phase-3 route-completion item, verify the
actual route body before scoping.

## 4. Dead / inert settings + overdue deprecations

| file:line | key/flag | disposition |
|---|---|---|
| cli/slot_commands.py:403-522 | `--backend` flag — docstring promised rename "in v0.2", now at 0.9.8 | **DEPRECATE — sunset OVERDUE**, remove this release (folds into FLAGS-own CLI tranche) |
| config/schema.py:354-361 | `SlotConfig.runtime` = `Literal["container"]` (single legal value) | DELETE next release (pure ceremony) |
| config/schema.py:140 | `ModelConfig.rope_freq_base` — "intentionally NOT emitted", only round-trips | DEPRECATE + strip from the UI edit drawer (it edits a no-op) |
| config/schema.py:473-482 | `SlotConfig.workers` — read only to warn if !=1, never emitted | DEPRECATE (confirm the "one release" window passed) |
| config/schema.py:2365-2374 | `MemoryConfig.engine="cognee"` — wrapper removed; value resolves to hindsight | DEPRECATE (accepted value with no real backing) |
| config/schema.py:342-348 | `SlotConfig.provider` — doc claims "UI labels only" but no UI consumer found | VERIFY before delete (possible dynamic use) |
| cli: main.py:166 `probe`, registry_commands.py:308 `registry import`, model_commands.py:149/209 `register`/`assign`, slot_commands.py:607/629 `add`/`remove`, upstream_commands.py:550 `set-credentials` | already-stamped deprecated aliases | DEPRECATE — batch a sunset-date decision (keep or cut this release) |

**KEEP (deprecated-label but load-bearing — do NOT delete):** `[models].pull_root` (active
`effective_store()` fallback), capabilities `backend=` key (active 1→2 migration). Env vars: all
~31 `HAL0_*`/`HF_*`/`HONCHO_REF` documented vars have live readers — **no orphans**.

## 5. Filler / placeholder strings  (4 prod-reaching — all must go before release)

| file:line | string | disposition |
|---|---|---|
| ui/index.html:15, :18 | `v0.5.0-alpha.1` in `<title>` + meta description | FIX (see §0 version reconcile) |
| ui/src/dash/settings/pages/models/LibraryDownloadsPage.jsx:21 | `"not yet wired — placeholder"` | FIX — **wire or demote** (this IS endgame Phase-2 "two placeholder pages") |
| ui/src/dash/settings/pages/observability/HealthStatsPage.jsx:20 | `"not yet wired — placeholder"` | FIX — **wire or demote** (second placeholder page) |
| agent-card.jsx:307 "Coming soon"; hermes_provision.py:4270 weak-key allowlist; manifest.json `_legacy_toolboxes` | — | OK / TEST-ONLY / VERIFY-low (legit roadmap label, security allowlist, deprecated legacy digests) |

---

## 6. Apply plan (SWEEP lanes — by collision class + model tier)

Merge like any lane: worktree, capped gate, Fable review, board SHA. Ordering respects §4 phase
windows where a SWEEP item overlaps a phase lane.

1. **`sweep-ui-endpoints`** (UI · Haiku): delete the 16 dead consts (or wire the 3 that have a
   literal-duplicate — pick one source). Runs anytime; coordinate with the Phase-1 mock-realignment
   lane to avoid a double-edit of `endpoints.ts`.
2. **`sweep-py-delete`** (mixed · Sonnet): the 4 H-confidence DELETEs (§1). Grep-reconfirm at apply.
3. **`sweep-py-verify`** (mixed · Sonnet, then Fable adjudication): the 8 VERIFY rows — especially
   the two §0 wire-ups; each resolves to fix / delete / keep-doc, not a blind delete.
4. **`sweep-settings-deprecate`** (MODEL/CLI · Sonnet): the overdue `--backend` + dead `dry_run` +
   single-value `runtime` + `rope_freq_base` UI-drawer strip. **Fold `--backend` into FLAGS-own**
   (Phase 2) and the settings items into the settings-seam lane — one owner per fact.
5. **`sweep-filler`** (UI · Haiku): the 2 index.html version strings (with §0 reconcile) + wire/demote
   the 2 placeholder settings pages (**fold into Phase-2 settings-seam** — same files).
6. **`curated-stt-tts`** (MODEL · Sonnet): the one real stub — needs multi-file pull mode; size it
   against `pull.py`. Candidate for Phase-1 or deferred if it grows.

**SWEEP DoD:** every prod-reaching filler fixed; every H-confidence dead symbol/const deleted;
every VERIFY row adjudicated (fixed/deleted/kept-doc with a reason); overdue deprecations removed
or given a real sunset stamp; version reconciled to one value; the **scar-ratchet baseline drops**
to match and does not regress; docs-reference ratchet green.
