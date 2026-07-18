I have everything needed. Compiling the implementer-ready spec.

---

# ML-1 — SQLite Registry Pilot: Implementation Spec

**Repo:** `/home/mint/hal0` (branch `rework/descar`, verified). Plan: `/home/mint/hal0-rework-plan.md` §7.5, §8.1–8.4, §7.1a–e. Tracker row: `hal0-rework-tracker.md:85` (ML-1) + `:67` (P1-migfw — repurpose the no-op config-migration framework here).

Everything below is verified against the code as it stands today.

---

## PART 0 — Current registry, mapped (file:line)

### 0.1 `ModelRegistry` — `src/hal0/registry/store.py`

TOML-backed catalog. One file `registry.toml` keyed by model id under `paths.registry_dir()` → `/var/lib/hal0/registry/registry.toml` (`store.py:142`, `:191-193`; `config/paths.py:93-95`).

Machinery that SQLite deletes/replaces:
- **Atomic write** `_atomic_write` (`store.py:288-323`): `tempfile.mkstemp` + `tomli_w.dump` + `fsync` + `os.replace` + `_fsync_dir` (`:122-136`).
- **Cross-process lock** `registry_write_lock` / `_process_lock` (`store.py:84-116`, `:329-336`): `fcntl.flock(LOCK_EX)` on stable sidecar `registry.toml.lock` (`:54`). Held across every mutator's read-modify-write.
- **In-process lock**: `threading.RLock` (`store.py:177`).
- **mtime cache**: `_stat_mtime` / `_read_locked` / `_ensure_fresh` / `_invalidate` (`store.py:197-284`, `:325-327`). Re-parses when file mtime advances; keeps stale cache on parse error (`:221-234`).
- **on_change hook** class attr (`store.py:163`) + `_notify_change` (`:365-379`) — fired after every successful add/update/remove, outside the lock, best-effort. **No src assignment site exists today** (grep found none) but it is part of the public surface and must be preserved.

Typed errors (subclass `hal0.errors.Hal0Error`, reshaped by API middleware): `RegistryError` (500, `store.py:60`), `ModelNotFound` (404, `:67`), `ModelAlreadyExists` (409, `:74`).

### 0.2 Public interface — every method (the drop-in contract)

| Member | Signature | Behavior | file:line |
|---|---|---|---|
| `__init__` | `(registry_dir: str\|Path\|None = None)` | override dir or resolve `paths.registry_dir()` lazily | `store.py:165` |
| `registry_dir` | property `-> Path` | override or `paths.registry_dir()` | `:183` |
| `registry_file` | property `-> Path` | `registry_dir / "registry.toml"` | `:191` |
| `list` | `() -> list[Model]` | all models, sorted by id | `:340` |
| `get` | `(model_id: str) -> Model` | raises `ModelNotFound` | `:345` |
| `has` | `(model_id: str) -> bool` | | `:359` |
| `add` | `(model: Model) -> None` | raises `ModelAlreadyExists`; fires `on_change` | `:381` |
| `remove` | `(model_id: str) -> bool` | `False` if absent; fires `on_change` | `:404` |
| `update` | `(model_id: str, updates: dict) -> Model` | flat field merge, `id` immutable; raises `ModelNotFound`/`RegistryError`; fires `on_change` | `:421` |
| `route_for` | `(model_id: str) -> str\|None` | reads `metadata["upstream_url"]` | `:462` |
| `reload` | `() -> None` | invalidate cache | `:486` |
| `on_change` | class attr `Callable[[],None]\|None` | post-mutation hook | `:163` |

Module-level exports also relied on (`store.py:534`): `registry_write_lock` (used by CLI `registry_commands.py:56,158`), `model_to_toml_dict` (`:492`), and the three error classes. Re-exported from `registry/__init__.py:25-38`.

### 0.3 `Model` / `ModelDefaults` — `src/hal0/registry/model.py`

