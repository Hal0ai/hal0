# Q4: SlotManager INFERRED edge accuracy

**Question (verbatim from BRIEF).** SlotManager has 177 INFERRED edges (model-reasoned, not AST-extracted). Are they actually correct?

**Method.** Locate canonical SlotManager node (`src_hal0_slots_manager_slotmanager`, file `src/hal0/slots/manager.py`, loc L255, community 2). Enumerate all 177 INFERRED edges from `graphify-out/graph.json`. Pick a 15-edge representative sample: 13 OUT `uses` edges (the full outgoing set), 1 IN production-side `calls` (`_build_offline_deps`), 1 IN test-side `indirect_call`. Open cited source at file:line and classify each: **TRUE** (real use/call exists), **FALSE** (no such relationship), or **AMBIGUOUS** (real but mislabeled or indirect).

**Composition of all 177 INFERRED edges on SlotManager** (from `graph.json`):

| relation | direction | count |
|----------|-----------|-------|
| calls | OUT (from SlotManager) | 0 |
| uses | OUT (from SlotManager) | 13 |
| calls | IN (to SlotManager) | 159 |
| indirect_call | IN (to SlotManager) | 5 |

Distribution: 13 production "uses" edges, 164 test-side incoming edges (159 `calls` + 5 `indirect_call`), 1 production-side incoming (`_build_offline_deps`).

## Verdict table — 15-edge sample

| # | Source → Target | Cited loc | Verdict | Evidence |
|---|-----------------|-----------|---------|----------|
| 1 | SlotManager **uses** FLMProvider | `src/hal0/providers/flm.py:L335` | **TRUE** | manager.py:2879 lazy-imports `from hal0.providers.flm import FLMProvider`; L2882 `if isinstance(provider, FLMProvider):` in `_await_ready` inference gate |
| 2 | SlotManager **uses** GpuArbiter | `src/hal0/slots/arbiter.py:L237` | **TRUE** | manager.py:126 lazy-imports; L403 type-annotated `self._arbiter: GpuArbiter \| None = None`; L2510–2518 constructs `GpuArbiter(...)` lazily |
| 3 | SlotManager **uses** SlotAlreadyExists | `src/hal0/slots/identity.py:L43` | **TRUE** | manager.py:825 lazy-imports inside `rename()`; L829 catches `except SlotAlreadyExists` and re-raises as `SlotConfigError` |
| 4 | SlotManager **uses** SlotInterface | `src/hal0/slots/interface.py:L160` | **TRUE** | manager.py:127 + L2496 lazy-import; L408 type-annotates `self._interface: SlotInterface \| None = None`; L2498 `self._interface = SlotInterface(self)` |
| 5 | SlotManager **uses** SlotReaper | `src/hal0/slots/reaper.py:L191` | **TRUE** | manager.py:83 top-of-file import; L399 `self._reaper = SlotReaper(self)` in `__init__` |
| 6 | SlotManager **uses** LoadedSlot | `src/hal0/slots/routing.py:L99` | **TRUE** | manager.py:92 imports `LoadedSlot`; L1617 `_loaded_slot_from_config -> LoadedSlot \| None`; L1621 `loaded_slot -> LoadedSlot \| None` |
| 7 | SlotManager **uses** IllegalSlotTransition | `src/hal0/slots/state.py:L178` | **TRUE** | manager.py:104 import; L960 `raise IllegalSlotTransition(...)` in `_transition` |
| 8 | SlotManager **uses** SlotConfigError | `src/hal0/slots/state.py:L206` | **TRUE** | manager.py:105 import; raised at L749, L798, L803, L810, L818, L830 (multiple call sites in rename/update_config) |
| 9 | SlotManager **uses** SlotNotFound | `src/hal0/slots/state.py:L171` | **TRUE** | manager.py:106 import; raised at L718, L725, L882 |
| 10 | SlotManager **uses** SlotPinned | `src/hal0/slots/state.py:L226` | **TRUE** | manager.py:107 import; L1909 `raise SlotPinned(...)` inside `delete()` after `is_pinned` check |
| 11 | SlotManager **uses** SlotState | `src/hal0/slots/state.py:L51` | **TRUE** | manager.py:108 import; ~40 uses as enum, type-hint, and `frozenset[SlotState]` at L893 |
| 12 | SlotManager **uses** SlotStateRecord | `src/hal0/slots/state.py:L250` | **TRUE** | manager.py:109 import; L342 type-annotates `dict[int, SlotStateRecord]`; L999 constructs; L1062 / L1090 type-annotate queue/broadcast params |
| 13 | SlotManager **uses** SlotWatchdog | `src/hal0/slots/watchdog.py:L101` | **TRUE** | manager.py:121 import; L354 `self._watchdog = SlotWatchdog(self)` |
| 14 | `_build_offline_deps()` **calls** SlotManager | `src/hal0/cli/setup_command.py:L431` | **TRUE** | setup_command.py:431–455 — direct import at L450, direct constructor at L454 `slot_manager = SlotManager(event_bus=event_bus, upstreams_registry=None)` |
| 15 | `test_no_false_drift_for_registry_id_model_and_alias` **indirect_call** SlotManager | `tests/slots/test_config_drift_aliases.py:L127` | **AMBIGUOUS** | Test does NOT use indirect call. At L161 it does a DIRECT `sm = SlotManager()` then `await sm.load(...)`/`sm.status(...)` (verified L161–163). The `indirect_call` label is mis-categorised — the test also touches SlotManager via `monkeypatch.setattr(SlotManager, "_resolve_model_info", ...)` at L144 and type-annotation at L141, but the dominant use is direct construction. The relationship is real but the **relation type is wrong** |

