Verified against `/home/mint/hal0` @ `rework/descar`. Every file:line below is current code. Plan refs: `/home/mint/hal0-rework-plan.md` §11.1–§11.2 (round 2 product reworks), §23.2 seams S3/S8, §23.4 build-DAG.

---

# P3 slot-identity + ports — implementation spec

Two round-2 reworks landing together because they share the SQLite substrate (seam S8, plan §23.2) and the slot-config table:

- **§11.1** — slots created equal + ID-keyed identity (opaque/numeric `id` PK; `name` becomes a mutable label)
- **§11.2** — single `PortAuthority` on a `port_claim` SQLite table (one allocator for the whole pool)

They ship as one spec because §11.2's `port_claim` rows reference the slot's `id` (not `name`); reversing §11.1 later would orphan every claim. Both are sequenced **after** ML-1 `db/` foundation (S8) and **after** P3-slots decomposition (S3 — slots.decomposition makes manager.py small enough to absorb the schema migration without churn).

---

## 0. Current state — what's broken and where

### 0.1 Name-keyed everywhere in `slots/manager.py`

Every on-disk artefact, in-memory dict, systemd unit, and API surface is keyed by the human-readable **name**. Renaming one slot touches N places and is not atomic — the break class is identical to the model-id-rename problem (plan §7.1e) but for slots.

| Surface | file:line | Name-keyed artefact |
|---|---|---|
| In-memory state cache | `manager.py:384` | `self._states: dict[str, SlotStateRecord]` |
| Per-slot locks | `manager.py:370` | `self._locks: dict[str, asyncio.Lock]` |
| Per-slot last-used | `manager.py:388` | `self._last_used: dict[str, float]` |
| TOML config cache | `manager.py:382` | `self._cfg_cache: dict[str, tuple[int,int,dict]]` |
| Fail-watcher tasks | `manager.py:393` | `self._fail_watchers: dict[str, asyncio.Task]` |
| Serving counter | `manager.py:407` | `self._serving_count: dict[str, int]` |
| Dispatch tickets | `manager.py:415` | `self._dispatch_tickets: dict[str, int]` |
| TOML path | `manager.py:562-563` | `_config_file(name)` → `paths.slots_config_dir() / f"{name}.toml"` |
| State path | `manager.py:559-560` | `_state_file(name)` → `paths.slot_data_dir(name) / "state.json"` |
| Systemd unit | providers use | `hal0-slot@<name>.service` (`providers/container.py:13,394,408,467,1417,1445`); references in `api/routes/{slots,backends,logs,installer,comfyui,journal}.py`, `api/_settings_apply.py:9`, `api/routes/board_chat.py:140` |
| Configured slot enumeration | `manager.py:565-570` | `_all_configured_slot_names()` → `sorted(p.stem for p in cfg_dir.glob("*.toml"))` |
| Snapshot/lookup | `manager.py:239-283` | `Slot` dataclass carries `self.name` as its identity; `as_dict()` returns `"name"` first |

### 0.2 Port management is ad-hoc (no allocator)

Three parallel places write/own ports today; the harvester (`hal0.ports`) only **observes** them — it does not enforce or reserve:

| Path | file:line | Mechanism |
|---|---|---|
| Slot TOML `port` field | `config/schema.py:303-307` (`SlotConfig.port`, `ge=8081, le=8200`) | Per-slot int. Hard default `8081` baked into `add_slot` (`manager.py:1756`). |
| Slot TOML `[server].port` | `ports.py:77-82` (`_config_claims`) | Second int slot writers may set; collected but never reconciled against `[slot].port`. |
| Container runtime (provider) | `providers/container.py:13,394,467` | The systemd+podman unit hard-codes `--port $_HAL0_SLOT_PORT` from the TOML. **Double-bind**: the TOML field drives both the runtime arg and the dashboard `slot.port`. |
| API itself | `api/routes/ports.py:45` | One reserved row `{8080: "api"}` passed into `port_report()`. |
| Listening sockets | `ports.py:97-120` (`_listener_claims`) | Reality check via `psutil.net_connections(kind="tcp")`. |

**Failure modes already observed:**
- `add_slot(..., port=8081)` default clashed with an existing slot at 8081 — the `feat/brain-tool-use-hardening` harvest note (plan §11.2 motivation, lines 692-694) documents `slot_create` handing out 8089 twice.
- Virtual FLM-trio ports (the three NPU shadows) appear in runtime snapshots but never in a TOML — `flm-stt`'s TOML says 8088 while its runtime row claims 8089 (`ports.py:1-25` docstring).
- The harvester deliberately stores **no allocation table** ("stored tables drift the moment something is deleted out-of-band", `ports.py:12-13`) — the right design for an *observer*, the wrong design for an *authority*.

### 0.3 NPU-trio shadow has a parallel lifecycle

`reconcile_npu_trio_slots` (`manager.py:2254-2393`) is a bespoke startup pass: it locates the `device=npu type=llm` anchor, then for each spec in `_TRIO_SHADOW_SPEC` (`manager.py:2249-2252`) it **renames legacy `stt-npu`/`embed-npu` → `{anchor}-stt`/`{anchor}-embed`**, normalizes the shadow TOML (`device=npu`, `profile=flm`, `served_by=<anchor>`, `port=<anchor port>`, `type=<transcription|embedding>`), and creates the record if missing. The runtime path `is_npu_trio_shadow` (`manager.py:307-321`) bypasses load() — the shadow is marked READY without a container, and trio dispatch routes to the anchor's FLM process instead. **Two slot types, two create-paths.** §11.1 dissolves this into the one uniform lifecycle.

### 0.4 What the harvester (`hal0.ports`) already gives us

This is the seed §11.2 builds on (plan §11.2 explicitly says "harvest it"). Today: **no** stored table, recompute claims from live truth on every question.

