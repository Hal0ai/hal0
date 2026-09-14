# ODS → hal0 issue map

**Scope.** All 106 open hal0 issues (snapshot 2026-09-05) triaged against the ODS
reference tree. Every "Direct port" / "Pattern adoption" row below was verified by
opening the cited ODS code *and* the cited hal0 code; "Informs only" rows were
verified on the ODS side and spot-checked on the hal0 side; "Unrelated" rows were
checked only far enough to establish that no ODS shape applies.

**Path conventions.** ODS paths are relative to `/home/user/ods/ods/` unless they
start with `.github/` (those are `/home/user/ods/.github/`). hal0 paths are relative
to `/home/user/hal0/`.

**Verdict definitions.**

| verdict | means |
|---|---|
| **Direct port** | An ODS file/function transliterates into hal0 with only naming/runtime substitution and closes the issue outright. |
| **Pattern adoption** | ODS's *structure* solves the issue; the code must be rewritten for hal0's runtime (podman/quadlet/FastAPI) but the design decision is settled. |
| **Informs only** | ODS's handling is a useful reference or precedent — it narrows the design space but does not itself close the issue. |
| **Unrelated** | No ODS shape applies (polish sweeps, test hygiene, upstream tracking, docs, hal0-only seams). |

---

## 1. Summary

| verdict | count | share |
|---|---:|---:|
| Direct port | 4 | 3.8% |
| Pattern adoption | 34 | 32.1% |
| Informs only | 29 | 27.4% |
| Unrelated | 39 | 36.8% |
| **total** | **106** | |

The honest read: **just over a third of the backlog is untouchable from ODS** — 31
polish/test/docs/upstream issues plus 8 that ODS simply does not model (Hindsight
memory internals, the runner image build, a11y). But the *high-severity operational*
half of the backlog maps very well: 18 of the 20 issues I'd call "an operator hits
this on a real box" land in Direct port or Pattern adoption.

---

## 2. Full triage table