## Findings

**Correctness rate: 14/15 = 93.3 %.** All 13 OUT `uses` edges are TRUE. The 1 production-side IN `calls` is TRUE. The 1 sampled test-side IN edge is real but mis-typed (`indirect_call` → should be `calls`).

**Inferred-edge population profile.** The 177 INFERRED edges partition cleanly:
- **13 OUT `uses`** (production code) — all TRUE in the sample. These are class-attribute references inside `manager.py` (types, errors raised, helpers instantiated). Easy to verify; almost tautological once you find the import line.
- **1 IN `calls` from `_build_offline_deps`** — TRUE; lone non-test caller.
- **159 IN `calls` from tests** — pattern: every edge is `test_X()` → `sm = SlotManager()` then `await sm.<method>(...)`. Verified on `test_manager.py:200–209` (`test_add_slot_writes_toml`). The relationship IS real, but graphify could not extract it via AST because the test fixture machinery (`container_stub`, `tmp_hal0_home`, `slot_root`) wraps construction. The "INFERRED" label means graphify guessed from co-occurrence / test name heuristics. These are TRUE in pattern but each one warrants a sanity check for the specific test.
- **5 IN `indirect_call` from tests** — relation type `indirect_call` is suspicious; sampled one (test_config_drift_aliases.py:127) and found it actually does a DIRECT constructor call at L161. The `indirect_call` label seems to be applied to any test where SlotManager is touched via something OTHER than just `SlotManager()` — e.g., `monkeypatch.setattr(SlotManager, ...)`, `self: SlotManager` annotation, `SlotManager.BUILTIN_SLOTS`. These are still real SlotManager uses, just not via the constructor.

## Risks / Smells

- **graphify under-classifies test coupling.** 159 of 177 (90 %) of SlotManager's INFERRED edges are test→SlotManager, and they collapse to a SINGLE pattern: `SlotManager()` + method calls. This means the god-node rank (277 edges) is heavily inflated by test files, not by real production coupling. For "real coupling" questions, filter `source_file` to `src/` only — count drops from 277 to ~14 edges.
- **`indirect_call` label is unreliable.** At least one sample (L127) has the wrong relation type. The label seems to mean "test that touches SlotManager outside the standard constructor pattern" rather than "truly indirect call". This is a labelling bug, not a relationship bug.
- **No method-level granularity.** All 13 production `uses` edges collapse to the entire `SlotManager` class (L255). Method-level edges (e.g., `_await_ready → FLMProvider.isinstance`, `rename → SlotAlreadyExists`) are not surfaced. A consumer of this graph cannot tell which methods actually drive each dependency.

