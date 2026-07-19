# .reindex

> 19 nodes

## Key Concepts

- **.reindex()** (7 connections) — `src/hal0/bench/store.py`
- **Any** (6 connections)
- **.append_record()** (5 connections) — `src/hal0/bench/store.py`
- **.iter_records()** (5 connections) — `src/hal0/bench/store.py`
- **.results()** (5 connections) — `src/hal0/bench/store.py`
- **.history()** (5 connections) — `src/hal0/bench/store.py`
- **.ensure_dirs()** (4 connections) — `src/hal0/bench/store.py`
- **._connect()** (4 connections) — `src/hal0/bench/store.py`
- **.newest_ok_by_cell()** (4 connections) — `src/hal0/bench/store.py`
- **_record_ts()** (4 connections) — `src/hal0/bench/store.py`
- **Connection** (1 connections)
- **Create the state root + artifacts dir. Idempotent; called before any         wri** (1 connections) — `src/hal0/bench/store.py`
- **Append one record as a single canonical JSON line. Accepts a Record         or a** (1 connections) — `src/hal0/bench/store.py`
- **Stream every record as a plain dict, in append (chronological) order.         Bl** (1 connections) — `src/hal0/bench/store.py`
- **Rebuild ``bench.db`` from records.jsonl from scratch and return the         numb** (1 connections) — `src/hal0/bench/store.py`
- **Map cell_key -> the full current (newest ok) record, read straight         from** (1 connections) — `src/hal0/bench/store.py`
- **Current-value rows for `benchlab results`, from the current_cells view.** (1 connections) — `src/hal0/bench/store.py`
- **Time-ordered ok records for a cell (trend line) or a whole model, for         `b** (1 connections) — `src/hal0/bench/store.py`
- **Best-effort ISO timestamp for a record. run_id is a UTC stamp + suffix     (``20** (1 connections) — `src/hal0/bench/store.py`

## Relationships

- [cli.py](cli.py.md) (8 shared connections)
- [runner.py](runner.py.md) (2 shared connections)

## Source Files

- `src/hal0/bench/store.py`

## Audit Trail

- EXTRACTED: 58 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*