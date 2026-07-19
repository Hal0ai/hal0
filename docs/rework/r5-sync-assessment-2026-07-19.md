# R5 sync assessment — how far every surface is from "back in sync" (2026-07-19)

_Prepared as the entry brief for the final major phases of the rework. Method: fresh
graphify knowledge graph over the whole tree (deep semantic extraction, 24,106 nodes /
48,174 edges / 1,067 communities, built from `462b28f6` — see `graphify-out/GRAPH_REPORT.md`),
then seven parallel surface surveys (backend↔frontend contract, API wiring, CLI,
installer/uninstaller, MCP, Hermes, UI/UX) cross-checked against the rework plan/board/specs,
with every critical/major claim independently adversarially re-verified against source
(all verdicts CONFIRMED except two CORRECTED; corrections folded in below). Evidence is
cited as `path:line`. Board rows referenced are `docs/rework/REWORK_BOARD.md`._

**Baseline:** R1–R4 are merged to main (`c91d0cf5` = rework-R4-stage). R5 (surface+launch)
is open. This document does NOT re-plan merged work; it maps the *distance from sync* per
surface and turns it into lanes.

**Baseline moved since this doc was first written (from the descar→main landing handoff,
`docs/rework/handoff-merge-followups-to-docs.md`, verified against `origin/rework/descar`
2026-07-19):** GitHub `main` has advanced past descar's branch base — `462b28f` + #1315
(HF update-check, merged) + **#1316 (UI-API-1 managed-arg gap CLOSED — screens on launch +
create + a new `POST /api/models/{id}/validate` route)**. On `rework/descar`, three lanes
this doc's scope digest lists as open are now **✔ done+CI-green**: P4-docs (`ecbdc6f6`),
P4-rules (`fa9085d0`, CONTRIBUTING now carries anti-scar rules 1–11), §21.11 golden-paths
(`fa9085d0` + `docs/rework/golden-paths-halo143-runbook.md`). Read the reconciliation notes
in **§10** before acting on §1–§9 — several items below are already closed on descar/main.

---

## 0. Executive summary — the distance, per surface

| Surface | Distance | Headline |
|---|---|---|
| Backend ↔ Frontend | **Close** | All ~35 frozen board routes, settings client, WS/SSE contracts verified in sync. Gaps: 1 shipped backend route the UI never adopted (model duplicate), 4 UI-declared routes with no backend, mock-layer rot, stale contract docs. |
| Backend internal wiring | **Mid** | P3-routers inc 3 is real and open: models.py 1147 vs DoD ≤550, slots.py 1328 vs ≤800; 39 `request.json()` sites vs DoD 0 (24 of them owned by NO lane); MCP route-map autogen not started; 5 importer flips + `_probe_power` never extracted. |
| CLI | **Mid** | Structurally healthy thin-HTTP client, but: 4 call sites bypass auth (break on auth-on boxes), zero verbs for the R3/R4 surface (rename, ports, model default/duplicate/update, board, brain, auth), docs drift, and a deletion backlog gated on P2-memory/P2-config/FLAGS-own. |
| Installer | **Close, one crater** | Post-R4-correct overall, but **no `hal0.target` unit exists anywhere** while every quadlet says `WantedBy=hal0.target` → no slot autostart after reboot. Model store born `root:root` with no PermRow. |
| Uninstaller | **Behind by a generation** | Predates R3/R4: never touches `/etc/containers/systemd` (quadlet sources survive), leaves the `hal0-podman-ro` sudoers grant, `--purge` bench-store removal documented but unimplemented, honcho/hermes shim gaps. |
| MCP tools | **Furthest behind** | Admin catalog (72 REST entries) is a full generation behind the API (no rename/by-id/default/duplicate/board/brain/system-info); `upstream_update` is hard-broken (PATCH unsupported); **raw admin key logged to journald as `client_id`**; Hermes/brain MCP wiring sends no credential (401 with auth on); memory plugin's §7.4 hindsight rename not started. |
| Hermes | **Close** | Shape/security/testing solid. Remaining: brain-lane relocation (5 markers), two live-validation re-runs (Phase 5 liveness, Phase 6 executor), drift-watch blind spot on the new kanban surfaces, P2-memory migration, bump runbook. |
| UI/UX | **Close** | Dual-root resolved, D1–D6 all shipped (board row stale). Real risks: prod mock fallback masks API outages and can fake non-GET success; UI-API-1 backend affordances keep shipped surfaces degraded; 47 window-global modules and 4 CSS eras remain. |
| Cross-repo (runner-images) | **One repin behind** | `ghcr.io/hal0ai/hal0-comfyui` published 2026-07-19 replaces `docker.io/kyuz0`, but manifest.json + RUNNER_IMAGES still pin the third-party image; cpu-runner lineage disagrees between repos. |

**The three launch-blocking correctness items found:**
1. `hal0.target` missing → slots don't come back after reboot (§6.1).
2. Raw admin key stamped into journald on every MCP tool call (§4.4).
3. Prod mock fallback can render fake data / fake mutation success when the API is down (§2.1).

---

