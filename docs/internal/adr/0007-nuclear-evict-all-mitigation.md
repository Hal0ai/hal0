# ADR 0007 — Nuclear-evict-all mitigation (Lemonade)

- **Status:** Superseded by ADR-0008 (2026-05-22)
- **Reason:** invalidated by spike #2 findings + grill session 2026-05-22. See ADR-0008.
- **Date:** 2026-05-22
- **Drivers:** Spike findings 2026-05-22; ADR-0006 §Operational risks
- **Scope:** v0.2 Lemonade migration only — does not apply to v0.1.x toolbox era

## Context

Lemonade's `Router` ([github.com/lemonade-sdk/lemonade `src/cpp/Multi-Model-Spec.md`](https://github.com/lemonade-sdk/lemonade/blob/main/src/cpp/Multi-Model-Spec.md)) implements an evict-on-failure policy:

> "If a WrappedServer load fails (with exceptions noted below), all WrappedServers of every type are evicted, and the load is re-attempted. This 'nuclear' policy simplifies implementation while remaining effective in practice. **Exception:** Models are not evicted if the load failed because a file was not found on disk."

**Confirmed live in spike 2026-05-22.** Server log verbatim: `"Load failed with non-file-not-found error, evicting all models and retrying..."`. Observed twice during a single spike session.

For hal0 v0.2 this means: any `/v1/load` failure (corrupted GGUF, model file path mismatch, llama.cpp incompatibility, OOM, GPU driver hiccup) blasts every loaded model. In prod, a single bad model selection during the day unloads every warm slot.

## Options considered

| Option | Reason accepted / rejected |
|---|---|
| **Accept the behavior** | Rejected — every slot dropping mid-day on a single bad pull is a user-visible failure mode |
| **Wrap `/v1/load` in retry loop, hope for the best** | Rejected — re-attempt doesn't help if the underlying cause is structural (corrupted file, OOM) |
| **Patch Lemonade upstream to make the policy configurable** | Deferred — worth proposing upstream but not on v0.2 critical path |
| **Pre-validate before `/v1/load`** | ACCEPTED — steer failures into the file-not-found branch (the one Lemonade explicitly exempts from evict-all) |

## Decision

### 1. Pre-validation guards before every `/v1/load`

Implemented in `src/hal0/lemonade/preload.py`:

```python
def preload_validate(slot_cfg, model_entry) -> None:
    """Raises PreloadError BEFORE we POST /v1/load. Better the slot stays
    offline than the whole pool blasted."""
    # 1. File exists at registry path
    if not Path(model_entry.path).is_file():
        raise PreloadError.FILE_NOT_FOUND(model_entry.path)

    # 2. sha256 matches registry (catches partial downloads, corruption)
    if sha256_file(model_entry.path) != model_entry.sha256:
        raise PreloadError.CHECKSUM_MISMATCH(model_entry.path)

    # 3. Size matches registry
    if Path(model_entry.path).stat().st_size != model_entry.size_bytes:
        raise PreloadError.SIZE_MISMATCH(model_entry.path)

    # 4. GGUF magic byte sanity check
    if not is_valid_gguf(model_entry.path):
        raise PreloadError.NOT_A_GGUF(model_entry.path)
```

### 2. SlotManager surfaces `PreloadError` as slot state `error`, NOT a `/v1/load` attempt

When `preload_validate` raises, SlotManager:
- Sets slot state to `error` with explicit error class
- Does NOT call `lemonade_client.load()`
- Dashboard surfaces `error: checksum_mismatch` etc.
- Other slots remain loaded — blast radius = this slot only

### 3. Race exception explicitly documented

If validation passes at T0 and the file disappears between T0 and `/v1/load` arriving at lemond, we hit evict-all anyway. Accept the small race window; pre-validation reduces blast radius from "any bad load" to "file deleted in the millisecond between validate and load". Worst case is bounded by the actual file delete pattern (rare in prod).

### 4. NO retry loop wrapping `/v1/load`

If Lemonade returns 500 on `/v1/load`, SlotManager records error and stops. Retry doesn't help if the cause is structural, and retrying GUARANTEES another evict-all if cause was non-file-not-found.

### 5. Hard timeout on `/v1/load`

Lemonade documents serialized loading + indefinite pending-load queue. SlotManager wraps `lemonade_client.load()` with a hard timeout (default 120s, configurable). Timeout → record `PreloadError.LOAD_TIMEOUT`, do NOT retry, surface in dashboard.

## Consequences

**Wins:**
- Blast radius reduced from "any non-file-not-found failure" to "raced file delete" (rare)
- Slot errors surface with explicit, actionable messages
- Dashboard can show pre-validation status BEFORE attempting Lemonade load
- Mitigation lives in hal0 code — no upstream PR dependency

**Losses:**
- Pre-validation adds latency to slot start (sha256 + GGUF check ~100ms for 14B Q5)
- hal0 carries explicit knowledge of model integrity that Lemonade arguably should own
- Hardcoded assumption that file-not-found is the only safe failure mode — if Lemonade changes the exemption list, mitigation needs revisit

**Maintenance:**
- Watch Lemonade CHANGELOG for changes to evict-on-failure policy
- If AMD makes the policy configurable upstream, swap mitigation for a config flag

## Related

- ADR-0006 — Lemonade migration (parent decision)
- Operational hazard observed in spike: `docs/internal/lemonade-spike-findings-2026-05-22.md` §Negative findings/risks/Operational
- Memory: `hal0_lemonade_gotchas` (item 1 — nuclear evict-all)
