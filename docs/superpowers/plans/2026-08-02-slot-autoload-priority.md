# Slot autoload + eviction priority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every slot an explicit `autoload` (start on boot) setting and an integer `priority` (0–100) that orders eviction victims, replacing the implicit model-bound boot start and the `lru=true` eviction opt-in.

**Architecture:** Two new `SlotConfig` fields persisted in per-slot TOML. `autoload` gates the Quadlet `[Install] WantedBy=hal0.target` stanza (the only thing that boot-starts a slot); a migration shim derives `true` for legacy TOMLs with a bound model. `priority` reorders the two victim-picking paths (`SlotReaper.pressure_evict_once`, `preload_evict`) to `(priority asc, last_used asc)` and retires the `lru=true` gate. Both fields are lifted into the `/api/slots` snapshot and edited from the slot drawer.

**Tech Stack:** Python 3.12 / pydantic v2 / FastAPI / pytest (backend), React + vitest (ui/), Podman Quadlet units.

Spec: `docs/superpowers/specs/2026-08-02-slot-autoload-priority-design.md`

## Global Constraints

- `autoload`: new slots default `false`; TOML missing the key with non-empty `[model].default` → effective `true` (migration shim); explicit value always wins.
- `priority`: int, 0–100 inclusive, default 50. Lower evicts first; `last_used` ascending breaks ties.
- `pinned` slots and default anchors (`agent`/`utility`/`npu`) stay never-evicted; `priority=100` is NOT pinned.
- `lru` TOML key: ignored, deprecation-warned once per slot, never an error.
- Adoption (`_maybe_adopt_running_slot`) is untouched.
- Test runs: prefix with `HAL0_HOME=$(mktemp -d)` (kills the ~204 `/etc/hal0-perms` pseudo-errors), e.g. `HAL0_HOME=$(mktemp -d) uv run pytest <path> -x -q`.
- Commit style: Conventional Commits, lowercase imperative summary, ≤72 chars.
- Before every commit: `make lint` (ruff) green on touched files.

---

### Task 1: SlotConfig fields + raw-dict helpers

**Files:**
- Modify: `src/hal0/config/schema.py` (add fields after `pinned`, ~line 565)
- Modify: `src/hal0/slots/activation.py` (add `autoload_enabled`)
- Modify: `src/hal0/slots/reaper.py` (add `eviction_priority`, export it)
- Test: `tests/slots/test_autoload_priority.py` (new file)

**Interfaces:**
- Produces: `SlotConfig.autoload: bool | None` (post-validation always `bool`), `SlotConfig.priority: int`.
- Produces: `hal0.slots.activation.autoload_enabled(cfg: dict | None) -> bool` — effective boot-start for a RAW TOML dict.
- Produces: `hal0.slots.reaper.eviction_priority(cfg: dict | None) -> int` — effective priority for a RAW TOML dict, clamped 0–100 (raw-dict readers clamp defensively — the sweep must never crash on a hand-authored TOML; pydantic paths hard-reject out-of-range instead).

Background: the eviction/render paths consume raw TOML dicts from `_load_slot_config`, not pydantic models — same split as `is_pinned(name, cfg)` (`reaper.py:89`). Both a schema field (validation + API acceptance) and a raw-dict helper are needed.

- [ ] **Step 1: Write the failing tests**

