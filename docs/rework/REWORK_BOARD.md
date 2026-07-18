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
| **R3** | Slot runtime | **✅ ON MAIN** (`ab3e88f3`, tag `rework-R3`, collapsed 2026-07-18 from descar `671ca623`, CI run 29656507947 all-green) | Complete: slot-id identity + PortAuthority + dict[int] re-key + inert M5 migrator + quoting fix + Quadlet units + deep interface + GTT. Held for halo143 window: quadlet `@`-name verify, M5 live rehearsal, runtime id-flip. |
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
| SlotManager-deepen | `inspect/apply(desired)/delete/subscribe` small interface (review #4) | ✔ | SLOT | §11.1✔ | merge `919b2f36` (lane `b47c039c`) | Opus-built, Fable-reviewed + independently re-run (466 combined); id-keyed SlotInterface facade via `SlotManager.interface`; additive-only (wide surface intact); DesiredSlotState documents FLAGS-own narrowing; 12 new interface tests | wide-surface collapse = later increment (after FLAGS-own) |
| P3-quadlet | Podman Quadlet `.container` units; hand-rendered ExecStart chain + docker fallback DELETED; instance token templated (`slots/naming.py` — M5 id-flip = 1 parameter); privilege seam `write-quadlet`/`remove-quadlet` (validated) | ✔ | INSTALL/SLOT | P3-perms✔, §11.1✔ | merge `ad821f9d` (lane `00843bfd`) | Opus-built, Fable-reviewed + independently re-run (554 lane + 504 combined incl. golden-paths); quoting regression ported; seam.py additive-only | **VALIDATED on halo150 (2026-07-18):** `@`-named `.container` accepted, generator converts (via podman-4.x `PodmanArgs=` compat `5adf6e0f` — AutoRemove/GroupAdd/SecurityOpt are 5.0-only keys; GPU groups preserved), container Up + health + GPU inference + clean teardown on podman 4.9.3 (= lxc105's substrate). Held: OwnershipStore row for /etc/containers/systemd. Q.3 OpenWebUI companion deferred. Podman-5 template refresh = R5 DEPLOY row (native keys + crash-path auto-remove) |
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
| hermes-provision inc 1 | six security/validation deliverables: unit-hardening CI test · terminal.cwd→scratch · strong random API_SERVER_KEY · AppArmor containers.conf preflight (smoke-triggered) · stale agent drop-in cleanup (241 class) · --repair ownership reconcile | ✔ | HERMES/INSTALL | §7.4✔, HP-contract-surface✔ | merge `db3c5513` (lane `0703a0ec`) | Opus-built, Fable-reviewed + independently re-run (71 targeted + 149 combined); all six with tests; contract suite green | deploy smoke on 150 (apparmor path) + 143 (clean path) per both-boxes policy |
| hermes-provision inc 2 | the convergence rewrite: delete PHASES/run()/provision.json machinery → linear `install_hermes()`; module split; substantial line/scar drop (file now 5,696); rewrite 3 coupled test files; brain-phase relocation (cross-fence with brain/api — coordinate) | ☐ | HERMES/INSTALL | inc 1✔ | spec `spec-hermes-provision.final.md` | double-run convergence test via fakes | R4 |
| HP-memory | exclusive `hal0-memory`: private raw turns, shared durable facts default, private durable override, ranked provenance recall | ✔ | HERMES | HP-core✔, HP-contract-surface✔ | merge `b010c7ec` (lane 2 commits) | Opus-built, Fable-reviewed; full 19-method roster; shared-default flip (was inverted private-default); 5 privacy + 4 injection-resistance tests; two-copy parity byte-verified; 141 passed + ratified terminal.backend xfail | queue_prefetch = single-slot park (no in-plugin worker, honest bound) |
| HP-provider | `hal0-provider` (`chat_completions`): live slot/model inventory and restart-free role aliases | ✔ | HERMES | HP-core, HP-role-api | merge `57461cbe` (lane `974b7609`) | Opus-built, Fable-reviewed; contract-fixture-driven registration (module-level `register_provider` per frozen 9de9c25f — spec §4's ctx seam doesn't exist); no-cache fetch_models = hot-swap; 57 lane tests, 174 combined green | plugin_targets dir-drop entry rides provision-inc2 (`"hal0-provider": …/model-providers/hal0`) |
| HP-voice | `hal0-voice`: Hermes STT/TTS routed through existing hal0 voice slots | ⏸ | HERMES | HP-core, §11.1✔, §11.2✔ | design `d75e5f88` (local) | capability/readiness, audio limits, interruption/fallback tests | **post-core (user 2026-07-18)** — below the R4 exit bar |
| HP-executor | hal0-board → Hermes worker bridge; hal0 remains canonical | ✔ | HERMES | HP-core, KB-4/5/6✔ | merge (lane commit `d41e1f8a`) | Opus-built, Fable-reviewed; attaches at KB-5 seam via register(); inert unless HERMES_DASHBOARD_BASE_URL set; 26 tests incl. spy-store invariant (zero canonical mutators called); 82 board tests green | ⚠ WORKER_BASE_PATH `/api/plugins/kanban/runs` is unpinned by contract fixtures — validate against live Hermes at next both-boxes deploy |
| HP-automation | Hermes Jobs/cron for scheduled agent work using stable hal0 role aliases | ⏸ | HERMES | HP-core, HP-provider | plan `447a851f`+ | CRUD/lifecycle, alias hot-swap, cron-memory isolation, no maintenance jobs | **post-core (user 2026-07-18)** — below the R4 exit bar |
| HP-context | optional non-memory context-engine plugin | ⏸ | HERMES | HP-core | design `d75e5f88` (local) | separate context contract suite if promoted | post-core; do not merge with memory |
| KB-2/3 | brain read-only default + approval-gate; resilience | ☐ | HERMES | — | — | — | — |
| KB-4/5/6 | hal0-owned board in SQLite (migration 005); Hermes proxy DELETED; local WS events; additive ETag/If-Match → 409; KB-5 executor seam (Protocol + no-op registry) | ✔ | HERMES | SQLite✔ | merge `516965d9` (lane 3 commits) | Opus-built, Fable-reviewed + independently re-run (240 lane + 283 combined); frozen wire contract; one-time tolerant import; board serves with hermes_kanban=None (core-without-Hermes) | HP-executor now unblocked (attach at the KB-5 seam) |
| HP-legacy-suite | superseded image/TTS/context/dashboard plugin bundle | ⏸ | HERMES | — | history/specs banked | superseded by focused HP-* lanes | post-core unless explicitly promoted |
| O12-store | rootful/rootless store split: hal0-api podman introspection now via `sudo -n hal0-podman-ro` seam (privileged seam #5, `images` verb only, hardcoded argv); honest rootless fallback + `podman_context` field on system-info; 9e07c0d3 `.config`/`.local` chown rows RETIRED (lock rows kept) | ✔ | DEPLOY/INSTALL | halo143/150 finding | merge `252e860c` (lane `cbc8e94d`) | Sonnet-built, Fable-reviewed; 116 lane + 56 combined green; visudo/bash -n clean; mcp.py had no podman site (brief assumption corrected) | live validation at next both-boxes deploy (seam installed → context flips rootful) |

### R5 — Surface + launch
| id | lane | status | class | deps | commit / branch | verify | deploy_state |
|----|------|:--:|--|--|--|--|--|
| P3-ui | settings.jsx 2598 split → Shell + 16 pages (MVP) | ✔ | UI | KB-1✔,§7.1d✔ | `2a1d2290`, PW-fix `c175980d` | 420 γ pass | — |
| P3-ui-dataseam | one typed settings client + schema + reload-class source (review #8/§K) + land Backend + Model-Defaults pages | ✔ | UI | ML-4✔ | `0c93a1f3` (was `41018109`) | CI+γ green (C7d flake cleared on re-run; local full γ 422/0); settingsClient façade + reloadClass source (ApplyBadge amber-chip fix) + useSettingsForm; Backend/GPU + Model-Defaults pages; scar 202 | — |
| §21.4 | doctor command + §21.3 system-info | ✔ | CLI | — | merge `0fdde455` (lane `9fbc81d1`) | Sonnet-built, Fable-reviewed (redaction path audited: canonical redact_config + key-name env scrub + Bearer/JWT text scrub) + independently re-run (77 lane + 33 combined); Diagnosis taxonomy (frozen HAL0-* IDs), --json on all 5 subcommands, `doctor bundle`, GET /api/system-info (CLIENT rule pre-landed — no exposure edit); PR6 gfx-guard IDs reserved for SLOT lane | bundle probes (rocminfo/rocm-smi/podman/journalctl) + system-info installed-state held for halo143 |
| P3-routers (inc 1) | extract pull-jobs/metrics/image-pull services from the two mega-routers (§J) | ✔ | API | — | merge `42d45603` (lane 3 extraction commits) | Opus-built, Fable-reviewed + independently re-run (509 lane + 31 combined); models.py 1774→1298, slots.py 1964→1513; re-export bindings keep api.__init__/monkeypatch seams; paths/order/payloads frozen, exposure untouched, route-collision guard green | — |
| P3-routers (inc 2) | slots voices/logs/flm_catalog/port_alloc → services; benchmarks/chat_templates/comfyui typed errors (routes/ HTTPException = 0) + comfyui typed bodies; models add-from-path/HF-inspect/list_all → models_service | ✔ | API | inc 1✔ | merge `b9339d27` (5 lane commits) | Opus-built, Fable-reviewed + independently re-run (213 post-rebase); status codes byte-preserved (kept 422 over spec's 400 slip); exposure untouched; port_alloc.py docstring-flagged as PortAuthority merge target | models.py 987 / slots.py 1289 vs DoD ≤550/≤800 — gap = deferred pull-orchestration extraction (spec step 9) |
| P3-routers (inc 3) | remaining: pull/update-pull orchestration extraction (highest-risk, monkeypatch-heavy), models/slots typed bodies (needs dashboard-key audit — status-code collision), MCP admin route-map autogen (spec step 20, big standalone) | ☐ | API | inc 2✔ | spec §5 steps 9/16/19/20 | — | each item its own lane; typed-body audit before shipping |
| route-collision-test | reject literal shadowed by param routes (review #5/§J) | ✔ | API | — | **pre-existing `43f29e30`** (row was stale) | already landed in the R3-A stack with version-proof `_IncludedRouter` flattening + positive control; the duplicate merged at `0b93a48b` failed CI on newer FastAPI (0 APIRoutes via lazy wrappers) and was removed (`fix: drop duplicate route-collision test`). Lesson: grep for an existing owner before dispatching a board row | — |
| golden-paths-early | CI-runnable §21.11 subset: #9 rename · #10 delete-cleanup · #14 api-restart-no-bounce · #15 core-without-Hermes | ✔ | DEPLOY/tests | — | merge on descar (`merge(golden)`) | 9 tests, interface-level only (public routes; survives SLOT-B internals rewrite); Opus-built, Fable-reviewed + independently re-run; deploy-only remainders documented per scenario in `tests/golden_paths/__init__.py` for the halo143 runbook | acceptance harness for SLOT increment B |
| FLAGS-own | flags stick to MODELS; profiles = copy-on-stamp templates; slots lose flag/device/template surface; kill `profile.image` pin; "Runtimes" panel for runner images | ☐ | MODEL/UI | ML-4/5✔, SLOT-B (quoting fix), P3-ui-dataseam✔ | spec `spec-flags-ownership.md` (fully ratified 2026-07-18 incl. §7 slot-purity fold: device + chat_template → model; slot = id/name/model/port/state; create-slot device picker stamps the model) | migration folds slot overrides into models (divergent-share refusal path); golden #5 asserts no profile read at launch | serialize behind SLOT-B merge; migration rides P2-config window |
| P4-docs | collapse ARCHITECTURE/CONTEXT/AGENTS → one; ADR-or-inline | ☐ | DOCS | — | — | — | Stream D (MiniMax) |
| P4-tests (infra) | CI installs from uv.lock (setup-uv + `uv sync --frozen` — kills the CI-vs-local version-skew class) + podman/systemd/network markers registered & applied | ✔ | CI | — | merge `ff8817ce` (lane commits salvaged after agent stop) | Fable-reviewed + verified (yaml parse, marker collection smoke 1323/1325); CI behavior unchanged, local capped verify can now run tests/api with `-m "not podman and not systemd and not network"` | remaining: C7d flake diagnosis, CI tier split — fold into a later P4-tests increment |
| P4-rules | anti-scar rules in CONTRIBUTING | ☐ | DOCS | — | — | — | — |
| UI-D1-D3 | design-canvas implementation: ModelDrawer (copy-on-stamp flags editor + divergence + reset + managed-arg pre-check + duplicate flow), slot purity surfaces (create/rename/delete dialogs, slot-modals −337 lines), Runtimes page | ✔ | UI | FLAGS-own spec (ratified), design canvas | merge `2cfec5e1` (lane 3 phase commits) | Opus-built, Fable-reviewed + independently re-run (lint/typecheck/build 0; 23-spec targeted γ re-run incl. rewritten slot-drawer-profile-v3; agent full run 127 γ green); client managed-arg mirror carries keep-in-sync note vs argv.py | D4-D6 (Security/migration-UX/diagnostics panel) = follow-up UI lane |
| UI-API-1 | backend affordances the D1-D3 surfaces flagged: (1) model-save managed-arg screen or POST /api/models/{id}/validate — UNVERIFIED whether model PUT screens extra_args vs §21.7 denylist (AUDIT FIRST, possible live gap); (2) RUNNER_IMAGES exposure w/ format/arch compat + writable preferred_runner for the override dropdown; (3) POST /api/models/{id}/duplicate (refcounted weights + template stamp); (4) per-runner digest-drift + pull route (ADMIN, SSE) for Runtimes actions | ☐ | API/MODEL | UI-D1-D3✔ | — | item 1 is the priority: a model whose extra_args smuggles --port would fail only at launch today | — |
| §21.11 golden-paths | the 15 deployment-shaped scenarios (plan-copy L602) | ☐ | DEPLOY | — | — | — | **pull earlier (review #7)** — subset #9/#10/#14/#15 landed CI-side (golden-paths-early row) |

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

## halo150 deploy-validation results (2026-07-18 — full report: `halo150-r3-deploy-issues.md`)
- **Runbook Phases 0–5 COMPLETE.** Greens: fresh R3 git install, auth, doctor (post-O2 CLI-auth
  fix), slot CRUD, quadlet generation via 4.x compat, GPU inference, teardown, M5 rehearsal
  (id-keying + idempotence on copies), rename semantics (offline-gated, id/port stable), no-think
  via the supported field+normalize path, system-info real GPU (Strix Halo 116GB).
- **Fixed forward from box findings (all on descar):** O1 slot-list degradation `c1ea4519` ·
  O2 CLI auth `c1ea4519` · O3 shim readiness `fc4f6d8d` · O6 lock-file perms rows + O7 legacy
  static-unit cleanup `78c6dd1c` · O8 quadlet 4.x compat `5adf6e0f` · **O9 SECURITY: bundle leaked
  HAL0_ADMIN/CLIENT_KEY (bare `_KEY` suffix missing from _SENSITIVE_RE) `64c956df`**.
- **Open (low): O10** — deprecated `[server].extra_args` UX trap: bare double-quoted JSON is eaten
  by (correct) shlex semantics; current code renders quoted input correctly (repro-verified).
  Candidate: store-time validation warning for JSON-looking tokens.
- **Deploy rows:** podman-5 template refresh (R5); hermes-provision lane gains: AppArmor
  containers.conf preflight (unconfined LXC), stale agent drop-in cleanup (241/CONFIGURATION_
  DIRECTORY class), `bootstrap --repair` correctness (O3 ownership drift).
- **halo143 second-box findings (2026-07-18 late):** O1 degrade fix + doctor-auth proven in
  anger on a second substrate; probe-cache poisoning fixed `86589fd1`; root-owned rootless-podman
  HOME dirs fixed via perms rows `9e07c0d3`; **version gate proven bidirectionally** (150 =
  compat branch, 143 = native branch). OPEN: (a) **rootful/rootless image-store split** —
  system-info probes hal0's rootless store while slots pull rootful → `installable` lie; probe
  the store slots use (via seam) — needs a row/lane; (b) **native AutoRemove on unprivileged
  podman-5-in-LXC suspected in slot start failure + netns teardown race** — under live diagnosis;
  likely resolution: stop emitting AutoRemove entirely. **STANDING POLICY: deploy-affecting
  lanes validate on BOTH 150 (4.9.3/privileged) and 143 (5.7/unprivileged).**

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