`Model` (`model.py:68`), flat pydantic v2, `populate_by_name`, `str_strip_whitespace`:
- Required: `id` (`:82`), `path` (`:89`) — both non-empty-validated (`:177-189`).
- Scalars: `name=""` (`:84`), `size_bytes=0` (`:97`), `quant: str|None` (`:102`), `license="unknown"` (`:114`), `hf_repo=""` (`:127`), `hf_filename=""` (`:132`), `mmproj: str|None` (`:151`).
- Lists: `capabilities: list[str]` (`:119`), `tags: list[str]` (`:137`), `backends: list[str]` (`:142`).
- `defaults: ModelDefaults|None` (`:161`).
- `metadata: dict[str,Any]` (`:168`) — reserved keys `context_length` (int) and `upstream_url` (str).

`ModelDefaults` (`model.py:22`), all optional: `context_size` (`:32`), `n_gpu_layers` (`:36`), `rope_freq_base` (deprecated, `:40`), `extra_args` (`:48`), `chat_template` (`:53`), `profile` (`:57`).

`_derive_ns` (`model.py:205`) classifies blessed/pulled by path shape — pure function of `model.path`, unaffected by storage backend.

### 0.4 All callers of `ModelRegistry` across `src` (method-call census)

Aggregate method usage: `get` ×26, `has` ×15, `add` ×7, `update` ×5, `list` ×5, `remove` ×3. `route_for`/`reload`/`on_change` have **zero live call sites in src** but are tested (§0.6) and part of the contract.

Construction sites (all bare `ModelRegistry()` or `registry_dir=`):
- `api/__init__.py:791` — lifespan singleton on `app.state` (the canonical instance).
- `cli/setup_command.py:311,326`; `cli/capabilities_commands.py:243`; `updater/updater.py:1114`; `providers/container.py:1608`; `slots/manager.py:2543,2771,2840,3440` — ad-hoc `ModelRegistry()` (rely on `paths.registry_dir()` resolution).
- DI: `api/deps.py:24-68` (`get_registry`, `RegistryDep`); `api/routes/stacks.py:136`.
- Consumers: `capabilities/orchestrator.py:57,178`; `capabilities/catalog.py` (many, `:492-937`); `dispatcher/router.py:62,433,1261`; `registry/discover.py:26,259,309,343` (`register_candidate`, `backfill_coordless`, `scan_and_register`); `api/routes/models.py` (CRUD endpoints — `add`/`update`/`remove`/`get`, `:643-1619`); `api/routes/slots.py:1274,1336`.

**Key point:** because construction is `ModelRegistry()` / `ModelRegistry(registry_dir=...)` and access is purely the §0.2 methods, a drop-in swap needs **zero caller edits** — see §C for the exact swap mechanism.

### 0.5 CLI surface — `src/hal0/cli/registry_commands.py`

Typer group `registry` wired at `cli/main.py:78`. Callback-only today (the `import` verb moved to `hal0 model import-backup`). This is where `hal0 registry export` and `hal0 registry import` (SQLite one-shot) land. Uses `registry_write_lock` (`:56,158`).

### 0.6 Existing tests (drop-in must keep green)

`tests/registry/`: `test_store.py` (34 tests — full CRUD, atomicity, mtime cache `test_invalidates_when_file_mtime_advances:175`, `test_reload_invalidates_cache:195`, corrupt-file `:204`, route_for `:230-238`, dir fsync `:358-377`), `test_store_concurrency.py` (cross-process flock: `test_two_instances_no_lost_update:62`, `test_multiprocessing_no_lost_update:135`, `test_registry_import_does_not_drop_concurrent_add:173`), `test_store_on_change.py`, `test_schema_migration.py` (legacy-entry round-trip). Plus `test_discover.py`, `test_pull.py`, `test_curated_image_models.py`, `test_update_check.py`. All construct `ModelRegistry(registry_dir=tmp_path/"registry")` (e.g. `test_schema_migration.py:26`).

### 0.7 House SQLite pattern already in-repo (match it)

`src/hal0/activity/__init__.py` is the reference: stdlib `sqlite3`, per-call `_connect()` (`:167-172`) doing `sqlite3.connect(path, timeout=5.0)` + `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000`, schema via `CREATE TABLE IF NOT EXISTS` + `PRAGMA user_version` (`:87,177-179`). Also `bench/store.py`, `memory/migrate.py`. The new `db/` foundation should generalize this (adds `foreign_keys=ON`, `synchronous=NORMAL`, and a real `schema_migrations` table per plan §8.1).