| Helper | file:line | Returns |
|---|---|---|
| `PortClaim` | `ports.py:44-57` | `port, owner, source, group` |
| `collect_claims` | `ports.py:123-145` | All claims (config + runtime + reserved + listener) |
| `conflicts` | `ports.py:159-180` | Ports with >1 distinct owner (group-folds the FLM trio) |
| `next_free` | `ports.py:183-189` | Lowest port in `[start,end]` with no claim |
| `port_report` | `ports.py:197-216` | Pool + claims + conflicts + next_free (drives `GET /api/ports`) |
| `claimed_by_other` | `ports.py:192-194` | Owners holding `port` other than `owner` (create/edit check) |

The harvester's *answer* functions are the right API surface; the missing piece is **the authority that issues new claims** (today: nothing — `add_slot` just trusts `port=8081` and writes TOML).

### 0.5 Schema (`config/schema.py`)

`SlotConfig` (`schema.py:290-369`, required `name`+`port`, `device`, optional `enabled`, `profile`, `model.default`, etc.) is the slot-TOML shape. There is **no** SlotConfig-equivalent that holds `id`; `SlotConfig.id` does not exist. Adding it requires `extra="allow"` (already on, `schema.py:299`) — new field round-trips cleanly.

Pool bounds: `_SLOT_PORT_MIN=8081`, `_SLOT_PORT_MAX=8200`, `_SLOT_PORT_POOL_END` (`schema.py:93-94`). `SlotsConfig.port_range_start/end` (`schema.py:2076`) lets operators shift the pool; `_slot_port_range()` (`api/routes/slots.py:342-361`) reads it (default returns the constants).

---

## 1. Target schema — `db/` tables (S8 substrate)

These land under `/home/mint/hal0/src/hal0/db/` per ML-1 (`spec-ml1-sqlite.final.md`), AFTER `db/connection.py` (`foreign_keys=ON`, `BEGIN IMMEDIATE`, `schema_migrations`) is merged. Both schemas migrate under the same `migrate.py` machinery — **do not roll a second migrator**.

### 1.1 `003_slots.sql` (id-keyed slot identity)

```sql
-- Slot identity. id is the stable primary key; name is a mutable label
-- (display only). Slot TOML stays on disk for back-compat reads during the
-- migration window, but the runtime, the router, the unit name, and every
-- port_claim row address the slot by id.
CREATE TABLE slot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,  -- opaque/numeric PK
    name            TEXT NOT NULL,                       -- mutable label
    slot_type       TEXT NOT NULL,                       -- llm | embedding | reranking |
                                                        -- transcription | tts | image | …
    device          TEXT NOT NULL DEFAULT '',            -- gpu-rocm | gpu-vulkan | npu | cpu
    runtime         TEXT NOT NULL DEFAULT 'container',   -- today always 'container'
    coresident_group TEXT,                               -- e.g. 'npu-flm-trio' (for shared port)
    is_seed         INTEGER NOT NULL DEFAULT 0,          -- 1 for SEEDED_SLOTS + NPU_SEEDED_SLOTS
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at      REAL NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(name)                                          -- names still unique (UI labels)
);

-- Anchor the FLM trio to its chat-anchor by id (was served_by=<name> in TOML).
-- The trio shadows carry the same coresident_group as the anchor; their port
-- is the anchor's port (one container, three virtual slots).
CREATE TABLE slot_link (
    parent_id       INTEGER NOT NULL REFERENCES slot(id) ON DELETE CASCADE,
    child_id        INTEGER NOT NULL REFERENCES slot(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,                       -- 'served_by' | 'gated_by'
    PRIMARY KEY (parent_id, child_id, kind)
);

CREATE INDEX idx_slot_name ON slot(name);                 -- name lookup is the legacy path
CREATE INDEX idx_slot_type ON slot(slot_type, enabled);  -- route_for_request fan-in
```

Notes:
- `id INTEGER PRIMARY KEY AUTOINCREMENT` — opaque, monotonic, never reused (even after `delete`). Survives rename. **Direct parallel of §7.1e `model.id` and S3 `by-id/<id>` (`hal0-rework-plan.md:1592`).**
- `slot_type` mirrors `SLOT_TYPES` (`hal0.model_meta.SLOT_TYPES`, imported by `manager.py:143`); values stay in lockstep with the taxonomy (§7.1d).
- `coresident_group` is the FLM-trio marker (today the snapshot field; see `ports.py:91`). One port, N virtual slots — no double-claim.
- `slot_link` absorbs today's `served_by=<name>` TOML field (`manager.py:2354-2356`). Group = `npu-flm-trio` mirrors `slots/npu/trio.py` predicate (post-decomposition).

### 1.2 `004_port_claim.sql` (PortAuthority, §11.2)

```sql
-- Single authority on who owns which port. Source of truth for the pool;
-- replaces per-slot TOML `port` as the writable surface (TOML keeps the field
-- for back-compat read-only during the migration window, written from the
-- claim on every create/update).
CREATE TABLE port_claim (
    port            INTEGER PRIMARY KEY,                  -- one row per port (sparse)
    slot_id         INTEGER REFERENCES slot(id) ON DELETE SET NULL,  -- owning slot (NULL=reserved/unowned)
    owner_kind      TEXT NOT NULL,                        -- 'slot' | 'reserved' | 'listener'
    owner_label     TEXT NOT NULL,                        -- e.g. 'api', 'listener:llama-server',
                                                        -- 'slot:agent', …
    coresident_group TEXT,                                -- shared with slot.coresident_group
    acquired_at     REAL NOT NULL DEFAULT (strftime('%s','now')),
    released_at     REAL                                  -- set when freed; live claim iff NULL
);

-- Idempotency for the unique-claim invariant: at most ONE non-released
-- (slot_id, port) row. Released rows (released_at IS NOT NULL) do not
-- participate in the partial unique index — a deleted-then-recreated slot
-- can re-claim the same port after release.
CREATE UNIQUE INDEX uq_port_claim_live
    ON port_claim(port)
    WHERE released_at IS NULL;

CREATE INDEX idx_port_claim_slot ON port_claim(slot_id, released_at);
```