| # | title (short) | verdict | ODS shape (file:line) | what to do in hal0 | eff | imp |
|---|---|---|---|---|---|---|
| 1974 | _flm_image_present caches False forever | Direct port | `extensions/services/dashboard-api/helpers.py:26-56 (_DirSizeCache TTL)` | Give the negative probe a TTL (or cache only the positive result) | S | M |
| 1869 | bound systemd/podman subprocesses | Direct port | `ods-uninstall.sh:321; scripts/bootstrap-upgrade.sh:939; ods-cli:2166-2172` | Pass an explicit timeout= on every seam call in the load path (container.py:2697/2708/2900) | S | H |
| 1521 | retention prune runs only at boot | Direct port | `memory-shepherd/install.sh:150-226 (timer+service pairs); memory-shepherd.sh:329` | Run prune on an interval task/systemd timer instead of once in the API lifespan | S | M |
| 1512 | stacks import never verifies checksum | Direct port | `ods-restore.sh:273-310` | Verify the envelope digest on the commit path; fail closed; keep --skip-verify as the explicit opt-out | S | H |
| 2234 | default_images[family] only on llama path | Pattern adoption | `installers/lib/compose-images.sh:40-71` | Resolve every slot image through one plan-derived seam so no provider path can bypass default_images[family] | M | M |
| 2221 | last_crash_line None on exit 64 | Pattern adoption | `installers/lib/compose-failure-report.sh:92-186; ods-cli:1080-1128` | Replace the single crash-line regex with a bounded evidence report: exit code + last N unit-log lines + likely image | M | M |
| 2216 | LXC /dev/kfd seeds gpu-vulkan | Pattern adoption | `installers/lib/detection.sh:187-233; installers/phases/02-detection.sh:159-168` | Make device-node presence (kfd_present()) sufficient for the ROCm lane; never gate a lane on a userspace tool being installed | S | H |
| 2212 | verify-files / re-pull for model + mmproj | Pattern adoption | `ods-restore.sh:273-310; config/model-library.json:5-14 (gguf_sha256)` | Carry per-file sha256 in the registry row and verify/re-pull on demand for model + mmproj | M | M |
| 2203 | seed-profile route emits changed_fields always | Pattern adoption | `ods-cli:6261-6324 (_template_preview delta)` | Compute the real delta once and emit changed_fields only when it is non-empty | S | M |
| 2201 | runs_on reads hardware per row per poll | Pattern adoption | `extensions/services/dashboard-api/helpers.py:26-56, 585-619` | Resolve hardware once per request from a TTL-cached snapshot written by a poller, not per row | S | M |
| 2195 | apply preview omits removed flags | Pattern adoption | `ods-cli:6261-6324` | Enumerate the complete delta (added/changed/removed) from one diff function shared with apply | S | M |
| 2192 | Omni never sees tts/stt/embed modality | Pattern adoption | `extensions/services/comfyui/manifest.yaml:19,38; extensions/services/dashboard-api/config.py:297-431` | Declare modality/capability facts in the slot profile manifest and derive Omni eligibility from that table | M | H |
| 2180 | migrate-flags aborts whole run | Pattern adoption | `ods-cli:2059-2113 (per-service accounting, continue then report)` | Make the sweep fault-isolated per slot and report a summary instead of aborting the run | S | M |
| 2164 | pi install fails as daemon uid 996 | Pattern adoption | `installers/phases/07-devtools.sh:66-73, 386-395` | Give the daemon identity its own npm prefix ($HOME/.npm-global) or run the install as the target user with HOME set | M | M |
| 2108 | tool_model has no UI path | Pattern adoption | `extensions/services/dashboard-api/settings.py:194-241; .env.schema.json` | Render settings fields from the config schema so a new key gets a UI path automatically | M | M |
| 2096 | update should restart slots on image roll | Pattern adoption | `ods-cli:2049 (up -d --force-recreate); ods-cli:1601 (--pull never)` | Make recreate-on-image-roll the default and require an explicit opt-out | S | H |
| 2028 | services/health static three-branch | Pattern adoption | `extensions/services/dashboard-api/config.py:297-431; helpers.py:624-700` | Build the health list from a manifest table and fan out with gather(return_exceptions=True) | M | H |
| 2019 | dev-deploy leaves wrappers stale | Pattern adoption | `installers/phases/13-summary.sh:283; ods-cli:2049` | Have deploy.sh run the same wrapper/PATH refresh step `hal0 update` runs (refresh_privileged_wrappers) | S | H |
| 1990 | _VALID_FACT_TYPES hardcoded allowlist | Pattern adoption | `extensions/schema/service-manifest.v1.json; scripts/validate-manifest-schema.sh` | Move the fact-type set into a shared schema both sides validate against, with a CI drift check | M | M |
| 1983 | gpu-perms Before=hal0.target loses race | Pattern adoption | `docker-compose.base.yml:463-465 (depends_on: condition: service_healthy)` | Add an explicit per-unit ordering edge on the slot template (After=/Requires=), not a target-level Before= | S | H |
| 1966 | ComfyUI has no lane on kfd-less AMD | Pattern adoption | `extensions/services/comfyui/manifest.yaml:19,38 (gpu_backends)` | Declare each runtime's supported lanes in the manifest; the picker offers only those and explains the gap | M | H |
| 1936 | segfault with zero GPU devices | Pattern adoption | `installers/lib/detection.sh:187-233, 235-257 (apply_cpu_gpu_fallback)` | Refuse to spawn a GPU lane when no device nodes are mapped; fall back to CPU with a named reason | S | H |
| 1870 | lifecycle verbs wait 966s silently | Pattern adoption | `installers/phases/12-health.sh:66-106; installers/lib/background-tasks.sh:119-148; readiness-summary.sh:52-127` | Stream per-phase progress while waiting and bound the wedged call with an elapsed/deadline loop | M | H |
| 1868 | static seeds warm brain at 65536 | Pattern adoption | `installers/lib/tier-map.sh:200-330; scripts/select-model.py:170-182` | Derive seed context_size from the detected memory envelope, exactly as derive_device already derives the lane | M | H |
| 1867 | anchor window below Hermes floor | Pattern adoption | `installers/phases/11-services.sh:1059 (_hermes_context=${MAX_CONTEXT})` | Derive the agent window from the slot's served window and raise the ceiling at provision time | M | H |
| 1862 | stale hardware.json books VRAM as RAM | Pattern adoption | `installers/lib/constants.sh:30; installers/lib/detection.sh:26-46; scripts/ods-doctor.sh:81-85` | Rebuild the hardware fact from a live probe when the cache is absent/stale instead of degrading to 'no GPU' | M | H |
| 1859 | llama-only ctx resolver on FLM/Kokoro | Pattern adoption | `extensions/schema/service-manifest.v1.json; scripts/load-backend-contract.sh` | Gate the context resolver on the slot's declared runtime family | S | M |
| 1845 | remediation panel cannot work | Pattern adoption | `scripts/ods-doctor.sh:1110-1163 (issue id -> next_steps table)` | Emit runnable next_steps as data from the server, including --stop-services | S | H |
| 1844 | update never refreshes /usr/local/bin/hal0 | Pattern adoption | `installers/phases/13-summary.sh:270-300 (ln -sf into the install dir)` | Re-assert the PATH symlink from the activated release next to refresh_privileged_wrappers | S | H |
| 1834 | extraction queue never drains on CPU | Pattern adoption | `installers/lib/tier-map.sh:200-330; ods-cli:400-480 (ensure_llama_cpu_budget)` | Size background extraction from the detected tier: concurrency cap, max_tokens, and a retry ladder floor | M | H |
| 1833 | memory status green on empty store | Pattern adoption | `installers/lib/readiness-summary.sh:47-96 (ready requires a real signal)` | Require a positive landed-fact signal for 'Writes landing', not absence of failures | S | M |
| 1825 | build_roster collapses model.gguf models | Pattern adoption | `config/model-library.json:5-14 (id + gguf_sha256); scripts/select-model.py:76-115` | Canonicalise the roster by content digest / registry id, never by gguf basename | S | M |
| 1823 | capability labels in hand-curated web file | Pattern adoption | `config/model-library.json:1-40; scripts/select-model.py:76-115` | Move capability labels into the versioned model registry and have the leaderboard read the registry | M | M |
| 1822 | LAN bind with auth off unsurfaced | Pattern adoption | `scripts/ods-doctor.sh:1074-1089; .env.example:50-70; installers/phases/13-summary.sh:419-427` | Add a doctor finding + install-summary line when the bind is non-loopback and auth is off | S | H |
| 1820 | netavark black hole has no repair owner | Pattern adoption | `ods-cli:975-1060 (detect known-recoverable failure, repair, bounded retry); ods-cli:6115-6204 (cmd_repair)` | Call the existing prune_dnat seam from the reconciler behind a bounded retry, and expose a repair verb | M | H |
| 1550 | deploy reports success on old code | Pattern adoption | `ods-update.sh:378-406 (wait_for_healthy); 647-742 (verify, then record version)` | Assert the served build identity after restart before printing success; roll back on failure | S | H |
| 1519 | uninstall prints 'Uninstalled' on 207 | Pattern adoption | `ods-uninstall.sh:362, 394-402 (INSTALL_DIR_CLEANED + residual remedy)` | Track per-step outcomes, gate the final line on them, and print residual paths with the remedy command | S | M |
| 1511 | stack apply unloads untouched slots | Pattern adoption | `ods-cli:6261-6324 (_template_preview: already / will-enable / nothing to change)` | Show the full delta including the unloads in the confirm dialog; make the unload sweep opt-in | M | H |
| 2200 | comfyui self-heal on list not single GET | Informs only | `extensions/services/dashboard-api/helpers.py:624-680` | Share one enrichment/self-heal function between the list and single-GET routes | S | M |
| 2191 | generate_image self-deadlocks single GPU | Informs only | `installers/lib/model-lifecycle-lock.sh:15-60` | Serialize GPU-exclusive ops behind a named lock with a bounded wait instead of a 503 mid-loop | M | H |
| 2190 | _runtime_family ignores runner aliases | Informs only | `lib/service-registry.sh:29-31 (SERVICE_ALIASES)` | Fold runner aliases in one registry seam every classifier consults | S | M |
| 2184 | curated slots reference demoted profiles | Informs only | `extensions/schema/service-manifest.v1.json; scripts/validate-manifests.sh` | Add a CI validator asserting every curated slot reference resolves to a shipped seed profile | S | M |
| 2178 | backfill Model.architecture | Informs only | `ods-update.sh:678-690 (migrations dir); config/model-library.json` | Ship an idempotent data migration executed by the updater's migration step | S | M |
| 2154 | bank hygiene surface | Informs only | `memory-shepherd/memory-shepherd.sh:110-148,329; memory-shepherd/install.sh:150-226` | Age/size thresholds + a retention sweep on a timer, with a guarded 'suspiciously small → skip' rule | M | M |
| 2118 | retire upstream runner-image variant | Informs only | `installers/lib/compose-images.sh:40-71` | Derive the image set from the resolved plan so retiring a variant is a one-line data change | S | L |
| 2101 | GA delivery checklist | Informs only | `ods-update.sh:456-505` | Resolve 'latest' from the release API itself so tagging IS delivery — no separate pointer to forget | M | H |
| 1969 | nothing pins llama-server LOADING | Informs only | `extensions/services/dashboard-api/helpers.py:661-672 (degraded != down)` | Model 'up but loading' as a distinct state and never feed it to the output-sanity gate | S | M |
| 1967 | save_slot_config drops keys on round-trip | Informs only | `extensions/services/dashboard-api/settings.py:194-241` | Round-trip through a schema-derived field table so unknown keys survive the write | M | M |
| 1947 | support CIRU vLLM distributions | Informs only | `extensions/services/*/manifest.yaml; docker-compose.*.yml overlays` | Add a runtime family as manifest + overlay data rather than code | L | M |
| 1932 | read_slot_ceiling assumes .toml | Informs only | `lib/service-registry.sh:29-48` | Resolve the ceiling through the naming seam rather than assuming <name>.toml | S | M |
| 1931 | extraction preflight trusts config | Informs only | `extensions/services/dashboard-api/helpers.py:624-680` | Preflight the LIVE drop-in/endpoint, not the configured intent | S | M |
| 1930 | MCP memory_add has no ctx preflight | Informs only | `extensions/services/dashboard-api/helpers.py:624-680` | Route both HTTP and MCP add paths through one preflight helper | S | M |
| 1929 | memory-map GTT fallback reads dead keys | Informs only | `installers/lib/llama-memory-budget.sh:6-31` | Same single-source memory envelope; fail loudly rather than silently substituting system RAM | S | M |
| 1928 | memory ruler header/bar different bases | Informs only | `installers/lib/llama-memory-budget.sh:18-31` | Derive header and bar from ONE effective-memory function so they cannot disagree | S | M |
| 1873 | self-call clients bare-float timeouts | Informs only | `ods-cli:3851-3880 (explicit per-call timeout argument)` | Import slot_lifecycle_budget in the brain self-call clients instead of bare floats | S | M |
| 1858 | by-name/by-id unenriched payload | Informs only | `extensions/services/dashboard-api/helpers.py:624-700` | Share one enrichment function across /api/slots, /by-name and /by-id | S | M |
| 1545 | every PR conflicts on CHANGELOG.md | Informs only | `.github/pull_request_template.md; CHANGELOG.md:1-30` | ODS has no fragments — but its PR template requires no CHANGELOG edit; notes are authored at cut time | M | M |
| 1537 | Ubuntu 26.04 py3.14 --ignore-requires-python | Informs only | `installers/lib/python-runtime.sh:36-72,127-171` | Provision/select a supported interpreter and refuse loudly with a named remedy instead of --ignore-requires-python | M | M |
| 1530 | stable channel pointer still 0.9.8 | Informs only | `ods-update.sh:456-505` | Same as #2101 — derive the update target from the release API rather than a hand-maintained channel pointer | S | H |
| 1522 | journald follow has no keep-alive | Informs only | `extensions/services/dashboard-api/routers/talk.py:393-397,455-495` | Wrap journalctl_sse in the keepalive + disconnect loop hal0 already ships in journal.py / slots/logs.py | S | M |
| 1436 | artefact names still derived from slot NAME | Informs only | `lib/service-registry.sh:29-48` | Route the four remaining artefact names through the naming seam | S | M |
| 1429 | quadlets hard-code LogDriver=none | Informs only | `docker-compose.base.yml:12-16 (single logging anchor + rotation)` | Already switched to LogDriver=passthrough (container.py:890); adopt one log-policy constant plus rotation | S | M |
| 1428 | slot_sample UNIQUE constraint floods log | Informs only | `extensions/services/token-spy/db.py:37 (surrogate PK, no natural-key UNIQUE)` | Drop the (ts, slot_id) natural key for a surrogate PK, or use INSERT ... ON CONFLICT DO UPDATE | S | M |
| 1426 | hal0-systemctl has no unmask verb | Informs only | `ods-cli:6115-6204 (cmd_repair dispatcher)` | Add unmask to _UNIT_VERBS + the wrapper allow-list and expose it as a repair verb | S | M |
| 1422 | /api/slots duplicate entries | Informs only | `lib/service-registry.sh:29-48 (id-keyed maps + alias table)` | Key the roster by durable id with names as an alias map, and dedupe at load | M | M |
| 1421 | POST /api/slots writes name-keyed | Informs only | `lib/service-registry.sh:29-48` | Route slot creation through the naming seam so an id-keyed box never gets a name-keyed artefact | M | H |
| 1319 | integrate llama-ai / CachyLLama | Informs only | `installers/lib/tier-map.sh:200-330; docker-compose.*.yml overlays` | Add a runtime as a data-declared backend + compose/profile overlay rather than new code paths | L | L |
| 2228 | comfyui phase4 tests patch shared isfile | Unrelated | `—` | Inject the filesystem seam per provider instead of patching os.path.isfile process-wide | S | L |
| 2202 | promote crud app fixtures to conftest | Unrelated | `—` | Test-fixture consolidation | S | L |
| 2169 | cut PR wall clock | Unrelated | `—` | ODS CI has no path gating/xdist to copy (single lint+smoke workflows) | M | M |
| 2168 | execute v1.1.0 sunset backlog | Unrelated | `—` | Repo chore; no ODS analogue | M | L |
| 2155 | memory migrate unify HTTP 410 | Unrelated | `—` | Upstream engine API version pin; no ODS analogue | M | M |
| 2153 | guide for external coding agents | Unrelated | `—` | Documentation | M | L |
| 2111 | docs-sync fragment anchors | Unrelated | `—` | Docs-sync anchor rewriting | S | L |
| 2017 | memory plugin one uuid4 doc per turn | Unrelated | `—` | Hermes plugin metadata; no ODS analogue | M | M |
| 2016 | no document-ingest endpoint | Unrelated | `—` | ODS delegates RAG ingest to Open WebUI's own Knowledge surface — no endpoint to port | M | M |
| 2011 | cross-backend profile switching dead code | Unrelated | `—` | Dead-code removal | S | L |
| 2003 | docs-discourse-sync reconcile | Unrelated | `—` | Forum sync bookkeeping | M | L |
| 2002 | flaky warming-slot test | Unrelated | `—` | CI-only state race; fix the test's readiness barrier | S | L |
| 1997 | no e2e for slab-truncation notice | Unrelated | `—` | Missing e2e coverage | S | L |
| 1996 | tag-chip dimming keys dead field | Unrelated | `—` | UI field-name fix | S | L |
| 1995 | memory v2 mock graph dialect | Unrelated | `—` | Test mock dialect fix | S | L |
| 1994 | no toast for queued agent delete | Unrelated | `—` | UI toast | S | L |
| 1993 | no operation-retry affordance | Unrelated | `—` | UI retry affordance | S | L |
| 1989 | list_memories comma-joins multi-type | Unrelated | `—` | Client query encoding bug | S | M |
| 1984 | _vulkan_lane_is_loadable stale pin | Unrelated | `—` | Cache/pin invalidation bug internal to the retag pass | S | M |
| 1948 | restore Vulkan backend / refresh upstreams | Unrelated | `—` | Runner image build work | L | H |
| 1925 | A/B non-AMD Vulkan lanes | Unrelated | `—` | Validation campaign | L | M |
| 1841 | polish(ui) rc.5 sweep | Unrelated | `—` | UI polish sweep | S | L |
| 1840 | polish(cli/api) rc.5 sweep | Unrelated | `—` | 13-item CLI/API polish sweep | M | L |
| 1829 | board tools 401 on fresh install | Unrelated | `—` | Upstream wheel packaging gap | M | H |
| 1821 | nothing asserts slot-owned flag reaches argv | Unrelated | `—` | Test coverage for argv rendering | M | M |
| 1783 | converge dashboard tables on .dtable | Unrelated | `—` | UI table convergence | M | L |
| 1756 | quadlet allow-list follow-ups | Unrelated | `—` | hal0-specific sudo wrapper allow-list; ODS has no equivalent seam | S | M |
| 1552 | Menu primitive has no keyboard support | Unrelated | `—` | Frontend a11y work | M | M |
| 1536 | polish(board/palette) GA sweep | Unrelated | `—` | Polish sweep | S | L |
| 1529 | polish(journal/logs) GA sweep | Unrelated | `—` | Polish sweep | S | L |
| 1528 | polish(agent-cli) GA sweep | Unrelated | `—` | CLI polish sweep | S | L |
| 1525 | polish(upstreams) GA sweep | Unrelated | `—` | Polish sweep | S | L |
| 1524 | polish(stacks) GA sweep | Unrelated | `—` | Polish sweep | S | L |
| 1502 | scar ratchet counts prose | Unrelated | `—` | Repo CI ratchet tuning | S | L |
| 1477 | low-priority polish sweep | Unrelated | `—` | 31-item polish sweep | L | L |
| 1445 | typed request bodies audit | Unrelated | `—` | Typed-body audit follow-through | M | M |
| 1349 | Strix Halo field results | Unrelated | `—` | Field-report tracking issue | L | L |
| 1249 | track hermes-agent py3.14 | Unrelated | `—` | Upstream tracking issue | S | M |
| 1070 | validate/promote ROCmFPX variant | Unrelated | `—` | Quantization validation work | L | M |