---

## PART (a) — `src/hal0/db/` foundation

### `src/hal0/db/connection.py`

Stdlib `sqlite3`. One connection per request/task (WAL makes this safe — plan §8.1). No global singleton connection.

```python
DB_FILENAME = "hal0.db"

def db_path() -> Path:
    # /var/lib/hal0/hal0.db (Runtime tier — survives update/uninstall --keep-data)
    return paths.data_root() / DB_FILENAME        # paths.data_root() → config/paths.py:60

@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path or db_path(), timeout=5.0, isolation_level=None)  # autocommit; explicit BEGIN in tx()
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")        # required for model_file/model_backend ON DELETE CASCADE
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        conn.close()

@contextmanager
def tx(conn) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")               # write-intent lock up front → replaces registry_write_lock flock
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
```

Notes for implementer:
- Add `db_path()` helper to `config/paths.py` (mirrors `activity_db` at `paths.py:126`) OR keep it in `connection.py`; prefer `paths.py` for the HAL0_HOME-rooted-dev-install override to work in tests (same pattern as `registry_dir()`).
- `foreign_keys=ON` is **per-connection** in SQLite — must be set on every `connect()`, not once. This is the top footgun; the CASCADE deletes in §(b) silently no-op without it.
- `BEGIN IMMEDIATE` in `tx()` acquires the write lock immediately, giving the same cross-process serialization the old `fcntl.flock` gave — and cross-thread safety SQLite provides natively. **The `threading.RLock`, the sidecar `.lock` file, and `registry_write_lock` all disappear** (but keep a `registry_write_lock` shim — see §C.4 — because CLI `registry_commands.py` imports it).

### `src/hal0/db/migrate.py`

Forward-only runner (plan §8.1). This is the concrete job the no-op config-migration framework (tracker P1-migfw) was pretending to do.

```python
def _ensure_migrations_table(conn): conn.execute(
    "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")

def applied_versions(conn) -> set[int]: ...

def migrate(conn, migrations_dir: Path | None = None) -> list[int]:
    # 1. ensure schema_migrations
    # 2. glob db/migrations/NNN_*.sql, sort by NNN
    # 3. for each version not in applied_versions: exec .sql inside tx(), insert (version, utcnow)
    # returns newly applied versions
```

- Migrations dir ships **inside the package**: `src/hal0/db/migrations/` (loaded via `importlib.resources`, not a runtime `/var/lib` path).
- `migrate()` is called **once on DB open at startup** (add to `api/__init__.py` lifespan, right where `ModelRegistry()` is built, `api/__init__.py:791`) and is **idempotent** — safe to run every boot.
- Forward-only: no down-migrations. Each `NNN_*.sql` is one atomic transaction.

### `src/hal0/db/migrations/001_registry.sql`

The DDL in §(b).

### `src/hal0/db/repository.py` (new — the pydantic⇄row seam)

Row↔`Model` mapping lives here so `SqliteModelRegistry` stays thin. Returns/accepts existing pydantic `Model` (plan §8.1: "thin repository layer returns/accepts the existing pydantic models, so validation stays where it is").

### Backup note (plan §8.1)

Document in `db/connection.py` docstring + operator docs: atomic snapshot via
```sql
VACUUM INTO '/var/lib/hal0/backups/hal0.db';   -- or conn.backup(dst)
```
One file replaces N concurrently-written JSON files → directly fixes the PBS/FUSE backup-hang class (memory: `hal0-backup-fuse-hangs`, `pbs-datastore-truenas-tank`). Wire a `hal0 db backup [--out PATH]` CLI verb (optional for ML-1; note it).

---

## PART (b) — Registry pilot SQL schema (`001_registry.sql`)

Extends plan §8.2 to be **lossless** against today's `Model`/`ModelDefaults` (§8.2 as written drops `capabilities`, `tags`, `name`, `size_bytes`, `quant`, `license`, `hf_repo`, `hf_filename`, and `ModelDefaults.context_size`/`rope_freq_base` — called out below).