Notes:
- `PRIMARY KEY (port)` is the **strong** no-two-rows-for-one-port invariant for **released** rows (audit trail).
- `uq_port_claim_live` is the **partial** invariant: at most one *live* claim per port — `next_free` becomes `SELECT port WHERE NOT EXISTS(live claim)` after extending the pool to a dense table (see §2.4).
- The harvester (`hal0.ports`) stays as the **observer/audit** path (today `GET /api/ports` → `port_report`). PortAuthority is the **writer**. Two roles, one substrate; both query the same `port_claim` rows.
- `ON DELETE SET NULL` on `slot_id` preserves the audit trail when a slot is deleted; `released_at` is stamped at the same transaction (no orphan `NULL` + `released_at IS NULL`).

### 1.3 Migration to a dense pool table (separate migration)

`port_claim` above is **sparse** (only ports someone owns have rows). `next_free` needs a dense range to walk. Two options:

- **(a) Generate on demand** — `next_free` does `WITH RECURSIVE r(p) AS (SELECT :start UNION ALL SELECT p+1 FROM r WHERE p < :end) SELECT MIN(p) FROM r WHERE p NOT IN (SELECT port FROM port_claim WHERE released_at IS NULL)`. Pure SQLite, no schema change.
- **(b) Dense rows** — add `port_pool(start,end,active)` or generate via a CTE in the claim queries. Pure read perf is irrelevant at this scale (120 ports).

**Pick (a).** No schema change; the recursive CTE is one query the index covers.

---

## 2. Target Python surface

### 2.1 New module `src/hal0/slots/identity.py`

Owns the slot-id ↔ slot-name bridge. Single source for the chokepoints.

```python
# src/hal0/slots/identity.py
class SlotIdentityStore:
    """Resolve slot id ↔ name. Thin SQLite wrapper around the `slot` table."""

    def __init__(self, conn_factory: Callable[[], sqlite3.Connection]): ...

    # id-keyed (the future)
    async def get(self, slot_id: int) -> SlotRow
    async def create(self, *, name: str, slot_type: str, device: str,
                     runtime: str = "container", coresident_group: str | None = None,
                     is_seed: bool = False, enabled: bool = True) -> SlotRow
    async def rename(self, slot_id: int, new_name: str) -> None
    async def set_coresident_group(self, slot_id: int, group: str | None) -> None
    async def delete(self, slot_id: int) -> None
    async def list_by_type(self, slot_type: str, *, enabled_only: bool = True) -> list[SlotRow]
    async def list_all(self) -> list[SlotRow]

    # name-keyed (the legacy bridge — kept for callers still on names)
    async def get_by_name(self, name: str) -> SlotRow | None
    async def list_seed_ids(self) -> list[int]                  # SEEDED_SLOTS + NPU_SEEDED_SLOTS

    # cross-table
    async def link(self, parent_id: int, child_id: int, kind: str) -> None
    async def children_of(self, parent_id: int, kind: str) -> list[int]
```

`SlotRow` is a frozen dataclass: `id, name, slot_type, device, runtime, coresident_group, is_seed, enabled, created_at, updated_at`. **No port on this row** — port lives in `port_claim` (§2.2). `SlotRow` is the lookup result; `Slot` (the API snapshot, `manager.py:239-283`) is enriched with port+state at the route boundary.

### 2.2 New module `src/hal0/ports/authority.py`

```python
# src/hal0/ports/authority.py
class PortAuthority:
    """Single source of port ownership. All slot creation/edit routes through this."""

    def __init__(self, conn_factory, *, pool: tuple[int, int],
                 reserved: dict[int, str] | None = None): ...

    # queries (mirrors hal0.ports API; reads port_claim directly)
    async def claims(self, *, include_listeners: bool = True) -> list[PortClaim]
    async def conflicts(self) -> list[dict[str, Any]]
    async def next_free(self, *, preferred: int | None = None) -> int | None
    async def held_by(self, slot_id: int) -> int | None
    async def is_free(self, port: int) -> bool
    async def is_held_by_other(self, port: int, *, slot_id: int) -> bool
    async def listener_claims(self) -> list[PortClaim]

    # writes (the new surface — everything that allocates goes here)
    async def reserve(self, port: int, *, label: str) -> None           # for reserved: rows (api, etc.)
    async def acquire(self, slot_id: int, *, preferred: int | None = None,
                      coresident_group: str | None = None) -> int       # allocates + binds to slot
    async def reallocate(self, slot_id: int, *, preferred: int | None = None) -> int
    async def release(self, slot_id: int) -> None                       # delete + unload
    async def reconcile_listeners(self) -> list[PortClaim]              # the startup vs psutil pass
```

Key contracts:
- `acquire(slot_id)` is the **only** path that allocates. It calls `next_free()` (or honours `preferred` when the slot already owns one during reallocate), inserts into `port_claim` inside the same `BEGIN IMMEDIATE` transaction as the slot insert, and returns the granted port. Two concurrent acquires for the same free port → `uq_port_claim_live` raises `sqlite3.IntegrityError`; the loser retries with the next free.
- `release(slot_id)` flips `released_at` (no row delete — audit trail). The same id may acquire the same port later (a recreate).
- `coresident_group` is the FLM-trio carve-out: when a slot in `npu-flm-trio` acquires, `next_free` excludes ports already held by a peer in the same group, AND the partial unique index tolerates the same port appearing under the same group (inserted by separate tx) — **but** the current partial unique index blocks this. See §2.4 (FOLLOW-UP).

### 2.3 Updated `SlotManager` (decomposed, post-P3-slots)

After `slots.decomposition` ships (target file: ~2,050 lines per `spec-p3-slots.final.md`), the manager keeps its public surface but **internals re-key on id**. The delegators required by spec-p3-slots §5 stay — public methods (`load`, `unload`, `swap`, `status`, `create`, `update_config`, `delete`) keep their `name: str` signature for one release; name resolves through `_resolve_alias` (`manager.py:547-557`) → `identity.get_by_name(name)` → `slot_id`, and the legacy callers continue to compile.

In-memory dicts re-key on `slot_id`:

