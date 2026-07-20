# ML-2 (file-set pulling) + ML-3 (unified store) — implementation spec

> 31 nodes

## Key Concepts

- **ML-2 (file-set pulling) + ML-3 (unified store) — implementation spec** (12 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **1. Current-state map (verified)** (9 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **PART (a) — file-SET pulling** (6 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **PART (c) — refcount + hardlink dedup + real GC** (4 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **PART (b) — unified store resolver + repo/revision layout** (3 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **spec-ml-store.final.md** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **0. Coordination with ML-1 (the schema I extend)** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **The 🔴 dual store resolver (root of divergence)** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **Single-file pull engine (`registry/pull.py`, 1534 ln)** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **Repo enumeration (`upstreams/huggingface.py:258 fetch_repo`)** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **Discovery deletes shards (`registry/discover.py`)** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **Path resolution / mounts** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **Store migration (`registry/model_store.py`)** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **Delete = metadata-only** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **ModelRegistry interface (drop-in target, `store.py`)** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **a1. New module `src/hal0/registry/fileset.py` (repo enumeration + planning)** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **a2. Deterministic mmproj pairing** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **a3. Multi-file download in `run_pull` (extend, don't fork)** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **a4. Discovery stops deleting shards** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **a5. Update-detect over the full set** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **b1. One resolver `src/hal0/config/store.py` (new) — kill the dual path** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **b2. Repo/revision-addressed layout (HF-cache-shaped)** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **c1. Migration `002_store.sql`** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **c2. Hardlink dedup on install** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- **c3. GC (orphan prune) + guarded delete** (1 connections) — `docs/rework/hal0-specs/spec-ml-store.final.md`
- *... and 6 more nodes in this community*

## Relationships

- No strong cross-community connections detected

## Source Files

- `docs/rework/hal0-specs/spec-ml-store.final.md`

## Audit Trail

- EXTRACTED: 60 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*