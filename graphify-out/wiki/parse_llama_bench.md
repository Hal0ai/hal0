# parse_llama_bench

> 20 nodes · cohesion 0.16

## Key Concepts

- **parse_llama_bench()** (17 connections) — `src/hal0/bench/parsers.py`
- **test_parsers.py** (13 connections) — `tests/bench/test_parsers.py`
- **_load()** (6 connections) — `tests/bench/test_parsers.py`
- **llama_bench_row_kind()** (5 connections) — `src/hal0/bench/parsers.py`
- **_select_row()** (4 connections) — `src/hal0/bench/parsers.py`
- **test_parse_llama_bench_missing_kind_is_empty()** (3 connections) — `tests/bench/test_parsers.py`
- **test_parse_server_ab_ab()** (3 connections) — `tests/bench/test_parsers.py`
- **test_parse_server_ab_embed()** (3 connections) — `tests/bench/test_parsers.py`
- **test_parse_server_ab_reuse()** (3 connections) — `tests/bench/test_parsers.py`
- **lb_meta()** (2 connections) — `tests/bench/test_parsers.py`
- **lb_rows()** (2 connections) — `tests/bench/test_parsers.py`
- **test_parse_llama_bench_pp()** (2 connections) — `tests/bench/test_parsers.py`
- **test_parse_llama_bench_resolves_argv()** (2 connections) — `tests/bench/test_parsers.py`
- **test_parse_llama_bench_single_rep_falls_back_to_avg()** (2 connections) — `tests/bench/test_parsers.py`
- **test_parse_llama_bench_tg()** (2 connections) — `tests/bench/test_parsers.py`
- **test_row_kind_classifies_pp_and_tg()** (2 connections) — `tests/bench/test_parsers.py`
- **Pick the row for this cell's kind. A sweep can emit several same-kind rows     (** (1 connections) — `src/hal0/bench/parsers.py`
- **Parse a llama-bench ``-o json`` array + ``.meta.json`` into one cell's     resul** (1 connections) — `src/hal0/bench/parsers.py`
- **Classify a llama-bench row: a pp test has n_gen==0 (n_prompt>0), a tg test     h** (1 connections) — `src/hal0/bench/parsers.py`
- **test_parsers.py — the two P2 engine-output parsers against REAL captured fixture** (1 connections) — `tests/bench/test_parsers.py`

## Relationships

- [planner.py](planner.py.md) (13 shared connections)
- [runner.py](runner.py.md) (2 shared connections)

## Source Files

- `src/hal0/bench/parsers.py`
- `tests/bench/test_parsers.py`

## Audit Trail

- EXTRACTED: 58 (77%)
- INFERRED: 17 (23%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*