## Recommendations

1. **Filter test edges when measuring coupling.** Add `graphify query` / `path` filters that exclude `tests/` from coupling metrics. SlotManager's "real" god-rank drops from 277 to ~14.
2. **Fix `indirect_call` inference rule.** Current rule over-matches tests that merely reference a class via monkeypatch or annotation. Either rename to `references` or restrict to genuine call chains (e.g., `fixture → SlotManager` via dependency injection).
3. **Add method-level edges for god nodes.** SlotManager has 95+ methods (`ctx_compose` / `ctx_read` confirms). The current class-level graph loses 95 % of the information. Emit at least method→dependency edges for the 13 `uses` targets.
4. **Re-run on `tests/` exclusion** to confirm the rest of the analysis (Q1–Q3 in this analysis batch) is not similarly inflated by test coupling.

## Sample edge to data table for downstream analysis

| Sample edge | Direction | Relation | Confidence | Source file | Source loc | Target file | Target loc |
|-------------|-----------|----------|------------|-------------|-----------|-------------|-----------|
| _build_offline_deps → SlotManager | IN | calls | INFERRED | src/hal0/cli/setup_command.py | L431 | src/hal0/slots/manager.py | L255 |
| SlotManager → FLMProvider | OUT | uses | INFERRED | src/hal0/slots/manager.py | L255 | src/hal0/providers/flm.py | L335 |
| SlotManager → GpuArbiter | OUT | uses | INFERRED | src/hal0/slots/manager.py | L255 | src/hal0/slots/arbiter.py | L237 |
| SlotManager → SlotAlreadyExists | OUT | uses | INFERRED | src/hal0/slots/manager.py | L255 | src/hal0/slots/identity.py | L43 |
| SlotManager → SlotInterface | OUT | uses | INFERRED | src/hal0/slots/manager.py | L255 | src/hal0/slots/interface.py | L160 |
| SlotManager → SlotReaper | OUT | uses | INFERRED | src/hal0/slots/manager.py | L255 | src/hal0/slots/reaper.py | L191 |
| SlotManager → LoadedSlot | OUT | uses | INFERRED | src/hal0/slots/manager.py | L255 | src/hal0/slots/routing.py | L99 |
| SlotManager → IllegalSlotTransition | OUT | uses | INFERRED | src/hal0/slots/manager.py | L255 | src/hal0/slots/state.py | L178 |
| SlotManager → SlotConfigError | OUT | uses | INFERRED | src/hal0/slots/manager.py | L255 | src/hal0/slots/state.py | L206 |
| SlotManager → SlotNotFound | OUT | uses | INFERRED | src/hal0/slots/manager.py | L255 | src/hal0/slots/state.py | L171 |
| SlotManager → SlotPinned | OUT | uses | INFERRED | src/hal0/slots/manager.py | L255 | src/hal0/slots/state.py | L226 |
| SlotManager → SlotState | OUT | uses | INFERRED | src/hal0/slots/manager.py | L255 | src/hal0/slots/state.py | L51 |
| SlotManager → SlotStateRecord | OUT | uses | INFERRED | src/hal0/slots/manager.py | L255 | src/hal0/slots/state.py | L250 |
| SlotManager → SlotWatchdog | OUT | uses | INFERRED | src/hal0/slots/manager.py | L255 | src/hal0/slots/watchdog.py | L101 |
| test_no_false_drift_for_registry_id_model_and_alias → SlotManager | IN | indirect_call | INFERRED | tests/slots/test_config_drift_aliases.py | L127 | src/hal0/slots/manager.py | L255 |

**Verdict (one line):** 14/15 sample edges (93.3 %) are TRUE; the single non-TRUE is a relation-type mislabel (`indirect_call` should be `calls`), not a phantom relationship. All 13 OUT `uses` edges are correct.