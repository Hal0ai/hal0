# seed_static_slots

> 19 nodes · cohesion 0.20

## Key Concepts

- **seed_static_slots()** (10 connections) — `src/hal0/install/static_seeds.py`
- **test_static_seeds.py** (9 connections) — `tests/install/test_static_seeds.py`
- **Path** (8 connections)
- **_fake_installer_root()** (7 connections) — `tests/install/test_static_seeds.py`
- **test_seed_static_slots_missing_source_logs_and_continues()** (5 connections) — `tests/install/test_static_seeds.py`
- **test_seed_static_slots_copies_all_missing()** (4 connections) — `tests/install/test_static_seeds.py`
- **test_seed_static_slots_creates_dest_dir()** (4 connections) — `tests/install/test_static_seeds.py`
- **test_seed_static_slots_default_args_seed_real_tree()** (4 connections) — `tests/install/test_static_seeds.py`
- **test_seed_static_slots_idempotent_second_run_noop()** (4 connections) — `tests/install/test_static_seeds.py`
- **test_seed_static_slots_skips_existing()** (4 connections) — `tests/install/test_static_seeds.py`
- **test_static_seed_slots_matches_shipped_files()** (3 connections) — `tests/install/test_static_seeds.py`
- **static_seeds.py** (2 connections) — `src/hal0/install/static_seeds.py`
- **Path** (1 connections)
- **Static slot-config seeds shipped in ``installer/etc-hal0/slots/``.  ``install.sh** (1 connections) — `src/hal0/install/static_seeds.py`
- **Copy any missing static seed TOML into the slots config dir.      Idempotent and** (1 connections) — `src/hal0/install/static_seeds.py`
- **Unit tests for :mod:`hal0.install.static_seeds`.  Closes the same fresh-install-** (1 connections) — `tests/install/test_static_seeds.py`
- **A partially-shipped installer tree (missing one seed source) must     not abort** (1 connections) — `tests/install/test_static_seeds.py`
- **Every name in STATIC_SEED_SLOTS must have a real TOML in     installer/etc-hal0/** (1 connections) — `tests/install/test_static_seeds.py`
- **Default args (no installer_root/slots_dir override) resolve the     real repo tr** (1 connections) — `tests/install/test_static_seeds.py`

## Relationships

- [lifespan](lifespan.md) (1 shared connections)

## Source Files

- `src/hal0/install/static_seeds.py`
- `tests/install/test_static_seeds.py`

## Audit Trail

- EXTRACTED: 58 (82%)
- INFERRED: 13 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*