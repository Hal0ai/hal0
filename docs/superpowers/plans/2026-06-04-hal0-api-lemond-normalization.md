# hal0-api lemond Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every agent that talks to lemond through hal0-api a stable virtual model name (`hal0/primary`/`hal0/npu`/`hal0/utility`) that live-resolves to the loaded slot, plus automatic reasoning suppression — then cut Hermes over and retire the Bifrost sidecar.

**Architecture:** Two pure transforms wired into hal0-api's chat path: a `LiveSlotResolver` that maps a virtual name → the currently-loaded slot's physical model id (role+device aware, reusing the `MetricsShim` health cache — no new lemond polling), and `apply_thinking_policy` that injects top-level `enable_thinking:false` for lemond-bound requests. A new optional `SlotConfig.role` field binds chain roles to user-named slots. hal0-api's `/v1/models` advertises the virtual names so Hermes discovers them. Hermes keeps `provider: custom` + `base_url: :8080/v1`.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, httpx, pytest (`httpx.MockTransport` for dispatcher, `TestClient` for api routes), Jinja2 (Hermes config template).

**Spec:** `docs/superpowers/specs/2026-06-04-hal0-api-lemond-normalization-design.md`

**Branch:** Work on `plan/hal0-api-lemond-normalization` (already based on `origin/main @ 17367b5`). Run all tests with the project venv: `cd /home/halo/dev/hal0 && python -m pytest <path> -v`. Do NOT run the whole suite (`pytest tests/` hangs on lemond health waits — memory `hal0_local_full_test_suite_hangs`); run targeted paths.

> **Refinement vs spec §4.1b:** the spec described two injection seams (composite forward + proxy fall-through). This plan collapses them into **one** seam — both transforms run at the top of `_dispatch_and_forward` and rewrite `request._body` once, so the dispatcher path AND the `NoRouteFound` proxy fall-through both observe the normalized body. The "local-only" gate for thinking becomes a lightweight upstreams-registry pre-check (skip injection if the model maps to a `kind=="remote"` upstream). This is simpler and DRY; behavior matches the spec.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `src/hal0/config/schema.py` | add optional `SlotConfig.role` | Modify |
| `src/hal0/normalize/__init__.py` | package marker + public exports | Create |
| `src/hal0/normalize/resolver.py` | pure chain resolution + `SlotView` + `classify` + `LiveSlotResolver` async wrapper | Create |
| `src/hal0/normalize/thinking.py` | pure `apply_thinking_policy` | Create |
| `src/hal0/api/routes/v1.py` | wire `resolve_model_name` + `apply_thinking_policy` into `_dispatch_and_forward`; advertise virtual rows in `list_models` | Modify |
| `src/hal0/api/__init__.py` | `hal0_llm_slot_views()` helper (role+device+ctx per llm slot) | Modify |
| `src/hal0/agents/hermes_templates/config.yaml.j2` | render `hal0/primary` when `live_resolve_enabled` | Modify |
| `src/hal0/agents/hermes_provision.py` | thread `live_resolve_enabled` into the template context | Modify |
| `tests/normalize/test_resolver.py` | resolver chain matrix | Create |
| `tests/normalize/test_thinking.py` | thinking opt-out / idempotency / no_think | Create |
| `tests/config/test_slot_role.py` | `role` field round-trips | Create |
| `tests/api/test_virtual_models.py` | `/v1/models` advertises virtual names | Create |
| `tests/agents/test_hermes_live_resolve_render.py` | template renders `hal0/primary` | Create |

---

## Task 1: Add optional `role` field to `SlotConfig`

**Files:**
- Modify: `src/hal0/config/schema.py` (SlotConfig, ~line 241 between `enabled` and `model`)
- Test: `tests/config/test_slot_role.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_slot_role.py
from hal0.config.schema import SlotConfig


def _base(**over):
    data = {"name": "utility", "port": 8081, "model": {"default": "tiny.gguf"}}
    data.update(over)
    return data


def test_role_defaults_to_none():
    cfg = SlotConfig.model_validate(_base())
    assert cfg.role is None


def test_role_round_trips():
    cfg = SlotConfig.model_validate(_base(role="utility"))
    assert cfg.role == "utility"
    # survives serialization (extra=allow keeps it on the model dump)
    assert SlotConfig.model_validate(cfg.model_dump()).role == "utility"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/config/test_slot_role.py -v`
Expected: `test_role_defaults_to_none` FAILS with `AttributeError: 'SlotConfig' object has no attribute 'role'`.

- [ ] **Step 3: Add the field**

In `src/hal0/config/schema.py`, inside `class SlotConfig`, add the field directly after the `enabled` field:

```python
    enabled: bool = Field(default=True, description="Whether the slot is active.")
    role: str | None = Field(
        default=None,
        description=(
            "Optional role hint for normalization chain binding "
            "(e.g. 'primary', 'utility', 'npu'). When unset, role is derived "
            "from the slot name. Authoritative over the name when set."
        ),
    )
    model: ModelConfig = Field(default_factory=ModelConfig)
```