## 1. UI/UX — improvements, wiring, changes

### 1.1 Correctness
- **Gate the mock fallback out of production** (major/M). `mockFetch`'s 404/network
  substitution is live in prod builds; legacy allowlist rows ignore HTTP method, so a
  network-erroring POST can return a 200 fixture body — a create-slot that "succeeded"
  against thin air. Fix: `method === 'GET'` for ALL rows (one line at `ui/src/api/mock.ts:1204`),
  gate the fallback behind FORCED/DEV (`ui/src/api/mock.ts:21`), lazy-import fixtures.
- **Wire model-duplicate to the real route** (major/S). Backend `POST /api/models/{id}/duplicate`
  ships refcount-safe weight sharing (`src/hal0/api/routes/models.py:676`); the UI still uses the
  add-from-path workaround + "not wired yet" toast (`ui/src/dash/model-drawer.jsx:275`), which
  loses the refcount guarantees.
- **NpuPage hardcoded amber chip** (minor): last ApplyBadge anti-pattern instance; needs a
  per-slot key in the apply-plan payload (backend S + UI S) (`NpuPage.jsx:99-107`).

### 1.2 Wiring (backend affordances the shipped UI is waiting on)
- **UI-API-1 — re-scoped, item 1 now CLOSED on main.** Verified: items 1–3 are ALREADY IN
  CODE (managed-arg screen + O10 guard at `models.py:566-588`; `preferred_runner` validation
  + RUNNER_IMAGES on system-info at `models.py:552-562`; duplicate route). **Item 1 (the
  "potentially critical validation bypass") was additionally closed by #1316** — screens
  extra_args on launch *and* create *and* ships the `POST /api/models/{id}/validate` route
  the recommendation called for (per the descar→main landing handoff); do NOT re-spec it
  (anti-scar: point the board row AT #1316). True residual: **item 4 only** — per-runner
  digest-drift + ADMIN SSE pull route (RuntimesPage ships its pull action disabled-with-
  reason, `RuntimesPage.jsx:39-40`) plus the RunnerSupports format/arch field. Board row
  needs the re-scope so R5 doesn't re-dispatch finished work.
- **New row UI-API-2 (auth affordances)**: SecurityPage stubs name three unowned API asks —
  `client_key_configured` on `/api/auth/status` (S), `GET /api/auth/throttle` (S),
  `POST /api/auth/keys/client` (M) (`SecurityPage.jsx:56-61`). UI lights up when they exist.
- **Four declared routes with no backend** (the D5/D6/Requests/Security gaps):
  `GET /api/stats/requests` (shape frozen in `useRequestsRollup.ts:20-29`),
  `GET /api/migrations/flag-report` (**add to FLAGS-own DoD** or the shipped
  MigrationBanner/ResolveView stays dormant forever — `useMigrationReport.ts:8-13`),
  `GET /api/doctor` (DiagnosisPanel feed), `GET /api/auth/exposure` (serialize
  RULES + OPEN_ALLOWLIST; exposure.py's own docstring names this consumer).
- **Two placeholder settings pages** need an owner: HealthStatsPage, LibraryDownloadsPage
  ship visible "not yet wired — placeholder" chips; wire from existing hooks/pull-jobs
  service or demote from nav.

### 1.3 Consolidation / cleanup
- **ESM continuation**: 47 files still publish via `Object.assign(window, …)`; continue the
  SettingsShell pattern (models trio → slots tree → board), delete typeof-guards per
  conversion; add an eslint ratchet banning new window-global publishes.
- **CSS eras**: 4 era files, 6,247 lines, all loaded unconditionally (`main.tsx:41-48`);
  run a coverage-based dead-rule audit, fold redesign/overhaul survivors into one system;
  `connections.css` is a deletion candidate (route dissolved).
- **God modules, next round**: slot-modals.jsx 1,917 → extract EditSlotDrawer (pairs with
  slots-tree ESM); chrome.jsx 1,206 → TopBar/Sidebar/Footer split; model-modals.jsx 1,277
  waits for FLAGS-own inc 2 (surface shrinks).
- **Small sweep** (one S-effort PR): 8 orphaned "ui-sweep-b owns" TODOs → move inline paths
  into ENDPOINTS; Benchmarks.tsx ~12 hardcoded paths + useFeatures inline path into
  ENDPOINTS; `index.html` hardcoded `v0.5.0-alpha.1` → build-time inject (same drift class
  as the O24 stale-dist tell); expand `ui/tests/e2e` README with the mock-allowlist
  intercept idiom + C7d pattern; re-enable or delete the 3 skipped memory-graph tests.
- **Board hygiene**: D4–D6 are shipped (SecurityPage, MigrationBanner/Resolve, DiagnosisPanel
  + wired RotateKeyDialog, AuthGate, ApplyBadge, default badge, live version) — the
  UI-D1-D3 row's "D4-D6 = follow-up lane" note is stale.

## 2. Backend↔Frontend contract sync

Verified in sync: all ~35 CONTRACTS.md board routes ↔ `routes/board.py`; memoryBank* family
↔ memory_admin `_FORWARDS`; typed settings client + apply classes ↔ `settings.py`/
`_settings_apply.py`; board WS batch-frames + brain SSE event vocab symmetrical.

Remaining desync, beyond §1.2's missing routes:
- **Mock fixture rot**: `buildCapabilities` returns the pre-orchestrator envelope
  (`{capabilities:{...}}` vs real `{backends, catalogs, selections}`) so Voice/ImageGen
  e2e render empty catalogs in forced-mock; `buildStatus` has a `hostname` key the real
  `/api/status` doesn't; `buildUpdateState` misses the `revoked*` trio (revocation banner
  untestable); 2 allowlist rows for auth routes that don't exist.
- **Dead client exports**: `useCapability`/`useCapabilityPatch` target nonexistent per-key
  routes (would 404); delete or implement.
- **Doc drift, one sweep**: endpoints.ts "may 404" warnings for routes that all exist;
  "no key-rotation route" 17 lines above `authRotate`; CONTRACTS.md freezes WS params
  (`?token=&tenant=`) the server ignores.
- **Unconsumed backend inventory** (feed to owning lanes, don't build UI speculatively):
  events ring (P3-runtime-db), agent chat-proxy WS/session surface (superseded — prune
  candidate), `/v1/realtime` (HP-voice ⏸), installer FirstRun HTTP routes (verify `hal0
  setup` actually drives them; else BOOTSTRAP-classed dead surface), most MCP admin REST
  mutations (autogen lane decides keep-vs-cut), slot by-id routes (UI adopt-or-defer).

## 3. Backend wiring (API internal)

- **P3-routers inc 3** (open board row, confirmed): pull/update-pull orchestration still
  route-inline (~480 lines across 8 handlers; extraction alone lands models.py ≈669, still
  above the ≤550 DoD — also thin `set_default`/`duplicate`); typed bodies for the 15
  models/slots `request.json()` sites (dashboard-key status-code audit first).
  **Correction (verified):** routes/ had 39 sites at spec publication and has 39 now —
  net-flat, not shrinking; inc-2 converted comfyui's 2 but two new hand-rolled sites landed
  (`set_model_default`, `duplicate_model`). New routes must be born Pydantic.
- **24 `request.json()` sites across 12 other route files are owned by NO lane** (spec S10
  defers to "a future lane" that isn't on the board). Open a `typed-bodies-rest` row or fold
  per-file into lanes already touching those routers, else the global DoD is unreachable.
- **Importer flips**: 5 modules outside api/ import route-module internals through the
  inc-1/2 re-export shims (`metrics/sampler.py:63,81`, `capabilities/orchestrator.py:859`,
  slot_view, brain/chat, lifespan) — flip to service modules, extract `_probe_power`
  (defined route-side, never extracted), then shrink shims to the monkeypatch-seam minimum.
- **Sunset guard is checking nothing**: zero `HAL0-SUNSET` markers exist in src/, while four
  deliberately temporary surfaces have no removal pin (GET /api/slots/{name} "kept for one
  release", legacy agent-hermes alias, embed-npu/stt-npu aliases, nested create body).
  Stamp them; retire GET /api/slots/{name} in inc 3 and lower the scar baseline (200/200,
  zero headroom).
- **Exposure table**: 100% coverage (338 pairs, 0 unclassified, 0 duplicates) — one dead
  shadowed rule ("model set-default" unreachable behind byte-identical "model duplicate"
  matcher, `exposure.py:171-186`); add a no-identical-matcher self-check.
- **Leftovers**: `port_alloc.py` still declares PortAuthority as its merge target though
  §11.2 merged — decide fold-or-ratify; comfyui preview has 4 hand-built JSONResponse 404s
  vs the spec's carve-out of 2 — convert the two convertible or re-ratify.
- **Declare the finish line for the last god files**: `v1.py` 1,685 and `api/__init__.py`
  1,892 are deliberately outside every DoD — give them explicit accepted-as-is (or split)
  rows so the wiring section has a bounded tail.

## 4. Memory/Admin MCP tools — buildout, edit, testing

The surface furthest from sync. Current shape: `hal0-admin` at `/mcp/admin` (80 tools:
72 REST passthroughs, 4 host probes, 4 memory delegates), `hal0-memory` at `/mcp/memory`
(5 typed tools), browser MCP (:9178), `/api/mcp/*` REST, Hermes memory plugin (2 parity-
locked copies). Every `_REST_MAP` target still resolves — the drift is coverage, transport,
and auth, not stale paths.

### 4.1 Broken now (fix before buildout)
- **`upstream_update` always crashes**: mapped to PATCH but `_call_rest` supports only
  GET/DELETE/POST/PUT (`admin.py:509` vs `:776-784`) — a gated tool that survives operator
  approval then raises. Add PATCH (or `httpx.request(method,…)`) + regression test + an
  import-time supported-method guard.
- **Interim route-sync test** (~30 lines, before autogen): build `create_app()`, assert every
  `_REST_MAP` (method, path) exists as a real route and placeholders match `_PATH_ARGS`.
  `tests/mcp/` never imports create_app today; the in-file drift table (`admin.py:408-422`)
  proves this class has already bitten once.

### 4.2 Security (both also launch-relevant)
- **Raw bearer = journald `client_id`**: `mcp_mount.bearer_resolver` returns
  `(bearer, bearer or "anonymous")` (`api/mcp_mount.py:147-159`) — post key-rotation that IS
  the live admin key, replayable from `/api/logs` (redactor regexes can't match a
  `client_id=<key>` line); a test pins the leak as correct. Fix: principal-derived label or
  sha256[:12]; update test; add belt-and-suspenders redactor pattern.
- **Hermes/brain MCP wiring sends no credential**: provisioned MCP client configs carry only
  `X-hal0-Agent`/`X-hal0-Private` (`hermes_provision.py:1224-1255`, `:3067-3080`) while `/mcp`
  is ADMIN-classed — every Hermes/brain tool call 401s the moment `require_auth` is on
  (steward-auth fixed the API's own outbound calls, not this inbound path). Thread
  service-identity bearer into the provisioned headers + golden-path test (auth on →
  `tools/list` works) + coordinate with key rotation re-propagation.

### 4.3 Admin catalog buildout (two tiers)
- **Tier a — hand-add now**: slot rename (gated), by-id/by-name reads, resolved/state reads,
  PATCH defaults, model default + duplicate (autonomous-write), pulls list/delete,
  system-info read, bench queue-item delete (gated). Also: add `memory_recall` to the admin
  catalog (handler already routes it; admin docstring promises "every tool"), and move
  `memory_search`/`memory_list` out of AUTONOMOUS_WRITE (their annotations say readOnly).
- **Tier b — policy decisions**: board CRUD (arguably better reached via the KB-2/3 brain
  tool tiers than raw MCP), brain chat, updater surface (destructive → gated +
  POLICY_NO_LOOSEN if exposed at all). Record deliberate exclusions (auth rotate, secrets,
  agent sessions) in an explicit EXCLUDED set so the sync test can tell "missing" from
  "excluded".
- **Param hints**: only 7/80 tools advertise arg schemas (the hints block itself documents
  agents inventing arg names). Cheap now: mirror the memory server's typed shapes on admin +
  hint stack_create/bench_enqueue/model_edit. Structural fix rides typed bodies →
  `model_json_schema()`-generated hints in the autogen lane.

### 4.4 Route-map autogen (P3-routers inc 3 step 20 — its own lane)
Spec §4 (`spec-p3-routers.final.md:514-601`) is sound and feasible (route table verified
walkable). Three gaps to settle in a one-page spec addendum first:
1. **Deny-by-default for unclassified auto-added routes** — under autogen every route lands
   in the map; unclassified must mean *hidden from tools/list* + CI report, not fatal, and
   definitely not auto-exposed.
2. **Transport exclusions** — PATCH support (per §4.1) plus an exclusion predicate for
   SSE/stream/WS routes (logs/pull streams, events, board WS).
3. **Re-key special-casing** — redaction/wrap overlays keyed on tool names must re-key on
   route id/response shape. (Spec's "86-entry" figure is stale; actual 72.)
Sequence: interim sync test → `build_admin_route_map(app)` + lifespan install + alias table
→ addendum items → keep POLICY_NO_LOOSEN + persona overlay (alias map preserves names).

### 4.5 Memory MCP (the §7.4/§18.1/§23.1 lane — untouched)
- Rename plugin tools `hal0_memory_search/recall/add` → upstream `hindsight_recall/retain`
  naming (keep hal0's `shared`/visibility param; decide one-release aliases for prompt
  compat), **implement the missing `reflect`** against `POST /api/memory/banks/{id}/reflect`
  (route exists), move config to `~/.hermes/hindsight/config.json` `local_external` shape
  (today: `$HERMES_HOME/hal0-memory.config.json` with `memory.hal0.*` keys), fix the two
  stale docstrings that each claim the *other* copy was deleted, update the provisioner's
  system-prompt tool references (`provider.py:220-231`) — applied to BOTH parity-locked
  copies + parity test.
- Admin fallback: memory tools without a dispatcher now dead-end in
  `mcp.memory_unconfigured` although `/api/memory/*` REST shims exist — map them or fix the
  error text; fix the `/api/auth/me` docstring fiction (`admin.py:92-97`).
- `/api/mcp` lifecycle: start/stop/restart 501 + hardcoded fake catalog (stars included) —
  decide minimal supervisor vs hiding the dead controls; stop advertising what can't run.
- Docs: `connect-mcp.mdx` + realtime recipe still document a no-auth hal0; add the auth-on
  variant (`Authorization: Bearer` via mcp-remote `--header`) → P4-docs.

### 4.6 Testing
Strong today (96 plugin + 23 memory-MCP tests). Add: route-sync pin (§4.1), gated-PATCH
regression, admin-mount memory_recall dispatch, auth-on golden path for provisioned
configs, and after the rename — parity + provisioner prompt assertions on the new roster.

## 5. CLI — wiring, improvements, consolidation, cleanup

Entry `src/hal0/cli/main.py`: 15 sub-apps, ~110 verbs, 34 modules, 14,133 lines; thin
HTTP client discipline is good; docs-parity test exists but matches too loosely.

### 5.1 Fix now (auth correctness)
Four transport sites bypass `_auth_headers()` and 401/traceback on auth-enabled boxes:
`slot logs --follow` (`slot_commands.py:366`), `doctor logs --follow`
(`doctor_commands.py:301` — its non-follow path IS authenticated: split behavior in one
command), `hal0 chat` (`chat_commands.py:259`), `hal0 setup`'s API-apply/probe path. Add an
authenticated stream helper in `_shared`; make setup handle 401/403 actionably; add an
auth-on smoke tier running every verb against a keyed TestClient.

### 5.2 Missing verbs (wire the R3/R4 surface)
- **`hal0 auth` sub-app** — status | rotate <admin|client> | require on|off. Rotation
  library is CLI-importable (`service_identity.py:145-220`); on-box rotate is the lockout
  recovery path. Also delete the two stale "daemon has no auth" claims (cli.mdx +
  agent_commands.py).
- **Value order after that**: `slot rename`; `model default`; `model update [--check]`;
  `model pull --cancel`; `hal0 ports` (PortAuthority view); `model duplicate`;
  `hal0 board list|show|add|move` (thin over /api/board); `hal0 chat --brain` (today chat
  speaks raw /v1 to the `agent` slot, not the steward). by-id addressing waits for the
  SLOT-B unit flip.
- **`model import-backup` is silently useless post-ML-1**: restores registry.toml which the
  SQLite-authoritative registry ignores on any non-empty DB (`sqlite_store.py:161-163`);
  chain `import_toml_to_sqlite` into it (idempotent) or at minimum print the follow-up;
  fix two "registry.toml is the sole catalog" docstrings.

### 5.3 Consolidation / behavior
- **Delete the client-side port scan** in `slot create` (races PortAuthority, falls back to
  a hardcoded constant; server auto-assign is runtime-aware) — omit `port` and print the
  server's answer.
- **`app list/uninstall`**: prefer `/api/services` when reachable, systemctl fallback when
  down (matches setup's hybrid pattern).
- **FLAGS-own tranche (serialize behind the migration)**: slot create/edit lose
  `--provider/--hardware/--backend` + the client-side `/etc/hal0/hardware.json` probe
  (`slot_commands.py:95-123`); deprecate to no-op warnings for one release, then delete.

### 5.4 Scheduled deletions (sequence, don't forget)
- **P2-memory checklist**: `memory sync-graph`, `memory honcho` (whole sub-app),
  `memory provider`, honcho arms of `memory migrate`, their cli.mdx rows + tests — they ARE
  the migration tool until the window closes, then they go in the same tranche as Honcho.
- **P2-config checklist**: `capabilities migrate` (+ the CLI's import of orchestrator
  privates `_CHILD_TO_CAPABILITY` — move legality checks server-side regardless).
- **Deprecated-alias tranche** (8 hidden aliases, all warn correctly): probe, slot
  add/remove, model register/assign, registry import, upstream set-credentials,
  capabilities --dry-run, slot --backend. Fix NOW the two help texts that actively point
  users AT deprecated verbs (`migrate_commands.py:755-757`, `model_commands.py:303-305`).

### 5.5 Docs
cli.mdx documents flags that don't exist (`update --source release|git`, `--channel
nightly` — CLI enum is stable-only while the API still validates nightly: resolve under
P2-updater-b), wrong flags (`registry export --dest`→`--out`; `import-sqlite --force`→
`--registry-file`), omits setup's four flags and the entire `hal0 bench` family; tighten
the parity test (backtick/table-context match + flag existence) so this class can't ship.
Profiles/stacks have a documented no-CLI stance — record it in the lane so audits stop
re-flagging.

## 6. Installer / Uninstaller

### 6.1 Critical
- **Ship `hal0.target`**: every rendered quadlet says `[Install] WantedBy=hal0.target`
  (`providers/container.py:763-764`) and the renderer's own docstring relies on it for
  boot-enable — but no `.target` unit exists in either repo, the installer never writes
  one, and startup reconcile only re-registers already-active containers. After reboot all
  slots stay down. Fix: ship + enable the target (Wants/After network-online,
  WantedBy=multi-user.target), add to uninstall list, add a doctor check; or flip the
  renderer to multi-user.target + rerender migration.

### 6.2 Installer gaps
- **Model store PermRow missing (O13 class)**: `${VAR_DIR}/models` born `root:root 0755`;
  ownership_table has rows for slots/, registry/, bench, skills, .hermes — none for the
  store (`install/perms.py:152-394`), so `doctor perms --fix` can't heal it and default-
  store dashboard pulls fail with PermissionError under the `User=hal0` daemon. (Live boxes
  used `/mnt/ai-models`, so the default path was plausibly never exercised.) Add the
  PermRow (2775 setgid + files) and chown in install.sh's O13 block; verify with a fresh
  default-store pull.
- **ComfyUI repin (cross-repo)**: hal0-runner-images published `ghcr.io/hal0ai/hal0-comfyui`
  @sha256:fd8c8930… (2026-07-19, explicitly "digest-pin in app manifest" as follow-up) but
  manifest.json + `_COMFYUI_IMAGE` (`runners/__init__.py:74`) still pin `docker.io/kyuz0`.
  Run `emit-manifest.sh`, verify in-container path assumptions, smoke the img slot.
- **cpu runner lineage**: runner-images declares built+verified `hal0-toolbox-cpu:v1` with
  `manifest_key: cpu`; hal0's cpu runner deliberately uses the vulkan fallback with
  `manifest_key=None` and manifest.json has no cpu key. Decide once, record in both repos.
- **Stale root-era comments** in install.sh (two blocks assert "hal0-api runs as root"
  contradicting the shipped User=hal0 unit forty lines away).
- **First-run lock is dead surface**: paths helper + PermRow + docstring contract exist,
  no writer/consumer anywhere — wire the OTP flow as R5 launch posture or delete the trio.

### 6.3 Uninstaller (behind by a generation — one lane fixes all)
Orphan inventory, verified:
- Quadlet sources: `/etc/containers/systemd/hal0-slot@*.container(.d/)` never removed
  (sweep is UNIT_DIR-only, `uninstall.sh:333-345`) → podman generator resurrects ghost
  slots on reload/reboot after reinstall.
- `/etc/sudoers.d/hal0-podman-ro` installed (`install.sh:1331-1344`), never uninstalled
  (`uninstall.sh:393-400`) — root grant left for a re-creatable principal.
- `--purge` says it removes `/var/lib/hal0-bench` twice in comments; no code path does.
- Honcho: runtime can still provision `hal0-honcho.service` + sync timer; uninstaller's
  lists cover none of them (cheap sweep now; full removal rides P2-memory).
- Hermes shims: `hal0-hermes` symlink dangles; a captured foreign `hermes.pre-hal0` backup
  is never restored.
- `--purge` leaves the lemonade-team PPA and the containers.conf apparmor edit; cuda
  fallback image matches no purge pattern; `/mnt/ai-models/comfyui` tree isn't in the
  documented keep-list; dead `uninstall_agents` companion-script contract.

### 6.4 Parity (verified healthy)
Hermes plugin seeds byte-identical src↔installer, locked by parity tests; auth-open summary
matches shipped auth-OFF default; venv same-version force-refresh present; four sudo seams
shipped; legacy static slot template removed.

## 7. Hermes — shape, integration, testing

Shape is landed and solid: linear convergent `install_hermes` (now 5,280 lines — regrew
from 5,065 via inc-1 security + kanban_db_init + O-fixes; split deferred, watch it),
18-step pipeline, double-run-mutates-nothing contract live-validated; contract freeze =
18 tracked files @ NousResearch `9de9c25f` with 25 fixture tests + weekly drift-watch;
ratified security checklist fully in code (unit hardening CI test, terminal.cwd scratch,
256-bit API_SERVER_KEY, loopback bind; terminal.backend=local stays a recorded, compensated
deviation with a strict xfail tripwire).

Remaining lanes:
1. **Brain-lane relocation** (major/L): 5 `RELOCATE(brain-lane)` steps (persona_seed,
   namespace_register, brain_profile_seed, brain_profile_mcp_wire, self_report) still
   execute inside install_hermes; move to the hal0-api lifespan, delete from
   `_INSTALL_STEPS`, drop the `_BRAIN_LANE_STEPS` convergence exemption. **Correction
   (verified):** these steps are already excluded from the convergence contract — the gap
   is *where they run*, not install-surface pollution; the marker-count test gives a clean
   deletion tripwire. Likely brings the module's line count back down naturally.
2. **Two validation re-runs, now unblocked**: Phase 5 full plugin liveness (provider model
   discovery + memory write/recall via live chat — steward now green after O17/O18 +
   steward-nomodel) and Phase 6 HP-executor first live contact (`WORKER_BASE_PATH =
   /api/plugins/kanban/runs`, fixture-unpinned, never exercised; deferral reasons all
   fixed). ~10 min/box each per the runbook; record a v2 validation doc.
3. **Drift-watch blind spot**: hal0 newly consumes `hermes_cli/kanban_db.py` (7-table
   roster), the kanban runs API, and the `window.__HERMES_SESSION_TOKEN__` injection seam —
   none in pyproject `tracked_files` or contract fixtures. Add to tracked_files now; vendor
   a kanban_runs fixture after the Phase-6 pass.
4. **P2-memory** (critical for upgrade-path boxes): rehearse with deterministic Honcho
   fixtures on fresh halo143 (never mutate lxc105), per-workspace migrate, verify persisted
   `[honcho]` tolerance, then the ordered deletion (UI→CLI→API→provision→registry→modules→
   schema→units) including §5.4's CLI tranche and §6.3's units.
5. **hermes-bump runbook** (minor/S): mechanics exist (SHA pin, 3-way lockstep tests,
   weekly diff, `--bump`) but `--bump` rewrites only pyproject and no doc lists the full
   procedure (bump → requirements.txt + HERMES_COMMIT → re-vendor fixtures → contract+
   parity suites → Phases 3/5/6 re-validation → `hal0 agent upgrade hermes`). Write it;
   optionally teach `--bump` the other two rewrites.
6. **Board hygiene**: R4 tails cell still lists host-net renderer (merged `bf05310d`,
   operator-validated); provision row's line count stale.

## 8. Cross-cutting sequencing

**Wave 0 — correctness/security fixes (all S/M, no design):**
hal0.target · uninstaller sweep lane (§6.3 as one PR) · model-store PermRow · MCP PATCH
fix · MCP client_id leak · Hermes/brain MCP credentials · CLI auth-bypass fixes · prod
mock gate · the two help texts pointing at deprecated verbs.

**Wave 1 — sync wiring (mostly S/M, unblocks shipped surfaces):**
MCP interim route-sync test + tier-a catalog adds + memory_recall/classification ·
`hal0 auth` + missing CLI verbs · UI model-duplicate wiring · import-backup chain ·
contract/mock doc sweep (§2) · UI-API-1 re-scope + item 4 · UI-API-2 row · ui-sweep-b
ENDPOINTS sweep.

**Wave 2 — structural lanes (existing board rows, now with corrected scope):**
P3-routers inc 3 (pull extraction → typed bodies [+ born-Pydantic rule + typed-bodies-rest
row] → route-map autogen with the 3-gap addendum) · FLAGS-own (+ flag-report endpoint in
DoD; CLI tranche §5.3; model-modals shrink) · settings data-seam completion + placeholder
pages · importer flips + sunset stamps.

**Wave 3 — memory/hermes:**
hindsight rename lane (§4.5) · brain-lane relocation · drift-watch fixture adds · bump
runbook · Phase 5/6 validation re-runs (deploy window).

**Wave 4 — migration windows + launch (orchestrator-run):**
P2-memory (with §5.4/§6.3 deletion checklists) · P2-config (+ capabilities CLI deletion) ·
P2-updater-b (+ cli.mdx channel resolution) · P3-runtime-db (SLOT-B flip coordination) ·
SLOT-B live flip · golden-paths deploy runbook · ComfyUI repin + cpu lineage · R5 cutover
program (side-by-side halo, lxc105 rollback).

**Docs (P4-docs additions from this assessment):** connect-mcp/realtime auth-on variants ·
cli.mdx corrections + bench section + parity-test tightening · CONTRACTS.md/endpoints.ts
sweep · board hygiene edits (UI-D1-D3 row, R4 tails cell, UI-API-1 re-scope, provision line
count, R1 "what remains" cell) · hermes-bump runbook.

**Proposed new board rows** (this doc is their spec until promoted):
`INSTALL-target` (hal0.target, critical) · `UNINSTALL-sync` (§6.3) · `MCP-sync` (§4.1–4.3)
· `MCP-mem-hindsight` (§4.5) · `CLI-auth+verbs` (§5.1–5.2) · `UI-API-2` (auth affordances)
· `typed-bodies-rest` · `mock-prod-gate` · `hermes-bump-runbook` · `comfyui-repin`.

## 9. Coverage gaps in this assessment (completeness pass)

The seven surveys did not open-code-verify four plan sections; they are captured from the
plan/board in the scope digest, but flagged here so they get a dedicated code pass before R5:
- **P2-updater-b — board status contradicts the code (major, re-verify first).** The board
  lists it as todo ("one cosign+swap+rollback path"), but `src/hal0/updater/updater.py` is
  already a **1,918-line implemented** cosign verify-blob + atomic `/usr/lib/hal0/current`
  symlink-swap + rollback pipeline. What's actually unverified: whether install.sh lays down
  the `/usr/lib/hal0/current` layout the updater assumes; whether a release pipeline publishes
  the tarball + sigstore bundle the manifest fetcher expects; exposure/UI/CLI wiring of
  `api/routes/updater.py`; and the stale `PLAN.md §9/§17` docstring ref (`updater.py:30`). So
  P2-updater-b is likely *scope-trim + verify + delete the extra mechanisms*, not build-from-todo.
- **P3-runtime-db (major)** — `state.json` still consumed across `slots/manager.py`,
  `arbiter.py`, `stacks/state.py`, `config/paths.py`; migration 006 pre-allocated; the
  "state.json double-touch" M5-vs-runtime-db sequencing decision (board:197) needs confirming
  at dispatch. Couples to the launch-blocking SLOT-B live flip (§7 wave 4).
- **Observability §13 (major)** — `002_metrics.sql` landed, but which of §13.1–13.7 (3-tier
  measurement, baseline/regression, opt-in Prometheus/Grafana) ships vs remains is unassessed;
  the §2 missing `/api/stats/requests` route is the OBS lane's deliverable, not standalone.
- **§20 bench** — assessed only via edges (UI routes unconsumed, CLI verb undocumented), not
  as a surface; see the scope digest for the full lane.

---

## 10. Reconciliation with the descar→main landing (handoff fold-in)

Folded from `docs/rework/handoff-merge-followups-to-docs.md` (the routing brief produced
while landing `rework/descar` onto `main`), verified against `origin/rework/descar`.

### 10.1 Items in this doc already closed on descar/main — do NOT re-dispatch
- **UI-API-1 item 1** (managed-arg validation bypass) → closed by **#1316** (launch + create
  + `/validate`). §1.2 updated to point at the PR.
- **P4-docs** (§1.3/§8 "Docs additions") → **✔ `ecbdc6f6`** on descar: ARCHITECTURE/CONTEXT/
  AGENTS collapsed to `ARCHITECTURE.md` as the single authoritative internal doc, ADR-inline.
  The doc-additions this assessment proposes (connect-mcp/realtime auth variants, cli.mdx
  corrections + bench section + parity-test tightening, CONTRACTS.md/endpoints.ts sweep,
  board-hygiene edits, hermes-bump runbook) are now **follow-ups onto a merged P4-docs**, not
  part of an open collapse lane.
- **P4-rules** → **✔ `fa9085d0`**: CONTRIBUTING carries anti-scar rules 1–11 (the "bit-twice"
  CI lessons landed as 10–11). New lessons from this assessment append as rule 12+ **only if
  earned and CI-gated** — candidates: "MCP `_REST_MAP` must be route-table-pinned" (§4.1),
  "new routes born with Pydantic bodies" (§3). One line rule + one line why; mark **(gated)**
  only when CI enforces it.
- **§21.11 golden-paths** → **✔ `fa9085d0`** + `golden-paths-halo143-runbook.md`. The §21.11
  deploy remainder in the scope digest is the runbook's deploy-only half, already mapped in
  `tests/golden_paths/__init__.py`; §6.1 `hal0.target` and §6.3 uninstaller gaps are new
  golden-path inputs (fresh-install autostart, uninstall-cleanliness) — route them into that
  existing map (assign a mechanism, cite the owner), don't fork a new one.

### 10.2 New meld/fix item surfaced by the landing
- **Root `AGENTS.md` resurrection** (P4-docs meld, verified): the P4-docs board row
  (`ecbdc6f6`) records deleting `CONTEXT.md` + `AGENTS.md`, and `CONTEXT.md` is gone on
  descar — but **`AGENTS.md` is present again at descar root** (a rebase kept the remote
  side). Decision needed on the P4-docs row: re-delete to finish the collapse, or keep it as
  a thin pointer to `ARCHITECTURE.md#bundled-agents-v03` — never a second content copy
  (anti-scar rule 1, one-owner-per-fact). Board delta below.

### 10.3 Delivery conventions (how the proposals in §1–§9 must be routed)
Per the handoff's routing table and hard conventions:
- **Board is single-writer** (the orchestrator session). This doc holds no writer token, so
  every "proposed new board row" (§8) and status correction (§3 UI-API-1 stale, §7 R4-tails
  cell, R1 "what remains") is a **row delta handed to the writer**, not a direct board edit.
- **ARCHITECTURE.md is the one authoritative internal doc** — standing-decision changes
  (e.g. §3 "accept v1.py/api factory as-is", §4.4 autogen deny-by-default policy) go inline
  there next to the code, **no ADR tree** (rule 9).
- **Deploy-affecting findings = both boxes** (150 privileged / 143 unprivileged), recorded
  per box: applies to §6.1 `hal0.target`, §6.3 uninstaller, §6.2 model-store PermRow, the
  Hermes Phase 5/6 re-runs (§7), and the ComfyUI repin (§6.2).
- **Fix follow-ups already shipped point AT the PR** (rule: UI-API-1 item 1 → #1316), never
  re-specced.

### 10.4 Board-row deltas (for the writer)
- `UI-API-1`: re-scope to **item 4 only**; mark item 1 done → #1316, items 2–3 done in code.
- `P4-docs`: add a sub-note — **AGENTS.md resurrection**, decision {re-delete | pointer stub
  to `ARCHITECTURE.md#bundled-agents-v03`}; the row's "deleted AGENTS.md" claim is currently
  falsified by the tree.
- `R1` checkpoint "what remains" cell + `R4` open-tails cell (host-net) + UI-D1-D3 "D4-D6
  follow-up" note: all stale per §7/§1.3 — clear them.
- New rows proposed in §8 (`INSTALL-target` critical, `UNINSTALL-sync`, `MCP-sync`,
  `MCP-mem-hindsight`, `CLI-auth+verbs`, `UI-API-2`, `typed-bodies-rest`, `mock-prod-gate`,
  `hermes-bump-runbook`, `comfyui-repin`) — hand to writer with the §-refs as their spec.
- P2-updater-b (§9): re-scope from "build" to **scope-trim + verify + delete extra
  mechanisms** — the pipeline is already implemented (`updater.py`, 1,918 lines).

---

_Graph artifacts for this assessment are committed under `graphify-out/` (report + wiki;
raw graph.json regenerates via `graphify update .`). Findings without a board row cite
source at the referenced `path:line`; corrections from adversarial verification are marked
inline. Two survey claims were materially corrected during verification; none were refuted.
§10 folds in the descar→main landing handoff (2026-07-19)._