---

## 3. Top 15 wins

Ranked by (impact ÷ effort). Each is an ODS shape I opened and a hal0 defect I
located; the pointers are exact.

### 1. #1869 — bound the systemd/podman lifecycle subprocesses  *(S / H, Direct port)*

`ContainerProvider._run` (`src/hal0/providers/container.py:2549-2566`) defaults
`timeout=None`, and the three calls on the load path pass nothing:
`container.py:2697` (`daemon-reload`), `container.py:2708` (`systemctl restart <unit>`),
`container.py:2900` (`daemon-reload` in the re-render sweep). `SystemCtlSeam.systemctl`
(`src/hal0/system/seam.py:415-489`) forwards the bound faithfully on both routes — the
plumbing already exists, nobody passes a number.

ODS never leaves a hang-prone privileged call unbounded:
`ods-uninstall.sh:321` — `timeout 20s sudo -n -- systemctl disable --now …`;
`scripts/bootstrap-upgrade.sh:939` — `ps_env_cmd+=(timeout --foreground --kill-after=5s "${restart_timeout}s")`;
`ods-cli:2166-2172` — a `perl -e 'alarm 3; exec "docker","info"'` guard, with the
comment explaining that `docker info` hangs 20+s on a half-booted daemon.

Fix: give `_run` a non-`None` default (the existing `_UNIT_STOP_TIMEOUT_S` is already
imported at `container.py:2943`) and let `subprocess.TimeoutExpired` become a typed
`slot.spawn_failed` the same way `CalledProcessError` already does at `container.py:2709-2716`.
That also shrinks the client budget in `slot_lifecycle_budget.py` — see #1870.