```python
"""Slot autoload + eviction-priority field semantics (spec 2026-08-02)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import SlotConfig
from hal0.slots.activation import autoload_enabled
from hal0.slots.reaper import eviction_priority


def _cfg(**kw) -> SlotConfig:
    base: dict = {"name": "s1", "port": 8090}
    base.update(kw)
    return SlotConfig(**base)


class TestSlotConfigAutoload:
    def test_default_false_without_model(self) -> None:
        assert _cfg().autoload is False

    def test_derives_true_from_bound_model(self) -> None:
        # Migration shim: legacy TOML (no autoload key) with a bound model
        # keeps its implicit boot start.
        assert _cfg(model={"default": "qwen3"}).autoload is True

    def test_explicit_false_wins_over_bound_model(self) -> None:
        assert _cfg(model={"default": "qwen3"}, autoload=False).autoload is False

    def test_explicit_true_without_model(self) -> None:
        assert _cfg(autoload=True).autoload is True


class TestSlotConfigPriority:
    def test_default_50(self) -> None:
        assert _cfg().priority == 50

    @pytest.mark.parametrize("value", [0, 100])
    def test_bounds_accepted(self, value: int) -> None:
        assert _cfg(priority=value).priority == value

    @pytest.mark.parametrize("value", [-1, 101])
    def test_out_of_range_rejected(self, value: int) -> None:
        with pytest.raises(ValidationError):
            _cfg(priority=value)


class TestAutoloadEnabledRawDict:
    def test_none_and_empty(self) -> None:
        assert autoload_enabled(None) is False
        assert autoload_enabled({}) is False

    def test_legacy_bound_model_derives_true(self) -> None:
        assert autoload_enabled({"model": {"default": "qwen3"}}) is True

    def test_explicit_false_wins(self) -> None:
        assert autoload_enabled({"autoload": False, "model": {"default": "qwen3"}}) is False

    def test_explicit_true_without_model(self) -> None:
        assert autoload_enabled({"autoload": True}) is True

    def test_model_without_default_is_false(self) -> None:
        assert autoload_enabled({"model": {}}) is False


class TestEvictionPriorityRawDict:
    def test_default_on_missing(self) -> None:
        assert eviction_priority(None) == 50
        assert eviction_priority({}) == 50

    def test_reads_value(self) -> None:
        assert eviction_priority({"priority": 10}) == 10

    @pytest.mark.parametrize("bad", [True, "10", 3.5, None])
    def test_non_int_falls_back(self, bad) -> None:
        assert eviction_priority({"priority": bad}) == 50

    def test_clamps(self) -> None:
        assert eviction_priority({"priority": -5}) == 0
        assert eviction_priority({"priority": 999}) == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `HAL0_HOME=$(mktemp -d) uv run pytest tests/slots/test_autoload_priority.py -x -q`
Expected: FAIL — `ImportError: cannot import name 'autoload_enabled'` (or `eviction_priority`).

- [ ] **Step 3: Implement**

`src/hal0/config/schema.py` — insert directly after the `pinned` field (~line 565), inside `SlotConfig`:

```python
    autoload: bool | None = Field(
        default=None,
        description=(
            "Explicit boot-start setting (spec 2026-08-02). True → the "
            "generated Quadlet unit carries [Install] WantedBy=hal0.target "
            "and the slot starts at boot; False → the unit exists but "
            "nothing starts it automatically (manual load/swap only). "
            "None (key absent on disk) is the migration shim: derived from "
            "the legacy implicit signal — non-empty [model].default — by "
            "_derive_autoload below, so pre-field TOMLs keep their boot "
            "behavior. API-created slots always persist an explicit value "
            "(create route defaults it to false)."
        ),
    )
    priority: int = Field(
        default=50,
        ge=0,
        le=100,
        description=(
            "Eviction priority (spec 2026-08-02): lower evicts first, "
            "last_used breaks ties. Orders victims in pressure eviction "
            "(SlotReaper.pressure_evict_once) and pre-load eviction "
            "(preload_evict). Replaces the deprecated lru=true opt-in — "
            "every non-pinned slot is now a candidate. 100 is NOT a pin "
            "(evicted last, still evictable); use `pinned` to exempt."
        ),
    )
```

And a `model_validator(mode="after")` next to the other `SlotConfig` validators (search for `@model_validator` inside the class and add alongside):

```python
    @model_validator(mode="after")
    def _derive_autoload(self) -> "SlotConfig":
        """Resolve the migration shim: absent key → legacy implicit signal.

        A TOML written before the field existed boot-started iff it had a
        model bound (#1369 activation semantics); deriving here means the
        next save writes the value back explicitly.
        """
        if self.autoload is None:
            self.autoload = bool(self.model.default)
        return self
```

`src/hal0/slots/activation.py` — add after `is_activated` (~line 71):

```python
def autoload_enabled(cfg: dict[str, Any] | None) -> bool:
    """Effective boot-start setting for a RAW slot TOML dict (spec 2026-08-02).

    Mirror of ``SlotConfig._derive_autoload`` for the raw-dict readers
    (unit render, slot_view lift): an explicit ``autoload`` key wins;
    an absent key falls back to the legacy implicit signal — a bound
    model (:func:`is_activated`) — so pre-field TOMLs keep their boot
    behavior. Same raw-dict contract as :func:`hal0.slots.reaper.is_pinned`.
    """
    if not isinstance(cfg, dict):
        return False
    raw = cfg.get("autoload")
    if raw is not None:
        return bool(raw)
    return is_activated(cfg)
```

Add `autoload_enabled` to `activation.py`'s `__all__` if one exists (check the file tail).

`src/hal0/slots/reaper.py` — add after `is_pinned` (~line 105):

```python
_DEFAULT_EVICTION_PRIORITY: int = 50


def eviction_priority(cfg: dict[str, Any] | None) -> int:
    """Effective eviction priority for a RAW slot TOML dict (0-100).

    Lower evicts first; ties fall back to LRU order at the call sites.
    Defensive by design — the sweep must never crash on a hand-authored
    TOML: a missing/bool/non-int value falls back to the default and an
    out-of-range int is clamped. The pydantic paths (SlotConfig ge/le)
    hard-reject instead; this is the fail-open mirror, same contract as
    :func:`is_pinned` reading the raw dict.
    """
    if not isinstance(cfg, dict):
        return _DEFAULT_EVICTION_PRIORITY
    raw = cfg.get("priority")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return _DEFAULT_EVICTION_PRIORITY
    return max(0, min(100, raw))
