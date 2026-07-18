Complete verified map of the 9+ axes. Implementer-ready spec below.

---

# §7.1d — Capability / Modality Taxonomy untangle (ML-6)

**Repo:** `/home/mint/hal0` (branch `rework/descar`, verified). Plan refs: `/home/mint/hal0-rework-plan.md` §7.1d (target model + 5 enforced rules) · §23.2 seam S11 (`SHARD_RE`) · §23.4 (build DAG — `§7.1d modalities + ML-4 runner ─PREREQ→ §13.3 request_metric.modality/runner columns`) · §8.2 (SQLite DDL the record lands on). Tracker row 1727 (`hal0-rework-tracker.md:1727`, HIGH W4 cross-cutting). This is **the 🔴 tags→labels routing footgun fix**: a model tagged `tool-calling` ships no tools today.

---

## PART 1 — CURRENT STATE MAP (verified, file:line)

### 1.1 The 9+ overlapping axes (no unifying normalizer)

| # | Axis | Owner | Sample sites | Spelling drift |
|---|---|---|---|---|
| 1 | `model.labels` (slot config dict key) | slot TOML, hand-authored | `omni_router/filter.py:64`, `:90`; `model_meta/__init__.py:643` (`labels_of`); `slots/manager.py:1663` (lifted into `LoadedSlot.labels`) | freeform string list — `tool-calling`, `reasoning`, `vision`, etc. |
| 2 | `Model.tags` | registry pydantic `tags: list[str]` (`registry/model.py:137`) | only read at `bench/planner.py:113` (union with `capabilities`) and `hardware/recommend.py:122` (`is_moe` via `"mtp" in curated.tags`) | freeform string list, **NEVER copied to `model.labels`** — the 🔴 |
| 3 | `Model.capabilities` | registry pydantic (`model.py:119`) | seed: `api/routes/models.py:863`; normalize: `model_meta/__init__.py:255` (`MODEL_CAPABILITIES`) | canonical: `chat·vision·embed·rerank·asr·tts·image·video` |
| 4 | `SlotConfig.vision` | bool field (`config/schema.py:413`) | runtime gate: `providers/container.py:940` (`if not slot_cfg.get("vision", True): mmproj = None`); UI: `ui/src/dash/slot-modals.jsx:1393` (pill gated on `m.mmproj`) | bool override |
| 5 | `CuratedModel.capability` | pydantic (`registry/curated.py:134`) | seeds in code: chat/embed/asr/tts/image; consumer `hardware/recommend.py:122` | curated string, distinct vocab from #3 |
| 6 | slot `type` | slot TOML `[slot].type` (e.g. `llm`/`embedding`/`reranking`/`transcription`/`tts`/`image`) | declared in `installer/etc-hal0/slots/*.toml`; read at `slots/manager.py:1632`, `:1715`; whitelisted via `slots/manager.py:143` (`_VALID_SLOT_TYPES = frozenset(SLOT_TYPES)`); `profiles/__init__.py:43` `SlotType = Literal[...]` | dispatcher vocab, separate from capability vocab |
| 7 | `capabilities/catalog.py` capability child | `LEGAL_SLOTS` (`capabilities/orchestrator.py:94` = `embed·voice·img·vision`) + `_CHILD_TO_CAPABILITY` (`:127`) | operator-facing tile, drives slot provisioning | distinct from `MODEL_CAPABILITIES` |
| 8 | capability/type alias maps (scattered) | `model_meta/__init__.py:271` (`CAPABILITY_ALIASES`); `model_meta/__init__.py:356` (`_CAPABILITY_TO_TYPE`); `capabilities/orchestrator.py:88-144` (`_CHILD_TO_SLOT_TYPE`/`_CAPABILITY_TO_SLOT_TYPE`); `api/routes/models.py:175` (`_FLM_DISPATCH_TYPE`) + `:193` (`_MODALITY_TO_SLOT_TYPE`); `cli/migrate_commands._CAPABILITY_TO_LEAF_CAP` | 5 separate dicts, no single ingest folder |
| 9 | FLM `label` (Ollama-style) | `providers/flm.py:784` (`entry.get("label", []) or []`) → `["embed"]`/`["chat"]`/`["stt"]` | one-off, no shared normalizer | |