### 2. #1822 — LAN bind with auth off is never surfaced  *(S / H)*

ODS surfaces this in three places, all data-driven:
`scripts/ods-doctor.sh:1074-1089` raises a `warn` finding
(`ODS-RUNTIME-EXTERNAL-LEMONADE-UNAUTHENTICATED-HOST-ROUTE`) whose detail says exactly
"if the daemon is bound beyond loopback so Docker can reach it, that same daemon may
also be reachable from the LAN"; `scripts/ods-doctor.sh:1150-1155` carries its
`next_steps`; `installers/phases/13-summary.sh:419-427` prints the LAN URL only when
`BIND_ADDRESS=0.0.0.0`, and `.env.example:50-58` states the danger inline on the
`OPENCLAW_DANGEROUSLY_DISABLE_DEVICE_AUTH` key.

Fix: add one doctor check (`src/hal0/cli/doctor_all.py`, alongside
`check_hermes_anchor_window` at `doctor_all.py:1046`) that reads the bind address and
the auth toggle together and emits a `warn` with a remedy; echo the same line in the
installer summary.

### 3. #1845 — the "Convergence incomplete" panel prints a remediation that cannot work  *(S / H)*

`src/hal0/cli/update_commands.py:466-492` renders `entry.get('command')` verbatim under
prose that says "Stop hal0, then run:" — but the command the server hands over does not
carry `--stop-services`, so a literal copy-paste refuses.