```

Append `"eviction_priority"` and `"_DEFAULT_EVICTION_PRIORITY"` to `reaper.py`'s `__all__` (keep it sorted).

- [ ] **Step 4: Run tests to verify they pass**

Run: `HAL0_HOME=$(mktemp -d) uv run pytest tests/slots/test_autoload_priority.py tests/slots/test_slot_schema.py -q`
Expected: PASS (schema round-trip suite included to catch field regressions).

- [ ] **Step 5: Commit**

```bash
make lint
git add src/hal0/config/schema.py src/hal0/slots/activation.py src/hal0/slots/reaper.py tests/slots/test_autoload_priority.py
git commit -m "feat(slots): add autoload and eviction-priority slot config fields"
```

---

### Task 2: Quadlet `[Install]` gated on autoload

**Files:**
- Modify: `src/hal0/providers/container.py` — `_render_quadlet_from_plan` (~line 622, `[Install]` emit ~line 797) and `_render_quadlet_text` (~line 1733)
- Test: `tests/providers/test_container.py` (class `TestRenderUnit`, `_render_llama` shim at line 67)

**Interfaces:**
- Consumes: `hal0.slots.activation.autoload_enabled(cfg)` (Task 1).
- Produces: `_render_quadlet_from_plan(instance_token, plan, *, publish_host=..., network_mode_default=..., autoload: bool = True) -> str`. With `autoload=False` the rendered unit has NO `[Install]` section (nothing boot-starts it; `systemctl start` / `_write_and_start_unit`'s `restart` still work — they never depended on `[Install]`).

- [ ] **Step 1: Write the failing tests**

In `tests/providers/test_container.py`: add a passthrough kwarg to the `_render_llama` shim — add `autoload=True` to its keyword params and thread it into the shim's final `_render_quadlet_from_plan(...)` call as `autoload=autoload`. Then add to `TestRenderUnit`:

```python
    def test_install_stanza_present_by_default(self) -> None:
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        assert "[Install]" in unit
        assert "WantedBy=hal0.target" in unit.splitlines()

    def test_autoload_false_omits_install_stanza(self) -> None:
        """autoload=false → no [Install] → nothing boot-starts the slot.

        The unit must remain manually startable: [Service]/Restart survive.
        """
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_llama(
            "test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags,
            autoload=False,
        )
        assert "[Install]" not in unit
        assert "WantedBy=hal0.target" not in unit
        assert "[Service]" in unit
        assert "Restart=always" in unit.splitlines()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `HAL0_HOME=$(mktemp -d) uv run pytest tests/providers/test_container.py::TestRenderUnit -q`
Expected: FAIL — `TypeError: _render_quadlet_from_plan() got an unexpected keyword argument 'autoload'`.

- [ ] **Step 3: Implement**

`_render_quadlet_from_plan` signature (line ~622) gains the kwarg:

```python
def _render_quadlet_from_plan(
    instance_token: str,
    plan: RuntimeLaunchPlan,
    *,
    publish_host: str = "127.0.0.1",
    network_mode_default: str = "",
    autoload: bool = True,
) -> str:
```

Replace the tail block that appends `[Service]`/`[Install]` (~line 787–801) so `[Install]` is conditional:

```python
    lines.extend(
        [
            "",
            "[Service]",
            "Restart=always",
            "RestartSec=3",
            f"SyslogIdentifier={container_name}",
            "StandardOutput=journal",
            "StandardError=journal",
        ]
    )
    # [Install] WantedBy=hal0.target is the ONLY thing that boot-starts a
    # slot (Quadlet's generator links the .wants symlink from unit content —
    # no systemctl enable anywhere). autoload=false omits it wholesale: the
    # unit stays start-able (load/swap use `systemctl restart`, which never
    # consulted [Install]) but nothing pulls it up at boot. Spec 2026-08-02.
    if autoload:
        lines.extend(["", "[Install]", "WantedBy=hal0.target"])
    lines.append("")
    return "\n".join(lines)
```

`_render_quadlet_text` (~line 1733) threads the slot's effective setting — add the import at the top of the file's import block (`from hal0.slots.activation import autoload_enabled`; check whether `activation` is already imported) and change the return:

```python
        return _render_quadlet_from_plan(
            token,
            plan,
            publish_host=_slot_publish_host(),
            network_mode_default=_slot_network_mode(),
            autoload=autoload_enabled(slot_cfg),
        )
```

Also update the docstring line in `_write_and_start_unit` (~line 1704) that claims `[Install] WantedBy=hal0.target` unconditionally handles boot-enable — note it is autoload-gated now.

- [ ] **Step 4: Run tests to verify they pass**

Run: `HAL0_HOME=$(mktemp -d) uv run pytest tests/providers/test_container.py -q`
Expected: PASS (whole file — `test_unit_has_expected_sections` must still pass, default is `autoload=True`).

- [ ] **Step 5: Commit**

```bash
make lint
git add src/hal0/providers/container.py tests/providers/test_container.py
git commit -m "feat(slots): gate quadlet [Install] boot-start on slot autoload"
```

---

### Task 3: re-render unit when autoload changes via PUT /config

**Files:**
- Modify: `src/hal0/api/routes/slots.py` — new helper + call in `update_slot_config` (~line 1082)
- Test: `tests/api/test_slots_routes.py`

**Interfaces:**
- Consumes: `ContainerProvider.rerender_unit_sync(cfg, model_info) -> bool`, `ContainerProvider.daemon_reload()`, `hal0.providers.container._best_effort_model_info(cfg, None)` (same trio the updater sweep uses, `src/hal0/updater/updater.py:2390-2418`).
- Produces: module-level `_rerender_slot_unit_best_effort(cfg: dict[str, Any]) -> None` in `routes/slots.py` (module-level so tests monkeypatch it).