```sql
-- 001_registry.sql  (schema version 1)

CREATE TABLE model (
  id               TEXT PRIMARY KEY,        -- Model.id
  -- §7.1 metadata record --------------------------------------------------
  source_repo      TEXT,                    -- Model.hf_repo (§7.1c)
  revision         TEXT,                    -- resolved commit sha (§7.1c update detection) — NEW field
  path             TEXT NOT NULL,           -- Model.path; entry point / shard-1
  preferred_runner TEXT,                    -- key into RUNNER_IMAGES (§7.1b) — NEW field
  mmproj           TEXT,                    -- Model.mmproj, nullable
  architecture     TEXT,                    -- FAMILY_DEFAULTS keying (§7.1a) — NEW field
  context_length   INTEGER,                 -- Model.metadata["context_length"]
  mtp              INTEGER,                  -- tri-state capability flag (§7.1a): NULL/0/1 — NEW field
  jinja            INTEGER,                  -- tri-state capability flag (§7.1a): NULL/0/1 — NEW field
  -- existing Model scalars (NOT in §8.2 draft — added for lossless round-trip)
  name             TEXT NOT NULL DEFAULT '',
  size_bytes       INTEGER NOT NULL DEFAULT 0,
  quant            TEXT,
  license          TEXT NOT NULL DEFAULT 'unknown',
  hf_filename      TEXT NOT NULL DEFAULT '',
  -- ModelDefaults folded onto the row -------------------------------------
  profile          TEXT,                    -- ModelDefaults.profile
  extra_args       TEXT,                    -- ModelDefaults.extra_args
  n_gpu_layers     INTEGER,                 -- ModelDefaults.n_gpu_layers
  chat_template    TEXT,                    -- ModelDefaults.chat_template
  context_size     INTEGER,                 -- ModelDefaults.context_size   (missing from §8.2 draft)
  rope_freq_base   REAL,                    -- ModelDefaults.rope_freq_base  (missing from §8.2 draft; deprecated but round-trip)
  -- lists too small to normalize for the pilot (§7.1d does the modality split later)
  capabilities     TEXT,                    -- JSON array (Model.capabilities)
  tags             TEXT,                    -- JSON array (Model.tags)
  -- bookkeeping -----------------------------------------------------------
  sha256           TEXT,
  pulled_at        TEXT,
  created_at       TEXT,
  updated_at       TEXT,
  extra            TEXT                      -- JSON: Model.metadata minus context_length/upstream_url handled explicitly
);

CREATE TABLE model_file (                     -- §7.1c file-SET abstraction (write empty for pilot import)
  model_id     TEXT NOT NULL REFERENCES model(id) ON DELETE CASCADE,
  rel          TEXT NOT NULL,                 -- path within the repo/snapshot
  dest         TEXT,                          -- resolved on-disk dest
  size_bytes   INTEGER,
  sha256       TEXT,
  lfs          INTEGER,                       -- 0/1
  role         TEXT,                          -- model|shard|mmproj|tokenizer|config
  shard_index  INTEGER,                       -- ordered; shard_index=1 = entry point
  PRIMARY KEY (model_id, rel)
);

CREATE TABLE model_backend (                  -- replaces Model.backends JSON list; queryable
  model_id TEXT NOT NULL REFERENCES model(id) ON DELETE CASCADE,
  backend  TEXT NOT NULL,                     -- rocm|vulkan|flm|kokoro|comfyui|cpu|cuda|moonshine
  PRIMARY KEY (model_id, backend)
);

CREATE INDEX idx_model_file_role    ON model_file(model_id, role);
CREATE INDEX idx_model_backend_be   ON model_backend(backend);
```

**Field-mapping decisions (implementer must honor these for round-trip):**