ODS keeps remediation as a **table keyed by a stable issue id**, never prose glued to a
value: `scripts/ods-doctor.sh:1110-1122` maps id → title, `:1124-1163` maps id →
`next_steps` (a list of runnable lines), and `:1165-1175` assembles the diagnosis.

Fix: have `/api/updates/convergence` return `next_steps: [str]` per pending migration
(built server-side, where `--stop-services` is known), and make the panel print exactly
those lines. Same change fixes the class, not just this instance.

### 4. #1844 — `hal0 update` never refreshes `/usr/local/bin/hal0`  *(S / H)*

`installer/install.sh:1077-1082` links `/usr/local/bin/hal0 → ${VENV_DIR}/bin/hal0` and
the comment claims it "survives upgrades because it points at the venv shim, not a copy" —
but `install.sh` is the only writer, and `hal0 update` never re-asserts it.

hal0 already solved the identical class for the sudo wrappers:
`src/hal0/updater/updater.py:3963-4096` `refresh_privileged_wrappers()`, called from
`updater.py:4159-4163`. ODS's equivalent is `installers/phases/13-summary.sh:270-300`,
which `ln -sf`s the CLI into the install dir on every install run and falls back to
`~/.local/bin` with a PATH warning when sudo is unavailable.

Fix: add `refresh_path_links(target)` next to `refresh_privileged_wrappers` and call it
from the same activation step. ~30 lines.

### 5. #1550 — `deploy.sh` reports success while the backend serves old code  *(S / H)*

`scripts/deploy.sh:213-231` polls `/api/status` for HTTP 200 and then prints
`deploy complete @ <sha>` — the sha comes from `git rev-parse`, i.e. from the *tree*,
never from the *process*.

ODS gates the success line on a health verdict and rolls back otherwise:
`ods-update.sh:378-406` `wait_for_healthy()` (deadline loop, per-attempt remaining-time
log, dumps the final failing status), `ods-update.sh:717-724` calls
`_update_rollback` when it fails, and only `:725-740` records the new version.
`ods-cli:2059-2113` does the per-service variant: enumerate the stack, check each
container's `State.Status`/`ExitCode`, and `exit 1` with "Run 'ods rollback'".

Fix: expose the running build's version/commit on `/api/status` (or read
`hal0.__version__` from the live process) and compare it to `git rev-parse HEAD` before
printing success.

