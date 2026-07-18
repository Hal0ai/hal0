# hal0 REWORK — canonical board (single source of truth)

> **How to use this board:** see `REWORK_BOARD_PROTOCOL.md` (paste-in prompt — reading order,
> single-writer rule, lane lifecycle, verify/merge/checkpoint discipline, agent-dispatch skeleton).

> **RATIFIED canonical board.** Replaces the dual trackers
> (`hal0-rework-tracker.md` = session-3/model-layer, `hal0-rework-tracker-surface.md` =
> session-4/surface+deploy). Once ratified, **retire both** — one writer-owned board only (review #10).
> Canonical spec = `REWORK.md` (finish line, checkpoints R1-R5, golden-paths, per-lane DoD; formerly
> `hal0-rework-plan-copy.md`). Full history/derivations = `hal0-rework-plan.md`. Latest handoffs:
> `hal0-rework-handoff-session3.md` (model-layer) + `/tmp/hal0-rework-handoff-session4-continue.md`
> (§7.4 privilege-drop pickup). This board is the *status* layer over the spec.
>
> Reconciled 2026-07-18 from both trackers + live session state. Base:
> `main` = `rework/descar` = `6aa565b8` (`rework-R2.1`); clean single checkout, zero divergence,
> no active agents. Scar 202 / baseline 202.
>
> **LIVE BOARD (2026-07-18, R4-primary remote session): this in-repo copy is now the live canonical
> board.** Sole orchestrator = the remote R4-primary session (single-writer rule applies to it);
> board updates ride merge pushes on `rework/descar`. Host-side `/home/mint/REWORK_BOARD.md` and
> `/tmp` handoffs are retired as authority — handoffs live in `docs/rework/`.

## Legend
`status`: ✔ done+CI-green · ▶ in-flight · ☐ todo · ⏸ deferred (safe, non-breaking) · ⛔ blocked
`owner_class` (collision class — serialize within a class): SEC · MODEL · RUNNER · SLOT · INSTALL ·
HERMES · API · UI · OBS · DOCS · DEPLOY
`checkpoint`: R1 secure+installable · R2 model-layer · R3 slot-runtime · R4 brain+hermes · R5 surface+launch

## Checkpoint status (the finish-line view)
| Ckpt | Theme | State | What remains |
|------|-------|-------|--------------|
| **R1** | Secure + installable | **✅ ON MAIN** (`ecdc0950`, tag `rework-R1`) | Merged 2026-07-18. Only tail open: KB-1 hardening (WS/SSE key audit, Security UI page, login rate-limit) — non-blocking. |
| **R2/R2.1** | Model layer + stabilization | **✅ ON MAIN** (`be4b0bba` / `6aa565b8`, tags `rework-R2` / `rework-R2.1`) | R2 model/runtime/docs/Hermes substrate plus R2.1 store-GC #8/#9 and vetted Hermes contract-pin foundation; main and descar have zero divergence. |
| **R3** | Slot runtime | **substrate stacked on descar** | §11.1 slot-id + §11.2 PortAuthority (migration 004) landed (inert/additive). Remaining: manager.py re-key (PR3-6), P3-quadlet, GTT capacity, `inspect/apply/delete/subscribe` interface. |
| **R4** | Brain + Hermes | **Hermes installer substrate landed; product adapters open** | Tool-loop and §7.4 privilege-drop/F.7 done. Open: P3-brain, KB-4 board, runtime role resolution, Hermes compatibility pin, core/provider/memory/voice adapters, core-without-Hermes proof. |
| **R5** | Surface + launch | **open** | Settings data-seam (MVP split done), §21.4 doctor, docs collapse (P4-docs), migration rehearsal, cutover plan. |

**R1 → main is the highest-ROI next move** once the in-flight lanes reach a freeze point (review #3).

---

## Lane table

### R1 — Secure + installable  (land to main first)
| id | lane | status | class | deps | commit / branch | verify | deploy_state |
|----|------|:--:|--|--|--|--|--|
| P0-* | consolidate / descar / sunset guardrail | ✔ | — | — | descar base, `01c9a01a` | baseline 216 green | — |
| P1-* | pure-deletion de-scar (cognee, updater-a, agents, providers, shims, docs) | ✔ | — | — | batch1 `d7b2fa36`/`4eb9376f`+ | scar→215 | — |
| P2-toolloop | one tool-loop engine | ✔ | API | — | P2 safe-batch | CI-green | — |
| P2-hf | one HF client | ✔ | API | — | P2 safe-batch | CI-green | — |
| P2-composite | de-pseudo hal0 composite upstream | ✔ | API | — | P2 safe-batch `1d7e8721` | CI-green (fixed sig-drift) | — |
| P2-device | `device` sole truth, drop 4 translators | ✔ | MODEL | — | descar | scar 214→204, 786+ tests | — |
| KB-1 | gate board/chat/events + exposure table + middleware | ✔ | SEC | — | `4a868895` | 35 auth + exposure-CI green | — |
| KB-1-tail | WS/SSE `?api_key` audit · origin-check DiD · login rate-limit (Security UI page split to later UI wave) | ✔ | SEC | KB-1 | `5f5eb913` (was `89063bb8`) | CI+γ green on descar; uvicorn.error WS log scrub + origin gate before tier-auth + sliding-window login 429; scar 202 | — |
| P3-perms | one `hal0` user, OwnershipStore born-owned, `hal0-systemctl` | ✔ | INSTALL | — | descar | validated on halo (`doctor perms` clean) | born-owned OK on fresh |
| §17.7 | remove installer Honcho path | ✔ | INSTALL | — | `630173c6` | grep-clean, services cleaned (`b60a6614`) | — |
| §17.8 | minimal setup wizard (delete setup_ui.py) | ✔ | INSTALL/CLI | — | `2f8e10c9` | 9 cli tests | — |
| installer-fixes | NFS-tolerant comfyui-share · self-aware :3001 port check | ✔ | INSTALL | — | `51c542fa`,`51c36fab` | preflight 6/6; **halo install EXIT=0** | idempotent on halo |
| §21.7 | managed-arg denylist | ✔ | API | — | descar | CI-green | shipped seeds clean |
| §21.14/.15/.12/.5 | hal0 chat · PR-template · client-docs · /v1/models ext | ✔ | API/DOCS | — | `77e41b93`+ | CI-green | — |
| **#9 wheel** | wheel ships migrations+data | ✔ | DEPLOY | — | wheel-verify2 | ships 3/3 mig + 4/4 toml; `uv build` | verified |
| **#10 perms live-migration** | chown lxc105 root→hal0 | ⏸ | DEPLOY | — | — | MOOT for fresh halo | only for old lxc105 (not touching) |
| **#11 live-config strip** | drop drifted denied flags | ⏸ | DEPLOY | — | — | MOOT for fresh halo | old lxc105 only |

### R2 — Model layer
| id | lane | status | class | deps | commit / branch | verify | deploy_state |
|----|------|:--:|--|--|--|--|--|
| ML-1 | SQLite registry + files/models/revision tables | ✔ | MODEL | SQLite | descar `001_registry.sql` | 283 db+registry tests | — |
| ML-2/3 (ml-store) | file-set pulling + unified store resolver + by-id + refcount GC + `003_store` | ✔ | MODEL | ML-1 | descar `38f9d9e3` | 1490+ tests, scar→202 | migrations ship in wheel |
| ML-6 (§7.1d) | modality/capability taxonomy; kill tags→labels routing | ✔ | MODEL | — | descar | CI-green | — |
| **ML-4** | runner-image registry (`RUNNER_IMAGES`) + unified image resolution + `preferred_runner` | ✔ | RUNNER | ml-store | merge `4b7e9cb6` | merged suite; CI-green before later descar merges | — |
| **ML-5** | flag resolution (mtp/jinja capability, kill nojinja clones, family→arch) | ✔ | RUNNER | ML-4 | through `0852edfd` | merged suite; CI-green before later descar merges | — |
| R2-golden | store GC + pull/revision/refcount + multi-shard/mmproj golden-path tests | ✔ | MODEL | ml-store | `b7461bbe`; checkpoint `be4b0bba` | CI-green and merged to main | R2 accepted |

### R3 — Slot runtime
| id | lane | status | class | deps | commit / branch | verify | deploy_state |
|----|------|:--:|--|--|--|--|--|
| P3-slots | split manager.py (capacity/drift/npu/reaper) +§21.10 GTT | ✔ | SLOT | — | `dbc2c771` (4145→2769) | 392 tests | — |
| §11.1 | stable opaque slot-id; name = display label | ✔ | SLOT | — | merge `5d134c3b` | substrate tests merged | inert/additive on halo143 |
| §11.2 | PortAuthority (`port_claim` SQLite) | ✔ | SLOT | SQLite | `639cfb60`, merge `5d134c3b` | migration 004 + allocator tests merged | inert/additive on halo143 |
| **SLOT re-key (PR3–6) — increment A** | slot-id identity + PortAuthority wired live (acquire/release + TOML writeback, drop port=8081); /rename + /by-id + /by-name + /api/ports 5th source; non-destructive `fold_identity()` boot-fold | ✔ | SLOT | §11.1✔,§11.2✔ | `e0fd6d7c` (was `1f062b89`) | CI+γ green; additive/bijective (name↔id), 9 new + 668 targeted, scar 202; no exposure/migration change | — |
| **SLOT re-key — increment B** | internal `dict[int]` re-key of 7 dicts (single `_key` chokepoint, surrogate→rebind, rename = pure relabel) + M5 one-shot name→id artefact migrator (`migrate_id_keying.py`, idempotent, ships INERT) | ✔ | SLOT | increment A✔ | merge `91b55ad0` (lane `cd5e091b`/`3a4c9011`/`69c14bfc`) | Opus-built (TDD), Fable-reviewed + independently re-run: 550 combined incl. golden-paths harness; ruff/format/import/sunset green; 3 cross-fence one-liner test touches reported | **held for deploy (halo143 migration window):** live unit `hal0-slot@<name>`→`@<id>` + podman rename, M5 on real state, runtime path/unit flip to id (`_state_file`/`_config_file` + unit rendering) — must land atomically with M5 going live; consumed by P3-runtime-db |
| **container.py arg-quoting bug** | ExecStart emitted space-less tokens bare → systemd stripped inner double quotes → `{enable_thinking:false}` → llama JSON parse error → slot won't start | ✔ | SLOT | — | `cd5e091b` (in SLOT-B merge `91b55ad0`) | `shlex.quote` every token (safe tokens stay bare); regression test with the exact lxc105 token | unblocks no-think default for the brain (lxc105 §6) |
| SlotManager-deepen | `inspect/apply(desired)/delete/subscribe` small interface (review #4) | ☐ | SLOT | §11.1 | — | — | — |
| P3-quadlet | Podman Quadlet `.container` units; delete hand-rendered strings | ☐ | INSTALL/SLOT | P3-perms✔, §11.1 | spec `spec-p3-quadlet.md` | — | shares container.py |
| §20 bench | bench_run onto OBS schema | ☐ | OBS | ML-4 | spec `spec-bench.final.md` | — | needs GPU box; **note: hal0 llama.cpp forks reject newer GGUFs (e.g. Qwen3.5 UD-Q4_K_XL) on `rocmfp4-server`/`c077206`** — bench only models hal0 already serves (lxc105 §5.4) |

### R4 — Brain + Hermes
| id | lane | status | class | deps | commit / branch | verify | deploy_state |
|----|------|:--:|--|--|--|--|--|
| P3-brain | first-class `src/hal0/brain/`, zero-Hermes-dep, `/api/brain/chat` primary + `/api/board/chat` alias | ✔ | HERMES | P2-toolloop✔ | merge `24502baa` (was banked `b66b5e1e`, rebased onto `eede41c1`) | rebased+re-verified by orchestrator: 172 targeted (brain/board/exposure/route-collision combined), ruff+format, import smoke, scar 202; independent Fable review approved (sys.modules alias, ADMIN exposure rule, zero-Hermes-import verified); CI pending on merged tip | **brain model assets exist** (lxc105): `hal0-brain-sft` f16 + `-fpx4`(609MB)/`-fpx8`(1.1GB) ROCmFP4 quants registered, profile `brain-agent-fpx` — see `hal0-105-changes-summary.md`. No-think default needs the container.py quoting fix OR a `minicpm5-nothink.jinja` (internal `enable_thinking=false`). Brain identity correct only with hal0-brain system prompt (Hermes supplies persona) |
| §7.4 hermes-slim | privilege-drop (born-owned) → F.7 chown deletion; convergent installer slimming continues separately | ✔ | HERMES/INSTALL | P3-perms✔ | merge `86283e94` | incs 1-5 + F.7 reviewed; born-owned halo143 validation | deployed/validated on halo143 |
| HP-compat | select and test reviewed official Hermes tag/commit against the three plugin contracts | ✔ | HERMES | §7.4✔ | `f3e4e3e6`, guard fix `6f844901`, checkpoint `6aa565b8` | independent review; 79 targeted tests; full CI + Playwright green after vetted-ref guard reconciliation | R2.1 on main; Stage 0 accepted |
| HP-core | shared Hermes adapter core: auth, discovery, typed errors, retry policy, correlation IDs | ✔ | HERMES | HP-compat✔, KB-1✔ | merge `b1115b9d` | independent review + security re-review approved; 61 combined tests; exact-head full CI + Playwright green | merged on descar; Stage 1 core accepted |
| HP-role-api | generation-stamped runtime `GET /api/agents/{agent_id}/role-slots` + invalidation events | ▶ | API/HERMES | §11.1✔, §11.2✔, KB-1✔ | Task 3 merge `b02447a4`; WIP evidence banked `c11b21cb`/`a9fe36bd` | independently approved; combined 134 pass + 2 environment-only exclusions; ruff/format/import/sunset green | merged/pushed on descar; exact-head CI `29651621820` + Playwright `29651621876` running |
| HP-contract-surface | drift-watch → official pin (NousResearch `9de9c25f`) + contract freeze expanded 6→18 tracked files across all adapter touchpoints (MemoryProvider ABC, ProviderProfile, voice/PluginContext, API-server routes + security defaults) | ✔ | HERMES | HP-compat✔ | merge on descar (`merge(hermes): contract freeze expanded`) | 24 tests + 1 strict xfail; Opus-built, Fable-reviewed + independently re-run post-rebase; fixtures verbatim from pinned source; design-doc mismatches adjudicated (events.py dropped — hal0-side EventBus; runtime.py unfrozen — no stable single-file seam at pin) | **DECIDED (user, 2026-07-18): accept `terminal.backend=local`, harden the seam hal0 owns** — (i) audit + pin unit hardening (NoNewPrivileges, ProtectSystem=strict, ProtectHome, minimal ReadWritePaths, no hal0-secret paths) with a CI-checked test on the rendered unit; (ii) move `terminal.cwd` off `/etc/hal0` to a scratch dir; both fold into the hermes-provision rewrite lane. Strict xfail stays as the upstream-default record |
| hermes-provision-rewrite | §I convergent installer rewrite of `agents/hermes_provision.py` (5,368 lines — largest diagnosed module) + DECIDED security items: unit-hardening directives CI-checked, `terminal.cwd` → scratch dir, strong random `API_SERVER_KEY` generation | ☐ | HERMES/INSTALL | §7.4✔, HP-contract-surface✔ | spec `spec-hermes-provision.final.md` | convergent rerun (already-converged detection), no wholesale config.yaml replace, smoke test | R4; was implied under "slim installer" — explicit row per plan review |
| HP-memory | exclusive `hal0-memory`: private raw turns, shared durable facts default, private durable override, ranked provenance recall | ☐ | HERMES | HP-core✔, HP-contract-surface✔ | design `d75e5f88` (local) | two-copy parity; prefetch/prompt/sync; privacy and injection-resistance tests | held for R4 |
| HP-provider | `hal0-provider` (`chat_completions`): live slot/model inventory and restart-free role aliases | ☐ | HERMES | HP-core, HP-role-api | design `d75e5f88` (local) | discovery, SSE/backfill/gap, hot-swap, tool/stream/reasoning/vision tests | held for R4 |
| HP-voice | `hal0-voice`: Hermes STT/TTS routed through existing hal0 voice slots | ⏸ | HERMES | HP-core, §11.1✔, §11.2✔ | design `d75e5f88` (local) | capability/readiness, audio limits, interruption/fallback tests | **post-core (user 2026-07-18)** — below the R4 exit bar |
| HP-executor | hal0-board → Hermes worker bridge; hal0 remains canonical | ☐ | HERMES | HP-core, KB-4/5/6 | plan `447a851f`+ | heartbeat/block/handoff/cancel/reconcile; no board mirroring | held for R4 after KB-4 |
| HP-automation | Hermes Jobs/cron for scheduled agent work using stable hal0 role aliases | ⏸ | HERMES | HP-core, HP-provider | plan `447a851f`+ | CRUD/lifecycle, alias hot-swap, cron-memory isolation, no maintenance jobs | **post-core (user 2026-07-18)** — below the R4 exit bar |
| HP-context | optional non-memory context-engine plugin | ⏸ | HERMES | HP-core | design `d75e5f88` (local) | separate context contract suite if promoted | post-core; do not merge with memory |
| KB-2/3 | brain read-only default + approval-gate; resilience | ☐ | HERMES | — | — | — | — |
| KB-4/5/6 | hal0-owned board in SQLite; narrow dispatch seam; ETag concurrency | ☐ | HERMES | SQLite | — | — | — |
| HP-legacy-suite | superseded image/TTS/context/dashboard plugin bundle | ⏸ | HERMES | — | history/specs banked | superseded by focused HP-* lanes | post-core unless explicitly promoted |

### R5 — Surface + launch
| id | lane | status | class | deps | commit / branch | verify | deploy_state |
|----|------|:--:|--|--|--|--|--|
| P3-ui | settings.jsx 2598 split → Shell + 16 pages (MVP) | ✔ | UI | KB-1✔,§7.1d✔ | `2a1d2290`, PW-fix `c175980d` | 420 γ pass | — |
| P3-ui-dataseam | one typed settings client + schema + reload-class source (review #8/§K) + land Backend + Model-Defaults pages | ✔ | UI | ML-4✔ | `0c93a1f3` (was `41018109`) | CI+γ green (C7d flake cleared on re-run; local full γ 422/0); settingsClient façade + reloadClass source (ApplyBadge amber-chip fix) + useSettingsForm; Backend/GPU + Model-Defaults pages; scar 202 | — |
| §21.4 | doctor command + §21.3 system-info | ✔ | CLI | — | merge `0fdde455` (lane `9fbc81d1`) | Sonnet-built, Fable-reviewed (redaction path audited: canonical redact_config + key-name env scrub + Bearer/JWT text scrub) + independently re-run (77 lane + 33 combined); Diagnosis taxonomy (frozen HAL0-* IDs), --json on all 5 subcommands, `doctor bundle`, GET /api/system-info (CLIENT rule pre-landed — no exposure edit); PR6 gfx-guard IDs reserved for SLOT lane | bundle probes (rocminfo/rocm-smi/podman/journalctl) + system-info installed-state held for halo143 |
| P3-routers (inc 1) | extract pull-jobs/metrics/image-pull services from the two mega-routers (§J) | ✔ | API | — | merge `42d45603` (lane 3 extraction commits) | Opus-built, Fable-reviewed + independently re-run (509 lane + 31 combined); models.py 1774→1298, slots.py 1964→1513; re-export bindings keep api.__init__/monkeypatch seams; paths/order/payloads frozen, exposure untouched, route-collision guard green | — |
| P3-routers (inc 2) | remaining spec steps 10–20: typed Pydantic bodies, comfyui/benchmarks/chat_templates typed errors, MCP admin autogen, smaller slots extractions (voices/logs/flm_catalog/port_alloc); DoD targets ≤550/≤800 lines | ☐ | API | inc 1✔ | spec `spec-p3-routers.final.md` §5 | — | keep exposure classes valid |
| route-collision-test | reject literal shadowed by param routes (review #5/§J) | ✔ | API | — | **pre-existing `43f29e30`** (row was stale) | already landed in the R3-A stack with version-proof `_IncludedRouter` flattening + positive control; the duplicate merged at `0b93a48b` failed CI on newer FastAPI (0 APIRoutes via lazy wrappers) and was removed (`fix: drop duplicate route-collision test`). Lesson: grep for an existing owner before dispatching a board row | — |
| golden-paths-early | CI-runnable §21.11 subset: #9 rename · #10 delete-cleanup · #14 api-restart-no-bounce · #15 core-without-Hermes | ✔ | DEPLOY/tests | — | merge on descar (`merge(golden)`) | 9 tests, interface-level only (public routes; survives SLOT-B internals rewrite); Opus-built, Fable-reviewed + independently re-run; deploy-only remainders documented per scenario in `tests/golden_paths/__init__.py` for the halo143 runbook | acceptance harness for SLOT increment B |
| FLAGS-own | flags stick to MODELS; profiles = copy-on-stamp templates; slots lose flag/device/template surface; kill `profile.image` pin; "Runtimes" panel for runner images | ☐ | MODEL/UI | ML-4/5✔, SLOT-B (quoting fix), P3-ui-dataseam✔ | spec `spec-flags-ownership.md` (fully ratified 2026-07-18 incl. §7 slot-purity fold: device + chat_template → model; slot = id/name/model/port/state; create-slot device picker stamps the model) | migration folds slot overrides into models (divergent-share refusal path); golden #5 asserts no profile read at launch | serialize behind SLOT-B merge; migration rides P2-config window |
| P4-docs | collapse ARCHITECTURE/CONTEXT/AGENTS → one; ADR-or-inline | ☐ | DOCS | — | — | — | Stream D (MiniMax) |
| P4-tests | integration markers + CI fast/box/real-podman tiers | ☐ | DOCS | — | — | — | — |
| P4-rules | anti-scar rules in CONTRIBUTING | ☐ | DOCS | — | — | — | — |
| §21.11 golden-paths | the 15 deployment-shaped scenarios (plan-copy L602) | ☐ | DEPLOY | — | — | — | **pull earlier (review #7)** |

### Migration-window lanes (orchestrator-run live steps, NOT agents — plan-copy §migration)
| id | lane | status | class | deps | notes |
|----|------|:--:|--|--|--|
| P2-config | capabilities.toml → derived view; one apply engine | ☐ | MODEL | — | 3-release window + create-on-select |
| P2-memory | Honcho→Hindsight migrate per workspace, then ordered deletion | ☐ | HERMES | HP-memory | use existing `hal0 memory migrate --from honcho --to hindsight`; seed deterministic Honcho fixtures on fresh halo143 (or sanitized read-only LXC105 export); verify persisted `[honcho]` tolerance; never mutate LXC105 during rehearsal |
| P2-updater-b | one cosign+swap+rollback path | ☐ | INSTALL | — | Model B part 2 |
| P3-runtime-db | state.json/pull-jobs/events → SQLite (one table at a time) | ☐ | MODEL | SQLite | — |

---

## Next checkpoint base
R2.1 is accepted on `main` and `rework/descar` at `6aa565b8`. New R3/R4 lanes branch from this exact
base in isolated worktrees. The Hermes suite proceeds with Stage 1 (`HP-core` and `HP-role-api`), while
`HP-executor` remains gated on KB-4/5/6. No lane may recreate divergence by building on a pre-R2.1 base.

## Open review-driven adds (not yet lanes anywhere)
- **metrics-db split** — measure write-lock latency under concurrent pull+registry+metrics; split
  `hal0-metrics.db` if nontrivial (plan-copy L479 already sanctions).
- **golden-path harness earlier** — pull §21.11 ahead of remaining structural waves (review #7);
  live installer already found 2 bugs (NFS chmod, self-port) the capped suite missed.
  **Accepted (Fable plan review 2026-07-18): land the automatable subset (#9 rename, #10 delete,
  #14 api-restart, #15 no-Hermes) as CI-runnable integration tests BEFORE SLOT increment B merges;**
  halo143-only paths become a scripted runbook.

## Fable plan-review adds (2026-07-18 — accepted "ok"; fold into waves)
- **Migration-number allocation (protocol add):** numbers assigned at DISPATCH, on this board.
  Allocated: **005 = KB-4/5/6 board · 006 = P3-runtime-db slot-state · 007 = metrics split (if
  triggered)**. Two files at one version = broken migrate.
- **state.json double-touch:** increment B's M5 renames `state.json` that P3-runtime-db later moves
  to SQLite — either fold the slot-state table into the SLOT wave or scope M5 so runtime-db never
  re-migrates. Decide at SLOT-B dispatch.
- **KB-2/3 needs a spec before HP-executor/HP-automation:** tool-tier classification (read/mutating/
  destructive), approval-gate, injection-resistance tests for tool-output→model→destructive-tool
  chains (same bar as HP-memory row).
- **§I hermes-provision convergent rewrite = explicit lane** (spec `spec-hermes-provision.final.md`;
  file still 5,368 lines) — not implied under "slim installer"; give it a row at R4 dispatch.
- **Core-without-Hermes proof goes continuous:** CI job/marker tier running core suite with Hermes
  extras absent (golden path #15 as a gate, not a one-off).
- **P4-tests markers pulled forward:** tag podman/systemd-dependent tests now → local capped verify
  can run `tests/api -m "not podman"`; natural home of the C7d flake fix.
- **Docs-reference ratchet:** CI grep resolving every doc/spec path referenced in tracked markdown
  (mirrors scar ratchet; keeps P4-docs honest).
- **`scripts/lane_verify.sh`:** encapsulate the capped gate (ruff check + format --check, import
  smoke, sunset, named pytest targets) — shrinks dispatch prompts, kills the forgot-format class.
- **CI env ≠ uv.lock (bit twice, 2026-07-18):** CI pip-installs a floating FastAPI (lazy
  `_IncludedRouter` app.routes) while local venvs sync `uv.lock` (0.136.x, eager routes) — caused
  the duplicate route-collision failure AND the gp15 route-set failure, both CI-only. Fold into
  P4-tests: install CI from the lockfile (or add a lock-matrix job); until then, never assert on
  `app.routes` shape — probe routes behaviorally.
- **Proposed, needs user decision at R4 planning:** demote HP-voice + HP-automation below the R4
  exit bar (finish line = "small, optional Hermes integration"); god-module LOC burn-down tracked
  per checkpoint (today 18,836 across the seven diagnosed modules).

## Folded from lxc105 live session (`/home/mint/hal0-105-changes-summary.md` — reference, NOT deploy state)
lxc105 (10.0.1.142) is the untouched live reference — never deploy there. These are the durable
code/requirement findings from that session; the live model-swaps/host fixes are context only.
- **container.py arg-quoting bug** — now a SLOT lane row in R3 (space-less JSON token loses quotes →
  slot crash). Real correctness bug; fix during increment B / P3-quadlet (both own container.py).
- **Reasoning-channel / no-think default** (SLOT + templates) — MiniCPM5/saber are reasoning models;
  under `--jinja` + default reasoning-format the answer lands in `reasoning_content` and `content` is
  empty unless `chat_template_kwargs:{enable_thinking:false}` is sent (currently only via the broken
  flag). Fix = the container.py quoting bug OR ship a `*-nothink.jinja` that defaults it internally
  (froggeric Qwen template already does this). Also consider `--reasoning-format` to strip the cosmetic
  empty `<think></think>` prefix. Fold into P3-brain + any chat-template lane.
- **Hermes API-server hardening** (HERMES/SEC) — the live Hermes API server was enabled on lxc105 with
  `API_SERVER_HOST=0.0.0.0`, a placeholder `API_SERVER_KEY=change-me-local-dev`, and terminal backend
  `local` (unsandboxed). The Hermes integration lanes (HP-*, §7.4 slimming) and the installer MUST:
  bind narrow / gate by real key (no placeholder), and never expose an unsandboxed local executor by
  default. Add to the Hermes suite security checklist + `security/exposure.py`/installer review.
- **Brain model assets** — trained `hal0-brain-sft` (f16) + `-fpx4`/`-fpx8` ROCmFP4 quants + profile
  `brain-agent-fpx` exist (registered on lxc105); noted on the P3-brain row. Serving image for ROCmFP4
  STRIX quants: `hal0-rocmfpx:c077206`. Deploy-to-halo143 decision is a live/migration step, not a
  code lane.
