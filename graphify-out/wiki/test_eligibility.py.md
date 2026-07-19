# test_eligibility.py

> 21 nodes

## Key Concepts

- **test_eligibility.py** (17 connections) — `tests/bundles/test_eligibility.py`
- **_write_meminfo()** (3 connections) — `tests/bundles/test_eligibility.py`
- **test_read_meminfo_parses_memtotal()** (2 connections) — `tests/bundles/test_eligibility.py`
- **test_eligible_tiers_unknown_ram_returns_all_tiers()** (2 connections) — `tests/bundles/test_eligibility.py`
- **test_eligible_tiers_at_8gb_yields_empty_list()** (2 connections) — `tests/bundles/test_eligibility.py`
- **setup_function()** (1 connections) — `tests/bundles/test_eligibility.py`
- **test_read_meminfo_handles_missing_file()** (1 connections) — `tests/bundles/test_eligibility.py`
- **test_read_meminfo_handles_empty_file()** (1 connections) — `tests/bundles/test_eligibility.py`
- **test_read_meminfo_handles_garbage()** (1 connections) — `tests/bundles/test_eligibility.py`
- **test_override_env_var_wins()** (1 connections) — `tests/bundles/test_eligibility.py`
- **test_override_env_var_rejects_garbage()** (1 connections) — `tests/bundles/test_eligibility.py`
- **test_host_ram_gb_caches_probe()** (1 connections) — `tests/bundles/test_eligibility.py`
- **test_eligible_tiers_at_16gb_yields_only_lite()** (1 connections) — `tests/bundles/test_eligibility.py`
- **test_eligible_tiers_at_32gb_yields_lite_and_default()** (1 connections) — `tests/bundles/test_eligibility.py`
- **test_eligible_tiers_at_64gb_yields_up_to_pro()** (1 connections) — `tests/bundles/test_eligibility.py`
- **test_eligible_tiers_at_128gb_yields_all_five()** (1 connections) — `tests/bundles/test_eligibility.py`
- **test_eligibility_boundaries()** (1 connections) — `tests/bundles/test_eligibility.py`
- **Tests for hal0.bundles.eligibility — RAM probe + tier filtering.** (1 connections) — `tests/bundles/test_eligibility.py`
- **Write a minimal /proc/meminfo-shaped fixture file.** (1 connections) — `tests/bundles/test_eligibility.py`
- **When the probe fails (returns 0), the picker shouldn't lock the     operator out** (1 connections) — `tests/bundles/test_eligibility.py`
- **A box below the Lite floor gets no tiers — the picker still shows     them all (** (1 connections) — `tests/bundles/test_eligibility.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/bundles/test_eligibility.py`

## Audit Trail

- EXTRACTED: 42 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*