### 6. #2216 — LXC with `/dev/kfd` forwarded seeds every slot `gpu-vulkan`  *(S / H)*

`src/hal0/install/profile_derive.py:110-113`:

```
rocm_ok = any(g.compute_capable for g in hw.gpus) or kfd_present()
if rocm_ok and (hw.platform == "strix-halo" or any(g.compute_capable for g in hw.gpus)):
    return "gpu-rocm"
```

`kfd_present()` can satisfy the first clause but never the second, and
`compute_capable` is set from a `rocm-smi`/`rocminfo` exit code
(`src/hal0/hardware/probe.py:483-484`). So a container with `/dev/kfd` forwarded but no
ROCm userspace installed falls through to Vulkan.

ODS decides the AMD lane from **device nodes only** —
`installers/lib/detection.sh:187-210` (`amd_gpu_missing_runtime_devices` checks
`/dev/kfd` is a char device and `/dev/dri/renderD*` exists) and
`installers/phases/02-detection.sh:159-168` gates the whole lane on that, with
`show_amd_gpu_device_guidance` (`detection.sh:218-233`) printing the LXC passthrough
commands when it fails.

Fix: make `kfd_present()` sufficient for the ROCm branch. hal0's `kfd_present`
(`src/hal0/providers/_gpu.py:251-280`) is already *stricter* than ODS's — it checks
openability by the slot runner uid — so this is a one-line predicate change.

### 7. #2028 — `/api/services/health` is a static three-branch construction  *(M / H)*

`src/hal0/api/routes/services_health.py:198-307` hand-writes comfyui, hermes and
openwebui as three literal blocks; the docstring at `:1-15` admits the list is fixed.
The dashboard footer's readiness count therefore cannot move on a hindsight-api outage.

ODS's dashboard-api is manifest-driven end to end:
`extensions/services/dashboard-api/config.py:297-424` `load_extension_manifests()` reads
every `extensions/services/*/manifest.yaml` (schema-pinned to `ods.services.v1`) into
`SERVICES`/`FEATURES` at `config.py:430-448`; `helpers.py:624-679`
`check_service_health()` probes one service from its manifest port + `health` path with
per-service timeouts and a `healthy/degraded/down/not_deployed` verdict; and
`helpers.py:682-700` `get_all_services()` fans out with
`asyncio.gather(..., return_exceptions=True)` so one misbehaving service can't take the
response down.

Fix: add a `services.yaml`-style table (or extend the existing slot/profile manifests)
carrying `id, name, unit, probe_url, health_path, timeout`, and replace the three blocks
with one loop. New services then appear by adding a row.

### 8. #1868 — static slot seeds are copied verbatim with no hardware budget  *(M / H)*

`installer/etc-hal0/slots/brain.toml` ends with a literal `[model] context_size = 65536`,
copied unchanged onto every box by `src/hal0/install/static_seeds.py:195-260`. That
module *already* re-derives `device` per host through
`LLAMA_SEED_CAPABILITIES` (`static_seeds.py:74-82`) → `derive_device`; the context
window is the one field it doesn't touch.

ODS never lets a context window escape the hardware envelope:
`installers/lib/tier-map.sh:200-330` binds `MAX_CONTEXT` to the tier (which is derived
from detected VRAM/RAM), and `scripts/select-model.py:170-182`
(`estimated_context_kv_gb` / `selector_required_memory_gb`) charges the KV cache against
the model's memory requirement, with `usable_memory_gb` (`select-model.py:134-144`)
giving the unified-memory box only 55% of RAM.

Fix: extend `static_seeds.py`'s derivation pass with a `_CONTEXT_LINE` rewrite that
clamps `context_size` to a tier-derived ceiling — the same shape, the same file, one
more regex.

### 9. #1862 — a stale or missing `hardware.json` books VRAM as RAM  *(M / H)*

`src/hal0/slots/capacity.py:499-522` `_host_has_capable_gpu()` reads the cached
`/etc/hal0/hardware.json` and returns `False` on *any* error, and
`build_per_slot` (`capacity.py:570-585`) uses that bool to decide whether resident
memory is charged to VRAM or RAM.

ODS has no durable hardware cache to go stale. `CAPABILITY_PROFILE_FILE` defaults to
`${TMPDIR:-/tmp}/ods-capabilities.json` (`installers/lib/constants.sh:30`), and it is
**rebuilt on every run** by `scripts/build-capability-profile.sh` — from the installer
(`installers/lib/detection.sh:26-46`) and from the doctor
(`scripts/ods-doctor.sh:81-85`).

Fix: when the cache is missing or older than N hours, fall through to
`hal0.hardware.probe`'s live light probe rather than to `False`. hal0 already has that
probe; only the fallback branch is wrong.

### 10. #1936 + #1966 — no GPU devices / no CPU lane  *(S–M / H)*

`#1936` is a segfault at model load when the container has zero GPU devices mapped;
`#1966` is ComfyUI offering only `gpu-vulkan` on a kfd-less AMD box.

Two ODS shapes cover both. `installers/lib/detection.sh:235-257`
`apply_cpu_gpu_fallback()` is a single function that rewrites the *whole* hardware
verdict (`GPU_BACKEND=cpu`, `GPU_VRAM=0`, `HAS_NPU=false`, overlays cleared) and prints
the reason; it is invoked from four guard sites
(`installers/phases/02-detection.sh:132,168,181`, `11-services.sh:311`). And
`extensions/services/comfyui/manifest.yaml:19` declares `gpu_backends: [amd, nvidia]`,
repeated on the feature at `:38` — so the dashboard can only offer a lane the service
declares.