```python
# manager.py (post-§11.1)
self._states: dict[int, SlotStateRecord]              # was dict[str, …]
self._locks: dict[int, asyncio.Lock]                  # was dict[str, …]
self._last_used: dict[int, float]                     # was dict[str, …]
self._cfg_cache: dict[int, tuple[int,int,dict]]       # was dict[str, …]
self._fail_watchers: dict[int, asyncio.Task]          # was dict[str, …]
self._serving_count: dict[int, int]                   # was dict[str, …]
self._dispatch_tickets: dict[int, int]                # was dict[str, …]
```

Per-method edits:

| Method | file:line | Change |
|---|---|---|
| `Slot.__init__` / `as_dict` | `manager.py:247-283` | Add `slot_id: int` field; `as_dict` returns `"id"` + `"name"`. `name` stays. |
| `_lock` | `manager.py:542-545` | Take `slot_id`, not `name`. |
| `_resolve_alias` | `manager.py:547-557` | **Keep as-is.** Returns the **canonical name**. The id-lookup seam (per spec-p3-slots §7) becomes: caller does `name → _resolve_alias → identity.get_by_name → slot_id`. Aliases never go in SQLite; the `name` column is the canonical post-alias label. |
| `_state_file` / `_config_file` | `manager.py:559-570` | Take `slot_id`; path becomes `/var/lib/hal0/slots/<id>/state.json` and `/etc/hal0/slots/<id>.toml` (see migration §3.1). |
| `state` / `is_ready_for_dispatch` | `manager.py:595-623` | `name → slot_id` at the top; return by name unchanged. |
| `_current_state` / `_transition` | `manager.py:627-758` | Internally keyed on `slot_id`; broadcast records carry `slot_id` in addition to `name`. |
| `_update_fail_watcher` / `_fail_watch_loop` | `manager.py:800-1000` | Keyed on `slot_id`; task name `hal0-slot-fail-watch-<id>` (was `<name>`). |
| `load` / `unload` / `restart` / `start` / `swap` | `manager.py:1107-1352` | `slot_name` parameter unchanged; resolve to `slot_id` once via `identity.get_by_name`. Acquire port BEFORE `_spawn_locked` (`manager.py:1837`) via `port_authority.acquire(slot_id=…)`; pass the granted port into `_spawn_locked`. |
| `create` | `manager.py:1900-2010` | Inserts the `slot` row first (id assigned), then writes the TOML **at the id-based path** (legacy name-path migration in §3.1), then calls `port_authority.acquire(slot_id, preferred=cfg.get('port'))`. The TOML's `port` field becomes the **mirror of the claim** (written from the claim on every create/update), not the writable surface. |
| `update_config` | `manager.py:2047-2182` | If `updates` carries a new `port`, call `port_authority.reallocate(slot_id, preferred=new_port)`; update the TOML from the granted value. |
| `delete` | `manager.py:2012-2045` | `port_authority.release(slot_id)` first, then drop `slot` row (`ON DELETE SET NULL` + `released_at` set in same tx), then unlink TOML/state. |
| `_register_container_upstream` | `manager.py:438-465` | Slot upstream name stays `<name>` for the URL stability of the dispatcher (the dispatcher caches `name` as the dispatch key today — see `dispatcher/router.py:495-525`). Use `slot.name` here, not `slot_id`. (Dispatch stability beats internal re-keying.) |
| `_spawn_locked` / `_spawn` | `manager.py:1825-1896` | Port comes from the authority-granted value (passed in by `load`), not from `cfg.get("port")`. **Hot path** — the unit template (`providers/container.py:13,394,467`) still templatizes `hal0-slot-<name>.service` → change to `hal0-slot-<id>.service` (see migration §3.1). |
| `add_slot` | `manager.py:1749-1804` | Drop the `port=8081` default; let `port_authority.acquire` pick. |
| `reconcile_npu_trio_slots` | `manager.py:2254-2393` | Move to `slots/npu/trio.py` (spec-p3-slots §1d). Idempotent — re-runs the same logic on `(parent_id, child_id)` from `slot_link`, not on `(anchor_name, anchor_port)` from the TOML. The TOML `served_by=<name>` field disappears; the link row replaces it. |
| `is_npu_trio_shadow` | `manager.py:307-321` | Becomes `is_npu_trio_shadow(slot_row) -> bool`: `(slot_row.device == 'npu') and (slot_row.slot_type in ('transcription','embedding')) and slot_row.coresident_group == 'npu-flm-trio'`. |
| `reconcile_container_upstreams` | `manager.py:475-540` | Iterates `identity.list_all()` instead of `iter_configs()`; checks `slot_row.slot_type` (skip shadows) and `port_authority.held_by(slot_id)` (skip ports not claimed). |

### 2.4 Group carve-out for coresident slots (FOLLOW-UP)

The partial unique index in §1.2 blocks same-port rows under different slot_ids even within the same `coresident_group`. Today the harvester handles this by group-folding in `conflicts()` (`ports.py:159-180`). Two options for the SQLite layer:

- **(a) Authoritative group write** — `PortAuthority.acquire` for a slot with `coresident_group='npu-flm-trio'` joins on the group's other live claims; if a peer holds port P in the same group, grant P to the new slot in the same tx. The partial unique index must drop "different `slot_id` in same `group`" — replace with `CREATE UNIQUE INDEX uq_port_claim_live_excl_group ON port_claim(port, coresident_group) WHERE released_at IS NULL`. The trio's three slots share `(8088, 'npu-flm-trio')`.
- **(b) Sparse carve-out** — trio slots have `port=NULL` in `port_claim` (the anchor holds the one row). Dispatch uses `slot.coresident_group → port_authority.held_by(anchor_id)`.

**Pick (b).** The trio's "port" is the **anchor's** port; shadows don't need their own claim. `port_claim.slot_id` is the anchor's id; `coresident_group` is set on both the anchor row and the shadow slot rows for routing. One row per port. The partial unique index stays as in §1.2.

### 2.5 API surface changes

