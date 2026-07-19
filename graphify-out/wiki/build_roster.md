# build_roster

> 16 nodes

## Key Concepts

- **build_roster()** (12 connections) — `src/hal0/bench/publish.py`
- **publish.py** (11 connections) — `src/hal0/bench/publish.py`
- **write_roster()** (8 connections) — `src/hal0/bench/publish.py`
- **emit_site_ts()** (6 connections) — `src/hal0/bench/publish.py`
- **_detail_from_record()** (5 connections) — `src/hal0/bench/publish.py`
- **Any** (5 connections)
- **_default_host()** (4 connections) — `src/hal0/bench/publish.py`
- **Path** (2 connections)
- **_date()** (2 connections) — `src/hal0/bench/publish.py`
- **_today()** (2 connections) — `src/hal0/bench/publish.py`
- **publish.py — the public roster contract (DESIGN §9).  ``build_roster`` renders t** (1 connections) — `src/hal0/bench/publish.py`
- **The per-model ``detail`` block (DESIGN §9.1) from a current record + its     tre** (1 connections) — `src/hal0/bench/publish.py`
- **Render the roster.json contract (DESIGN §9.1) from current cell values.      One** (1 connections) — `src/hal0/bench/publish.py`
- **Write ``roster.json`` under the state root (DESIGN §3.1 layout). Returns     the** (1 connections) — `src/hal0/bench/publish.py`
- **Generate the site repo's ``data/model-roster.ts`` from roster.json     (DESIGN §** (1 connections) — `src/hal0/bench/publish.py`
- **Derive the roster host block from a current record's host (DESIGN §9.1     host:** (1 connections) — `src/hal0/bench/publish.py`

## Relationships

- [cli.py](cli.py.md) (10 shared connections)
- [runner.py](runner.py.md) (1 shared connections)
- [planner.py](planner.py.md) (1 shared connections)
- [argv.py](argv.py.md) (1 shared connections)

## Source Files

- `src/hal0/bench/publish.py`

## Audit Trail

- EXTRACTED: 61 (97%)
- INFERRED: 2 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*