Fix: (a) refuse to spawn a GPU-lane container when the device nodes are absent, with a
named reason instead of a SIGSEGV; (b) put `supported_backends` on the ComfyUI runner
in the profile manifest and have the device picker render "requires /dev/kfd" instead of
an unusable row.

### 11. #1870 — lifecycle verbs wait 966s with no progress  *(M / H)*

`src/hal0/cli/slot_commands.py:51-54` derives the client budget from
`slot_lifecycle_budget.slot_lifecycle_timeout_s()` — correct, but the CLI then blocks on
one `api_post` for the whole window with nothing on stdout.

ODS's two halves: `installers/phases/12-health.sh:66-106` `_check_container_health()`
prints `... Waiting for <name>` and rewrites the line in place with `\r` each poll,
short-circuiting on `exited|dead|missing`; and `installers/lib/background-tasks.sh:119-148`
`bg_task_wait()` is the bounded elapsed/timeout loop with three distinct exits
(ok / failed / timeout). `installers/lib/readiness-summary.sh:52-127` is the closing
report: `Ready now: N/total`, then `Ready:` / `Needs attention:` / `Next:` with the log
paths.

Fix: stream the server's phase transitions (the state machine already stamps them) over
the existing SSE seam and render a per-phase progress line; keep the derived budget as
the hard deadline.

### 12. #2096 — update should restart slots when the runner image rolls  *(S / H)*

hal0 makes this opt-in: `src/hal0/cli/update_commands.py:516-531` explicitly says "we
never bounce automatically" and tells the operator to run `hal0 update --restart-slots`.

ODS makes recreate the default and *pinning* the opt-out: `ods-cli:2049` runs
`up -d --force-recreate` on every update, while `ods-cli:1601` uses
`--force-recreate --no-build --pull never` for a plain restart so a restart can never
silently change the image. The image set itself is derived from the resolved plan, not
per-code-path (`installers/lib/compose-images.sh:40-71`).

Fix: invert the default when — and only when — `/api/updates/slot-drift` reports an
image change; keep `--no-restart-slots` for the operator who wants the old behaviour.

### 13. #1512 — stack import commit never verifies the envelope checksum  *(S / H, Direct port)*

`ods-restore.sh:273-310` is nearly transliterable: it looks for `checksums.sha256`,
picks `sha256sum -c` or `shasum -a 256 -c`, **returns 1 on mismatch** with
"This backup may be corrupted or tampered with", warns loudly when the file is absent
(older format), and only skips when `--skip-verify` was passed explicitly (`:308-310`).
It also warns when the archive is missing expected data paths (`:313-320`).

Fix: move the digest check hal0 currently runs only in `dry_run` onto the commit path,
fail closed, and add an explicit `--skip-verify`.

### 14. #1974 — `_flm_image_present` caches `False` and never re-probes  *(S / M, Direct port)*

`src/hal0/capabilities/catalog.py:178-235` caches the probe at module scope and clears it
only after a successful FLM pull (`reset_flm_image_present_cache`) — so a transiently
broken podman permanently drops the NPU backend from `/api/capabilities`.

ODS's `_DirSizeCache` (`extensions/services/dashboard-api/helpers.py:26-56`) is the
30-line answer: per-key TTL, expiry sweep on write, bounded size, explicit `invalidate`.

Fix: cache the positive result indefinitely and the negative one for ~60s, or don't
cache the "no runtime / probe failed" branch at all — it is a different answer from
"image absent".

### 15. #1820 — the netavark port black hole has no repair owner  *(M / H)*

hal0 has both halves already and no wiring: detection in
`src/hal0/system/netavark.py:58-170` (parses `nft -a list table inet netavark`) and the
privileged repair in `src/hal0/system/seam.py:363-413` `prune_dnat()` (with a
`dry_run`/`check-dnat` mode). The reconciler parks the slot in ERROR instead of calling it.

ODS's `_compose_run_with_summary` (`ods-cli:960-1140`) is the template for
"known-recoverable failure → repair → bounded retry": it classifies the compose log
(`ods-cli:1006` network errors, `:1021` port-already-allocated, `:1042` stale container
name conflict), performs the matching repair (`_compose_remove_conflicting_ods_containers`,
`ods-cli:978-996` — which *refuses* to remove a non-ODS container), and retries under
per-class attempt counters and backoff (`:996-1060`). `ods-cli:6115-6204` `cmd_repair`
is the operator-facing companion verb.

Fix: on the ERROR transition, run `prune_dnat(dry_run=True)`; if it identifies a stale
rule, prune and retry the start once, then park with the evidence.

---

## 4. Clusters — one change, many issues

### A. A progress / timeout / evidence seam
**Closes or de-risks: #1870, #1869, #1873, #2221, #1550, #1820, #2180.**

ODS treats "a long privileged call" as one shape with four obligations: a hard bound
(`ods-uninstall.sh:321`, `bootstrap-upgrade.sh:939`, `ods-cli:2166-2172`), visible
progress while waiting (`12-health.sh:66-106`, `background-tasks.sh:119-148`), a
classified retry for known-recoverable failures (`ods-cli:996-1060`), and a persisted
evidence report when it finally fails (`compose-failure-report.sh:92-186` — redacted
env, port occupancy, `compose ps`, log tail — plus `readiness-summary.sh:52-127`).