| Route | Today | After |
|---|---|---|
| `GET /api/slots` | `list()` (`manager.py:1536-1545`) | `identity.list_all()` + `port_authority.held_by(slot_id)` enrichment; each `Slot` carries `id` + `name` + `port` + `state`. |
| `GET /api/slots/{name}` | name-keyed | 301 → `/api/slots/by-id/{id}` OR `/api/slots/by-name/{name}`; both resolve via `identity`. Accept either for one release. |
| `POST /api/slots` | name-keyed create | Body accepts optional `id` (rare; default = assigned). Returns `{id, name, port, …}`. |
| `PATCH /api/slots/{name}` | name-keyed update | Body may carry `port` → `port_authority.reallocate`. |
| `DELETE /api/slots/{name}` | name-keyed delete | `port_authority.release` first; cascades to `slot` row + TOML + state.json + unit. |
| `POST /api/slots/{name}/rename` | doesn't exist | New. Calls `identity.rename`. **Only touch is the label** — zero state/unit/port churn. |
| `GET /api/ports` | `port_report` (observer) | Adds `authority_claims` to the payload: `port_claim` rows with `slot_id`, `owner_label`, `acquired_at`, `released_at`. Harvester keeps reporting its 4-source view; authority is the 5th. |
| UI SlotCard | name only | `id` (small, mono) under name; rename is the new affordance. |

The UI rename affordance is the operator-visible win — today rename is destructive (forces every reference to break); after §11.1 it is `UPDATE slot SET name=? WHERE id=?`.

---

## 3. Migration window — name → id keying

This is the only path that touches user data on disk. Total downtime target: one api restart (the slots spec for migration `M5-7` sets the bar). Plan §12 already authorises a downtime window; this spec lands inside it.

### 3.1 File:line renames (one-shot boot fold)

On first api boot after the schema lands, a one-shot migration (`migrate.py` step `M5` or `M6`) walks `/etc/hal0/slots/` and `/var/lib/hal0/slots/`:

| From | To |
|---|---|
| `/etc/hal0/slots/<name>.toml` | `/etc/hal0/slots/<id>.toml` (insert `id` into TOML as a new field — required going forward) |
| `/var/lib/hal0/slots/<name>/state.json` | `/var/lib/hal0/slots/<id>/state.json` (rewrite the `name` field inside state.json to the canonical post-alias label; add `"slot_id": <id>` field) |
| `hal0-slot@<name>.service` | `hal0-slot@<id>.service` (`systemctl daemon-reload` + `systemctl reenable hal0-slot@<id>.service`; the old name symlink is **not** kept — old units are dead) |
| `hal0-slot-<name>` (podman container name) | `hal0-slot-<id>` |
| systemd `SyslogIdentifier=hal0-slot-<name>` | `SyslogIdentifier=hal0-slot-<id>` |

Steps:
1. `slot` table populated from `_all_configured_slot_names()` → `identity.create(name=name, slot_type=…, device=…, is_seed=…)` in deterministic order (sorted by TOML stem).
2. TOML move + rewrite under `<id>.toml`; add `[slot] id = <id>` and remove the now-redundant `[slot] name` (keep a `[slot] name` mirror on disk for one release for tooling that still reads it).
3. state.json move + rewrite under `<id>/state.json`; add `"slot_id": <id>`.
4. For each id, if the container is currently active: rename via `podman rename hal0-slot-<name> hal0-slot-<id>` + `systemctl reenable` + `systemctl restart hal0-slot-<id>`.
5. Stamp `port_claim` from the TOML's `port` (port-claim-write side of cutover is **separate** — see §3.2).

Migration is idempotent: a re-run after partial completion is safe (TOML `id` field is the marker; missing → migrate; present → skip).

### 3.2 Port-claim seeding (separate step)

After §3.1, the TOMLs still carry `port` (the mirror). A second pass:

```sql
-- per row
INSERT INTO port_claim (port, slot_id, owner_kind, owner_label, coresident_group)
SELECT cfg.port, slot.id, 'slot', 'slot:' || slot.name, slot.coresident_group
FROM slot
JOIN read_slot_toml(slot.id) AS cfg USING (id)   -- helper view
WHERE NOT EXISTS (SELECT 1 FROM port_claim WHERE port = cfg.port AND released_at IS NULL);
```