Why: `update_config` only rewrites TOML ("the unit is re-rendered on the next load/restart"). Without this, flipping autoload off then rebooting still starts the slot from the stale unit's `[Install]` — the exact surprise the feature removes.

- [ ] **Step 1: Write the failing test**

Add to `tests/api/test_slots_routes.py`, following the file's existing client/monkeypatch fixture pattern (reuse the same fixtures the neighboring `PUT /config` tests use — read two of them first and copy their setup):

```python
def test_put_config_autoload_triggers_unit_rerender(client, monkeypatch):
    """PUT {autoload: false} rewrites the on-disk Quadlet unit immediately —
    otherwise a reboot before the next load still boot-starts the slot."""
    from hal0.api.routes import slots as slots_routes

    calls: list[dict] = []
    monkeypatch.setattr(
        slots_routes, "_rerender_slot_unit_best_effort", lambda cfg: calls.append(cfg)
    )
    resp = client.put("/api/slots/<existing-slot>/config", json={"autoload": False})
    assert resp.status_code == 200
    assert len(calls) == 1


def test_put_config_priority_skips_unit_rerender(client, monkeypatch):
    from hal0.api.routes import slots as slots_routes

    calls: list[dict] = []
    monkeypatch.setattr(
        slots_routes, "_rerender_slot_unit_best_effort", lambda cfg: calls.append(cfg)
    )
    resp = client.put("/api/slots/<existing-slot>/config", json={"priority": 10})
    assert resp.status_code == 200
    assert calls == []
```

(`<existing-slot>`: use whatever seeded slot name the neighboring PUT-config tests in that file target.) Also assert the priority test round-trips: `resp.json()` or a follow-up GET carries `priority == 10` if the file's pattern makes that cheap.

Value validation: the PUT path merges raw dicts (`reconcile_slot_updates`) — pydantic's `ge/le` never runs there. Add tests:

```python
@pytest.mark.parametrize("bad", [-1, 101, "high", 3.5, True])
def test_put_config_priority_out_of_range_rejected(client, bad):
    resp = client.put("/api/slots/<existing-slot>/config", json={"priority": bad})
    assert resp.status_code == 400
    assert resp.json()["code"] == "validation.invalid_value"


def test_put_config_autoload_must_be_bool(client):
    resp = client.put("/api/slots/<existing-slot>/config", json={"autoload": "yes"})
    assert resp.status_code == 400
```