1. `metadata` split on write: `context_length` → `model.context_length` column; `upstream_url` stays inside `extra` JSON (so `route_for` reads `json.loads(extra).get("upstream_url")`); all other metadata keys → `extra`. On read, reconstruct `metadata = {**json.loads(extra or "{}")}` then set `metadata["context_length"]` if the column is non-null. Preserves the two reserved keys exactly (`model.py:168-175`).
2. `capabilities`/`tags`: stored as JSON text columns for the pilot (small, and §7.1d hasn't split modality yet). Note in a comment that §7.1d may promote these to child tables.
3. `backends` → `model_backend` rows (per §8.2). Empty list = no rows.
4. `defaults` is `None` when **all** ModelDefaults columns are NULL; otherwise reconstruct `ModelDefaults(...)` from the columns. This mirrors `_model_to_toml`'s "collapse to no key when nothing set" (`store.py:521-525`).
5. `mtp`/`jinja` are tri-state: SQLite `NULL` → Python `None`, `0`/`1` → bool. (These are new fields not yet on `Model` — see §(e) note on adding them alongside §7.1a. For the pilot they can be written from `metadata`/`defaults` if present, else NULL.)
6. Timestamps: ISO-8601 UTC strings (`created_at` set on insert, `updated_at` on every write) — matches activity/bench string-time convention.

---

## PART (c) — `SqliteModelRegistry` (drop-in behind `ModelRegistry`)

New file `src/hal0/registry/sqlite_store.py`. Implements the **complete §0.2 surface** so no caller changes.

**Must implement (exhaustive):**

| Member | Implementation sketch |
|---|---|
| `__init__(self, registry_dir=None)` | Keep the `registry_dir` param **for signature compatibility** (callers/tests pass it, `test_schema_migration.py:26`). Derive `db_path` from it when given (dev/test isolation): `registry_dir/../hal0.db` or accept a `db_path=` kwarg; when `None`, use `db.connection.db_path()`. Runs `migrate()` on first use. Keep `on_change=None` class attr. |
| `registry_dir` property | return the resolved dir (compat; tests read it) |
| `registry_file` property | return the TOML **export** path `registry_dir/"registry.toml"` (now a derived artifact, not the source) — keeps `test_store.py:72 test_add_persists_to_disk` semantics only if you also export; otherwise those specific disk-assertion tests move to the export test (see §e test impact) |
| `list()` | `SELECT * FROM model ORDER BY id` → `[Model]` via repository |
| `get(id)` | `SELECT ... WHERE id=?`; raise `ModelNotFound(details={"model_id":id})` if no row |
| `has(id)` | `SELECT 1 FROM model WHERE id=? LIMIT 1` |
| `add(model)` | `with tx(conn):` `INSERT`; on `sqlite3.IntegrityError` (PK) → `ModelAlreadyExists`; insert `model_backend`/`model_file` rows; then `_notify_change()` outside tx |
| `remove(id)` | `with tx: DELETE FROM model WHERE id=?`; `rowcount` → bool; CASCADE handles children; `_notify_change()` if removed |
| `update(id, updates)` | validate `updates` is dict (`RegistryError` else, `store.py:433`); `SELECT` existing → `model_dump` → merge (drop `id`) → `Model.model_validate` (raise `RegistryError` on failure, `store.py:449-455`) → `UPDATE` row + replace child rows in one tx; `_notify_change()`; return new Model |
| `route_for(id)` | `get()` then `metadata.get("upstream_url")` (unchanged logic, `store.py:462-484`) |
| `reload()` | no-op (SQLite has no mtime cache) — keep as method so `test_reload_invalidates_cache` and any caller stay valid |
| `on_change` / `_notify_change()` | identical best-effort semantics (`store.py:365-379`): fire after commit, outside the tx, swallow+log exceptions |

**Behavioral parity checklist (from the tests in §0.6):**
- `add` duplicate → `ModelAlreadyExists` ✓ (IntegrityError map).
- `update` immutable `id`, invalid-merge → `RegistryError` ✓ (reuse the exact merge code — factor `_merge_update` out of `store.py:446-456` into a shared helper both stores call).
- `route_for` three cases ✓.
- Concurrency (`test_store_concurrency.py`): SQLite WAL + `BEGIN IMMEDIATE` gives the same "no lost update across processes" guarantee the flock gave. These tests should pass **as-is** against SQLite (two writers serialize on the write lock). Verify — this is the one place to test adversarially.
- Corrupt-file / mtime tests (`test_store.py:175,195,204`) are TOML-file-specific → they move/retire (see §e).

**Concrete deletions vs the TOML store** (plan §7.5 "deletes the hand-rolled transaction log, the slot_write_lock, and the mtime cache"): no `_atomic_write`, no `_read_locked`/`_ensure_fresh`/`_stat_mtime`/`_invalidate`, no `RLock`, no sidecar flock inside the store.

---

## PART (d) — Import, export, cutover

### One-shot import (idempotent) — `src/hal0/registry/import_toml.py`

```python
def import_toml_to_sqlite(*, registry_file: Path | None = None, db: Path | None = None) -> ImportReport:
    # 1. migrate(db)                    # ensure schema
    # 2. parse registry.toml via the EXISTING read path (reuse store._read_locked logic
    #    or tomllib + Model.model_validate — same validation, so malformed rows are skipped+logged)
    # 3. for each Model: INSERT OR IGNORE into model (+ children)   → idempotent / re-runnable
    #    (use INSERT OR IGNORE, not REPLACE, so a re-run never clobbers SQLite edits made post-cutover)
    # 4. return counts {imported, skipped_existing, skipped_invalid}
```
- Runs automatically **on first boot** when `model` table is empty AND `registry.toml` exists (call from lifespan after `migrate()`), and also exposed as `hal0 registry import` (explicit/idempotent). Plan §8.3 step 3.
- Reuses `Model.model_validate` so import inherits the same "skip invalid, keep going" behavior as `_read_locked` (`store.py:247-255`).

### Export (SQLite → TOML) — `hal0 registry export`

New verb in `cli/registry_commands.py` (group already wired, `main.py:78`):
```
hal0 registry export [--out /var/lib/hal0/registry/registry.toml]
```
- `SqliteModelRegistry().list()` → reuse **`model_to_toml_dict`** (`store.py:492/503`, the None-stripping serializer) → `{"models": {id: dict}}` → `tomli_w` atomic write (reuse `config.loader.write_toml_atomic`). Plan §8.3 step 3.
- Optionally have `SqliteModelRegistry` fire export via its `on_change` hook so `registry.toml` stays a live read-only mirror for grep/git/debug — matches the existing `on_change`→catalog-regen pattern and keeps `test_add_persists_to_disk` spirit.

### Cutover (plan §8.3 steps 2 & 4–5)

1. Land `db/` + `001_registry.sql` + `SqliteModelRegistry` + import/export.
2. **Swap the name** so callers don't change (§C): in `registry/store.py`, after defining `SqliteModelRegistry`, bind `ModelRegistry = SqliteModelRegistry` (or re-export from `sqlite_store`). Keep the old TOML class as `TomlModelRegistry` for the migration/export path + a fallback flag.
3. First-boot import runs; reads/writes now hit SQLite; `registry.toml` becomes a derived export artifact, not a source of truth.
4. Ship §7.1 columns (`revision`, `preferred_runner`, `mtp`, `jinja`, `architecture`, `model_file` rows) **from day one** — pilot + §7.1 land together so nothing is double-written (plan §8.3 step 5 / §7.1e "must land together").

---

## PART (e) — Files to add / touch, and test impact

### Files to ADD
- `src/hal0/db/__init__.py`
- `src/hal0/db/connection.py` — `connect()`, `tx()`, `db_path()`, backup helper.
- `src/hal0/db/migrate.py` — `schema_migrations` runner.
- `src/hal0/db/repository.py` — Model⇄row mapping (the pydantic seam).
- `src/hal0/db/migrations/001_registry.sql` — §(b) DDL.
- `src/hal0/registry/sqlite_store.py` — `SqliteModelRegistry`.
- `src/hal0/registry/import_toml.py` — one-shot idempotent import.
- Tests: `tests/db/test_connection.py` (pragmas incl. `foreign_keys=ON` per-conn, CASCADE actually fires), `tests/db/test_migrate.py` (idempotent, forward-only, partial-apply), `tests/registry/test_sqlite_store.py` (mirror `test_store.py` CRUD/errors against SQLite), `tests/registry/test_import_export_roundtrip.py`, `tests/registry/test_sqlite_concurrency.py` (port `test_store_concurrency.py`).

### Files to TOUCH
- `src/hal0/config/paths.py` — add `db_path()` (`/var/lib/hal0/hal0.db`, HAL0_HOME-aware, beside `data_root()` `:60` and `activity_db` `:126`).
- `src/hal0/registry/store.py` — factor the `update` merge (`:446-456`) into a shared helper; rename current class `TomlModelRegistry`; **bind `ModelRegistry = SqliteModelRegistry`** (or re-export). Keep `registry_write_lock`, `model_to_toml_dict`, and the three error classes exported unchanged (CLI + out-of-tree callers import them, `registry_commands.py:56`).
- `src/hal0/registry/__init__.py` — export `SqliteModelRegistry` (keep `ModelRegistry` name as the public alias, `:25-38`).
- `src/hal0/cli/registry_commands.py` — add `export` + `import` verbs (group already wired at `main.py:78`).
- `src/hal0/api/__init__.py` — in lifespan (`:791`), call `migrate()` + first-boot import before building the registry singleton.
- `pyproject.toml` — **no new dependency** (stdlib `sqlite3`); optionally trim `tomli_w`/`tomllib` usage stays (still needed for export + config).
- `installer/` / uninstall `--keep-data` — ensure `hal0.db` is in the Runtime tier preserved set (same tier as `activity.db`).

### What STAYS behind the interface (unchanged by design)
- `registry/model.py` (`Model`/`ModelDefaults`/`_derive_ns`) — pydantic validation is the seam; storage swap doesn't touch it. (§7.1 later adds `revision`, `files[]`, `shards[]`, `preferred_runner`, `mtp`, `jinja`, `architecture`, modalities split — those field additions ride with §7.1a–e, columns already present.)
- All 60+ call sites in §0.4 — zero edits (they see the `ModelRegistry` name + §0.2 methods).
- `registry/discover.py`, `capabilities/catalog.py`, `dispatcher/router.py`, `api/routes/models.py` CRUD — untouched.
- The three typed errors (middleware relies on their `code`/`status`).

### Test impact (concrete)
- **Pass as-is** against SQLite (behavior parity): `test_store.py` CRUD/error/route_for/list-sorted/update-immutable-id groups (`:44-238`); `test_store_concurrency.py` (WAL+`BEGIN IMMEDIATE` provides the no-lost-update guarantee) — **verify adversarially**; `test_schema_migration.py` (legacy round-trip → becomes import round-trip); `test_store_on_change.py` (hook semantics preserved).
- **Retire or re-home** (TOML-file-implementation-specific, no SQLite analogue): `test_invalidates_when_file_mtime_advances` (`:175`), `test_reload_invalidates_cache` (`:195` — `reload` is now a no-op; keep a trivial "reload doesn't error" test), `test_corrupt_file_keeps_stale_cache_warns` (`:204`), `test_initial_load_with_corrupt_file_returns_empty` (`:331`), `test_entry_not_a_table_is_skipped` (`:344`), `test_add_fsyncs_registry_dir`/`test_add_survives_dir_fsync_failure` (`:358,377`), `test_add_writes_atomically` (`:85`). Their intent (corruption tolerance, atomicity) moves to import-path tests + relying on SQLite's own atomicity/WAL. `test_add_persists_to_disk` (`:72`) migrates to the export round-trip test (or passes if you wire `on_change`→export).
- Downstream tests that only construct `ModelRegistry(registry_dir=...)` and exercise methods (e.g. `test_pull.py`, `test_discover.py`, `dispatcher/test_router.py`, `slots/test_model_fallback.py`, `capabilities/test_vision_mmproj_autosurface.py`, `stacks/test_*`) — **unaffected** as long as the `registry_dir=` constructor kwarg keeps isolating each test's DB (route `registry_dir` → a per-test `hal0.db`).

### Load-bearing risks to flag to the implementer
1. `PRAGMA foreign_keys=ON` is per-connection — the CASCADE deletes in `model_file`/`model_backend` silently no-op otherwise. Set it in every `connect()`; test it (`tests/db/test_connection.py`).
2. Keep the `registry_dir=` constructor param even though SQLite doesn't use a dir — dozens of tests and CLI/setup code pass it; map it to a per-instance `hal0.db` location for isolation.
3. `route_for` depends on `upstream_url` surviving in `extra` JSON — don't accidentally route it into a column.
4. `on_change` has no src assignment today but is public + tested; preserve it, and it's the natural hook for the TOML mirror-export.
5. `INSERT OR IGNORE` (not `REPLACE`) in import so re-runs never clobber post-cutover SQLite edits (idempotency, plan §8.3 step 3).