If two slots have `port=8081` (the documented pre-spec bug), this fails loudly on `uq_port_claim_live`. The migration **detects** the conflict, logs the offending slots, and **does not** auto-reassign (operator picks via the dashboard's port picker; rerun the seeding step after). The `flm-stt`/`flm-embed` shadows with `port=8089` while the anchor is `port=8088` are auto-corrected: the shadow's TOML `port` is rewritten to match the anchor (`served_by` is now `slot_link.parent_id`), and the seeded row uses the anchor's port (the trio carve-out, §2.4 option b).

### 3.3 Idempotency + crash safety

Both migrations run inside `BEGIN IMMEDIATE` (S8 contract). The migrator stamps a row in `schema_migrations` after each successful step. A crash mid-§3.1 leaves a half-migrated filesystem; the next boot detects the partial state via `TOML has no id AND slot row has no TOML` and rolls forward. **Test:** re-running the migrator three times must produce the same disk state (`test_port_authority_idempotent`).

### 3.4 What is **not** migrated

- Operator aliases (`SLOT_ALIASES`, `manager.py:133-138`) — these are dispatch aids, not on-disk names. Stays.
- `capabilities.toml` references to slot names — capabilities is being collapsed by P2-config; do not migrate here.
- `/etc/hal0/api.env` or any external config that hand-types a slot name — only changes if the operator wants to switch to id-based references. Document, don't rewrite.

---

## 4. Edit plan — file, order, what to keep

Sequenced so each commit is independently green + small diff. Each step is one PR.

### PR 1 — `db/` foundation + slot identity table

- Merge `src/hal0/db/{connection,migrate,__init__}.py` from ML-1.
- Add `src/hal0/db/migrations/003_slots.sql` (§1.1) + register in `migrate.py`.
- New module `src/hal0/slots/identity.py` (§2.1) — pure CRUD on the `slot` table.
- New module `src/hal0/ports/authority.py` (§2.2) — wait, authority depends on §11.2; defer. Land `identity.py` only.
- **No** manager.py changes; the table is unused. Existing `Model` registry SQLite pilot ships first (ML-1).

### PR 2 — `port_claim` schema + PortAuthority (no callers)

- Add `src/hal0/db/migrations/004_port_claim.sql` (§1.2) + register.
- Add `src/hal0/ports/authority.py` (§2.2) — `PortAuthority` class, full API surface, **no callers**. Wraps `identity.py` for `slot_id ↔ name` lookups.
- Extend `src/hal0/ports.py` harvester with `authority_claims()` helper that reads `port_claim` and folds into the existing 4-source report. `port_report()` adds the authority claims to its payload without changing the conflict/group semantics.
- Update `GET /api/ports` (`api/routes/ports.py:17-46`) to inject `authority_claims`. No behaviour change for non-§11.2 callers — the harvester still reads the live truth.
- Tests: `tests/ports/test_authority.py` (acquire/release/conflict/group/reconcile), `tests/ports/test_authority_harvester.py` (5-source report).

### PR 3 — `slots/manager.py` re-keying on id (delegators intact)

> **Sequence rule:** this PR **must land after P3-slots decomposition** (spec-p3-slots PRs 1-8). Both touch manager.py; landing one before the other conflicts. The decomposition cuts the file to ~2,050 lines first, then this PR makes those edits inside a smaller surface.

- `Slot` (`manager.py:239-283`) gains `slot_id: int`; `as_dict` returns `id` first, then `name`.
- All seven in-memory dicts (`manager.py:370-393, 407, 415`) re-key on `slot_id`.
- `_state_file`/`_config_file` (`manager.py:559-563`) take `slot_id`; paths become id-keyed.
- `load`/`unload`/`start`/`restart`/`swap`/`create`/`delete`/`update_config` resolve `name → slot_id` at the top (via `identity.get_by_name`); internal logic uses `slot_id`.
- `_spawn_locked` (`manager.py:1837-1870`) accepts `port: int` as a parameter (was reading `cfg["port"]`). `_spawn` (`manager.py:1825-1835`) passes the port from the caller.
- All public method signatures keep `name: str`; `_resolve_alias` (`manager.py:547-557`) is preserved as the dispatch-time seam.
- `is_npu_trio_shadow` (`manager.py:307-321`) re-exports the new predicate from `slots/npu/trio.py` (per spec-p3-slots PR 4).
- `reconcile_npu_trio_slots` (`manager.py:2254-2393`) moves to `slots/npu/trio.py` (PR 4 of decomposition); it now operates on `slot_link` rows.
- **Port allocation does NOT route through PortAuthority yet** — the TOML `port` field is still authoritative; PR 5 flips it.
- Tests: all `tests/slots/` keep passing (delegator-based re-export from spec-p3-slots §5 covers the symbol-level imports); new `tests/slots/test_id_keying.py` covers re-keying + alias coexistence.

### PR 4 — One-shot migration M5 (file:line renames + `slot` row population)

- Add `migrate.py` step `M5_slot_id_keying`: walks `/etc/hal0/slots/` + `/var/lib/hal0/slots/`, populates `slot`, rewrites paths under id, restarts units (see §3.1). Idempotent (re-runnable on partial state).
- Systemd unit template (`providers/container.py:13,394,408,467`) parameterized on `slot_id`, not `slot_name`. The four `container_name = f"hal0-slot-{slot_name}"` calls change to `f"hal0-slot-{slot_id}"`.
- `journald` `SyslogIdentifier` parameterised similarly.
- Boot smoke: every slot TOML has `id` field; every state.json under id-based path; `systemctl list-units hal0-slot@*` shows id-based names only; container names match.
- Tests: `tests/migration/test_slot_id_keying.py` — feeds the migrator a fixture of N TOMLs + state.json + active containers, asserts all surfaces end up id-keyed.

### PR 5 — PortAuthority wired into create/edit/delete

> **Sequence rule:** this PR lands **after** §11.2's authority module is in `ports/authority.py` (PR 2) and the manager's id-keying is complete (PR 3). It is the flip.

- `SlotManager.create` (`manager.py:1900-2010`) → after `identity.create(...)` returns the new `slot_id`, call `port_authority.acquire(slot_id, preferred=cfg.get('port'), coresident_group=...)`. The granted port writes the TOML `port` field. The TOML `port` becomes **read-only mirror of the claim**.
- `SlotManager.update_config` (`manager.py:2047-2182`) → if `updates['port']` is present, call `port_authority.reallocate(slot_id, preferred=updates['port'])`; rewrite the TOML from the granted value. If a `port` change is requested and the slot is currently loaded, the slot is reloaded (`_spawn_locked` with the new port).
- `SlotManager.delete` (`manager.py:2012-2045`) → `port_authority.release(slot_id)` first; cascades via `ON DELETE SET NULL`.
- `SlotManager.add_slot` (`manager.py:1749-1804`) → drop the `port=8081` default; let `port_authority.acquire` pick.
- `SlotManager._spawn_locked` (`manager.py:1837-1870`) → `port` argument is now mandatory (was `int(cfg.get("port", 0))`).
- NPU trio reconcile (`slots/npu/trio.py`) → trio shadows have `port=NULL` in their TOML after the migration; the carve-out (§2.4 option b) has the trio's port = anchor's port. Reconcile ensures `slot_link(parent=anchor, child=shadow, kind='served_by')` exists + the shadow's `coresident_group` matches.
- Startup reconcile: `port_authority.reconcile_listeners()` runs at lifespan start (matches today's `reconcile_container_upstreams` timing at `api/__init__.py:948`); ports held by orphan listeners surface as conflicts in `/api/ports`.
- Tests: `tests/slots/test_authority_integration.py` — full create→load→unload→delete cycle asserts port_claim transitions; `test_double_claim_rejected` (two parallel `create()` for the same free port → exactly one wins, the other retries); `test_release_then_reacquire_same_port` (delete slot X, create slot Y, port may be reused).

### PR 6 — API + UI surface

- `GET /api/slots` (`api/routes/slots.py`, lookup `sm.list()`) — enriched with `id`; UI iterates the new shape.
- `POST /api/slots/{name}/rename` — new route; `identity.rename(slot_id, new_name)`. No state churn.
- `PATCH /api/slots/{name}` with `port` body field — routes through `port_authority.reallocate`.
- UI SlotCard (`ui/src/dash/slot-modals.jsx` ~2,190 lines, also flagged in plan §1) — add `id` chip + rename input.
- Backward-compat: `GET /api/slots/by-name/{name}` is the canonical name-keyed path; legacy `GET /api/slots/{name}` returns the same payload as `/by-name/{name}`. Both coexist for one release; `/by-name/` is the doc'd surface going forward.

### Delegators to keep (per spec-p3-slots §5)

These **must survive** on `SlotManager` as one-line `slot_id → name` resolution wrappers, or the lifespan + dispatcher break:

- `reconcile_unconfigured_slots` (`manager.py:2184`, lifespan `:903`)
- `reconcile_npu_trio_slots` (`manager.py:2254`, lifespan `:911`)
- `start_idle_monitor` (`manager.py:3008`, lifespan `:921`)
- `reconcile_container_upstreams` (`manager.py:475`, lifespan `:948`)
- `arbiter` (`manager.py:2957`, lifespan `:1182`)
- `container_readiness_check` (`manager.py:1064`, `dispatcher/router.py:924`)
- `compute_config_drift` (`manager.py:1465`, `api/routes/updater.py:889`) — optional; the spec-p3-slots §6 drift-delete investigation may retire this entirely
- `LoadedSlot` (`manager.py:286-305`) — stays in `slots/routing.py` (spec-p3-slots PR 3); gains `slot_id` alongside `name`

### Module-level re-exports (no caller breakage)

`manager.py:4135-4146` (`__all__`) gains: `SlotIdentityStore`, `PortAuthority`. Internal call sites using `from hal0.slots.manager import X` keep working.

---

## 5. Cross-lane sequencing — the build DAG

Per `hal0-rework-plan.md` §23.4:

```
S8 db/ foundation (connection+migrate+schema_migrations)  [ML-1, FIRST]
  ├─ §11.1 slot (003_slots.sql) + §11.2 port_claim (004_port_claim.sql)
  │     └─REQUIRE→ P3-slots decomposition (cuts manager.py to ~2,050 lines)
  │                  └─REQUIRE→ §11.1 manager.py re-keying (PR 3 above)
  │                                └─REQUIRE→ §11.2 PortAuthority wiring (PR 5)
  └─ ML-2 fileset, ML-3 store, §13.3 metrics — independent

P3-quadlet (providers.container unit rendering + expected_argv) — independent of this spec
P3-brain, P3-perms, §7.4 hermes — independent
KB-1 auth — gates D2 metrics-auth, §21.11 exposure-CI, NOT this spec
```

**Critical path:**
1. ML-1 `db/` foundation merged (PR 1 of `spec-ml1-sqlite.final.md`).
2. P3-slots decomposition (8 PRs of `spec-p3-slots.final.md`) lands first — manager.py must be small before §11.1 edits inside it.
3. This spec's PR 1 (`003_slots.sql` + `identity.py`) — independent of P3-slots.
4. This spec's PR 2 (`004_port_claim.sql` + `authority.py`) — independent of P3-slots; can run in parallel.
5. This spec's PR 3-6 — require P3-slots decomposition complete.

**Independent (parallelisable):** P3-quadlet, P3-brain, P3-perms, §7.4 hermes, KB-1, P2-config migration window.

---

## 6. Capped verification — what's required to merge

Per spec-p3-slots §8 (test gates) + ML-1 verification rules. Cap the test surface — don't ship the world.

| Layer | Required green | Notes |
|---|---|---|
| Unit | `tests/ports/test_authority.py` (NEW, ~12 tests: acquire, release, double-claim, conflict, group carve-out, reconcile_listeners, idempotent re-seed); `tests/slots/test_id_keying.py` (NEW, ~8 tests: name→id resolution, alias coexistence, rename is pure); `tests/migration/test_slot_id_keying.py` (NEW, ~4 tests: full file:line migration + idempotent re-run + crash-mid-state rollforward) | New modules only |
| Existing | `tests/slots/test_manager.py`, `tests/slots/test_npu_trio_shadow.py`, `tests/slots/test_npu_exclusivity.py`, `tests/slots/test_default_uniqueness.py`, `tests/slots/test_device_profile_coherence.py`, `tests/slots/test_pressure_eviction.py`, `tests/slots/test_adopted_slot_eviction.py`, `tests/slots/test_pulling_serving_idle.py`, `tests/slots/test_health_probe_cfg.py`, `tests/slots/test_npu_trio_reconcile.py`, `tests/slots/test_model_fallback.py`, `tests/slots/test_model_preferred_profile.py`, `tests/slots/test_mtp_defuse.py`, `tests/registry/test_store.py`, `tests/registry/test_store_concurrency.py` | Must remain green through every step (delegator-based re-exports make this work without per-test edits) |
| API integration | `tests/api/test_slot_id_api.py` (NEW: `POST /api/slots` → response carries `id`; `POST /api/slots/{name}/rename` round-trip; `PATCH /api/slots/{name}` with `port` field reallocates; `GET /api/ports` returns the 5-source report) | |
| Concurrency | `test_double_claim_rejected`: N=16 parallel `create()` calls for the same free port → exactly 1 wins, N-1 retry and land on distinct ports. `test_release_then_reacquire_same_port` | Direct test of `uq_port_claim_live` |
| Migration | `test_migration_idempotent`: run the migrator 3x on a fixture of 8 slots → final state byte-identical to single-run. `test_migration_crash_mid_state`: kill the migrator between steps 2 and 3 of §3.1; rerun → consistent state | |
| Manual smoke (boots on lxc105 or the fresh `halo` LXC per §12) | After PR 4 + PR 5: every TOML has `id`; every state.json under id-based path; `systemctl list-units hal0-slot@*` shows id-based names; `GET /api/ports` shows `authority_claims` rows aligned with the live truth; rename a slot in the UI, confirm no state churn + no port churn. | Single ad-hoc check, no automated gate |

**Cap rationale:** PR 3 is the largest (re-keys ~30 methods in manager.py). It depends on every existing slots test staying green — that is the regression gate. PRs 4-5 add ~10 new tests total; PR 6 is UI-only and not gate-critical. The drift-delete decision (spec-p3-slots §6) is **independent** and remains open.

---

## 7. Risks + mitigations

1. **Migration crash mid-§3.1 leaves half-migrated filesystem.** Mitigation: idempotent migrator + presence-of-id in TOML as the "done" marker + a single `BEGIN IMMEDIATE` per slot. Crash test in §6 is the gate.
2. **Port-claim double-insert race on first boot after migration.** Mitigation: `uq_port_claim_live` partial unique index raises `IntegrityError`; the migrator catches + logs the conflicting slot pair + aborts (does not auto-reassign). Operator picks the port for one slot, re-runs the seeding.
3. **`_register_container_upstream` keeps using `slot.name`** (for URL stability — the dispatcher caches the slot name as the dispatch key, `dispatcher/router.py:495-525`). Renaming a slot would change the URL path. Mitigation: document the dispatcher-name contract in `upstreams/registry.py:480` (`from_slot(slot_name)`); UI shows "rename = change label, URLs unchanged" on the rename modal.
4. **`Slot.as_dict()` shape change** (`manager.py:273-283`) is observable to every API consumer. Mitigation: `id` added as an extra field; existing `name`/`state`/`port`/`model_id`/`backend`/`metadata`/`last_used_at` keys unchanged. UI consumes `id` opportunistically (graceful if absent — pre-§11.1 boxes still render).
5. **`is_npu_trio_shadow` re-keying** (predicate switches from `cfg.get("device")=="npu" and cfg.get("type") in (...)` to `slot_row` shape). Called in 5 core sites (`load`, `status`, `compute_config_drift`, `reconcile_container_upstreams`, `_probe_health`) plus tests. Mitigation: `slots/npu/trio.py` exports both predicates (legacy cfg-based + new slot_row-based); `manager.py` re-exports; tests that monkey-patch the cfg-based one keep working.
6. **`capabilities.toml` references slot by name.** P2-config collapses this (separate spec); do not migrate here. If §11.1 ships before P2-config's capabilities migration, the capabilities side reads the TOML name (still present) — safe. Document the dependency in the PR 4 commit message.
7. **`on_change` + `Slot` snapshot ordering during rename.** The SSE stream broadcasts `SlotStateRecord` keyed by `slot_id`; subscribers key on `name` today. Mitigation: the broadcast record carries both `slot_id` and `name` (post-rename: name changes, slot_id doesn't); UI subscribers re-key on `slot_id`. Test `test_rename_sse_frame_carries_new_name`.
8. **Drift-delete decision (spec-p3-slots §6) interacts.** If drift is deleted, `_CONFIG_DRIFT_KEYS` comparisons on `port` go away — and port-claim becomes the only authority on `port`. If drift survives, `--port` stays in `_CONFIG_DRIFT_KEYS` and the resolver compares `port_authority.held_by(slot_id)` (the granted value) to the unit's rendered arg (read from `_cfg_port`-shaped lookup). P3-quadlet owner signs off. Document the dependency in PR 5.

---

## 8. Files referenced

- Target: `/home/mint/hal0/src/hal0/slots/manager.py` (4,146 lines, re-keyed + delegators)
- New: `/home/mint/hal0/src/hal0/slots/identity.py`
- New: `/home/mint/hal0/src/hal0/ports/authority.py`
- New: `/home/mint/hal0/src/hal0/db/migrations/{003_slots,004_port_claim}.sql`
- Modified: `/home/mint/hal0/src/hal0/db/migrate.py` (adds 2 steps)
- Modified: `/home/mint/hal0/src/hal0/ports.py` (harvester gains `authority_claims()`)
- Modified: `/home/mint/hal0/src/hal0/api/routes/ports.py` (5-source report)
- Modified: `/home/mint/hal0/src/hal0/api/routes/slots.py` (new `/rename` route; `id` in responses)
- Modified: `/home/mint/hal0/src/hal0/providers/container.py` (lines 13, 394, 408, 467, 1417, 1445 — `slot_name` → `slot_id` in unit template + container name + SyslogIdentifier)
- Modified: `/home/mint/hal0/src/hal0/slots/npu/trio.py` (post-decomposition; operates on `slot_link`)
- Callers (delegators preserved): `/home/mint/hal0/src/hal0/api/__init__.py` (:903, :911, :921, :948, :1182); `/home/mint/hal0/src/hal0/dispatcher/router.py` (:924); `/home/mint/hal0/src/hal0/api/routes/updater.py` (:889 — `compute_config_drift`, conditional); `/home/mint/hal0/src/hal0/upstreams/registry.py` (:480)
- UI: `/home/mint/hal0/ui/src/dash/slot-modals.jsx` (id chip + rename input)
- Tests: `/home/mint/hal0/tests/ports/test_authority.py` (NEW), `/home/mint/hal0/tests/slots/test_id_keying.py` (NEW), `/home/mint/hal0/tests/migration/test_slot_id_keying.py` (NEW), `/home/mint/hal0/tests/api/test_slot_id_api.py` (NEW); existing 35 files under `/home/mint/hal0/tests/slots/` regression-gated
- Plan: `/home/mint/hal0-rework-plan.md` (§11.1 L675-689, §11.2 L691-700, §23.2 S3 L1592 + S8 L1598, §23.4 L1620-1647)
- Companion specs: `/home/mint/hal0-specs/spec-ml1-sqlite.final.md` (S8 substrate), `/home/mint/hal0-specs/spec-p3-slots.final.md` (manager.py decomposition — **must land first**), `/home/mint/hal0-specs/spec-p2-config.final.md` (capabilities collapse — independent), `/home/mint/hal0-specs/spec-p3-quadlet.final.md` (when it ships; drift-delete decision)