In hal0 that is one module: a `bounded_call()` helper every seam invocation goes through
(`container.py:2549`, `seam.py:415`), a phase-progress event on the existing SSE bus, a
retry classifier for the netavark/name-conflict/port classes, and a
`write_slot_failure_report()` that the crash-line extractor feeds instead of replacing.
Doing this once removes the need to hand-tune `slot_lifecycle_budget.py`'s
`EVICTION_UNLOAD_ALLOWANCE` caveat (`src/hal0/slot_lifecycle_budget.py:95-107` explicitly
names #1869 as the real fix).

### B. Manifest-driven service + capability registry
**Closes or de-risks: #2028, #2192, #1966, #1859, #2190, #2184, #1990, #1823, #2234, #1947, #1319.**

Every one of these is "a fact about a service/runtime is hard-coded in one code path and
the other paths can't see it." ODS answers all of them with one mechanism:
`extensions/services/<id>/manifest.yaml` validated by
`extensions/schema/service-manifest.v1.json`, loaded once by
`lib/service-registry.sh:29-48` (bash) and
`extensions/services/dashboard-api/config.py:297-431` (Python), and consumed by health
(`helpers.py:624-700`), features (`routers/features.py:19-113`) and the CLI. Backend
compatibility is a manifest field (`comfyui/manifest.yaml:19`); model capability is a
registry field (`config/model-library.json`); the image set is derived from the resolved
plan (`compose-images.sh:40-71`).

hal0 has the pieces scattered — `RUNNER_IMAGES`, seed profiles, `_SLOT_META`,
`services_health.py`'s three literals, `capabilities/catalog.py`. One
`hal0.services.registry` loaded from a schema-validated table, read by the health route,
the Omni eligibility check, the device picker and the context resolver, would retire the
whole cluster.

### C. Hardware re-detect + honest CPU fallback
**Closes or de-risks: #1862, #2216, #1936, #1966, #1868, #1834, #1867, #2201, #1928, #1929.**

ODS makes detection cheap and *always fresh* (tmp-file capability profile rebuilt every
run, `constants.sh:30` + `detection.sh:26-46` + `ods-doctor.sh:81-85`), decides GPU lanes
from device nodes rather than userspace tools (`detection.sh:187-210`), has exactly one
function that demotes a box to CPU with a stated reason
(`detection.sh:235-257`), and derives every memory/context number from the resulting
envelope (`tier-map.sh:200-330`, `select-model.py:134-182`,
`llama-memory-budget.sh:6-31`).

In hal0 this is: (1) live-probe fallback when `hardware.json` is stale/absent;
(2) `kfd_present()` sufficient for the ROCm lane; (3) one `apply_cpu_fallback()` that
rewrites the whole verdict; (4) one memory-envelope function that seed context sizes, the
memory ruler, the extraction sizing and the anchor-window floor all read.

### D. Update / deploy delivery integrity
**Closes or de-risks: #1844, #2019, #1550, #2096, #1845, #1519, #1512, #1530, #2101.**

ODS's update is a transaction: snapshot → change → **verify** → record version, with
auto-rollback at every step (`ods-update.sh:647-742`, `_update_rollback` at `:412-449`,
`wait_for_healthy` at `:378-406`), the installed artefacts re-asserted on every run
(`13-summary.sh:270-300`), verification of the *running* stack before success
(`ods-cli:2059-2113`), and "latest" resolved from the release API so tagging is delivery
(`ods-update.sh:456-505`). Uninstall tracks per-step residue and names the remedy
(`ods-uninstall.sh:362,394-402`).

hal0 already has the hardest parts (cosign, staged trees, atomic symlink swap,
`refresh_privileged_wrappers`). What's missing is the cheap discipline: re-assert the
PATH link, verify the *served* build, verify digests on commit, gate the success line on
per-step outcomes, and let `scripts/deploy.sh` share the update path's refresh steps.

### E. Schema-driven settings and previews
**Closes or de-risks: #2108, #1967, #2195, #2203, #1511, #1445.**

`extensions/services/dashboard-api/settings.py:194-241` `_build_env_fields()` renders a
UI field for **every** key in `.env.schema.json` — label, type, enum, secret flag,
read-only reason, default — and falls through to a generic string field for keys the
schema doesn't describe. A new config key gets a UI path by existing, not by someone
hand-writing a control. The apply side is a declared allow-list per service
(`settings.py:33-58`). The preview side is `ods-cli:6261-6324` `_template_preview`:
compute the delta, print "Already enabled / Will enable / Nothing to change", and keep
apply additive.

For hal0 that means one `ChangeSet` diff function shared by preview and apply (so
removals can't go unreported), and a schema-derived settings renderer so `tool_model`
and its siblings stop needing bespoke UI.

---

## 5. Where ODS has nothing to offer

Called out so nobody goes looking:

- **Memory / Hindsight internals** (#2016, #2017, #1989, #1990-partial, #1993-#1997, #2155): ODS's RAG is Qdrant + TEI behind Open WebUI's own Knowledge surface, and its "memory-shepherd" is a `MEMORY.md` file-reset tool on a systemd timer — a different problem entirely. Only the hygiene/retention shape transfers (#2154, #1521).
- **CI throughput** (#2169, #1502): ODS's workflows have no path gating, no concurrency groups and no test tiering (`/home/user/ods/.github/workflows/lint-python.yml:3-8`). hal0's CI is the more mature of the two.
- **Changelog fragments** (#1545): ODS uses a plain Keep-a-Changelog file too. The only transferable observation is that its PR template (`/home/user/ods/.github/pull_request_template.md`) has no changelog checkbox — notes are written at cut time, so PRs never touch the file.
- **Polish sweeps** (#1477, #1524-#1529, #1536, #1840, #1841, #1783, #1552): item-level UI/CLI work with no structural analogue.
- **Upstream tracking** (#1249, #1948, #1829, #2118, #1070, #1349, #1925): pins, builds and third-party bugs.