(No loader change: `model_config = {"extra": "allow"}` + `_flatten_slot_toml` already hoist a `role = "..."` key from the `[slot]` table.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/config/test_slot_role.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/hal0/config/schema.py tests/config/test_slot_role.py
git commit -m "feat(slots): add optional SlotConfig.role for normalization chain binding"
```

---

## Task 2: Pure resolver core (`SlotView`, classification, chain resolution)

**Files:**
- Create: `src/hal0/normalize/__init__.py`
- Create: `src/hal0/normalize/resolver.py`
- Test: `tests/normalize/test_resolver.py`

This task builds the **pure** (no I/O) core so it's fully unit-testable. The async wrapper that reads slot configs + the health cache comes in Task 3.

- [ ] **Step 1: Write the failing test**

```python
# tests/normalize/test_resolver.py
import pytest

from hal0.normalize.resolver import (
    SlotView,
    DEFAULT_CHAINS,
    is_npu_or_flm,
    resolve_chain,
)


def _slots():
    return [
        SlotView(name="primary", role=None, device="gpu-vulkan", model_id="big-35b", context_length=65536),
        SlotView(name="utility", role=None, device="gpu-vulkan", model_id="tiny-0.8b", context_length=65536),
        SlotView(name="agent",   role=None, device="npu",        model_id="qwen3-4b-FLM", context_length=32768),
    ]


def test_primary_prefers_igpu_when_loaded():
    r = resolve_chain("hal0/primary", _slots(), loaded={"big-35b", "qwen3-4b-FLM"})
    assert r.model_id == "big-35b"
    assert r.context_length == 65536
    assert r.fallback is False


def test_npu_picks_npu_first_never_commandeers_primary():
    r = resolve_chain("hal0/npu", _slots(), loaded={"qwen3-4b-FLM", "big-35b"})
    assert r.model_id == "qwen3-4b-FLM"          # npu role wins
    assert r.matched_role == "npu"


def test_npu_falls_to_utility_before_primary():
    # npu slot not loaded; utility loaded; primary loaded
    r = resolve_chain("hal0/npu", _slots(), loaded={"tiny-0.8b", "big-35b"})
    assert r.model_id == "tiny-0.8b"             # utility before primary
    assert r.matched_role == "utility"


def test_utility_chain_order():
    r = resolve_chain("hal0/utility", _slots(), loaded={"qwen3-4b-FLM", "big-35b"})
    assert r.model_id == "qwen3-4b-FLM"          # utility-miss -> npu before primary


def test_role_tag_overrides_name():
    slots = [
        SlotView(name="coder-mini", role="utility", device="gpu-vulkan", model_id="cm", context_length=8192),
        SlotView(name="primary", role=None, device="gpu-vulkan", model_id="big", context_length=65536),
    ]
    r = resolve_chain("hal0/utility", slots, loaded={"cm"})
    assert r.model_id == "cm"                     # bound by role tag, not name


def test_full_miss_falls_back_to_configured_primary_unloaded():
    r = resolve_chain("hal0/utility", _slots(), loaded=set())
    assert r.model_id == "big-35b"               # configured primary fallback
    assert r.fallback is True                     # signals caller to ensure-load


def test_flm_alias_resolves_same_as_npu():
    r = resolve_chain("hal0/flm", _slots(), loaded={"qwen3-4b-FLM"})
    assert r.model_id == "qwen3-4b-FLM"


def test_is_npu_or_flm_name_heuristic():
    assert is_npu_or_flm("qwen3-4b-FLM") is True
    assert is_npu_or_flm("big-35b") is False


def test_unknown_virtual_name_returns_none():
    assert resolve_chain("hal0/nope", _slots(), loaded={"big-35b"}) is None


def test_default_chains_shape():
    assert DEFAULT_CHAINS["hal0/primary"] == ("primary",)
    assert DEFAULT_CHAINS["hal0/npu"] == ("npu", "utility", "primary")
    assert DEFAULT_CHAINS["hal0/utility"] == ("utility", "npu", "primary")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/normalize/test_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hal0.normalize'`.

- [ ] **Step 3: Create the package marker**

```python
# src/hal0/normalize/__init__.py
"""Request normalization for lemond-bound chat traffic (model resolution + thinking)."""

from hal0.normalize.resolver import (  # noqa: F401
    SlotView,
    Resolution,
    DEFAULT_CHAINS,
    VIRTUAL_ALIASES,
    is_npu_or_flm,
    resolve_chain,
    LiveSlotResolver,
)
from hal0.normalize.thinking import apply_thinking_policy  # noqa: F401
```

- [ ] **Step 4: Write the pure resolver**

```python
# src/hal0/normalize/resolver.py
"""Live-slot model resolution for hal0 virtual model names.

Pure core (``resolve_chain``) + an async wrapper (``LiveSlotResolver``) that reads
slot config and the cached lemond health snapshot. No new lemond polling — the
wrapper reuses ``MetricsShim._health`` (see hal0-api lifespan).
"""

from __future__ import annotations

from dataclasses import dataclass


# Canonical virtual names -> ordered chain of roles to try against loaded slots.
DEFAULT_CHAINS: dict[str, tuple[str, ...]] = {
    "hal0/primary": ("primary",),
    "hal0/npu": ("npu", "utility", "primary"),
    "hal0/utility": ("utility", "npu", "primary"),
}

# Aliases that resolve to a canonical name before chain lookup.
VIRTUAL_ALIASES: dict[str, str] = {
    "hal0/flm": "hal0/npu",
}


@dataclass(frozen=True)
class SlotView:
    """Minimal view of one enabled llm slot, drawn from slot config."""

    name: str
    role: str | None
    device: str           # "gpu-vulkan" | "gpu-rocm" | "cpu" | "npu"
    model_id: str
    context_length: int


@dataclass(frozen=True)
class Resolution:
    model_id: str
    context_length: int
    matched_role: str | None   # role that matched a loaded slot, or None on fallback
    fallback: bool             # True => nothing in the chain was loaded; caller should ensure-load


def is_npu_or_flm(model_name: str) -> bool:
    """Name-suffix heuristic used only for a loaded model with no slot-config match."""
    upper = model_name.upper()
    return upper.endswith("-FLM") or "FLM" in upper or "NPU" in upper


def _slot_matches_role(slot: SlotView, role: str) -> bool:
    """Authoritative role binding: device for npu, role tag (else name) for primary/utility."""
    if role == "npu":
        return slot.device == "npu" or (slot.role or "").lower() == "npu"
    effective = (slot.role or slot.name).lower()
    return effective == role


def _configured_primary(slots: list[SlotView]) -> SlotView | None:
    for s in slots:
        if _slot_matches_role(s, "primary"):
            return s
    return slots[0] if slots else None


def resolve_chain(
    virtual_name: str,
    slots: list[SlotView],
    loaded: set[str],
) -> Resolution | None:
    """Resolve a virtual name to a live slot's physical model id.

    Returns ``None`` if ``virtual_name`` is not a known virtual name. Otherwise
    always returns a ``Resolution`` (falling back to the configured primary,
    ``fallback=True``, when no chain role is currently loaded).
    """
    canonical = VIRTUAL_ALIASES.get(virtual_name, virtual_name)
    chain = DEFAULT_CHAINS.get(canonical)
    if chain is None:
        return None

    for role in chain:
        for slot in slots:
            if _slot_matches_role(slot, role) and slot.model_id in loaded:
                return Resolution(slot.model_id, slot.context_length, role, fallback=False)

    primary = _configured_primary(slots)
    if primary is not None:
        return Resolution(primary.model_id, primary.context_length, None, fallback=True)
    # No slots at all: degrade to a bare model id so the caller can still 503 cleanly.
    return Resolution("", 0, None, fallback=True)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/normalize/test_resolver.py -v`
Expected: PASS (all cases). (`LiveSlotResolver` is imported in `__init__` but not yet defined — add a stub to keep the import working: append the class in Step 6 of Task 3. To keep this task green, temporarily add `LiveSlotResolver = None  # filled in Task 3` at the end of `resolver.py`.)

- [ ] **Step 6: Add the temporary export stub**

Append to `src/hal0/normalize/resolver.py`:

```python
LiveSlotResolver = None  # type: ignore[assignment]  # implemented in Task 3
```

Re-run Step 5; confirm imports resolve and tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/hal0/normalize/__init__.py src/hal0/normalize/resolver.py tests/normalize/test_resolver.py
git commit -m "feat(normalize): pure live-slot chain resolver (role+device aware)"
```

---

## Task 3: `LiveSlotResolver` async wrapper + `hal0_llm_slot_views` helper

**Files:**
- Modify: `src/hal0/api/__init__.py` (add `hal0_llm_slot_views`, sibling to `hal0_chat_slot_alias_map` ~line 397)
- Modify: `src/hal0/normalize/resolver.py` (replace the stub with the real class)
- Test: `tests/normalize/test_resolver.py` (append wrapper tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/normalize/test_resolver.py`:

```python
class _FakeShimHealth:
    def __init__(self, loaded): self.loaded_models = tuple(loaded)


class _FakeShim:
    def __init__(self, loaded): self._health = _FakeShimHealth(loaded)


@pytest.mark.asyncio
async def test_live_resolver_reads_views_and_health():
    from hal0.normalize.resolver import LiveSlotResolver, SlotView

    views = [
        SlotView(name="primary", role=None, device="gpu-vulkan", model_id="big", context_length=65536),
        SlotView(name="utility", role=None, device="gpu-vulkan", model_id="tiny", context_length=65536),
    ]
    resolver = LiveSlotResolver(
        slot_views_provider=lambda: views,
        loaded_models_provider=lambda: {"tiny", "big"},
    )
    res = await resolver.resolve("hal0/utility")
    assert res.model_id == "tiny"
    # passthrough: non-virtual names return None so the caller leaves the body alone
    assert await resolver.resolve("some-physical-model") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/normalize/test_resolver.py::test_live_resolver_reads_views_and_health -v`
Expected: FAIL (`LiveSlotResolver` is `None`).

- [ ] **Step 3: Replace the stub with the real wrapper**

In `src/hal0/normalize/resolver.py`, delete the `LiveSlotResolver = None` line and append:

```python
from collections.abc import Callable


class LiveSlotResolver:
    """Async wrapper around ``resolve_chain``.

    ``slot_views_provider`` returns the current list of ``SlotView`` (built from
    slot config). ``loaded_models_provider`` returns the set of currently-loaded
    model ids from the cached health snapshot — NO new lemond poll.
    """

    def __init__(
        self,
        slot_views_provider: Callable[[], list[SlotView]],
        loaded_models_provider: Callable[[], set[str]],
    ) -> None:
        self._views = slot_views_provider
        self._loaded = loaded_models_provider

    async def resolve(self, model_name: str) -> Resolution | None:
        if model_name not in DEFAULT_CHAINS and model_name not in VIRTUAL_ALIASES:
            return None
        try:
            views = list(self._views() or [])
            loaded = set(self._loaded() or set())
        except Exception:
            views, loaded = [], set()
        return resolve_chain(model_name, views, loaded)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/normalize/test_resolver.py -v`
Expected: PASS (all, including the new wrapper test).

- [ ] **Step 5: Add `hal0_llm_slot_views` helper to `api/__init__.py`**

In `src/hal0/api/__init__.py`, directly after `hal0_chat_slot_alias_map` (ends ~line 429), add:

```python
async def hal0_llm_slot_views(slot_manager: "SlotManager") -> list[dict[str, Any]]:
    """Return one dict per enabled llm slot: {name, role, device, model_id, context_length}.

    Source for normalize.LiveSlotResolver's SlotView list. Mirrors
    hal0_chat_slot_alias_map's iteration but carries role + device + context.
    """
    try:
        cfgs = await slot_manager.iter_configs()
    except Exception as exc:
        log.warning("v1.llm_slot_views_iter_failed", error=str(exc))
        return []
    out: list[dict[str, Any]] = []
    for cfg in cfgs:
        if (cfg.get("type") or "").lower() != "llm":
            continue
        if cfg.get("enabled") is False:
            continue
        name = str(cfg.get("name") or "").strip()
        model_id = _slot_model_id(cfg)
        if not name or not model_id:
            continue
        model_section = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
        out.append({
            "name": name,
            "role": cfg.get("role"),
            "device": (cfg.get("device") or "").strip(),
            "model_id": model_id,
            "context_length": int(model_section.get("context_size") or 0),
        })
    return out
```

- [ ] **Step 6: Run the resolver test suite again**

Run: `python -m pytest tests/normalize/ -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/hal0/normalize/resolver.py src/hal0/api/__init__.py tests/normalize/test_resolver.py
git commit -m "feat(normalize): LiveSlotResolver wrapper + hal0_llm_slot_views helper"
```

---

## Task 4: Pure `apply_thinking_policy`

**Files:**
- Create: `src/hal0/normalize/thinking.py`
- Test: `tests/normalize/test_thinking.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/normalize/test_thinking.py
from hal0.normalize.thinking import apply_thinking_policy


def test_injects_top_level_enable_thinking_false_by_default():
    out = apply_thinking_policy({"model": "m", "messages": []})
    assert out["enable_thinking"] is False


def test_opt_out_enable_thinking_preserved():
    out = apply_thinking_policy({"enable_thinking": True})
    assert out["enable_thinking"] is True


def test_opt_out_thinking_field_preserved():
    out = apply_thinking_policy({"thinking": {"type": "enabled"}})
    assert "enable_thinking" not in out
    assert out["thinking"] == {"type": "enabled"}


def test_opt_out_chat_template_kwargs_preserved():
    body = {"chat_template_kwargs": {"enable_thinking": True}}
    out = apply_thinking_policy(body)
    assert "enable_thinking" not in out
    assert out["chat_template_kwargs"] == {"enable_thinking": True}


def test_no_think_marker_passthrough_untouched():
    body = {"messages": [{"role": "user", "content": "/no_think hi"}], "no_think": True}
    out = apply_thinking_policy(body)
    assert out["no_think"] is True
    # we still inject the structured field (lemond honors top-level enable_thinking)
    assert out["enable_thinking"] is False


def test_idempotent():
    once = apply_thinking_policy({"model": "m"})
    twice = apply_thinking_policy(once)
    assert once == twice


def test_does_not_mutate_input():
    src = {"model": "m"}
    apply_thinking_policy(src)
    assert "enable_thinking" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/normalize/test_thinking.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hal0.normalize.thinking'`.

- [ ] **Step 3: Implement**

```python
# src/hal0/normalize/thinking.py
"""Reasoning-suppression policy for lemond-bound chat requests.

lemond (SHA 1bce071) reads TOP-LEVEL ``enable_thinking``/``thinking`` and does its
own ``/no_think`` injection (server.cpp:58-114). We inject top-level
``enable_thinking: false`` unless the caller already expressed a thinking
preference. Idempotent and non-mutating.
"""

from __future__ import annotations

from typing import Any


def _caller_opted(body: dict[str, Any]) -> bool:
    if "enable_thinking" in body or "thinking" in body:
        return True
    ctk = body.get("chat_template_kwargs")
    return isinstance(ctk, dict) and "enable_thinking" in ctk


def apply_thinking_policy(body: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``body`` with ``enable_thinking: false`` injected unless
    the caller already set a thinking control field. A ``no_think`` prompt marker
    is left untouched (passthrough)."""
    if _caller_opted(body):
        return body
    return {**body, "enable_thinking": False}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/normalize/test_thinking.py -v`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add src/hal0/normalize/thinking.py tests/normalize/test_thinking.py
git commit -m "feat(normalize): apply_thinking_policy (top-level enable_thinking, opt-out, idempotent)"
```

---

## Task 5: Wire normalization into the chat path (`_dispatch_and_forward`)

**Files:**
- Modify: `src/hal0/api/routes/v1.py` (`_dispatch_and_forward` ~345-421; add two small helpers + a `_is_remote_model` gate)
- Test: `tests/api/test_chat_normalization.py`

The seam: at the top of `_dispatch_and_forward`, (a) resolve a virtual name → physical model id (rewriting `request._body`), then (b) inject thinking policy unless the model maps to a remote upstream. Both the dispatcher path and the `NoRouteFound` proxy fall-through read the rewritten `request._body`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_chat_normalization.py
import json

import pytest

from hal0.normalize import resolver as R


@pytest.fixture
def patched_resolver(monkeypatch):
    """Make hal0-api resolve hal0/primary -> 'big' regardless of real slots/health."""
    views = [R.SlotView(name="primary", role=None, device="gpu-vulkan", model_id="big", context_length=4096)]
    monkeypatch.setattr("hal0.api.routes.v1._normalize_slot_views", lambda request: views)
    monkeypatch.setattr("hal0.api.routes.v1._normalize_loaded_models", lambda request: {"big"})
    return views


def test_virtual_name_rewritten_and_thinking_injected(client, patched_resolver, monkeypatch):
    captured = {}

    async def fake_dispatch_forward_capture(request, dispatcher, *, body):
        captured["body"] = body
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True})

    # Intercept just the downstream dispatch so we assert on the normalized body.
    monkeypatch.setattr("hal0.api.routes.v1._forward_normalized", fake_dispatch_forward_capture)

    resp = client.post("/v1/chat/completions", json={"model": "hal0/primary", "messages": []})
    assert resp.status_code == 200
    assert captured["body"]["model"] == "big"
    assert captured["body"]["enable_thinking"] is False


def test_physical_model_passthrough_unchanged(client, patched_resolver, monkeypatch):
    captured = {}

    async def cap(request, dispatcher, *, body):
        captured["body"] = body
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True})

    monkeypatch.setattr("hal0.api.routes.v1._forward_normalized", cap)
    client.post("/v1/chat/completions", json={"model": "big", "messages": []})
    # physical name not a virtual -> not rewritten; still lemond-bound -> thinking injected
    assert captured["body"]["model"] == "big"
    assert captured["body"]["enable_thinking"] is False
```

> Note: the test patches three small indirection points (`_normalize_slot_views`, `_normalize_loaded_models`, `_forward_normalized`) introduced in Step 3 so the normalization logic is testable without a live SlotManager/shim. Keep these as module-level functions.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_chat_normalization.py -v`
Expected: FAIL (`AttributeError: ... _normalize_slot_views` / `_forward_normalized` not found).

- [ ] **Step 3: Add the normalization seam to `v1.py`**

Add these module-level helpers to `src/hal0/api/routes/v1.py` (near `_rewrite_chat_slot_alias`):

```python
def _normalize_slot_views(request: Request) -> list:
    """Build SlotView list from cached slot configs (sync view for the resolver)."""
    from hal0.normalize.resolver import SlotView
    cache = getattr(request.app.state, "llm_slot_views_cache", None)
    rows = cache or []
    return [
        SlotView(
            name=r["name"], role=r.get("role"), device=r.get("device", ""),
            model_id=r["model_id"], context_length=int(r.get("context_length") or 0),
        )
        for r in rows
    ]


def _normalize_loaded_models(request: Request) -> set[str]:
    """Currently-loaded model ids from the cached health snapshot (NO new poll)."""
    shim = getattr(request.app.state, "lemonade_metrics_shim", None)
    if shim is None:
        return set()
    try:
        return set(shim._health.loaded_models)
    except Exception:
        return set()


def _is_remote_model(request: Request, model_id: str) -> bool:
    """True if model_id maps to a kind=='remote' upstream (skip thinking injection)."""
    upstreams = getattr(request.app.state, "upstreams", None)
    cache = getattr(request.app.state, "upstream_models", {}) or {}
    if upstreams is None:
        return False
    for u in upstreams.list():
        if getattr(u, "kind", "") == "remote" and model_id in set(cache.get(u.name, [])):
            return True
    return False


async def _normalize_chat_body(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Resolve virtual model names + inject thinking policy for lemond-bound calls."""
    from hal0.normalize.resolver import LiveSlotResolver
    from hal0.normalize.thinking import apply_thinking_policy

    resolver = LiveSlotResolver(
        slot_views_provider=lambda: _normalize_slot_views(request),
        loaded_models_provider=lambda: _normalize_loaded_models(request),
    )
    raw_model = body.get("model")
    if isinstance(raw_model, str) and raw_model:
        res = await resolver.resolve(raw_model)
        if res is not None and res.model_id:
            body = {**body, "model": res.model_id}

    model_id = body.get("model")
    if isinstance(model_id, str) and not _is_remote_model(request, model_id):
        body = apply_thinking_policy(body)

    import contextlib, json
    with contextlib.suppress(Exception):
        request._body = json.dumps(body).encode("utf-8")  # type: ignore[attr-defined]
    return body
```

Then, in `_dispatch_and_forward`, call it **immediately after** the existing `_rewrite_chat_slot_alias` line and route the rest through a thin `_forward_normalized` wrapper so the test can intercept. Concretely, change the body of `_dispatch_and_forward` so its tail reads:

```python
    body = await _rewrite_chat_slot_alias(request, body)
    body = await _normalize_chat_body(request, body)
    return await _forward_normalized(request, dispatcher, body=body)


async def _forward_normalized(request: Request, dispatcher, *, body: dict[str, Any]) -> Response:
    await _ensure_backend_for_model(request, body)
    # ... existing dispatch/forward + NoRouteFound fall-through, unchanged ...
```

Move the existing dispatch/`NoRouteFound`/`_proxy` logic verbatim into `_forward_normalized` (it previously lived at the tail of `_dispatch_and_forward`). No behavior change for existing model names beyond the added thinking field.

- [ ] **Step 4: Populate `llm_slot_views_cache` in the lifespan**

The resolver reads `app.state.llm_slot_views_cache`. Populate it where the existing chat-slot alias map is refreshed (slot-change events / lifespan). In `src/hal0/api/__init__.py`, find where slot state is refreshed (the alias-map refresh on slot-change) and add, alongside it:

```python
    app.state.llm_slot_views_cache = await hal0_llm_slot_views(slot_manager)
```

If no central refresh hook exists, populate it lazily in `_normalize_slot_views` by calling the async helper through a short-TTL cache. Minimum viable: set it once in the lifespan startup after `slot_manager` is ready, and re-set it in the slot-change handler that already rebuilds the alias map.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/api/test_chat_normalization.py -v`
Expected: PASS.

- [ ] **Step 6: Run the broader api + dispatcher tests for regressions**

Run: `python -m pytest tests/api/test_smoke.py tests/dispatcher/test_forward.py -v`
Expected: PASS (no regressions from the refactor).

- [ ] **Step 7: Commit**

```bash
git add src/hal0/api/routes/v1.py src/hal0/api/__init__.py tests/api/test_chat_normalization.py
git commit -m "feat(api): resolve hal0/* virtual names + inject thinking policy in chat path"
```

---

## Task 6: Advertise virtual names in `/v1/models`

**Files:**
- Modify: `src/hal0/api/routes/v1.py` (`list_models` ~424-522, append rows before `return`)
- Test: `tests/api/test_virtual_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_virtual_models.py
from hal0.normalize import resolver as R


def test_virtual_names_present_with_context_length(client, monkeypatch):
    views = [R.SlotView(name="primary", role=None, device="gpu-vulkan", model_id="big", context_length=65536)]
    monkeypatch.setattr("hal0.api.routes.v1._normalize_slot_views", lambda request: views)
    monkeypatch.setattr("hal0.api.routes.v1._normalize_loaded_models", lambda request: {"big"})

    data = client.get("/v1/models").json()["data"]
    by_id = {row["id"]: row for row in data}
    assert "hal0/primary" in by_id
    row = by_id["hal0/primary"]
    assert row["context_length"] == 65536          # FIRST key in Hermes precedence
    assert row["_hal0"]["virtual"] is True
    assert row["_hal0"]["resolves_to"] == "big"
    assert row["_hal0"]["device"] == "gpu-vulkan"


def test_virtual_rows_do_not_duplicate(client, monkeypatch):
    views = [R.SlotView(name="primary", role=None, device="gpu-vulkan", model_id="big", context_length=4096)]
    monkeypatch.setattr("hal0.api.routes.v1._normalize_slot_views", lambda request: views)
    monkeypatch.setattr("hal0.api.routes.v1._normalize_loaded_models", lambda request: {"big"})
    ids = [r["id"] for r in client.get("/v1/models").json()["data"]]
    assert ids.count("hal0/primary") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_virtual_models.py -v`
Expected: FAIL (`hal0/primary` not in the returned ids).

- [ ] **Step 3: Append virtual rows in `list_models`**

In `src/hal0/api/routes/v1.py`, inside `list_models`, immediately before `return {"object": "list", "data": data}`:

```python
    # Advertise live-resolve virtual names so Hermes' /model picker discovers them.
    from hal0.normalize.resolver import LiveSlotResolver, DEFAULT_CHAINS

    resolver = LiveSlotResolver(
        slot_views_provider=lambda: _normalize_slot_views(request),
        loaded_models_provider=lambda: _normalize_loaded_models(request),
    )
    for vname in DEFAULT_CHAINS:           # canonical names only (skip aliases in the picker)
        if vname in seen:
            continue
        res = await resolver.resolve(vname)
        if res is None or not res.model_id:
            continue
        seen.add(vname)
        device = next(
            (v.device for v in _normalize_slot_views(request) if v.model_id == res.model_id),
            "",
        )
        data.append({
            "id": vname,
            "object": "model",
            "created": now,
            "owned_by": "hal0",
            "context_length": res.context_length,   # MANDATORY: else Hermes assumes 256K
            "_hal0": {
                "virtual": True,
                "kind": "live-resolve",
                "resolves_to": res.model_id,
                "device": device,
            },
        })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/api/test_virtual_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hal0/api/routes/v1.py tests/api/test_virtual_models.py
git commit -m "feat(api): advertise hal0/* virtual names in /v1/models with context_length"
```

---

## Task 7: Hermes template renders `hal0/primary` under `live_resolve_enabled`

**Files:**
- Modify: `src/hal0/agents/hermes_templates/config.yaml.j2` (`model:` block ~45-57)
- Modify: `src/hal0/agents/hermes_provision.py` (`_render_config_yaml` ~717/776; `_phase_config_write` ~891-979)
- Test: `tests/agents/test_hermes_live_resolve_render.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_hermes_live_resolve_render.py
from hal0.agents.hermes_provision import _render_config_yaml


def _ctx(**over):
    base = dict(
        primary={"model_id": "phys-35b", "backend_url": "http://127.0.0.1:8080/v1", "context_length": 65536},
        chat_slots=[],
        agent_id="hermes",
        mcp_servers=None,
        system_prompt="x",
        personality_name="default",
        delegation=None,
        auxiliary_tasks=None,
        custom_providers=None,
    )
    base.update(over)
    return base


def test_live_resolve_renders_virtual_default():
    out = _render_config_yaml(live_resolve_enabled=True, **_ctx())
    assert 'default: "hal0/primary"' in out


def test_disabled_renders_physical_default():
    out = _render_config_yaml(live_resolve_enabled=False, **_ctx())
    assert "phys-35b" in out
    assert "hal0/primary" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agents/test_hermes_live_resolve_render.py -v`
Expected: FAIL (`_render_config_yaml() got an unexpected keyword argument 'live_resolve_enabled'`).

- [ ] **Step 3: Thread `live_resolve_enabled` through the renderer**

In `src/hal0/agents/hermes_provision.py`, add `live_resolve_enabled: bool = False` to the `_render_config_yaml` signature and pass it into the Jinja context (`tpl.render(...)`):

```python
def _render_config_yaml(*, live_resolve_enabled: bool = False, primary, chat_slots, agent_id,
                        mcp_servers, system_prompt, personality_name, delegation,
                        auxiliary_tasks, custom_providers) -> str:
    ...
    return tpl.render(
        live_resolve_enabled=live_resolve_enabled,
        primary=primary,
        chat_slots=chat_slots,
        # ... existing kwargs unchanged ...
    )
```

- [ ] **Step 4: Update the template `model:` block**

In `src/hal0/agents/hermes_templates/config.yaml.j2`, replace the `model:` block (~45-57) with:

```jinja2
model:
{%- if live_resolve_enabled %}
  default: "hal0/primary"
  provider: "custom"
  base_url: "http://127.0.0.1:8080/v1"
{%- elif primary %}
  default: {{ primary.model_id | tojson }}
  provider: "custom"
  base_url: {{ primary.backend_url | tojson }}
{%- else %}
  default: ""
  provider: "custom"
  base_url: "http://127.0.0.1:8080/v1"
{%- endif %}
```

(Leave `model_aliases` and `custom_providers` blocks unchanged — virtual names are discovered via `/v1/models`, and we deliberately do NOT add a `custom_providers` entry for `hal0/primary`, which would out-prioritize the live `/v1/models` context.)

- [ ] **Step 5: Set `live_resolve_enabled` in `_phase_config_write`**

In `_phase_config_write`, compute the flag and pass it to `_render_config_yaml`. Default it on once the feature ships (gate on an env/config so rollout step 3 is a deliberate flip):

```python
    import os
    live_resolve_enabled = os.environ.get("HAL0_HERMES_LIVE_RESOLVE", "0") == "1"
    rendered = _render_config_yaml(
        live_resolve_enabled=live_resolve_enabled,
        primary=primary,
        chat_slots=chat_slots,
        # ... existing kwargs unchanged ...
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/agents/test_hermes_live_resolve_render.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/hal0/agents/hermes_templates/config.yaml.j2 src/hal0/agents/hermes_provision.py tests/agents/test_hermes_live_resolve_render.py
git commit -m "feat(hermes): render hal0/primary default under live_resolve_enabled flag"
```

---

## Task 8: Full normalize suite + lint gate

**Files:** none (verification task)

- [ ] **Step 1: Run all new + adjacent tests**

Run:
```bash
python -m pytest tests/normalize/ tests/config/test_slot_role.py tests/api/test_chat_normalization.py tests/api/test_virtual_models.py tests/api/test_smoke.py tests/dispatcher/test_forward.py tests/agents/test_hermes_live_resolve_render.py -v
```
Expected: ALL PASS.

- [ ] **Step 2: Lint + format (CI gate — both are fatal in CI)**

Run:
```bash
python -m ruff check src/hal0/normalize src/hal0/api/routes/v1.py src/hal0/api/__init__.py src/hal0/config/schema.py src/hal0/agents/hermes_provision.py
python -m ruff format --check src/hal0/normalize src/hal0/api/routes/v1.py src/hal0/api/__init__.py
```
Expected: PASS. If `format --check` fails, run `python -m ruff format <paths>` and re-commit (memory `feedback_hal0_ci_ruff_format_check`: the format step is separately fatal).

- [ ] **Step 3: Commit any format fixes**

```bash
git add -A && git commit -m "style: ruff format normalize + api normalization wiring" || echo "nothing to format"
```

---

## Task 9: CT105 deploy + smoke (operational — not TDD)

> Tier-2/operational. `/opt/hal0` is the SHARED runtime — run `~/.claude/bin/wip hal0 status` first; if not on `main`/clean, coordinate. Never run `hermes` as root. Don't restart hal0-api casually.

- [ ] **Step 1: Push branch + open PR**

```bash
git push -u origin plan/hal0-api-lemond-normalization
gh pr create --fill --base main
```

- [ ] **Step 2: Deploy to CT105** (after PR review/merge, or to a test checkout)

Sync the merged `main` to `/opt/hal0` per the normal hal0 update path, then restart hal0-api **once** (expect a brief dashboard blip):
```bash
ssh hal0 "systemctl restart hal0-api && sleep 3 && systemctl is-active hal0-api"
```

- [ ] **Step 3: Smoke — `/v1/models` advertises the virtual name**

```bash
ssh hal0 "curl -s http://127.0.0.1:8080/v1/models | python3 -c 'import sys,json; d=json.load(sys.stdin)[\"data\"]; r=[x for x in d if x[\"id\"]==\"hal0/primary\"][0]; print(r[\"id\"], r[\"context_length\"], r[\"_hal0\"][\"resolves_to\"])'"
```
Expected: `hal0/primary <ctx> <loaded-model-id>`.

- [ ] **Step 4: Smoke — chat via virtual name + thinking off on the wire**

```bash
ssh hal0 "curl -s http://127.0.0.1:8080/v1/chat/completions -H 'content-type: application/json' -d '{\"model\":\"hal0/primary\",\"messages\":[{\"role\":\"user\",\"content\":\"say hi in 3 words\"}]}' | head -c 400"
```
Expected: a normal completion (no `<think>` block, fast first token). Cross-check `gateway.log`/lemond logs show the request hit the loaded slot.

- [ ] **Step 5: Verify the `npu`/`utility` chains** (only if those slots exist)

```bash
ssh hal0 "curl -s http://127.0.0.1:8080/v1/models | python3 -c 'import sys,json; [print(x[\"id\"], x[\"_hal0\"][\"resolves_to\"]) for x in json.load(sys.stdin)[\"data\"] if x[\"id\"].startswith(\"hal0/\")]'"
```
Expected: each virtual name resolves to a sensible slot; `hal0/npu` never resolves to the primary while a utility/npu slot is loaded.

---

## Task 10: Hermes cutover (OpenRouter → local) — operational, gated

> Real behavior change (cloud → local primary). Re-read `config.yaml` first; restart as the `hal0` user.

- [ ] **Step 1: Enable live-resolve render + re-provision Hermes**

```bash
ssh hal0 "HAL0_HERMES_LIVE_RESOLVE=1 hal0 agent bootstrap hermes --repair"   # or the project's provision command
ssh hal0 "grep -A2 '^model:' /var/lib/hal0/.hermes/config.yaml"
```
Expected: `default: "hal0/primary"`, `provider: custom`, `base_url: http://127.0.0.1:8080/v1`.

- [ ] **Step 2: Restart Hermes (as hal0, never root) + confirm model discovery**

```bash
ssh hal0 "systemctl restart hal0-agent@hermes && sleep 3 && systemctl is-active hal0-agent@hermes"
```

- [ ] **Step 3: End-to-end agent turn**

Drive one full reason→tool→reason turn through Hermes and confirm it completes without `<think>` timeouts and uses the local slot. (Use the dashboard or the Hermes CLI as the operator normally does.)

- [ ] **Step 4: Verify context window**

Confirm Hermes shows the right context for `hal0/primary` (probed from `/v1/models` `context_length`, not the 256K fallback) — e.g. via Hermes `/info`.

---

## Task 11: Retire Bifrost

- [ ] **Step 1: Stop + disable the sidecar on CT105**

```bash
ssh hal0 "systemctl disable --now hal0-bifrost 2>/dev/null; systemctl is-active hal0-bifrost || echo 'bifrost stopped'"
```
(Do NOT `pkill -f bifrost` — memory `feedback_opt_hal0_shared_tree_runtime_trap`/handoff: it suicides the shell.)

- [ ] **Step 2: Close PR #469**

```bash
gh pr close 469 --comment "Superseded by in-hal0-api normalization (plan/hal0-api-lemond-normalization). resolve.go ported to src/hal0/normalize/resolver.py; thinking moved to top-level enable_thinking. Branch kept for reference."
```

- [ ] **Step 3: Confirm the loop**

Verify Hermes is serving from local `hal0/primary` with Bifrost down (Task 10 Step 3 still passes), and the dashboard slot status is green.

---

## Self-review notes (spec coverage)

- Spec §4.1a/§4.2 → Tasks 2,3 (resolver, role+device, chains, context_length return).
- Spec §4.1b/§5 → Task 4 + Task 5 (thinking policy, idempotent, no_think, single-seam refinement documented).
- Spec §4.3 → Task 6 (`/v1/models` virtual rows, `context_length` first key, `_hal0` block, no dup).
- Spec §4.4 → Task 1 (`SlotConfig.role`).
- Spec §6 → Task 7 (`live_resolve_enabled` render; no `custom_providers`/`model_aliases` virtual entries — confirmed unnecessary).
- Spec §7 (ensure-load / 503 terminal) → existing `_ensure_backend_for_model` retained in Task 5; resolver `fallback=True` signals ensure-load; the 503 terminal path is the existing `_check_slot_ready_for_dispatch` behavior (no new code, covered by smoke).
- Spec §8 → Tasks 2,4,6 unit + Task 8 suite + Task 9 smoke.
- Spec §8.2 rollout → Tasks 9,10,11.
- Health-cache reuse / no-new-poll (#474) → Task 5 `_normalize_loaded_models` reads `MetricsShim._health` only.

### Deferred (YAGNI — narrower than spec §4.2)

- **Operator-reorderable chains** are deferred. v1 ships built-in `DEFAULT_CHAINS`; the operator's lever
  is the per-slot `role` tag (Task 1) + which slots they keep loaded — which already covers the
  "protect the primary / utility absorbs slack" intent. Making `DEFAULT_CHAINS` config-overridable
  (e.g. a `[normalize.chains]` table) is a small, isolated follow-up if ever needed.
- **Ensure-load on a non-primary role** (spec §4.2 "opt-in") is deferred: v1 only ensure-loads the
  configured primary via the existing `_ensure_backend_for_model`. Resolver `fallback=True` is the
  hook a future opt-in would read.
- **Soft spot to resolve during implementation:** Task 5 Step 4 — confirm the exact lifespan/slot-change
  hook that refreshes `app.state.llm_slot_views_cache` (mirror wherever `hal0_chat_slot_alias_map` is
  refreshed). If none exists, a short-TTL lazy build in `_normalize_slot_views` is the fallback.
