# test_channel.py

> 24 nodes · cohesion 0.12

## Key Concepts

- **test_channel.py** (9 connections) — `tests/release/test_channel.py`
- **channel.py** (7 connections) — `src/hal0/release/channel.py`
- **nightlies_to_prune()** (5 connections) — `src/hal0/release/channel.py`
- **nightly_tag()** (5 connections) — `src/hal0/release/channel.py`
- **nightly_version()** (5 connections) — `src/hal0/release/channel.py`
- **base_matches()** (4 connections) — `src/hal0/release/channel.py`
- **base_version()** (4 connections) — `src/hal0/release/channel.py`
- **channel_for_tag()** (3 connections) — `src/hal0/release/channel.py`
- **test_nightly_version_and_tag()** (3 connections) — `tests/release/test_channel.py`
- **test_nightly_version_uses_full_stamp_and_is_monotonic()** (3 connections) — `tests/release/test_channel.py`
- **test_base_matches_relaxed_gate()** (2 connections) — `tests/release/test_channel.py`
- **test_base_version()** (2 connections) — `tests/release/test_channel.py`
- **test_channel_for_tag()** (2 connections) — `tests/release/test_channel.py`
- **test_nightlies_to_prune_keeps_newest()** (2 connections) — `tests/release/test_channel.py`
- **test_nightlies_to_prune_nothing_when_under_keep()** (2 connections) — `tests/release/test_channel.py`
- **test_nightlies_to_prune_orders_by_full_numeric_stamp()** (2 connections) — `tests/release/test_channel.py`
- **Release-channel helpers shared by the release + nightly workflows.  Pure functio** (1 connections) — `src/hal0/release/channel.py`
- **Return the release channel implied by a git tag.      A version carrying a ``-ni** (1 connections) — `src/hal0/release/channel.py`
- **Strip a leading ``v`` and any pre-release suffix → the base ``X.Y.Z``.      ``v0** (1 connections) — `src/hal0/release/channel.py`
- **Compose a nightly version from a base ``X.Y.Z`` and a UTC ``stamp``.      ``stam** (1 connections) — `src/hal0/release/channel.py`
- **Compose the nightly git tag (``v`` + nightly version).      See :func:`nightly_v** (1 connections) — `src/hal0/release/channel.py`
- **True when ``tag`` and ``pyproject_version`` share the same base X.Y.Z.      The** (1 connections) — `src/hal0/release/channel.py`
- **Return the nightly tags to delete, keeping the ``keep`` most recent.      Only `** (1 connections) — `src/hal0/release/channel.py`
- **Unit tests for hal0.release.channel — the version/channel helpers shared by the** (1 connections) — `tests/release/test_channel.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `src/hal0/release/channel.py`
- `tests/release/test_channel.py`

## Audit Trail

- EXTRACTED: 48 (71%)
- INFERRED: 20 (29%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*