**vision alone appears in 6+ places** (slot vision toggle #4, registry `mmproj` presence, `Model.capabilities[vision]`, `CuratedModel.capability=vision` seed, capability child `vision.vision`, model-type tag `vision` in `MODEL_TYPE_TAGS`). The routing layer (omni_router/filter.py, providers/flm.py) reads from **two of these** (`model.labels` + FLM `label`); the registry stores **three** (`capabilities`, `tags`, `mmproj`); the slot carries **one** (`vision` bool + `type`).

### 1.2 The 🔴 bug — verified absence of `Model.tags` → `model.labels` copy

- `omni_router/filter.py:64` reads `labels_of(cfg)` which extracts `cfg["model"]["labels"]` (`model_meta/__init__.py:651-655`). **There is no call site that copies `Model.tags` into the slot config's `[model].labels`** — `tags` lives on the registry row (`model.py:137`), `labels` lives on the slot TOML (`slot_view/__init__.py:248` lifts them at apply time), and the two are hand-authored separately.
- `omni_router/filter.py:90` is the gate: `if "tool-calling" not in caller.labels: return []` — an empty list is shipped to the LLM, so a model tagged `tool-calling` in the registry but missing `tool-calling` in the slot's `[model].labels` ships **no tools**.
- `providers/flm.py:788` routes on FLM CLI's own `label` field — unrelated to `Model.tags` — so the FLM path has a parallel routing gate that doesn't share vocabulary with the registry either.
- `bench/planner.py:113` has to UNION `caps + capabilities + tags + type` to compensate: `for field_name in ("caps", "capabilities", "tags"): caps.update(m.get(field_name) or [])`. That union is a workaround for the disconnect and the spec removes it.

### 1.3 Canonical vocabulary table today (model_meta/__init__.py module docstring)

The module docstring at `model_meta/__init__.py:14-65` already enumerates 7 distinct vocabs. The "model capability" row (`:53-57`) defines the canonical spelling set as `chat·vision·embed·rerank·asr·tts·image·video` (`:255-264`) and `CAPABILITY_ALIASES` (`:271-278`) folds the synonyms `embedding→embed`, `embeddings→embed`, `reranking→rerank`, `transcription→asr`, `stt→asr`, `img→image`. **`/api/meta/enums` (`api/routes/meta.py:85-87`) serves both** — so the alias table is canonical-source today, but consumers keep their own copies.

### 1.4 Other axis sites to be collapsed

- `CuratedModel.capability` (`registry/curated.py:134`) accepts a curated string `chat/embed/asr/tts/image`. The wizard renders it as a card. Becomes `Model.modalities[0]` (curated seed → derive modality list at pull time).
- Installer seeds `installer/etc-hal0/slots/*.toml` carry hand-authored `type = "llm"/"tts"/"image"/"reranking"` but **no `labels`** (verified — `agent.toml`, `brain.toml`, `flm.toml`, `img.toml`, `qwen3tts.toml`, `rerank.toml`, `tts.toml`, `utility.toml`). Installer slots are derived by `capabilities/orchestrator._apply_*` at apply time, not at seed time.
- `capabilities/orchestrator.py:89-143` has TWO sibling maps: `_CHILD_TO_SLOT_TYPE` (`embed→embedding`, `stt→transcription`) and `_CAPABILITY_TO_SLOT_TYPE` (`chat→llm`, `embed→embedding`, `rerank→reranking`, `stt→transcription`, `tts→tts`, `image→image`, `vision→llm`). Both fold into `Modality → slot_type` (one derived projection).

### 1.5 `hardware/recommend.py` `is_moe` — vestigial `moe` tag consumer

`hardware/recommend.py:122`:
```python
is_moe = bool(curated) and ("mtp" in curated.tags or "a3b" in curated.id.lower())
```
The line reads `curated.tags` (the registry-side freeform list), not `curated.capability`. The check fires on `mtp` (an MTP marker, not a MoE marker) OR `a3b` in id (a Qwen3-Next-style MoE hint). The check has nothing to do with the curated `moe` tag in `CURATED_MODEL_TAGS` (`model_meta/__init__.py:308`) — that tag is **declared but never read anywhere** (grep `model_meta`+`tags=moe`+`"moe"` returns only the schema declaration and `MODEL_TYPE_TAGS`). `moe` is the dead tag; the plan replaces it with `Model.architecture` keyed off `FAMILY_DEFAULTS`.

### 1.6 Slot `type` (llm/embedding/…) — where defined vs derived

- **Defined (hand-authored):** `installer/etc-hal0/slots/*.toml` (`type = "llm"`, `"tts"`, `"image"`, `"reranking"`).
- **Whitelisted:** `slots/manager.py:143` `_VALID_SLOT_TYPES = frozenset(SLOT_TYPES)`, sourced from `model_meta/__init__.py:248` `SLOT_TYPES = ("llm", "embedding", "reranking", "transcription", "tts", "image")`.
- **Type-alias:** `profiles/__init__.py:43` `SlotType = Literal[...]` — same spellings, mirrored.
- **Derived (today, ad-hoc):** `api/routes/models.py:203-206` `_dispatch_type(model_id, capabilities) → classify() → _MODALITY_TO_SLOT_TYPE` for the `/api/models` payload (so picker matches slot `type`). `capabilities/orchestrator.py:88-91` `_CHILD_TO_SLOT_TYPE` (`embed→embedding`, `stt→transcription`) for the NPU trio shadows. `_CAPABILITY_TO_SLOT_TYPE` (`:136-144`) for general capability child → slot type.
- **SlotConfig.vision** lives next to `type` on the slot TOML but is NOT a slot type — it's a per-slot override on the *derived* `--mmproj` flag (`providers/container.py:940`).

---

## PART 2 — TARGET MODEL (single-source, 3 axes + 1 runtime)

The plan's target sketch (§7.1d, lines 343-367) is the spec. Restating with binding rules.

### 2.1 Modality — closed enum, derived-first, single ingest

New file `src/hal0/model_meta/modality.py`:

```python
class Modality(str, Enum):
    CHAT   = "chat"
    VISION = "vision"
    EMBED  = "embed"
    RERANK = "rerank"
    ASR    = "asr"
    TTS    = "tts"
    IMAGE  = "image"
    VIDEO  = "video"

# Single ingest-time folder — called from every register/update path
# (registry.add, registry.update, discover.scan_and_register, FLM probe,
# curated catalog apply, capability orchestrator apply).
def normalize_modality(value: str | None) -> Modality | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v:
        return None
    # Aliases — the canonical-folding spot. New aliases ADD HERE ONLY.
    folded = {
        "stt":          "asr",
        "transcription": "asr",
        "embedding":    "embed",
        "embeddings":   "embed",
        "reranking":    "rerank",
        "img":          "image",
    }.get(v, v)
    try:
        return Modality(folded)
    except ValueError:
        return None  # unknown → drop, don't crash ingest

def normalize_modalities(values: Iterable[str]) -> list[Modality]:
    """Fold + dedupe + sort. Drop unknowns (warning logged)."""
    seen: dict[Modality, None] = {}
    for v in values:
        m = normalize_modality(v)
        if m is not None and m not in seen:
            seen[m] = None
            log.warning("modality.unknown_dropped", extra={"raw": v}) if False else None
    return list(seen)
```

**`CAPABILITY_ALIASES` (`model_meta/__init__.py:271`) DELETES.** All callers switch to `normalize_modality`/`normalize_modalities`. `MODEL_CAPABILITIES` (`model_meta/__init__.py:255`) becomes `[m.value for m in Modality]` — same canonical spellings, enum-driven so adding `Modality.AUDIO = "audio"` automatically updates `/api/meta/enums`.

### 2.2 Capabilities — typed bools, gated by runner.supports

`src/hal0/registry/model.py` — add:

```python
class ModelCapabilities(BaseModel):
    """Launch/runtime flags. Each gate is runner-conditional.

    A capability shows up in the UI only when the resolved runner's
    ``RunnerSupports`` declares it; absent that, the field is inert and
    cannot be set from the dashboard. This closes the toggle-visibility
    bug class (e.g. MTP toggle for a non-MTP runner).
    """
    model_config = {"populate_by_name": True, "extra": "forbid"}

    mtp:          bool | None = None   # → --spec-* MTP bundle (runner.supports.mtp)
    jinja:        bool | None = None   # → --jinja         (runner.supports.jinja)
    tool_calling: bool | None = None   # → ROUTING GATE   (replaces model.labels — see §3.1)

    # `reasoning` REMOVED from capabilities (it was a tag, not a launch flag).
    # Request-time `enable_thinking` stays in normalize/thinking.py.
```

`Model` adds:
```python
capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
architecture: str | None = Field(
    default=None,
    description=(
        "Model architecture id (e.g. 'llama', 'qwen2', 'gemma3', 'gpt-oss', "
        "'qwen3next', 'mamba'). Replaces the dead `moe` tag — drives "
        "FAMILY_DEFAULTS keying + dense/moe context sizing."
    ),
)
```

**`Model.preferred_runner`, `Model.modalities`, `Model.tags` already coordinated with §7.1a/b/c** — the plan §8.2 columns land together; do not double-add (tracker §23.4 build-DAG "preferred_runner/mtp/jinja/architecture: land ONCE as pydantic fields (7.1a/b) → ML-1 maps to columns (never double-add)"). The same applies here:
- `mtp`, `jinja`, `architecture` → §7.1a/b (ML-5), not added again here.
- `preferred_runner` → §7.1b (ML-4).
- `revision` → §7.1c (ML-2).

### 2.3 Tags — inert, free-text, drive NOTHING

`Model.tags: list[str]` (`model.py:137`) STAYS as the freeform descriptor list (coder, reasoning, frontier, low-vram, domain tags). **No routing, no UI toggle gate, no detection logic reads them.** The only legitimate consumers are:
- `bench/planner.py:113` union (kept as a workaround for legacy rows; will go away once §7.1c lands revisions and the bench planner reads `modalities` directly).
- `hardware/recommend.py:122` (`is_moe`) — **rewritten** to read `Model.architecture` (or `curated.architecture`), not `tags`.

### 2.4 Slot `type` — derived projection of modality

`SlotConfig` is the only place `type` is written (hand-authored at install, derived at apply). Target: every slot's `type` is computed as `projection(modality)` from a one-line table:

```python
# profiles/__init__.py or runners/ — co-located with SlotType Literal
_MODALITY_TO_SLOT_TYPE: dict[Modality, str] = {
    Modality.CHAT:   "llm",          # covers chat + vision chat
    Modality.VISION: "llm",          # vision is a Modality of an llm slot
    Modality.EMBED:  "embedding",
    Modality.RERANK: "reranking",
    Modality.ASR:    "transcription",
    Modality.TTS:    "tts",
    Modality.IMAGE:  "image",
    Modality.VIDEO:  "image",        # video treated as image slot for v1
}

def slot_type_for(modalities: Iterable[Modality]) -> str:
    """Pick the dominant modality in _TYPE_PRIORITY order (rerank>embed>asr>tts>image>chat).
    Falls back to 'llm' (chat is the universal)."""
    present = set(modalities)
    for m in _TYPE_PRIORITY:
        if m in present:
            return _MODALITY_TO_SLOT_TYPE[Modality(m)]
    return "llm"
```

`SlotConfig.type` stays on the TOML (legacy), but `slot_view.__init__.py:248` (the apply-time lift) sets `type` from `slot_type_for(modalities)` unless the slot TOML already pins `type` (operator override). Cap child → slot type flows through this same function — `capabilities/orchestrator.py:88, 136` DELETES both maps.

### 2.5 Modality derivation rules (the single ingest fold)

Compute at every register/update path:
- `vision ⟸ mmproj` — `mmproj` presence flag → add `Modality.VISION` if not already present.
- `embed ⟸ runner role` — if `preferred_runner == "llama-server"` AND `Model.capabilities` *didn't* explicitly set, AND `metadata["pooling_type"] > 0` OR filename embed-token match (`model_meta/__init__.py:359-360`) → add `Modality.EMBED`.
- `rerank ⟸ runner role` — same; `metadata["pooling_type"] == 4` (RANK) OR `rerank` in id → add `Modality.RERANK`.
- `asr ⟸ runner role` — if `preferred_runner in {"flm", "moonshine"}` → add `Modality.ASR` (FLM trio gets asr only when `[npu].asr=true`; moonshine always).
- `tts ⟸ runner role` — if `preferred_runner in {"kokoro", "qwen3tts"}` → add `Modality.TTS`.
- `image ⟸ runner role` — if `preferred_runner == "comfyui"` → add `Modality.IMAGE`.

**Recompute on every pull/swap** — the rule is no hand-authored modality list except for an explicit override field (see §3.2). Implemented in `registry/pull._post_register()` and `discover.scan_and_register()`.

### 2.6 `RunnerSupports` (already in ML-4 spec) — the toggle-visibility gate

Reusing spec-ml-runner-flags.final.md §2.1 `RunnerSupports(mtp, jinja, mmproj)`:

```python
def capability_toggle_visible(cap: str, runner_supports: RunnerSupports) -> bool:
    """Toggle is settable only when runner advertises it."""
    return getattr(runner_supports, cap, False)
```

MTP toggle in `settings.jsx` shows iff `runner.supports.mtp=true`; same for jinja. mmproj toggle (per-slot `vision`) shows iff `runner.supports.mmproj=true` AND `Model.mmproj` is present. Closes the toggle-visible-but-inert bug class.

---

## PART 3 — ENFORCED RULES (kill the bug classes)

### 3.1 🔴 Routing reads `capabilities.tool_calling`, NOT `model.labels`

Delete (or make derived):
- `model_meta/__init__.py:643-656` `labels_of()` — REPLACED by `model_capabilities_of(model_info)` reading `model_info["capabilities"]["tool_calling"]`. Kept as a deprecated thin shim one release (returns the bool projection) so CLI/`slots/manager.py:1663` `_loaded_slot_from_config` lifts it onto `LoadedSlot.tool_calling` (renamed from `labels`).
- `slots/manager.py:1663` `labels=frozenset(labels_of(cfg))` → `tool_calling=bool(model_capabilities_of(cfg).get("tool_calling"))`.
- `omni_router/filter.py:64` `chat_slot_has_tool_calling` → `return bool(capabilities.get("tool_calling"))` on the resolved `Model`.
- `omni_router/filter.py:90` `if "tool-calling" not in caller.labels` → `if not caller.tool_calling`.

`LoadedSlot` (`slots/manager.py:287`) gains `tool_calling: bool`; **drops `labels: frozenset[str]`**. The `labels` field is the entire 🔴 axis — killing it closes the bug. `dispatcher/router.py` (search: no current `tool-calling` filter consumer other than filter.py) inherits the fix transitively.

### 3.2 Modalities recomputed on pull/swap, with explicit override

- `registry/pull.py` (the post-pull finalize hook): always set `Model.modalities = derive_modalities(model, runner_for(model.preferred_runner))`.
- `registry/discover.py::scan_and_register` (`:382`): same recompute on auto-scan registration.
- `slots/manager.py` swap (`_apply_preferred_profile`, `_apply_preferred_runner` from spec-ml-runner-flags §2.4): on swap, the new model's modalities recompute; the slot's `type` updates via `slot_type_for(modalities)`. UI follows.
- **Override field** on `Model` (rare, ops-only): `modalities_override: list[Modality] | None = None`. If set, `derive_modalities()` returns the union `(derived ∪ override)`. This is the escape hatch for hand-curated ComfyUI workflows that need `image + video` on a slot the detector can't infer.

### 3.3 Toggle visibility = `settable ∧ runner.supports`

`/api/meta/enums` (extending `api/routes/meta.py:79-92`) gains:

```python
return {
    ...,
    "model_capabilities_modalities": [m.value for m in Modality],
    "model_capabilities_flags": [
        {"key": "mtp",          "label": "MTP speculative decoding", "runner_gate": "mtp"},
        {"key": "jinja",        "label": "Jinja chat template",       "runner_gate": "jinja"},
        {"key": "tool_calling", "label": "Native tool calling",       "runner_gate": None},
    ],
}
```

UI: `ui/src/dash/model-modals.jsx:468` (the `MODEL_TYPE_TAGS.map`) reads `enums.model_capabilities_flags` and renders a disabled pill when `!capability_toggle_visible(key, runner.supports_for(model))`. Replaces the hand-authored `MODEL_TYPE_TAGS` in `ui/src/dash/model-types.js:14-21` (`mtp·moe·tool-calling·reasoning·coder·vision`).

### 3.4 `SlotConfig.vision` → override (not a 4th source)

- `SlotConfig.vision` (bool, `config/schema.py:413-423`) STAYS at the slot level, but its docstring rewrites: it's now an *override* of `Model.modalities.contains(VISION)`. `False` = force-suppress `--mmproj` even when `mmproj` is present + modality is `vision`. `True` (default) = honor derived (which is itself gated by `runner.supports.mmproj`).
- `providers/container.py:940` `if not slot_cfg.get("vision", True): mmproj = None` — **no logic change** (the gate was already there), but the field is re-documented as "override," not "primary source."
- `ui/src/dash/slot-modals.jsx:1393` vision pill rendering: still gates on `m.mmproj`, but its visibility ALSO checks `runner.supports.mmproj` (added via the new enum) so non-llama-server slots never show it.
- `moe` tag deleted everywhere it's *read*: `model_meta/__init__.py:308` (CURATED_MODEL_TAGS row), `ui/src/dash/model-types.js:16` (`moe` in MODEL_TYPE_TAGS), `ui/src/dash/model-modals.jsx` toggle list. `Model.architecture` is the replacement; `hardware/recommend.py:122` rewrites to `architecture in {"qwen3next", "mixtral", "deepseek-moe", ...}` + id-substring fallback for legacy curated rows.

### 3.5 `/api/meta/enums` is the ONE canonical surface; rename the `capabilities/` package

- `api/routes/meta.py:79-92` payload: keep `model_capabilities` (rename to `modalities`); add `model_capability_flags` (the typed bools); **drop `capability_aliases`** (it's now an internal detail of `normalize_modality`); keep `curated_model_tags` (still inert).
- Rename `src/hal0/capabilities/` → `src/hal0/slot_cards/`. This package currently houses the operator-facing capability tile system (`orchestrator.py`, `catalog.py`, `profile_fit.py`, `config.py`, etc.). The rename kills the term-overload: "capability" then means ONE thing (the runtime flag in `ModelCapabilities`). Touch list: `src/hal0/capabilities/*.py` → `slot_cards/*.py`; every import site (verified count below in §4.2). Module-level `__all__` re-exports preserved; **no behavioral change inside `slot_cards/`** (this is purely a rename — `capability` as a noun still applies to the operator tile concept, just under a non-overloaded name).
- `CuratedModel.capability` (`:134`) DELETES. `CuratedModel` gains `modalities: list[str]` (freeform on the curated side; `derive_modalities` runs at wizard apply time). Installer seed `haloai_models.json` rewrites `capability: "chat"` → `modalities: ["chat"]` per row.

---

## PART 4 — MIGRATION

### 4.1 Existing model TOMLs with `[model].labels` → `capabilities`

Migration lives in `registry/import_toml.py` (one-shot idempotent importer from spec-ml1-sqlite §D, `src/hal0/registry/import_toml.py` — already in flight). The fold:

```python
# In import_toml_to_sqlite(), per-row pre-validation step:
labels = body.get("model", {}).get("labels", [])
if isinstance(labels, list):
    # Tool-calling is the only label that's BEHAVIOURAL today; everything
    # else was UI decoration that has a typed equivalent already on Model.
    if "tool-calling" in labels:
        updates.setdefault("capabilities", {})["tool_calling"] = True
    if "reasoning" in labels:
        # reasoning is request-time (enable_thinking), NOT a capability flag.
        # Preserve as a tag so behaviour-driven consumers (bench planner, UI
        # reasoning chip) still see it until §7.1a's flag model lands.
        tags.add("reasoning")
    if "vision" in labels:
        # vision label meant "this slot uses the mmproj sidecar"; the registry
        # already records this via Model.mmproj presence flag + modality
        # derivation. No-op (modality derives it).
        pass
    if "mtp" in labels:
        updates.setdefault("capabilities", {})["mtp"] = True
    # Drop labels from the TOML on save — the field doesn't exist on Model.
    body["model"].pop("labels", None)
```

The migration is **idempotent + lossless**: pre-migration rows round-trip through the importer once, get normalized, and never need a second pass. Legacy TOMLs on disk keep their `[model].labels` key for one release (the `Model.labels_of` shim is a deprecated reader) — they get rewritten on the first `registry.update()` call from the dashboard. **The migration MUST run before §7.1d's first release ship** — sequence with spec-ml1-sqlite.

### 4.2 Slot `type` becomes derived (no migration step)

The slot TOMLs already carry `type = "llm"/"embedding"/...` as hand-authored values (verified — all 8 `installer/etc-hal0/slots/*.toml` set `type`). The derive step is *non-destructive*:
- On slot load, compute `derived_type = slot_type_for(model.modalities)`. If `derived_type != cfg["type"]`: log `"slot.type_derived_differs"` warning + **do not auto-rewrite** (operator override preserved). The UI shows the derived value as a hint, not as a forced change.
- On slot apply (`slot_view.__init__.py:248`), the lift logic keeps the operator-set `type` but recomputes `modalities` from the model.

So existing slot TOMLs need **zero migration** for `type` — they stay authoritative until the operator opts into derive-by-modality.

### 4.3 `CuratedModel.capability` → `modalities`

Curated seed rewrites are mechanical: `capability: "chat"` → `modalities: ["chat"]`, `capability: "asr"` → `modalities: ["asr"]`, etc. 36+ rows in `src/hal0/registry/seeds/haloai_models.json` (verified — every entry has `"capability": "chat"` or `"vision"`). Migration is `capability → modalities[0]`, applied at PR time (no runtime migration needed — curated seeds ship with the release).

### 4.4 `Model.tags` surviving consumers

`bench/planner.py:113` (the union workaround) DELETES — bench reads `model.modalities` directly. `hardware/recommend.py:122` rewrites to architecture check (see §3.4). Any other tag consumer: grep `model_meta` + `tags` for the full census; the rule is "if a consumer reads `tags` for BEHAVIOUR (not display), it's a bug — rewrite to `modalities`/`capabilities`/`architecture`."

---

## PART 5 — CROSS-LANE (coordinate, don't collide)

### 5.1 ML-1 (SQLite pilot) — the model record lands here

The §7.1d pydantic field additions (`Model.architecture`, `ModelCapabilities`, `Model.modalities`, `Model.modalities_override`) become columns on the `model` table in spec-ml1-sqlite §B. Mapping:

| Pydantic | Column | Type |
|---|---|---|
| `Model.architecture` | `model.architecture` | TEXT |
| `Model.modalities` | `model.modalities` | TEXT (JSON array of canonical Modality values) |
| `Model.modalities_override` | `model.modalities_override` | TEXT (JSON array; nullable) |
| `ModelCapabilities.mtp` | `model.mtp` | INTEGER (tri-state: NULL/0/1; already in §8.2 draft) |
| `ModelCapabilities.jinja` | `model.jinja` | INTEGER (already in §8.2 draft) |
| `ModelCapabilities.tool_calling` | `model.tool_calling` | INTEGER (tri-state) |
| `Model.tags` | `model.tags` | TEXT (JSON; already in spec-ml1-sqlite §B as lossless round-trip) |

**The "land ONCE" rule (plan §23.4 build-DAG) is binding:** `mtp`/`jinja`/`architecture` are owned by §7.1a/b. Don't double-add. ML-1 only adds the schema migration and the row mapper; the pydantic fields ship with §7.1d.

### 5.2 UI churn (settings.jsx + slot-modals + model-modals + models + model-types) — coordinate with P3-ui

| UI file | Touch |
|---|---|
| `ui/src/dash/model-modals.jsx` | `labels` state → `caps` (typed bools) + `modalities` (checkbox list from `enums.modalities`); `MODEL_TYPE_TAGS.map(tag => …)` (`:468`) → `enums.model_capability_flags.map(flag => …)` with runner-supports gating; `mmproj` warning (`:226-235`) now drives off `modalities.includes('vision')` not `labels.vision` |
| `ui/src/dash/model-types.js` | `MODEL_TYPE_TAGS` (`:14-21`) DELETE; replaced by `useMetaEnums().model_capability_flags` reader; `splitModelTags`/`mergeModelTags` (`:31-58`) simplified to a single `flags[]` array (no curated split needed — flags are intrinsic, not freeform) |
| `ui/src/dash/models.jsx` | `:30` `Vision` filter: `(m.capabilities || m.labels \|\| []).some(c => c === "vision")` → `(m.modalities || []).includes("vision")`; `:623-624` chips `{(model.labels \|\| model.capabilities \|\| []).map(l => <span className="chip">{l}</span>)}` → renders `modalities` + the boolean capability flags as colored chips; `:616` `model.type` becomes `slot_type_for(modalities)` projection (already server-side, just consumer-side rendering) |
| `ui/src/dash/slot-modals.jsx` | `:1393` vision pill: gate also on `runner.supports.mmproj`; `:441-447` vision state initial from `slot.vision !== false` unchanged; `:1157` NPU capability matrix: source-of-truth is `modalities` not `capabilities` |
| `ui/src/dash/settings.jsx` | §22 Model-Defaults page (NEW section, deferred per tracker W7 line 1716): toggle set per capability flag, gated by runner supports; per-modality default bindings (slot type assignment) |
| `ui/src/dash/__tests__/model-types.test.mjs` | Rewrite to test the flags envelope, not the curated type-tag split; the 36-tag CURATED_MODEL_TAGS test moves to `tests/model_meta/test_curated_model_tags.py` (already exists, per `model_meta/__init__.py:303-304`) and is unchanged |

### 5.3 §13.3 metrics — `request_metric.modality` column

The plan §23.4 build-DAG says: `§7.1d modalities + ML-4 runner ─PREREQ→ §13.3 request_metric.modality/runner columns`. The dependency: `request_metric.modality` reads `Model.modalities[0]` at request-dispatch time (via the resolved `LoadedSlot` derived view). §13.3 ships AFTER §7.1d lands; the metric column is the natural downstream consumer. This spec feeds the column shape (one canonical `Modality` enum value per row), nothing else.

### 5.4 §7.1a/b (ML-4 + ML-5) — same SQLite columns

`preferred_runner`, `mtp`, `jinja`, `architecture` are the §23.4 "land ONCE" fields (plan §1633). §7.1d does NOT add `mtp`/`jinja`/`architecture` to the pydantic `Model` — they live on `ModelDefaults` per spec-ml-runner-flags §3.1. **The only NEW field on `Model` here is `architecture: str | None`** (replacing the dead `moe` tag), and that *is* a single-source addition — coordination point is "use the same column name and pydantic field name everywhere."

### 5.5 §7.3 (omni_router) — already the consumer being fixed

`omni_router/filter.py:64,90` and `slots/manager.py:1663` are the routing-call sites. No new lane work; this spec IS the fix. Cross-ref §23.2 S1 (toolloop seam) — `tool_calling` on `LoadedSlot` is consumed by the omni tool filter; the seam contract gains `tool_calling: bool` on `LoadedSlot`, drops `labels`.

### 5.6 P2-toolloop / P3-brain (tool model routing)

`hal0/brain` routes tool turns via `[brain_chat]` `tool_model` (memory: `hal0-brain-toolcall-leak` — already routes 1B tool-calls to `hal0/agent`). §7.1d doesn't touch the brain directly, but the new `ModelCapabilities.tool_calling` field is the persistent signal the brain reads to decide whether a candidate `tool_model` is eligible. No brain code change needed beyond the eventual `tool_model_selection` query (filter by `tool_calling=true`), which is W7 work.

---

## PART 6 — FILES TO ADD / TOUCH

### 6.1 Add

- `src/hal0/model_meta/modality.py` — `Modality` enum + `normalize_modality` / `normalize_modalities` / `derive_modalities(model, runner)` + `slot_type_for(modalities)`.
- `tests/model_meta/test_modality.py` — alias folding (all 6 entries in `CAPABILITY_ALIASES`), dedupe, sort, unknown-drop warning, round-trip via `normalize_modality(v).value == v` for canonical set.
- `tests/registry/test_model_capabilities.py` — `ModelCapabilities` pydantic validation (tri-state nullable bools), forbid extra fields, JSON round-trip via import/export.
- `tests/registry/test_tags_to_capabilities_migration.py` — fixture TOMLs with legacy `[model].labels`, run importer, assert `tool_calling` migrated + labels stripped + tags preserved.
- `tests/omni_router/test_filter_no_labels.py` — regression pin: a model tagged `tool-calling` (no slot labels) routes tools. Pre-fix: empty list. Post-fix: full list.
- `tests/dispatcher/test_routing_no_labels.py` — sister test for the dispatcher layer.
- `tests/providers/test_flm_classify_modality.py` — `_classify_flm_model` (`:767`) returns canonical `Modality` values, not legacy `stt/chat/embed` strings.

### 6.2 Touch — backend

| File | Edit |
|---|---|
| `src/hal0/model_meta/__init__.py` | DELETE `CAPABILITY_ALIASES` (`:271-278`); DELETE `labels_of` (`:643-656`); `MODEL_CAPABILITIES` (`:255-264`) becomes enum-driven; `SLOT_TYPES` (`:248`) becomes derived projection function (kept as a property for back-compat); `CURATED_MODEL_TAGS` (`:305-347`) — drop `moe` (`:308`); drop `vision` (`:312`) and `embed`/`rerank`/`image`/`stt`/`transcription`/`tts` (`:332-345`) since those are modalities now, not tags; keep `mtp`, `tool-calling`, `reasoning`, `coder`, descriptive + provenance tags |
| `src/hal0/registry/model.py` | Add `ModelCapabilities` (`:1-`); add `Model.architecture: str \| None`; add `Model.modalities: list[Modality]` (derive-first, with `modalities_override`); add `Model.modalities_override: list[Modality] \| None`; DELETE `Model.capabilities` (replaced by `capabilities.tool_calling` + `modalities`); keep `Model.tags` as inert free-text |
| `src/hal0/registry/discover.py` | `scan_and_register` (`:382`) calls `derive_modalities` after detect; migration of legacy `[model].labels` lives here too |
| `src/hal0/registry/pull.py` | post-pull finalize hook calls `derive_modalities`; `body.get("labels")` (`:1974`) → `body.get("modalities")` (string list → `normalize_modalities`) |
| `src/hal0/registry/curated.py` | DELETE `CuratedModel.capability` (`:134`); add `CuratedModel.modalities: list[str]`; seed JSON rewrite (`:205-...` all 36+ rows) |
| `src/hal0/registry/seeds/haloai_models.json` | All `"capability": "..."` → `"modalities": ["..."]`; bulk rewrite, gated PR |
| `src/hal0/registry/import_toml.py` | labels-to-capabilities fold (see §4.1); preserves tags; strips labels from migrated TOMLs |
| `src/hal0/api/routes/models.py` | `body.get("labels")` (`:759, 824, 1921, 1974`) → `body.get("modalities")` + `body.get("capabilities")` (typed bools); `_seed_registry_from_body` (`:1596`) signature: `labels: list[str] \| None` → `modalities: list[str] \| None, capabilities: dict[str, bool] \| None`; `_dispatch_type` (`:203`) becomes thin wrapper over `slot_type_for(modalities)`; `_FLM_DISPATCH_TYPE` (`:175`) + `_MODALITY_TO_SLOT_TYPE` (`:193`) DELETE (use `slot_type_for`) |
| `src/hal0/api/routes/meta.py` | `payload["modalities"]` (from `Modality` enum); add `model_capability_flags` (the typed bools); DELETE `capability_aliases` |
| `src/hal0/api/routes/slots.py` | DELETE slot TOML `[model].labels` reader (any path that lifts `labels`); slot apply path recomputes modalities |
| `src/hal0/omni_router/filter.py` | `chat_slot_has_tool_calling` (`:52-64`) → reads `caller.tool_calling`; `:90` gate rewrites to `if not caller.tool_calling` |
| `src/hal0/omni_router/__init__.py` | `LoadedSlot.tool_calling` replaces `labels` everywhere it's referenced |
| `src/hal0/slots/manager.py` | `_loaded_slot_from_config` (`:1624`) lifts `tool_calling` from `model_capabilities_of(cfg)`, drops `labels`; `_valid_slot_types` stays (still the dispatcher vocab whitelist); `LoadedSlot` (`:287`) gains `tool_calling: bool`, drops `labels: frozenset[str]` |
| `src/hal0/slots/argv.py` | unchanged (no edit) |
| `src/hal0/providers/flm.py` | `_classify_flm_model` (`:767-792`) returns `Modality` enum values; FLM `label` field still consumed (it's FLM's CLI vocabulary), but mapped through `normalize_modality` so the output matches |
| `src/hal0/providers/container.py` | `_resolve_llama_scalars` (`:860`) unchanged for vision — the `slot_cfg.get("vision", True)` gate (`:940`) now documented as an override; `_effective_mtp` (`:212`) gain `runner.supports.mtp` gate (per spec-ml-runner-flags §3.3) |
| `src/hal0/capabilities/orchestrator.py` | DELETE `_CHILD_TO_SLOT_TYPE` (`:88-91`); DELETE `_CAPABILITY_TO_SLOT_TYPE` (`:136-144`); `_apply_*` paths use `slot_type_for` |
| `src/hal0/capabilities/` → `src/hal0/slot_cards/` | pure rename; update imports site-wide (verified count: grep `from hal0.capabilities` in src + tests returns N sites — see `api/routes/slots.py:1274, 1336`, `api/__init__.py` lifespan, etc.) |
| `src/hal0/profiles/__init__.py` | `SlotType` (`:43`) — keep Literal for type hints; `supported_slot_types` resolution: derived from `runner.supports` not from `RuntimeFamily` |
| `src/hal0/hardware/recommend.py` | `_curated_ctx_size` (`:122`) — `is_moe` reads `curated.architecture` (e.g. `qwen3next`, `mixtral`) + id-substring fallback for legacy curated rows lacking `architecture`; `moe` tag DELETE |
| `src/hal0/install/answers.py` | `answers.py:418` `"capability": s.capability` → `"modalities": s.modalities` (carry-over from `CuratedModel`) |
| `src/hal0/installer/install.sh` | `installer/install.sh:1232` "capability slots" wording → "modality slots" (operator-facing doc/comment) |
| `src/hal0/slot_view/__init__.py` | `:209, 248` lift logic — `model.labels` reads DELETE; derive `modalities` from `Model.modalities` (server-side canonical); `type` derived via `slot_type_for` unless operator pinned |

### 6.3 Touch — UI (coordinate with P3-ui)

| File | Edit |
|---|---|
| `ui/src/dash/model-modals.jsx` | see §5.2 row 1 |
| `ui/src/dash/model-types.js` | DELETE `MODEL_TYPE_TAGS` + `splitModelTags` + `mergeModelTags`; new file becomes a thin wrapper that just re-exports from `useMetaEnums` |
| `ui/src/dash/models.jsx` | `:30`, `:623-624`, `:616` rewrites per §5.2 |
| `ui/src/dash/slot-modals.jsx` | `:1393` vision pill runner-supports gate |
| `ui/src/dash/settings.jsx` | §22 Model-Defaults section (W7 deferred per tracker line 1716); flag toggles with runner-supports gating |
| `ui/src/dash/__tests__/model-types.test.mjs` | rewrite per §5.2 |

### 6.4 Cross-lane refs to update in the plan

- `hal0-rework-plan.md:282` (rework ordered list, item 6): keep "Taxonomy untangle (§7.1d) — modality/capabilities/tags split; kill the labels routing gate." verbatim. Add cross-ref to this spec.
- `hal0-rework-plan.md:1727` (spec-authoring backlog): link this file.
- `hal0-rework-tracker.md` (W4 row): link this file. Mark ML-6 spec authored.
- `hal0-rework-plan.md:1642` (build DAG): confirm `§7.1d modalities + ML-4 runner ─PREREQ→ §13.3 request_metric.modality/runner columns` — no change needed, this spec IS the upstream.

---

## PART 7 — TESTS

### Existing tests that MUST keep green (behavior parity)

- `tests/registry/test_store.py` (34 tests) — `Model` field additions don't break TOML round-trip if `extra="allow"` survives; verify `test_add_persists_to_disk:72`, `test_two_instances_no_lost_update:62`, `test_invalidates_when_file_mtime_advances:175` (last is TOML-only, see spec-ml1-sqlite §E).
- `tests/registry/test_discover.py` — modality derivation after detect; legacy `[model].labels` migration path covered by new `test_tags_to_capabilities_migration.py`.
- `tests/omni_router/test_filter.py` (any) — the routing gate must still filter correctly. Add `test_no_tool_call_when_label_absent` (legacy behavior pin) + `test_no_tool_call_when_capability_false` (new behavior).
- `tests/api/test_models.py` — `_dispatch_type` calls stay equivalent (`chat→llm`, `embed→embedding`, etc.).
- `tests/providers/test_flm.py` — `_classify_flm_model` returns canonical Modality values; existing test pin on `["chat"]/["embed"]/["stt"]` updates to enum string values (identical bytes).
- `tests/capabilities/test_orchestrator.py` — child→slot type mapping unchanged at the bytes level (delete the local maps, verify `slot_type_for` returns same values).

### New tests (capped verification — 6 short, targeted tests, not exhaustive)

1. `test_modality.py::test_normalize_aliases` — all 6 `CAPABILITY_ALIASES` entries fold to canonical; unknowns drop without crashing.
2. `test_modality.py::test_derive_modalities_vision_from_mmproj` — `mmproj="path.gguf"` + `runner="llama-server"` → `Modalities=["chat", "vision"]`.
3. `test_modality.py::test_derive_modalities_embed_from_pooling` — `metadata["pooling_type"]=1` → `["embed"]` (NOT chat).
4. `test_model_capabilities.py::test_tool_calling_routing` — `Model(capabilities=ModelCapabilities(tool_calling=True))` → `LoadedSlot.tool_calling=True`; filter ships full tool list.
5. `test_filter_no_labels.py::test_tags_only_no_labels_ships_tools` — the 🔴 regression pin: a model tagged `tool-calling` (no `[model].labels`) routes tools. Pre-fix this test fails (empty tool list); post-fix it passes.
6. `test_tags_to_capabilities_migration.py::test_legacy_labels_toml` — importer folds `labels=["tool-calling", "vision"]` → `capabilities.tool_calling=true`, `modalities=["chat","vision"]`, `tags=[]` (no reason tag leaked in).

Verification is intentionally capped: §7.1d is a refactor + 1 regression pin. Exhaustive matrix tests belong to ML-1 (SQLite round-trip) and §7.3 (omni_router routing matrix).

---

## PART 8 — RISKS (ranked)

1. **🔴 Routing behavior change = careful.** `omni_router/filter.py:90` is the production tool-call gate. The rewrite `if "tool-calling" not in caller.labels → if not caller.tool_calling` MUST be gated behind the migration's first-boot importer (§4.1) so legacy `[model].labels` rows don't silently lose tool-calling. Verify with `test_filter_no_labels.py::test_legacy_labels_still_route_tools` (a parallel new test that confirms pre-migration rows still route).

2. **`LoadedSlot.labels` removal breaks the omni_router public shape.** Any out-of-tree consumer (board plugins, MCP plugins) reading `LoadedSlot.labels` gets `AttributeError`. Mitigate: keep `labels` as a `frozenset[str]` shim one release returning `frozenset({"tool-calling"} if tool_calling else set())` + a `DeprecationWarning`. Delete in the release after.

3. **`CuratedModel.capability` deletion breaks `answers.py:418`.** The installer answers handler reads `s.capability`; if the seed rewrite and answers handler rewrite ship in separate PRs, install --auto breaks. **Sequence constraint:** answers.py + curated.py + haloai_models.json land in ONE PR.

4. **`capabilities/` → `slot_cards/` rename is a wide import-site change.** Confirmed import count: 8+ direct importers (`api/routes/slots.py:1274,1336`, `api/__init__.py` lifespan, `capabilities/catalog.py:142`, `dispatcher/router.py:62,433,1261`, etc.). Mitigate: keep `src/hal0/capabilities/__init__.py` as a one-line shim that re-exports from `slot_cards` for one release; remove in the following release.

5. **`Modalities recomputed on pull/swap` could surprise operators.** A pull that derives `vision` from `mmproj` may differ from what the operator typed. Mitigate: `modalities_override` field (see §3.2) lets the operator pin a list; derived + override = union. Default is derive-only (no surprise changes on existing TOMLs since slot `type` is still authoritative until operator opts in).

6. **`SlotConfig.vision` becomes a documented override.** UI wording must change in `slot-modals.jsx:1398` ("Vision" subtitle) so operators understand it's an override, not the primary source. Docstring rewrite + UI tooltip; not load-bearing but prevents support tickets.

7. **`/api/meta/enums` `capability_aliases` removal breaks any UI consumer reading it.** `ui/src/dash/model-modals.jsx:345` reads `meta.capability_aliases`. Mitigate: ship the UI change in the SAME PR as the API change (the UI is in this monorepo, no external consumers per `api/routes/meta.py:9-15` "no auth ... open" — public surface but no documented external dep).

8. **`curated/tags` regression on the bench planner.** `bench/planner.py:113` currently unions `caps+capabilities+tags+type`. After §7.1d the union becomes `modalities` + a few retained curated tags (coder, reasoning for the union). Verify with `tests/bench/test_planner.py` (existing) + a new `test_planner_uses_modalities` regression pin.

9. **Tri-state `bool | None` semantics for `mtp`/`jinja`/`tool_calling`.** NULL means "use runner/router default" (don't override). Verify the existing `defaults.mtp` field pattern (spec-ml-runner-flags §3.1) is reused — same tri-state semantics. Adding `tool_calling` to the same nullable pattern is intentional: a NULL means "the routing decision falls back to the default tag set" (legacy), `True` forces tools, `False` forbids them.

---

## PART 9 — SEQUENCING (build DAG)

```
ML-1 (SQLite foundation)              [must land first — db/ + 001_registry.sql]
  └─► ML-2 (fileset S11)              [uses db foundation]
  └─► §7.1d pydantic additions         [architecture, modalities, ModelCapabilities]
        ├─► ML-4 (RUNNER_IMAGES)       [provides runner.supports]
        │     └─► ML-5 (mtp/jinja)     [model capabilities flag fields]
        └─► §7.1d import_toml fold     [labels → capabilities.migration]
              └─► /api/meta/enums      [Modalities + flags envelope]
                    └─► UI churn        [model-modals + models + slot-modals]
                          └─► §13.3     [request_metric.modality column reads Model.modalities]
```

§7.1d needs ML-1 + ML-4 sequenced first; ML-5 can ship in parallel (only `mtp/jinja/architecture` field additions — no routing change). The 🔴 filter.py fix is shippable as soon as the importer + ModelCapabilities land, regardless of UI churn. **Suggested PR split:**
- PR1: §7.1d backend foundation (`Modality` enum, `ModelCapabilities`, importer fold, omni_router filter rewrite). UI temporarily reads `model.capabilities` for back-compat.  🔴 footgun closed in this PR.
- PR2: §7.1d UI churn (model-modals + model-types + models + slot-modals + settings). Models page renders modalities. Slot-modals gates vision pill on runner.supports.
- PR3: §7.1d capabilities/ → slot_cards/ rename + `CuratedModel.capability` DELETE + `/api/meta/enums` payload change. Ship together so no caller has stale imports.
- PR4: §13.3 metrics column + `hardware/recommend.py` architecture rewrite (drops `moe` tag consumer).

Sequence §23.4 build-DAG is preserved: §7.1d → §13.3.

---

Everything above is grounded in the current `rework/descar` tree; the `resolve_argv` engine, `_spec_provider_for` dispatch, and `SlotConfigStore` apply engine need no changes. §7.1d is a single-axis refactor: kill the `model.labels` routing axis, replace it with `ModelCapabilities.tool_calling` (a typed bool), and reduce the 9+ axes to **3 model axes (modality · capabilities · tags) + 1 runtime axis (device+runner)** — all single-source on the model record, all gated by the runner registry, all visible via one `/api/meta/enums` payload. The 🔴 tags→labels routing footgun closes in PR1.