(Match the error-body shape the file's other BadRequest assertions use — the repo's `BadRequest` is HTTP 400 with a `code`; the spec's "422" maps to this house convention.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `HAL0_HOME=$(mktemp -d) uv run pytest tests/api/test_slots_routes.py -q -k autoload`
Expected: FAIL — `AttributeError: ... has no attribute '_rerender_slot_unit_best_effort'`.

- [ ] **Step 3: Implement**

In `routes/slots.py`, module level (near the other underscore helpers):

```python
def _rerender_slot_unit_best_effort(cfg: dict[str, Any]) -> None:
    """Rewrite a slot's on-disk Quadlet unit after an ``autoload`` change.

    The unit's [Install] stanza is baked at render time; without this, a
    toggle only takes effect on the next load — and a reboot in between
    boot-starts a slot the operator just opted out. Same provider trio as
    the updater's post-update unit sweep (updater.py). Never raises past
    the caller's guard; never starts/stops anything.
    """
    from hal0.providers.container import ContainerProvider, _best_effort_model_info

    provider = ContainerProvider()
    model_info = _best_effort_model_info(cfg, None)
    if provider.rerender_unit_sync(cfg, model_info):
        provider.daemon_reload()
```

In `update_slot_config`, after the `record_action` block (after `_rec.after = body`), before building `out`:

```python
    if "autoload" in body:
        try:
            cfg_after = await _safe_config(sm, name)
            if cfg_after:
                await asyncio.to_thread(_rerender_slot_unit_best_effort, cfg_after)
        except Exception:
            # Best-effort: the TOML is the source of truth; the updater
            # sweep / next load converges the unit if this fails.
            pass
```

(`asyncio` is already imported in this module. If the module has a `log`, log the swallowed exception at warning level instead of a bare `pass`.)

Value validation helper, module level next to `_reject_unknown_config_keys`:

```python
def _validate_autoload_priority(body: dict[str, Any]) -> None:
    """Value-level guard for the two spec-2026-08-02 fields.

    The config write path merges RAW dicts (reconcile_slot_updates), so
    SlotConfig's ge/le never runs — an out-of-range priority would persist
    to TOML silently and the eviction helper would clamp it forever.
    """
    if "autoload" in body and not isinstance(body["autoload"], bool):
        raise BadRequest(
            "autoload must be a boolean",
            details={"autoload": repr(body["autoload"])},
            code="validation.invalid_value",
        )
    if "priority" in body:
        prio = body["priority"]
        if isinstance(prio, bool) or not isinstance(prio, int) or not 0 <= prio <= 100:
            raise BadRequest(
                "priority must be an integer between 0 and 100",
                details={"priority": repr(prio)},
                code="validation.invalid_value",
            )
```

Call it in `update_slot_config` and `create_slot`, right after their existing `_reject_unknown_config_keys(body)` calls:

```python
    _validate_autoload_priority(body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `HAL0_HOME=$(mktemp -d) uv run pytest tests/api/test_slots_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
make lint
git add src/hal0/api/routes/slots.py tests/api/test_slots_routes.py
git commit -m "feat(api): re-render slot unit when autoload changes"
```

---

### Task 4: priority ordering in pressure eviction, retire lru gate

**Files:**
- Modify: `src/hal0/slots/reaper.py` — `pressure_evict_once` (~line 381–439), new `_warn_lru_deprecated`
- Test: `tests/slots/test_pressure_eviction.py`

**Interfaces:**
- Consumes: `eviction_priority(cfg)` (Task 1).
- Produces: `pressure_evict_once` victim order `(priority asc, last_used asc)`; `lru` key ignored + warned. `_warn_lru_deprecated(canonical_name: str, cfg: dict | None) -> None` (shared with Task 5 — import it there).

- [ ] **Step 1: Update existing tests + write new failing tests**

First read `tests/slots/test_pressure_eviction.py` fully. Existing expectations that change:
- Any test asserting a slot WITHOUT `lru = true` is NOT pressure-evicted → now IS evicted (flip the assertion; rename the test to describe the new contract, e.g. `test_non_lru_slot_now_evictable_priority_order`).
- Tests that set `lru = true` merely to opt in keep passing (key is ignored, slots were already eligible) — leave them, they now also prove the key is harmless.

Add new tests in that file, following its existing manager/monkeypatch harness (reuse the same fixture helpers the surrounding tests use for fake slots, `_last_used`, and the free-mb probe):

```python
async def test_pressure_evicts_lowest_priority_first(...):
    """Three resident slots, equal idle age, priorities 10/50/90 →
    eviction order is the priority order, not LRU."""
    # setup: three READY slots, same last_used, configs with
    # priority 90 ("keeper"), 50 ("mid"), 10 ("cheap"); probe returns
    # below-floor until two evictions have happened.
    # assert: unload called for "cheap" then "mid"; "keeper" untouched.


async def test_pressure_priority_tie_breaks_lru(...):
    """Equal priority → oldest last_used evicted first (old behavior kept
    within a priority tier)."""


async def test_pressure_skips_pinned_regardless_of_priority(...):
    """pinned=true + priority=0 is still never evicted."""


async def test_lru_key_ignored_and_warned_once(caplog, ...):
    """A cfg carrying lru=false is evictable anyway; slot.lru_flag_deprecated
    logged exactly once across two sweeps."""
```

Flesh these out against the file's real harness — the docstring contracts above are the requirements; the fixture mechanics must match the existing tests in that file (they monkeypatch `sm._probe_host_free_mb` and drive `sm._reaper.pressure_evict_once()` directly).

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `HAL0_HOME=$(mktemp -d) uv run pytest tests/slots/test_pressure_eviction.py -q`
Expected: new tests FAIL (non-lru slot still skipped; no priority ordering); flipped legacy tests FAIL too.

- [ ] **Step 3: Implement**

In `reaper.py`, add near `eviction_priority`:

```python
_lru_flag_warned: set[str] = set()


def _warn_lru_deprecated(canonical_name: str, cfg: dict[str, Any] | None) -> None:
    """One-shot deprecation warning for the retired ``lru`` TOML key.

    The key is IGNORED (never an error): eviction eligibility is now
    priority-ordered for every non-pinned slot (spec 2026-08-02). Warned
    once per slot per process so a 30 s sweep doesn't spam the journal.
    """
    if not isinstance(cfg, dict) or cfg.get("lru") is None:
        return
    if canonical_name in _lru_flag_warned:
        return
    _lru_flag_warned.add(canonical_name)
    log.warning(
        "slot.lru_flag_deprecated",
        extra={
            "slot": canonical_name,
            "hint": "lru is ignored; eviction order is priority (0-100, "
            "lower first) — remove the key, use `priority`/`pinned`",
        },
    )
```

In `pressure_evict_once`, replace the candidate build + sort (~lines 419–440):

```python
        # Build the victim list ordered (priority asc, last_used asc):
        # lowest priority first, LRU within a tier (spec 2026-08-02).
        # sweep_candidates() unions _last_used with dispatchable slots known
        # only via state.json (adopted / restart-surviving), timestamped by
        # their last observed transition, so pressure eviction can also
        # reclaim those. The retired ``lru = true`` opt-in no longer gates —
        # every non-pinned resident slot is a candidate.
        candidates: list[tuple[int, float, str]] = []
        for slot_name, ts in self.sweep_candidates().items():
            if host._serving_count.get(host._key(slot_name), 0) > 0:
                continue
            canonical = host._resolve_alias(slot_name)
            state = host._current_state(slot_name)
            if state not in (SlotState.READY, SlotState.IDLE):
                continue
            try:
                cfg = await host._load_slot_config(slot_name)
            except (SlotConfigError, SlotNotFound):
                continue
            if is_pinned(canonical, cfg):
                continue
            _warn_lru_deprecated(canonical, cfg)
            candidates.append((eviction_priority(cfg), ts, slot_name))

        candidates.sort(key=lambda item: (item[0], item[1]))
        for _prio, _ts, slot_name in candidates:
```

(the loop body below is unchanged.) Update the `pressure_evict_once` docstring: replace the "Only slots with ``lru = true`` …" guard bullet with the priority-order description; same for the module docstring's `lru` mentions. Add `_warn_lru_deprecated` to `__all__` (Task 5 imports it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `HAL0_HOME=$(mktemp -d) uv run pytest tests/slots/test_pressure_eviction.py tests/slots/test_adopted_slot_eviction.py tests/slots/test_pin_semantics.py -q`
Expected: PASS (adjacent eviction/pin suites included — they exercise the same sweep).

- [ ] **Step 5: Commit**

```bash
make lint
git add src/hal0/slots/reaper.py tests/slots/test_pressure_eviction.py
git commit -m "feat(slots): priority-ordered pressure eviction, retire lru opt-in"
```

---

### Task 5: priority ordering in pre-load eviction

**Files:**
- Modify: `src/hal0/slots/preload_evict.py` — `CandidateSlot`, `select_eviction_order` (~line 160), `_gather_candidates` (~line 280), module docstring
- Test: `tests/slots/test_preload_eviction.py`

**Interfaces:**
- Consumes: `eviction_priority`, `_warn_lru_deprecated` from `hal0.slots.reaper` (Tasks 1/4).
- Produces: `CandidateSlot` gains `priority: int = 50` (keyword-position after `reason`); `select_eviction_order` picks eligible candidates sorted `(priority, last_used)`.

- [ ] **Step 1: Update existing tests + write new failing tests**

Read `tests/slots/test_preload_eviction.py` first. Existing changes:
- Any test constructing `CandidateSlot(..., eligible=False, reason="not_lru")` or asserting the `not_lru` reason → drop/flip (the reason no longer exists; those candidates become eligible).
- `select_eviction_order` tests keep passing where all candidates default `priority=50` (pure LRU within the tier — unchanged behavior).

New tests (pure-function level — no manager needed):

```python
def test_select_orders_by_priority_then_lru() -> None:
    """priority beats recency: an old high-priority slot survives while a
    newer low-priority one is selected first."""
    cands = [
        CandidateSlot(name="hi", last_used=100.0, footprint_mb=4000.0, eligible=True, priority=90),
        CandidateSlot(name="lo-new", last_used=900.0, footprint_mb=4000.0, eligible=True, priority=10),
        CandidateSlot(name="lo-old", last_used=100.0, footprint_mb=4000.0, eligible=True, priority=10),
    ]
    plan = select_eviction_order(cands, needed_mb=7000.0, headroom_mb=1000.0, free_mb=0.0)
    assert [c.name for c in plan.selected] == ["lo-old", "lo-new"]
    assert plan.fits is True


def test_select_ineligible_never_selected_regardless_of_priority() -> None:
    cands = [
        CandidateSlot(name="pinned", last_used=1.0, footprint_mb=9000.0, eligible=False,
                      reason="pinned", priority=0),
        CandidateSlot(name="ok", last_used=2.0, footprint_mb=9000.0, eligible=True, priority=100),
    ]
    plan = select_eviction_order(cands, needed_mb=8000.0, headroom_mb=0.0, free_mb=0.0)
    assert [c.name for c in plan.selected] == ["ok"]
```

Plus a `_gather_candidates`-level test following the file's existing host-stub pattern: a slot whose cfg has no `lru` key at all is `eligible=True` with `priority` read from cfg (was `eligible=False, reason="not_lru"`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `HAL0_HOME=$(mktemp -d) uv run pytest tests/slots/test_preload_eviction.py -q`
Expected: FAIL — `TypeError: CandidateSlot.__init__() got an unexpected keyword argument 'priority'`.

- [ ] **Step 3: Implement**

`CandidateSlot` gains the field (after `reason`):

```python
    priority: int = 50
```

`select_eviction_order` — replace the eligible sort line:

```python
    eligible = sorted(
        (c for c in candidates if c.eligible), key=lambda c: (c.priority, c.last_used)
    )
```

and update its docstring ("oldest-first" → "lowest priority first, oldest within a tier"). `_gather_candidates` — import `_warn_lru_deprecated, eviction_priority` alongside the existing `is_pinned` import from `hal0.slots.reaper`; replace the eligibility tail:

```python
                else:
                    if is_pinned(canonical, cfg):
                        eligible, reason = False, "pinned"
                    else:
                        _warn_lru_deprecated(canonical, cfg)
```

and build the candidate with the priority (cfg may be unbound when the config load failed — hoist a `priority = 50` default before the `try`, set `priority = eviction_priority(cfg)` in the `else` branch):

```python
        out.append(
            CandidateSlot(
                name=name,
                last_used=ts,
                footprint_mb=footprint_mb,
                eligible=eligible,
                reason=reason,
                priority=priority,
            )
        )
```

Update the module docstring's two `lru = true` mentions (eligibility bullet ~line 21 and `_gather_candidates` docstring) to the priority contract.

- [ ] **Step 4: Run tests to verify they pass**

Run: `HAL0_HOME=$(mktemp -d) uv run pytest tests/slots/test_preload_eviction.py tests/slots/test_pressure_eviction.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
make lint
git add src/hal0/slots/preload_evict.py tests/slots/test_preload_eviction.py
git commit -m "feat(slots): priority-ordered pre-load eviction"
```

---

### Task 6: lift autoload + priority into the /api/slots snapshot

**Files:**
- Modify: `src/hal0/slot_view/__init__.py` (config enrichment block, ~line 265 next to the `pinned` lift)
- Test: `tests/slot_view/test_aggregator.py`

**Interfaces:**
- Consumes: `autoload_enabled`, `eviction_priority` (Task 1).
- Produces: every real slot entry in `GET /api/slots` carries `"autoload": bool` (effective — shim applied) and `"priority": int`. The drawer reads `slot.autoload` / `slot.priority` (Task 7).

- [ ] **Step 1: Write the failing test**

In `tests/slot_view/test_aggregator.py`, next to whatever existing test asserts the `pinned` lift (find it; copy its fixture shape):

```python
def test_lifts_autoload_and_priority(...):
    """Effective autoload (shim: bound model + no key → true) and priority
    are lifted so the drawer renders without a per-slot /config fetch."""
    # cfg A: {"model": {"default": "m"}} (no autoload key, no priority)
    #   → entry["autoload"] is True, entry["priority"] == 50
    # cfg B: {"autoload": False, "priority": 10, "model": {"default": "m"}}
    #   → entry["autoload"] is False, entry["priority"] == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `HAL0_HOME=$(mktemp -d) uv run pytest tests/slot_view/ -q`
Expected: FAIL — `KeyError: 'autoload'`.

- [ ] **Step 3: Implement**

In `slot_view/__init__.py`, imports: add `autoload_enabled` (the module already imports `is_pinned` as `reaper_is_pinned` at line 56 — add `from hal0.slots.activation import autoload_enabled` and `from hal0.slots.reaper import eviction_priority`; check whether `activation` imports already exist near the top). In the enrichment loop, directly under the `entry["pinned"]` lift (~line 269):

```python
        # Spec 2026-08-02: lift the EFFECTIVE autoload (migration shim
        # applied — absent key + bound model reads true) and eviction
        # priority so the drawer's controls render without a per-slot
        # /config fetch, same pattern as the pinned lift above.
        entry["autoload"] = autoload_enabled(cfg)
        entry["priority"] = eviction_priority(cfg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `HAL0_HOME=$(mktemp -d) uv run pytest tests/slot_view/ tests/api/test_slots_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
make lint
git add src/hal0/slot_view/__init__.py tests/slot_view/test_aggregator.py
git commit -m "feat(api): surface slot autoload and priority in /api/slots"
```

---

### Task 7: drawer + create-modal controls

**Files:**
- Modify: `ui/src/dash/slot-modals.jsx` — `EditSlotDrawer` Model group (starts line ~1348), create modal (top of file), instant-apply handlers (pattern: `onTogglePinned`, line ~661)
- Modify: `src/hal0/api/routes/slots.py` — `create_slot` (~line 606): default `autoload` explicitly

**Interfaces:**
- Consumes: `slot.autoload` / `slot.priority` from the poll (Task 6); `PUT /api/slots/{name}/config` bodies `{autoload: bool}` / `{priority: int}`; `editMut` mutation already used by `onTogglePinned`.
- Produces: drawer rows "Auto-load on start" (toggle) and "Eviction priority" (number 0–100); create modal fields; create route persists explicit `autoload` (default `false`).

- [ ] **Step 1: create route default (backend, tiny)**

In `create_slot`, right after `_normalize_create_body(...)` returns (before the `_reject_*` calls):

```python
    # Spec 2026-08-02: new slots never inherit the legacy implicit boot
    # start — persist an explicit autoload so the migration shim (absent
    # key + bound model → true) only ever applies to pre-field TOMLs.
    body.setdefault("autoload", False)
```

Add a test next to the existing create tests in `tests/api/test_slots_routes.py`: POST a slot with a model and WITHOUT `autoload` → follow-up config read shows `autoload` False (assert via the same config-fetch pattern neighboring create tests use). Run it (fails before the change, passes after).

- [ ] **Step 2: drawer — instant-apply handlers**

In `EditSlotDrawer`, next to `onTogglePinned` (same shape — instant PUT + toast + poll re-render; reuse `setEnableBusy`-style busy state only if `onTogglePinned` does):

```jsx
	// Instant-apply autoload toggle + priority commit (spec 2026-08-02).
	// Same contract as the pinned toggle above: fire the PUT, toast, let the
	// slots poll re-render from server truth. Excluded from the Save batch.
	const autoload = slot.autoload === true;
	const onToggleAutoload = async (next) => {
		try {
			await editMut.mutateAsync({ name: slot.name, body: { autoload: next } });
			window.__hal0Toast &&
				window.__hal0Toast(
					`${slot.name} auto-load ${next ? "on" : "off"}`,
					"ok",
				);
		} catch (err) {
			window.__hal0Toast &&
				window.__hal0Toast(
					err?.message ? `${slot.name}: ${err.message}` : `${slot.name}: toggle failed`,
					"warn",
				);
		}
	};
	const [prio, setPrio] = React.useState(
		Number.isInteger(slot.priority) ? slot.priority : 50,
	);
	React.useEffect(() => {
		if (Number.isInteger(slot.priority)) setPrio(slot.priority);
	}, [slot.priority]);
	const commitPriority = async () => {
		const v = Math.max(0, Math.min(100, Number(prio) || 0));
		setPrio(v);
		if (v === slot.priority) return;
		try {
			await editMut.mutateAsync({ name: slot.name, body: { priority: v } });
			window.__hal0Toast && window.__hal0Toast(`${slot.name} priority → ${v}`, "ok");
		} catch (err) {
			setPrio(Number.isInteger(slot.priority) ? slot.priority : 50);
			window.__hal0Toast &&
				window.__hal0Toast(
					err?.message ? `${slot.name}: ${err.message}` : `${slot.name}: priority save failed`,
					"warn",
				);
		}
	};
```

(Match the file's state-hook import style — it may use `useState` bare imports rather than `React.useState`; follow whatever the file does.)

- [ ] **Step 3: drawer — rows in the Model group**

Inside `<FieldGroup label="Model">`, after the model-select row (and after the Profile row if it renders inside this group), add:

```jsx
						<div className="form-row">
							<div className="form-lbl">
								<span>Auto-load on start</span>
								<FieldInfoIcon description="Start this slot automatically at boot. Off: the slot
									only loads when you load or swap it — binding a model no
									longer implies boot start." />
							</div>
							<div className="form-ctl">
								<label className="slot-enable-toggle">
									<input
										type="checkbox"
										data-testid="slot-autoload-toggle"
										checked={autoload}
										onChange={() => onToggleAutoload(!autoload)}
										aria-label={autoload ? "Disable auto-load on start" : "Enable auto-load on start"}
									/>
									<span className="slot-enable-track" aria-hidden="true" />
								</label>
							</div>
						</div>
						<div className="form-row">
							<div className="form-lbl">
								<span>Eviction priority</span>
								<FieldInfoIcon description="0-100 — lower unloads first when memory is needed.
									Ties go to the least recently used. Pin the slot to exempt
									it entirely." />
							</div>
							<div className="form-ctl">
								<input
									className="input mono"
									data-testid="slot-priority-input"
									type="number"
									min={0}
									max={100}
									step={1}
									value={prio}
									onChange={(e) => setPrio(e.target.value)}
									onBlur={commitPriority}
									onKeyDown={(e) => {
										if (e.key === "Enter") e.currentTarget.blur();
									}}
									style={{ width: 90 }}
								/>
								<div className="hint">lower unloads first</div>
							</div>
						</div>
```

Adapt toggle markup to the exact classes the pinned header toggle uses if `slot-enable-toggle`/`slot-enable-track` don't render standalone in a form row — visual match beats literal copy.

- [ ] **Step 4: create modal**

Find the create modal component at the top of `slot-modals.jsx` (investigator: line ~2). Add the same two controls to its form with local state defaults `autoload=false`, `priority=50`, and include both keys in the POST body it already builds (`autoload: <bool>`, `priority: <number>`). Omit the migration hint copy — new slots have no legacy behavior.

- [ ] **Step 5: Verify**

```bash
cd ui && npm run test:unit && npm run build
```
Expected: vitest suite green (no new UI unit tests required — the touched logic is instant-apply wiring; existing suites must not break), build succeeds. Then backend spot-check:

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/api/test_slots_routes.py -q
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
make lint
git add ui/src/dash/slot-modals.jsx src/hal0/api/routes/slots.py tests/api/test_slots_routes.py
git commit -m "feat(ui): slot drawer autoload toggle and eviction priority"
```

---

### Task 8: changelog, docs sweep, full verification

**Files:**
- Modify: `CHANGELOG.md` (`## [Unreleased]` → `### Added`)
- Modify: any `docs/` page mentioning `lru = true` or implicit boot start (`grep -rn "lru" docs/ --include="*.md*"`, `grep -rn "WantedBy=hal0.target" docs/`)

- [ ] **Step 1: CHANGELOG entry**

Under `## [Unreleased]`, add (create `### Added` if absent):

```markdown
### Added

- Slots: explicit `autoload` setting — a slot starts at boot only when
  `autoload = true` (slot drawer toggle). Binding a model no longer
  implies boot start; existing slots with a bound model migrate as
  `true`, so upgrade changes nothing until toggled.
- Slots: eviction `priority` (0–100, default 50, drawer field) — memory
  pressure and pre-load eviction now unload the lowest-priority slot
  first (least-recently-used within a tier). The `lru = true` opt-in is
  retired: every non-pinned slot is evictable; the key is ignored with a
  one-time deprecation warning. `pinned` still exempts entirely.
```

- [ ] **Step 2: docs sweep**

Run the greps above; update any operator-facing doc that documents `lru = true` eligibility or "a slot with a model starts at boot" to the new contract. Skip `docs/superpowers/` and historical specs.

- [ ] **Step 3: full verification**

```bash
make lint
make typecheck
HAL0_HOME=$(mktemp -d) uv run pytest tests/slots tests/providers tests/api/test_slots_routes.py tests/slot_view tests/config -q
cd ui && npm run test:unit && npm run build
```
Expected: all green. If `make typecheck` flags pre-existing unrelated errors, only new errors in touched files block.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md docs/
git commit -m "docs: changelog and docs for slot autoload and eviction priority"
```
