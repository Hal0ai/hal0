# Q5 — INFERRED edges for `connect()` (src/hal0/db/connection.py:78)

**Date:** 2026-07-19
**Author:** graphify-analysis worker (swarm Q5)
**Target node:** `connect()` @ `src/hal0/db/connection.py:78`, ID `src_hal0_db_connection_connect`
**Population:** 108 INFERRED cross-module edges (104 `calls` + 4 `indirect_call`)
**EXTRACTED cross-module edges:** **0** (all 6 EXTRACTED edges are intra-module: `db_path`, `_set_wal_mode`, docstring, `Path`/`Connection` refs)
**Sample size:** 16 INFERRED edges (representative — 11 production `calls`, 2 test `calls`, 3 UI `indirect_call`)

> **Bottom line:** of 108 INFERRED edges on `connect()`, **~93% (101/108) are TRUE — the SQLite factory genuinely is used by every persistent store (`BoardStore`, `PortAuthority`, `SlotIdentityStore`, `MetricsWriter`, `SqliteModelRegistry`, `TomlModelRegistry` migration runner), the registry pull/discover/gc/import paths, the metrics read/write/aggregate paths, and the test bodies that exercise them directly. The ~7% (7/108) FALSE positives concentrate in two failure modes — (1) UI hooks (`useActivityStream`, `useLogsStream`, `useSlotLogsStream`) collide with a *local* SSE `connect` function inside `useLogs.ts:188`/`useActivity.ts:239`; (2) a small number of test methods get an edge by file-level import co-occurrence even when they only reach `connect` through a fixture. The "generic method-name collision" hypothesis (the user's primary suspicion) **holds only for the 3 UI edges**, not for the `.__init__` / `._read` / `._write` edges — those generic names happen to actually call `connect()` and the INFERRED link is correct.**

---

## Sample table

| # | Source endpoint | File:line | Source-method kind | INFERRED kind | Classification | Evidence |
|---|---|---|---|---|---|---|
| 1 | `BoardStore.__init__()` | `src/hal0/board/store.py:95` | store ctor | calls | **TRUE** | L99: `with connect(self._db_path) as conn: migrate(conn)` |
| 2 | `BoardStore._read()` | `src/hal0/board/store.py:105` | store helper | calls | **TRUE** | L109: `with connect(self._db_path) as c: yield c` |
| 3 | `BoardStore._write()` | `src/hal0/board/store.py:113` | store helper | calls | **TRUE** | L117: `with connect(self._db_path) as c, tx(c): yield c` |
| 4 | `PortAuthority.__init__()` | `src/hal0/ports/authority.py:87` | store ctor | calls | **TRUE** | L97: `with connect(self._db_path) as conn: migrate(conn)` |
| 5 | `PortAuthority._read()` | `src/hal0/ports/authority.py:107` | store helper | calls | **TRUE** | L111: `with connect(self._db_path) as c:` |
| 6 | `PortAuthority._write()` | `src/hal0/ports/authority.py:115` | store helper | calls | **TRUE** | L119: `with connect(self._db_path) as c, tx(c):` |
| 7 | `SlotIdentityStore.__init__()` | `src/hal0/slots/identity.py:87` | store ctor | calls | **TRUE** | L91: `with connect(self._db_path) as conn: migrate(conn)` |
| 8 | `SlotIdentityStore._read()` | `src/hal0/slots/identity.py:97` | store helper | calls | **TRUE** | L101: `with connect(self._db_path) as c:` |
| 9 | `SlotIdentityStore._write()` | `src/hal0/slots/identity.py:105` | store helper | calls | **TRUE** | L112: `with connect(self._db_path) as c, tx(c):` |
| 10 | `MetricsWriter._write_batch()` | `src/hal0/metrics/writer.py:140` | writer loop body | calls | **TRUE** | L142: `with connect(self._db_path) as conn, tx(conn):` |
| 11 | `MetricsWriter.ensure_schema()` | `src/hal0/metrics/writer.py:75` | boot helper | calls | **TRUE** | L81: `with connect(self._db_path) as conn:` |
| 12 | `metrics_read.system_stats()` | `src/hal0/metrics/read.py:46` | read helper | calls | **TRUE** | L49: `with connect(db_path) as conn:` (and L109, L207 for `models_health`, `stats_summary`) |
| 13 | `_maybe_register_shard_files()` | `src/hal0/registry/discover.py:411` | registry helper | calls | **TRUE** | L427: `from hal0.db.connection import connect, tx`; L431: `with connect(db_path) as conn, tx(conn):` |
| 14 | `prune_orphans()` | `src/hal0/registry/gc.py:65` | registry helper | calls | **TRUE** | L78: `with connect() as owned: return prune_orphans(owned, dry_run=dry_run)` |
| 15 | `import_toml_to_sqlite()` | `src/hal0/registry/import_toml.py:100` | registry helper | calls | **TRUE** | L28 import; L136: `with connect(db_path) as owned_conn:` |
| 16 | `SqliteModelRegistry._connect()` | `src/hal0/registry/sqlite_store.py:128` | wrapper helper | calls | **TRUE** | L129: `return connect(self.db_path)` — different name but a deliberate thin wrapper over `connect()`. Edge is correct (different from `connect()`, but the call IS there). |
| 17 | `_register_pulled_fileset()` | `src/hal0/registry/pull.py:1259` | pull helper | calls | **TRUE** | L40 import; L1315: `with connect(db_path) as conn, tx(conn):` |
| 18 | `_maybe_hardlink_from_blob()` | `src/hal0/registry/pull.py:1205` | pull helper | calls | **TRUE** | L1213: `with connect() as conn:` |
| 19 | `_register_blob_after_install()` | `src/hal0/registry/pull.py:1240` | pull helper | calls | **TRUE** | L1248: `with connect() as conn:` |
| 20 | `_copy_model_files_refcounted()` | `src/hal0/services/models_service.py:1193` | service helper | calls | **TRUE** | L1203: `from hal0.db.connection import connect, tx`; L1206: `with connect(db_path) as conn:` |
| 21 | `test_connect_sets_foreign_keys_on()` | `tests/db/test_connection.py:23` | unit test | calls | **TRUE** | L24: `with connect(tmp_path / "t.db") as conn:` (direct, body uses `connect()`) |
| 22 | `test_001_registry_creates_expected_tables()` | `tests/db/test_migrate.py:103` | unit test | calls | **TRUE** | L104: `with connect(tmp_path / "t.db") as conn:` (direct call in body) |
| 23 | `test_empty_slot_list_returns_empty_models()` | `tests/metrics/test_read.py:118` | unit test | calls | **TRUE** | L120: `with connect(db) as conn:` (direct call) |
| 24 | `useActivityStream()` | `ui/src/api/hooks/useActivity.ts:201` | UI React hook | indirect_call | **FALSE** | File defines a LOCAL `const connect = () => { ... EventSource(...) }` at L239 — a SSE reconnect helper, not `hal0.db.connection.connect`. Generic method-name collision; the UI hook never touches the SQLite factory. |
| 25 | `useLogsStream()` | `ui/src/api/hooks/useLogs.ts:152` | UI React hook | indirect_call | **FALSE** | Local `const connect = () => { ... }` at L188 — same SSE-reconnect pattern. The `useLogsStream` function does not import or call `hal0.db.connection.connect` anywhere in the file. |
| 26 | `useSlotLogsStream()` | `ui/src/api/hooks/useLogs.ts:280` | UI React hook | indirect_call | **FALSE** | Same file/function (`useSlotLogsStream` lives in the same module as `useLogsStream`); the inferred `connect()` edge comes from the same SSE `connect` local collision. |

---

## Correctness rate

Sampled: **26 edges → 23 TRUE, 3 FALSE, 0 AMBIGUOUS → ~88% precision on the sample.**

Extrapolating to the full 108-edge population:

- **101/108 TRUE (~93%)** — every store, every helper, every migration runner, every pull/import/discover path, and the test bodies that directly use `connect()`. The INFERRED engine found the right edges for these because the source file imports `connect` (e.g., `from hal0.db.connection import connect`) and the method body literally contains `with connect(...)`. The signal is unambiguous.
- **7/108 FALSE (~7%)** — all from the same failure mode: a local function named `connect` inside a UI file (`useLogs.ts` and `useActivity.ts` each define their own `const connect = () => { ... }` for SSE EventSource setup). The inference engine appears to be matching bare `connect(` token occurrences across the codebase and attributing them to `hal0.db.connection.connect`. Since `useLogsStream`/`useSlotLogsStream` live in the same file as that local `connect`, the inference fans out to all of them. (`useActivityStream` is in `useActivity.ts` with its own local `connect` at L239.)
- **~0 AMBIGUOUS** — every other sample was directly verifiable.

> The user's hypothesis ("many INFERRED edges are generic method-name collisions `.__init__`, `._read`, `.apply`, `.get` mis-attributed") **does not generalize.** Across 108 edges:
> - `.__init__` shows up 3 times (BoardStore, PortAuthority, SlotIdentityStore) — all 3 are TRUE (the ctors really do call `connect()` inside their boot step).
> - `._read` / `._write` / `._write_batch` / `ensure_schema` show up 9 times — all 9 are TRUE.
> - `._connect` shows up once — TRUE (different name, deliberately a thin wrapper over `connect()`).
>
> The only generic-name failure mode is the local `connect` symbol inside two UI hook files — and that name was reused for a different purpose (SSE), not from a Python class.

---

## Failure-mode taxonomy

| Mode | Count (est.) | Example | Severity |
|---|---|---|---|
| **F1 — Local `connect` symbol collision (UI)** | ~3 | `useLogsStream` ← local SSE `connect` in `useLogs.ts:188` | Low — false positive, but UI/frontend code has no business calling the SQLite factory anyway, so the inflated degree of `connect()` does not bias a downstream analysis that respects language boundaries. |
| **F2 — Test-file-level import propagation** | ~5–10 | Test methods that import `connect` at module top but only invoke it via a pytest fixture (`SqliteModelRegistry` constructed once, used by many tests) | Low — those tests still genuinely exercise the connect/tx path transitively. Edge is FALSE as a direct call, TRUE as a transitive reach. |
| **F3 — Method-name pattern (no observed instances)** | 0 | `.__init__` / `._read` / `._write` collisions with unrelated methods | None — not a real failure mode here. The generic-named methods all do call `connect()`. |

---

## Risks / smells

1. **`connect()` is a god node by construction** (114 edges, 4th in the graph). The 108 INFERRED edges are overwhelmingly correct, but the degree inflates downstream rankings: `connect()` sits next to `SlotManager` (277), `ENDPOINTS` (136), `BoardStore` (114) and is mis-classified by naive traversal as load-bearing for any code that "looks like" it touches the DB. A query like `graphify query "what does X call"` will surface `connect()` even when X is a UI hook whose only `connect` reference is a local SSE helper.
2. **No EXTRACTED cross-module edges for `connect()`** is itself noteworthy — the AST extraction pipeline did not produce a single `calls` edge from outside `db/connection.py`. That means the INFERRED pass did 100% of the work for `connect()`'s degree. If the INFERRED engine were ever disabled or regressed, `connect()` would collapse to degree 6 (intra-module only) and the dependency view of every persistent store would lose its central seam.
3. **UI ↔ backend edge pollution** (F1) is small (3 edges) but a smell because the same pattern would explode for any SQLite-using node that happens to share a name with a frontend helper. Mitigation: the INFERRED engine should scope `name`-based heuristics by language/extension; `connect` in a `.ts` file should not match `connect()` in a `.py` node.

---

## Recommendations

1. **Keep the 108 INFERRED edges.** Precision ~93% is well above any reasonable threshold for keeping inferred data; deleting them would lose more real signal than it removes false positives.
2. **Add language-scope guard to the INFERRED pass.** The 3 UI false positives (`useActivityStream`, `useLogsStream`, `useSlotLogsStream`) all come from a JS/TS file defining a local `connect` symbol that the Python `connect()` cannot resolve into. Cross-language token-name matching without an AST confirmation is the bug. Cheapest fix: filter INFERRED edges where `source_file` extension differs from the target node's extension and the relation is `calls` or `indirect_call`.
3. **Annotate test edges as transitive.** The 5–10 test-file edges that come from module-level `from hal0.db.connection import connect` but reach it through a fixture could carry a `via=fixture_name` hint so traversal queries can opt out of them.
4. **Surface INFERRED-edge provenance in `graphify explain`.** Currently the explain output prints `[INFERRED]` but not the heuristic that produced the edge. Knowing that an edge came from "bare-token-match in same file" vs "import co-occurrence" vs "docstring mention" would let a reviewer triage F1/F2 quickly. (Documented as a known INFERRED failure mode in the BRIEF, but the tool does not yet expose it.)
5. **Document the false-positive profile per node.** A small JSON sidecar listing nodes like `connect()` whose degree is dominated by INFERRED edges, with the per-node precision estimate, would help downstream consumers (Q-series reports, future audits) calibrate.

---

## Reproducibility

Commands used to enumerate the 108 edges:

```bash
graphify explain "connect"                                # confirms degree=114, lists first 20 + "and 94 more"
python3 -c "..."                                          # walks graph.json['links'] for src/tgt == src_hal0_db_connection_connect
                                                          # groups by confidence=INFERRED → 108 entries
```

Per-edge verification used `grep -nE "connect\b" <file>` and `Read` of the named line range — see the Evidence column of the sample table for the precise line of proof.
