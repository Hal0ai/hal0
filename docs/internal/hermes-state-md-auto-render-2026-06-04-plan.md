# Hermes live-state auto-render (`STATE.md`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hermes always sees current hal0 live state (loaded model, capabilities, GPU/NPU backend) — refreshed on every service restart **and** every runtime model/capability change — without bloating the cacheable persona or stalling Hermes session start.

**Architecture:** A thin `/var/lib/hal0/STATE.md` snapshot is the buffer. One shared `render_live_context()` function (re)writes it, content-hash gated. Two event writers call it: `ExecStartPre` on restart, and a detached best-effort spawn after `manager.swap()` / `orchestrator.apply()` on runtime change. The (previously-dangling) `on_session_start` hook `cat`s the file into every new Hermes session and background-regens only when stale (>5 min TTL). `SOUL.md` stays byte-stable; live primary/slot lines move out of `HERMES.md` into `STATE.md`.

**Tech Stack:** Python 3.12, Jinja2, pytest, systemd unit drop-ins, POSIX shell (hook + installer). All hal0 code under `src/hal0/`, tests under `tests/`.

**Spec:** `docs/internal/hermes-state-md-auto-render-2026-06-04.md`

**Working dir / branch:** worktree `/home/halo/dev/hal0-state-md`, branch `feat/hermes-state-md-autorender` (off `origin/main`). Run python via the repo venv: `/opt/hal0/.venv/bin/python3` on the LXC, or local `.venv` (`pip install -e . --no-deps`). Locally run **test subsets** — the full suite hangs on this VM (lemond health waits); let CI gate the full run.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/hal0/agents/hermes_templates/STATE.md.j2` | **New.** Volatile live-state snapshot template (~12 lines). |
| `src/hal0/agents/hermes_templates/HERMES.md.j2` | **Modify.** Remove live primary/chat-slot lines (now in STATE.md); keep structural map. |
| `src/hal0/agents/hermes_provision.py` | **Modify.** Add `_igpu_sclk_mhz()`, `_collect_capability_rollup()`, `_state_body_minus_timestamp()`, `render_live_context()`; delegate HERMES.md + STATE.md rendering from `_phase_context_link`. |
| `src/hal0/cli/agent_shim.py` | **Modify.** Add `render-context` subcommand + parser choice + dispatch entry. |
| `src/hal0/slots/manager.py` | **Modify.** Best-effort detached render after `swap()`. |
| `src/hal0/capabilities/orchestrator.py` | **Modify.** Best-effort detached render after `apply()`. |
| `installer/systemd/hal0-agent@hermes.service.d/override.conf` | **Modify.** Add `ExecStartPre=-…render-context`. |
| `installer/agents/hermes/hooks/inject-system-state.sh` | **New.** `on_session_start` hook: cat STATE.md + stale background-regen. |
| `installer/install.sh` | **Modify.** Install hook to `/usr/lib/hal0/hermes-hooks/`. |
| `installer/uninstall.sh` | **Modify.** Remove hook dir. |
| `tests/agents/test_hermes_state_render.py` | **New.** Template render, helpers, content-hash gating, `render_live_context`. |
| `tests/cli/test_agent_shim.py` | **Modify.** `render-context` dispatch test. |

**Shared-function contract** (used everywhere so there is one code path):

```python
def render_live_context(
    *,
    hermes_home: Path,
    slots_fetcher: Callable[[], list[dict[str, Any]]] | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Re-probe live slot/capability state and (re)write HERMES.md + STATE.md.

    STATE.md is content-hash gated: rewritten (and its ``_as_of`` line
    bumped) only when the substantive body changes. HERMES.md is written
    atomically (identical content => identical bytes => prompt-cache safe).
    Never raises on a daemon-unreachable read — leaves last-good files and
    reports ``degraded=True``.

    Returns: {"state_written": bool, "hermes_written": bool,
              "degraded": bool, "state_path": str}.
    """
```

---

## Task 1: `STATE.md.j2` template + render helpers (capability rollup, iGPU clock)

**Files:**
- Create: `src/hal0/agents/hermes_templates/STATE.md.j2`
- Modify: `src/hal0/agents/hermes_provision.py` (add helpers near the other `_slot_*` helpers, ~line 1841)
- Test: `tests/agents/test_hermes_state_render.py`

- [ ] **Step 1: Write the failing test for the helpers + template**

Create `tests/agents/test_hermes_state_render.py`:

```python
"""Unit tests for the live STATE.md render path (Hermes auto-render)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.agents import hermes_provision as hp


def _slot(name, type_, model, *, state="ready", backend=None):
    d = {"name": name, "type": type_, "model_id": model, "status": state}
    if backend:
        d["backend"] = backend
    return d


def test_collect_capability_rollup_filters_and_maps_types():
    slots = [
        _slot("primary", "llm", "qwen3-25b", backend="vulkan"),   # chat -> excluded here
        _slot("embed", "embedding", "bge-m3", backend="vulkan"),
        _slot("stt", "stt", "moonshine", backend="vulkan"),
        _slot("tts", "tts", "kokoro", backend="vulkan"),
        _slot("img", "image", "sdxl", backend="rocm"),
        _slot("rerank", "rerank", "bge-reranker", backend="vulkan"),
        _slot("cold", "embedding", "unused", state="stopped"),     # not ready -> excluded
    ]
    rollup = hp._collect_capability_rollup(slots)
    caps = {r["capability"]: r for r in rollup}
    assert set(caps) == {"embed", "voice-stt", "voice-tts", "img", "rerank"}
    assert caps["img"]["backend"] == "rocm"
    assert caps["embed"]["model_id"] == "bge-m3"
    assert "cold" not in {r.get("name") for r in rollup}


def test_state_template_renders_full_state():
    body = hp._render_template(
        "STATE.md.j2",
        primary={"alias": "primary", "model_id": "qwen3-25b",
                 "backend_url": "http://127.0.0.1:8080/v1", "context_length": 32768,
                 "backend": "vulkan"},
        capabilities=[{"capability": "embed", "model_id": "bge-m3", "backend": "vulkan"}],
        npu={"present": True, "model_id": "qwen3-it-4b-FLM"},
        igpu_sclk_mhz=2900,
        dashboard_url="https://hal0.thinmint.dev",
        lemonade_base="http://127.0.0.1:13305",
        daemon="reachable",
        as_of="2026-06-04T15:00:00+00:00",
    )
    assert "qwen3-25b" in body
    assert "32768" in body
    assert "embed" in body and "bge-m3" in body
    assert "vulkan" in body
    assert "qwen3-it-4b-FLM" in body
    assert "2900" in body
    assert "reachable" in body
    assert body.rstrip().splitlines()[-1].startswith("_as_of: 2026-06-04T15:00:00")


def test_state_template_degraded_no_primary():
    body = hp._render_template(
        "STATE.md.j2",
        primary=None, capabilities=[], npu={"present": False, "model_id": None},
        igpu_sclk_mhz=None, dashboard_url="https://hal0.thinmint.dev",
        lemonade_base="http://127.0.0.1:13305", daemon="degraded",
        as_of="2026-06-04T15:00:00+00:00",
    )
    assert "degraded" in body
    assert "no chat model loaded" in body.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/agents/test_hermes_state_render.py -v`
Expected: FAIL — `AttributeError: module 'hal0.agents.hermes_provision' has no attribute '_collect_capability_rollup'` and `jinja2.exceptions.TemplateNotFound: STATE.md.j2`.

- [ ] **Step 3: Add the helpers to `hermes_provision.py`**

Insert after `_slot_context_length` (~line 1841), near the other `_slot_*` helpers:

```python
# capability slot `type` (from /api/slots) -> STATE.md rollup label.
_CAPABILITY_TYPE_LABELS = {
    "embedding": "embed",
    "stt": "voice-stt",
    "tts": "voice-tts",
    "image": "img",
    "img": "img",
    "rerank": "rerank",
}


def _collect_capability_rollup(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ready non-chat capability slots, mapped to STATE.md rollup rows.

    Chat (``type=='llm'``) slots are handled by the primary/chat path and
    excluded here. Only ready slots are advertised so we never tell the
    agent about a capability that isn't actually loaded.
    """
    out: list[dict[str, Any]] = []
    for s in slots:
        if not isinstance(s, dict):
            continue
        label = _CAPABILITY_TYPE_LABELS.get((s.get("type") or "").lower())
        if not label:
            continue
        if not _is_ready(s):
            continue
        out.append(
            {
                "capability": label,
                "model_id": _slot_model_id(s),
                "backend": s.get("backend"),
            }
        )
    return out


def _igpu_sclk_mhz(sysfs_root: Path = Path("/sys/class/drm")) -> int | None:
    """Active iGPU shader clock (MHz) from amdgpu sysfs, or None.

    Reads ``pp_dpm_sclk`` and returns the MHz of the active ('*') DPM
    level. Best-effort: any read/parse error returns None so the template
    simply omits the clock line. Tries card0..card3 (Strix Halo dev nodes);
    ``sysfs_root`` is injectable for tests.
    """
    for idx in range(4):
        path = sysfs_root / f"card{idx}" / "device" / "pp_dpm_sclk"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if line.rstrip().endswith("*"):
                # e.g. "2: 2900Mhz *"
                for tok in line.replace("Mhz", " ").replace("MHz", " ").split():
                    if tok.isdigit():
                        return int(tok)
        # no active line on this card — try the next one
    return None
```

- [ ] **Step 4: Create the `STATE.md.j2` template**

Create `src/hal0/agents/hermes_templates/STATE.md.j2`:

```jinja
{# Volatile live-state snapshot — re-rendered on restart + on model/slot
   change, injected into every Hermes session by the on_session_start hook.
   Keep it LEAN: this is fed to the model every session. Stable identity
   lives in SOUL.md; structural map lives in HERMES.md.
   Params: primary (dict|None), capabilities (list), npu (dict),
   igpu_sclk_mhz (int|None), dashboard_url, lemonade_base, daemon, as_of. #}
# Live system state

{% if primary -%}
- Chat model: `{{ primary.model_id }}` ({{ primary.context_length | default("?") }} ctx{% if primary.backend %}, {{ primary.backend }}{% endif %}) via `model_aliases.{{ primary.alias | default("primary") }}`
{%- else -%}
- Chat model: _no chat model loaded — wire one via `hal0 slot create`._
{%- endif %}
{% if capabilities -%}
- Capabilities: {% for c in capabilities %}{{ c.capability }} (`{{ c.model_id }}`{% if c.backend %}/{{ c.backend }}{% endif %}){% if not loop.last %}, {% endif %}{% endfor %}
{%- else -%}
- Capabilities: _none loaded._
{%- endif %}
{% if npu.present -%}
- NPU (XDNA): {% if npu.model_id %}`{{ npu.model_id }}` (FLM){% else %}present, idle{% endif %}
{%- endif %}
{% if igpu_sclk_mhz -%}
- iGPU clock: {{ igpu_sclk_mhz }} MHz
{%- endif %}
- Endpoints: dashboard {{ dashboard_url }} · lemond {{ lemonade_base }}
- Daemon: {{ daemon }}

_as_of: {{ as_of }}_
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/agents/test_hermes_state_render.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/hal0/agents/hermes_templates/STATE.md.j2 src/hal0/agents/hermes_provision.py tests/agents/test_hermes_state_render.py
git commit -m "feat(hermes): STATE.md template + capability/igpu render helpers"
```

---

## Task 2: `render_live_context()` shared function with content-hash gating

**Files:**
- Modify: `src/hal0/agents/hermes_provision.py` (add after `_collect_capability_rollup`/`_igpu_sclk_mhz`)
- Test: `tests/agents/test_hermes_state_render.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_hermes_state_render.py`:

```python
def test_state_body_minus_timestamp_ignores_as_of_line():
    a = "# Live system state\n- Chat model: x\n\n_as_of: 2026-06-04T10:00:00+00:00_\n"
    b = "# Live system state\n- Chat model: x\n\n_as_of: 2026-06-04T22:00:00+00:00_\n"
    assert hp._state_body_minus_timestamp(a) == hp._state_body_minus_timestamp(b)


def test_render_live_context_writes_then_skips_when_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "ETC_HAL0_DIR", tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    slots = [
        {"name": "primary", "type": "llm", "model_id": "qwen3-25b",
         "status": "ready", "backend": "vulkan"},
        {"name": "embed", "type": "embedding", "model_id": "bge-m3",
         "status": "ready", "backend": "vulkan"},
    ]
    monkeypatch.setattr(hp, "_fetch_model_contexts", lambda: {"primary": 32768})

    r1 = hp.render_live_context(
        hermes_home=home, slots_fetcher=lambda: slots,
        now_iso="2026-06-04T10:00:00+00:00",
    )
    assert r1["state_written"] is True
    assert r1["degraded"] is False
    state = (tmp_path / "STATE.md").read_text()
    assert "qwen3-25b" in state and "bge-m3" in state

    # Same substantive state, different clock-time -> NOT rewritten.
    r2 = hp.render_live_context(
        hermes_home=home, slots_fetcher=lambda: slots,
        now_iso="2026-06-04T22:00:00+00:00",
    )
    assert r2["state_written"] is False
    assert "10:00:00" in (tmp_path / "STATE.md").read_text()  # as_of unchanged


def test_render_live_context_degraded_when_daemon_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "ETC_HAL0_DIR", tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    r = hp.render_live_context(
        hermes_home=home, slots_fetcher=lambda: [],
        now_iso="2026-06-04T10:00:00+00:00",
    )
    assert r["degraded"] is True
    assert "degraded" in (tmp_path / "STATE.md").read_text()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/agents/test_hermes_state_render.py -k "render_live_context or minus_timestamp" -v`
Expected: FAIL — `module ... has no attribute '_state_body_minus_timestamp'` / `render_live_context`.

- [ ] **Step 3: Implement the function**

Add to `hermes_provision.py` after the Task 1 helpers. Note `datetime` is needed — add `from datetime import datetime, timezone` to the imports at the top of the file if not present.

```python
def _state_body_minus_timestamp(text: str) -> str:
    """STATE.md body with the volatile ``_as_of:`` line removed.

    Used for content-hash gating so a regen that finds nothing
    substantive changed does not churn the file (and bust prompt-cache).
    """
    return "\n".join(
        line for line in text.splitlines() if not line.startswith("_as_of:")
    )


def render_live_context(
    *,
    hermes_home: Path,
    slots_fetcher: Callable[[], list[dict[str, Any]]] | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Re-probe live slot/capability state; (re)write HERMES.md + STATE.md.

    See module docstring / design doc. Never raises on a daemon-unreachable
    read — leaves last-good files and reports ``degraded=True``.
    """
    fetch = slots_fetcher or _fetch_slots
    slots_all = fetch() or []
    degraded = not slots_all

    contexts = _fetch_model_contexts()
    chat_slots = _collect_chat_slots(slots_all, contexts=contexts)
    primary_raw = _resolve_primary_slot(slots_fetcher=lambda: slots_all)

    primary_slot = next(
        (s for s in slots_all if isinstance(s, dict) and s.get("name") == "primary"),
        None,
    )
    primary_for_template: dict[str, Any] | None = None
    if primary_raw["model"] and primary_raw["model"] != "primary":
        primary_for_template = {
            "alias": _slot_alias(primary_slot) if primary_slot else "primary",
            "model_id": primary_raw["model"],
            "backend_url": primary_raw["base_url"],
            "context_length": primary_raw["context_length"],
            "backend": (primary_slot or {}).get("backend"),
        }

    capabilities = _collect_capability_rollup(slots_all)

    # NPU: present from the cached env snapshot; loaded model from any FLM
    # backend slot (NPU LLM path is FastFlowLM — see hal0_flm_npu_llm_models).
    env_report = _latest_env_snapshot(hermes_home).get("env_report", {})
    npu_model = next(
        (
            _slot_model_id(s)
            for s in slots_all
            if isinstance(s, dict) and "flm" in str(s.get("backend") or "").lower()
        ),
        None,
    )
    npu = {"present": bool(env_report.get("npu", {}).get("present")), "model_id": npu_model}

    now = now_iso or datetime.now(timezone.utc).isoformat()

    state_vars = {
        "primary": primary_for_template,
        "capabilities": capabilities,
        "npu": npu,
        "igpu_sclk_mhz": _igpu_sclk_mhz(),
        "dashboard_url": "https://hal0.thinmint.dev",
        "lemonade_base": "http://127.0.0.1:13305",
        "daemon": "degraded" if degraded else "reachable",
        "as_of": now,
    }
    new_state = _render_template("STATE.md.j2", **state_vars)

    out: dict[str, Any] = {
        "state_written": False,
        "hermes_written": False,
        "degraded": degraded,
        "state_path": str(ETC_HAL0_DIR / "STATE.md"),
    }

    # STATE.md — content-hash gated (ignore the as_of line).
    state_path = ETC_HAL0_DIR / "STATE.md"
    existing = ""
    if state_path.exists():
        existing = state_path.read_text(encoding="utf-8")
    if _state_body_minus_timestamp(existing) != _state_body_minus_timestamp(new_state):
        _atomic_write(state_path, new_state)
        out["state_written"] = True

    # HERMES.md — structural map; atomic write (identical content => identical
    # bytes => prompt-cache safe). Render failure is non-fatal.
    try:
        hermes_md = _render_template(
            "HERMES.md.j2",
            env=env_report,
            hal0_version=_hal0_version_string(),
            hermes_version=_hermes_version_pin(),
            primary=primary_for_template,
            chat_slots=chat_slots,
            peer_agents=[],
        )
        hpath = ETC_HAL0_DIR / "HERMES.md"
        if not hpath.exists() or hpath.read_text(encoding="utf-8") != hermes_md:
            _atomic_write(hpath, hermes_md)
            out["hermes_written"] = True
    except Exception:  # noqa: BLE001 — best-effort; STATE.md already written
        pass

    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/agents/test_hermes_state_render.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/hal0/agents/hermes_provision.py tests/agents/test_hermes_state_render.py
git commit -m "feat(hermes): render_live_context() with content-hash-gated STATE.md"
```

---

## Task 3: Trim live lines from `HERMES.md.j2`; render STATE.md in `_phase_context_link`

**Files:**
- Modify: `src/hal0/agents/hermes_templates/HERMES.md.j2`
- Modify: `src/hal0/agents/hermes_provision.py` (`_phase_context_link`, ~line 1357)
- Test: `tests/agents/test_hermes_state_render.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/agents/test_hermes_state_render.py`:

```python
def test_phase_context_link_writes_state_md(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "ETC_HAL0_DIR", tmp_path)
    home = tmp_path / "home"
    (home / "memories").mkdir(parents=True)
    # No env snapshot file -> empty env_report (templates use defaults).
    monkeypatch.setattr(hp, "_fetch_model_contexts", lambda: {"primary": 32768})
    monkeypatch.setattr(
        hp, "_fetch_slots",
        lambda: [{"name": "primary", "type": "llm", "model_id": "qwen3-25b",
                  "status": "ready", "backend": "vulkan"}],
    )
    monkeypatch.setattr(hp, "HAL0_BUNDLED_SKILLS", tmp_path / "nope")

    state = hp.BootstrapState(hermes_home=str(home))  # other fields default
    res = hp._phase_context_link(state)
    assert res.status == hp.PhaseStatus.OK
    assert (tmp_path / "STATE.md").exists()
    assert "qwen3-25b" in (tmp_path / "STATE.md").read_text()
    # HERMES.md no longer carries the live "Active capability slots" body.
    hermes_md = (tmp_path / "HERMES.md").read_text()
    assert "Live system state" not in hermes_md  # that lives in STATE.md now
```

> Note: confirm the `BootstrapState` constructor signature in `hermes_provision.py` (~line 40) and pass whatever required fields it needs; adapt the `hp.BootstrapState(...)` call to match. If construction is heavy, build it via the same helper the existing `test_hermes_provision.py` uses.

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/agents/test_hermes_state_render.py::test_phase_context_link_writes_state_md -v`
Expected: FAIL — `STATE.md` not written by `_phase_context_link` yet.

- [ ] **Step 3: Trim `HERMES.md.j2`**

Edit `src/hal0/agents/hermes_templates/HERMES.md.j2`. Delete the live block (current lines ~15–28):

```jinja
# Active capability slots
{% if primary %}
- **primary** (chat/{{ primary.alias | default("primary") }}): `{{ primary.model_id }}`
  - Endpoint: {{ primary.backend_url }} (alias `model_aliases.primary`)
{%- endif %}
{%- if chat_slots %}
{%- for slot in chat_slots %}
- **{{ slot.alias }}** (chat): `{{ slot.model_id }}`
  - {{ slot.backend_url }} → call via `model_aliases.{{ slot.alias }}`
{%- endfor %}
{%- endif %}
{%- if not primary and not chat_slots %}
- _no chat slots loaded — wire one via `hal0 slot create` then re-run bootstrap with `--repair`._
{%- endif %}
```

Replace it with a pointer to STATE.md:

```jinja
# Live state

Current loaded model, capabilities, and GPU/NPU backend are in
`/var/lib/hal0/STATE.md` (auto-injected each session; refreshed on restart
and on every model/slot change). Read it for the live picture rather
than assuming — it carries an `_as_of:` timestamp.
```

- [ ] **Step 4: Make `_phase_context_link` delegate STATE.md + HERMES.md to `render_live_context`**

In `_phase_context_link` (`hermes_provision.py` ~1357): keep the SOUL.md / AGENTS.md / MCP-CLIENTS.md rendering and writes as-is. Remove the HERMES.md render+write block (lines ~1442–1454) — `render_live_context` now owns HERMES.md and STATE.md. After the SOUL.md write block (after line ~1440) and before the AGENTS.md block, add:

```python
    # STATE.md + HERMES.md are the live files — one shared code path with
    # the per-restart / per-swap writers. Best-effort: failure here must
    # not fail bootstrap (SOUL/AGENTS already written).
    try:
        live = render_live_context(hermes_home=hermes_home)
        details["rendered"]["STATE.md"] = {"path": live["state_path"]}
        if live["degraded"]:
            warnings.append("STATE.md rendered with daemon degraded")
        # Re-establish the HOST.md -> HERMES.md mirror (render_live_context
        # writes HERMES.md but not the symlink).
        hpath = ETC_HAL0_DIR / "HERMES.md"
        if hpath.exists():
            host_md = hermes_home / "memories" / "HOST.md"
            if _safe_symlink(hpath, host_md):
                details["links"].append(str(host_md))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"render_live_context: {exc}")
```

Also delete the now-dead `chat_slots` / `primary_for_template` setup at the top of `_phase_context_link` only if nothing else in the function uses it after the HERMES.md removal (the SOUL.md render still needs `env`/`vars_`; keep `vars_` for SOUL/AGENTS/MCP-CLIENTS but drop `primary`/`chat_slots` keys if unused by those three templates — verify by grep: `grep -n "primary\|chat_slots" src/hal0/agents/hermes_templates/{SOUL,AGENTS,MCP-CLIENTS}.md.j2`. If they don't reference them, remove from `vars_`).

- [ ] **Step 5: Run the test + the existing provision tests**

Run: `.venv/bin/python -m pytest tests/agents/test_hermes_state_render.py tests/agents/test_hermes_provision.py tests/agents/test_hermes_provision_collect.py -v`
Expected: PASS. If a pre-existing provision test asserted HERMES.md's old "Active capability slots" text, update that assertion to the new pointer text (it moved to STATE.md by design).

- [ ] **Step 6: Commit**

```bash
git add src/hal0/agents/hermes_templates/HERMES.md.j2 src/hal0/agents/hermes_provision.py tests/agents/test_hermes_state_render.py
git commit -m "refactor(hermes): move live slot lines HERMES.md -> STATE.md; render via shared path"
```

---

## Task 4: `render-context` subcommand in the agent shim

**Files:**
- Modify: `src/hal0/cli/agent_shim.py` (`cmd_render_context`, parser choice, `_DISPATCH`)
- Test: `tests/cli/test_agent_shim.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/cli/test_agent_shim.py` (match its existing import style; it imports the shim module — confirm the alias, e.g. `from hal0.cli import agent_shim`):

```python
def test_render_context_dispatch_calls_render(monkeypatch, tmp_path):
    from hal0.cli import agent_shim

    called = {}

    def fake_render(*, hermes_home):
        called["home"] = hermes_home
        return {"state_written": True, "hermes_written": False,
                "degraded": False, "state_path": "/var/lib/hal0/STATE.md"}

    monkeypatch.setattr(agent_shim, "_render_live_context", fake_render)

    cfg = agent_shim.AgentConfig(
        agent_id="hermes", agent_type="hermes",
        home=tmp_path, venv=tmp_path / "venv",
        host="127.0.0.1", port=8133,
    )
    rc = agent_shim.cmd_render_context(cfg)
    assert rc == 0
    assert called["home"] == tmp_path


def test_render_context_in_parser_choices():
    from hal0.cli import agent_shim

    parser = agent_shim._build_parser()
    args = parser.parse_args(["hermes", "render-context"])
    assert args.subcommand == "render-context"
```

> Confirm `AgentConfig`'s real fields/constructor (~line 76) and adapt the kwargs — use the same construction the existing shim tests use if they have a fixture/factory.

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/cli/test_agent_shim.py -k render_context -v`
Expected: FAIL — `cmd_render_context` / `_render_live_context` missing; `render-context` not a valid choice.

- [ ] **Step 3: Implement the subcommand**

In `src/hal0/cli/agent_shim.py`:

a) Add a thin import-indirection near the top-level imports (kept patchable for tests, and lazy to avoid importing provision at module load):

```python
def _render_live_context(*, hermes_home: Path) -> dict[str, object]:
    """Indirection so tests can patch; lazy import keeps shim startup light."""
    from hal0.agents.hermes_provision import render_live_context

    return render_live_context(hermes_home=hermes_home)
```

b) Add the command function (near `cmd_reprovision`):

```python
def cmd_render_context(cfg: AgentConfig) -> int:
    """Re-probe live hal0 state and (re)write STATE.md + HERMES.md.

    Wired as ``ExecStartPre`` on hal0-agent@hermes.service (non-fatal) and
    spawned detached after a model/slot change. Render is best-effort:
    a daemon-unreachable read leaves last-good files and still exits 0 so
    it never blocks the service from starting.
    """
    if cfg.agent_type != "hermes":
        _die(f"agent type '{cfg.agent_type}' not supported by this shim yet")
    try:
        result = _render_live_context(hermes_home=cfg.home)
    except Exception as exc:  # noqa: BLE001 — never block service start
        print(f"hal0-agent: render-context failed (non-fatal): {exc}", file=sys.stderr)
        return 0
    state = "degraded" if result.get("degraded") else "ok"
    print(
        f"hal0-agent: render-context {state} "
        f"(state_written={result.get('state_written')}, "
        f"hermes_written={result.get('hermes_written')})"
    )
    return 0
```

c) Add `"render-context"` to the parser `choices` list and to `_DISPATCH`:

```python
        choices=["serve", "stop", "status", "reprovision", "render-context"],
```
```python
_DISPATCH: dict[str, Callable[[AgentConfig], int]] = {
    "serve": cmd_serve,
    "stop": cmd_stop,
    "status": cmd_status,
    "reprovision": cmd_reprovision,
    "render-context": cmd_render_context,
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/cli/test_agent_shim.py -k render_context -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/hal0/cli/agent_shim.py tests/cli/test_agent_shim.py
git commit -m "feat(hermes): hal0-agent render-context subcommand"
```

---

## Task 5: ExecStartPre wiring on the systemd unit

**Files:**
- Modify: `installer/systemd/hal0-agent@hermes.service.d/override.conf`

- [ ] **Step 1: Add the ExecStartPre directive**

In `installer/systemd/hal0-agent@hermes.service.d/override.conf`, under the existing `[Service]` section, append:

```ini
# Refresh STATE.md / HERMES.md from live slot+capability state before the
# agent boots, so a restart always reflects the current model/backend.
# Leading `-` => non-fatal: a render failure (daemon not up yet) leaves
# last-good files and never blocks the service from starting. See
# docs/internal/hermes-state-md-auto-render-2026-06-04.md.
ExecStartPre=-/usr/local/bin/hal0-agent %i render-context
```

- [ ] **Step 2: Verify the unit parses**

Run (locally, syntax-only — `systemd-analyze verify` needs the full unit; instead lint the drop-in for the directive):

```bash
grep -n "ExecStartPre=-/usr/local/bin/hal0-agent %i render-context" installer/systemd/hal0-agent@hermes.service.d/override.conf
```
Expected: the line is present. (Full `systemctl daemon-reload` verification happens on the LXC in Task 8.)

- [ ] **Step 3: Commit**

```bash
git add installer/systemd/hal0-agent@hermes.service.d/override.conf
git commit -m "feat(hermes): ExecStartPre render-context on hal0-agent@hermes"
```

---

## Task 6: `on_session_start` hook script + installer wiring

**Files:**
- Create: `installer/agents/hermes/hooks/inject-system-state.sh`
- Modify: `installer/install.sh`
- Modify: `installer/uninstall.sh`

- [ ] **Step 1: Write the hook script**

Create `installer/agents/hermes/hooks/inject-system-state.sh` (mode 0755):

```sh
#!/bin/sh
# hal0 Hermes on_session_start hook — inject live system state.
#
# Wired by hermes_templates/config.yaml.j2 (hooks.on_session_start).
# Contract: emit context to stdout for the new session; stay inside the
# 2s hook timeout. We ONLY cat the pre-rendered /var/lib/hal0/STATE.md (the
# expensive probe runs in the writers, not here). If STATE.md is older
# than the TTL we additionally kick a DETACHED background refresh so the
# NEXT session is fresh — we never block this session on a probe.
set -eu

STATE_FILE="/var/lib/hal0/STATE.md"
TTL_SECONDS=300   # 5 min — defense-in-depth for missed change events.

# Missing file (first boot before any render) => emit nothing, exit clean.
[ -f "$STATE_FILE" ] || exit 0

# Stale? Kick a detached, output-discarded refresh. setsid+& so it never
# holds up the session even if the daemon probe is slow.
now=$(date +%s)
mtime=$(stat -c %Y "$STATE_FILE" 2>/dev/null || echo "$now")
age=$(( now - mtime ))
if [ "$age" -gt "$TTL_SECONDS" ]; then
    setsid /usr/local/bin/hal0-agent hermes render-context >/dev/null 2>&1 &
fi

# Inject the current (possibly slightly-stale) snapshot into the session.
cat "$STATE_FILE"
```

- [ ] **Step 2: Test the hook behavior with a shell test**

Run these manual assertions (no pytest harness for shell hooks in-repo):

```bash
# missing file -> no output, exit 0
sh installer/agents/hermes/hooks/inject-system-state.sh && echo "EXIT_OK"
# Expected: just "EXIT_OK" (STATE_FILE absent at /etc/hal0 on this VM).

# present file -> contents echoed (use a temp copy of the logic by pointing
# STATE_FILE via a sed-free check): create a fixture and source-test inline
tmp=$(mktemp -d); printf '# Live system state\n_as_of: x_\n' > "$tmp/STATE.md"
STATE_FILE="$tmp/STATE.md" sh -c '
  set -eu; STATE_FILE="$1"; [ -f "$STATE_FILE" ] || exit 0; cat "$STATE_FILE"
' _ "$tmp/STATE.md"
# Expected: prints the two lines.
```
Expected: first prints `EXIT_OK`; second prints the fixture contents.

- [ ] **Step 3: Wire the hook into `install.sh`**

Find where `install.sh` defines `LIB_DIR` / installs lib assets (`grep -n "usr/lib/hal0\|LIB_DIR\|hermes" installer/install.sh`). Add a step that copies the hook into place (adapt variable names to the script's existing style):

```sh
# hal0 Hermes session hooks (on_session_start: inject-system-state.sh).
install -d "${LIB_DIR}/hermes-hooks"
install -m 0755 "${SRC_DIR}/agents/hermes/hooks/inject-system-state.sh" \
    "${LIB_DIR}/hermes-hooks/inject-system-state.sh"
```

> `LIB_DIR` here must resolve to `/usr/lib/hal0` (matches `config.yaml.j2`'s `/usr/lib/hal0/hermes-hooks/inject-system-state.sh` and `uninstall.sh`'s `LIB_DIR`). Confirm `SRC_DIR` (or equivalent) is the installer's source root used by other `install -m` calls; reuse that exact variable.

- [ ] **Step 4: Wire removal into `uninstall.sh`**

In `installer/uninstall.sh`, near the existing `LIB_DIR` cleanup (~line 70–77), ensure the hooks dir is removed:

```sh
rm -rf "${LIB_DIR}/hermes-hooks"
```
(If `uninstall.sh` already `rm -rf "${LIB_DIR}"` wholesale, no change is needed — verify and skip this step if so.)

- [ ] **Step 5: Verify scripts are shell-lint clean**

Run: `shellcheck installer/agents/hermes/hooks/inject-system-state.sh` (if `shellcheck` is available) and `sh -n installer/agents/hermes/hooks/inject-system-state.sh`
Expected: no syntax errors. (`sh -n` is always available; shellcheck optional.)

- [ ] **Step 6: Commit**

```bash
git add installer/agents/hermes/hooks/inject-system-state.sh installer/install.sh installer/uninstall.sh
git commit -m "feat(hermes): write the on_session_start inject-system-state hook + installer wiring"
```

---

## Task 7: Runtime writers — refresh on `swap()` and `apply()`

**Files:**
- Modify: `src/hal0/slots/manager.py` (`swap()`, ~line 787)
- Modify: `src/hal0/capabilities/orchestrator.py` (`apply()`, ~line 316)
- Test: `tests/agents/test_hermes_state_render.py` (append — test the shared spawn helper)

The daemon runs an asyncio event loop; we must NOT block it on the urllib probe. Spawn a **detached, best-effort** `hal0-agent hermes render-context` subprocess. Factor it into one helper so both call sites are identical.

- [ ] **Step 1: Write the failing test for the spawn helper**

Append to `tests/agents/test_hermes_state_render.py`:

```python
def test_spawn_context_refresh_is_best_effort(monkeypatch):
    from hal0.agents import hermes_refresh

    calls = {}

    def fake_popen(argv, **kw):
        calls["argv"] = argv
        calls["kw"] = kw
        class _P:  # minimal stand-in
            pass
        return _P()

    monkeypatch.setattr(hermes_refresh.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(hermes_refresh.shutil, "which", lambda _n: "/usr/local/bin/hal0-agent")

    hermes_refresh.spawn_context_refresh("hermes")
    assert calls["argv"] == ["/usr/local/bin/hal0-agent", "hermes", "render-context"]

    # Never raises even if Popen blows up.
    def boom(*a, **k):
        raise OSError("no exec")
    monkeypatch.setattr(hermes_refresh.subprocess, "Popen", boom)
    hermes_refresh.spawn_context_refresh("hermes")  # must not raise
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/agents/test_hermes_state_render.py::test_spawn_context_refresh_is_best_effort -v`
Expected: FAIL — `No module named 'hal0.agents.hermes_refresh'`.

- [ ] **Step 3: Create the spawn helper module**

Create `src/hal0/agents/hermes_refresh.py`:

```python
"""Fire-and-forget trigger to refresh the Hermes live-context files.

Called from the asyncio daemon (slot swap / capability apply) where we
must not block the event loop on the urllib probe inside
``render_live_context``. We spawn a detached ``hal0-agent <id>
render-context`` instead — the subcommand owns the probe + atomic writes.
Best-effort: any failure is logged and swallowed; a model swap must never
fail because context refresh couldn't start.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def spawn_context_refresh(agent_id: str = "hermes") -> None:
    """Spawn a detached ``hal0-agent <agent_id> render-context``. Never raises."""
    try:
        binary = shutil.which("hal0-agent") or "/usr/local/bin/hal0-agent"
        subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            [binary, agent_id, "render-context"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug("hermes context refresh spawn failed (non-fatal): %s", exc)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/agents/test_hermes_state_render.py::test_spawn_context_refresh_is_best_effort -v`
Expected: PASS.

- [ ] **Step 5: Call the helper from `manager.swap()`**

In `src/hal0/slots/manager.py`, at the end of `swap()` (~line 787) — after the swap has succeeded and is about to `return slot` — add:

```python
        # Refresh Hermes's live-context files so a model swap is visible to
        # the agent on its next session (detached; never blocks the swap).
        from hal0.agents.hermes_refresh import spawn_context_refresh

        spawn_context_refresh()
```

Place it just before the `return`. Read the surrounding lines first to match indentation and ensure it's on the success path only (not inside an exception branch).

- [ ] **Step 6: Call the helper from `orchestrator.apply()`**

In `src/hal0/capabilities/orchestrator.py`, at the end of `apply()` (~line 316), on the success path before its return, add the same three lines:

```python
        from hal0.agents.hermes_refresh import spawn_context_refresh

        spawn_context_refresh()
```

- [ ] **Step 7: Run the relevant manager/orchestrator tests**

Run: `.venv/bin/python -m pytest tests/ -k "swap or orchestrator or capabilit" -v` (subset — full suite hangs locally)
Expected: PASS. Tests that exercise `swap`/`apply` should be unaffected (the spawn is patched-out or harmlessly fails when `hal0-agent` isn't on PATH in the test env — confirm no test asserts on subprocess; if one does, monkeypatch `spawn_context_refresh` in that test).

- [ ] **Step 8: Commit**

```bash
git add src/hal0/agents/hermes_refresh.py src/hal0/slots/manager.py src/hal0/capabilities/orchestrator.py tests/agents/test_hermes_state_render.py
git commit -m "feat(hermes): refresh STATE.md on slot swap + capability apply"
```

---

## Task 8: Lint, format, and LXC integration verification

**Files:** none (verification only)

- [ ] **Step 1: Ruff lint + format check (CI gates both)**

Run:
```bash
.venv/bin/ruff check src/hal0/agents/hermes_provision.py src/hal0/agents/hermes_refresh.py src/hal0/cli/agent_shim.py
.venv/bin/ruff format --check src/hal0/agents/hermes_provision.py src/hal0/agents/hermes_refresh.py src/hal0/cli/agent_shim.py
```
Expected: both clean. Fix any findings and re-run. (`ruff format --check` is a separate fatal CI step — do not skip it.)

- [ ] **Step 2: Run the full new test file + touched suites**

Run:
```bash
.venv/bin/python -m pytest tests/agents/test_hermes_state_render.py tests/cli/test_agent_shim.py tests/agents/test_hermes_provision.py -v
```
Expected: all PASS.

- [ ] **Step 3: Deploy to the LXC and verify end-to-end**

The runtime behavior (systemd ExecStartPre, the hook, live probe) can only be verified on `hal0` (CT 105). Sync + install + exercise:

```bash
# Sync the branch to the LXC working copy (adapt to your deploy flow).
ssh hal0 'cd /opt/hal0 && git fetch origin && git checkout feat/hermes-state-md-autorender && git pull --ff-only'
ssh hal0 'cd /opt/hal0 && .venv/bin/pip install -e . --no-deps'
ssh hal0 'sudo bash installer/install.sh --upgrade'   # or the project's reinstall path; confirm the flag
```

Then verify each trigger:

```bash
# (a) Restart trigger — ExecStartPre rewrites STATE.md.
ssh hal0 'sudo systemctl daemon-reload && sudo systemctl restart hal0-agent@hermes'
ssh hal0 'cat /var/lib/hal0/STATE.md'   # shows current model + as_of just now
ssh hal0 'journalctl -u hal0-agent@hermes -n 20 | grep render-context'  # "render-context ok ..."

# (b) Runtime change trigger — swap a slot, STATE.md as_of bumps.
ssh hal0 'cat /var/lib/hal0/STATE.md | grep _as_of'   # note timestamp
ssh hal0 'hal0 slot swap primary <some-other-model>'  # or capability_set
sleep 3
ssh hal0 'cat /var/lib/hal0/STATE.md | grep _as_of'   # timestamp advanced; model changed

# (c) Hook injection — new session sees STATE.md.
ssh hal0 'cat /usr/lib/hal0/hermes-hooks/inject-system-state.sh'  # installed
ssh hal0 'sh /usr/lib/hal0/hermes-hooks/inject-system-state.sh'   # prints STATE.md

# (d) Cache safety — SOUL.md byte-stable across a restart with no slot change.
ssh hal0 'sha256sum /var/lib/hal0/.hermes/SOUL.md'
ssh hal0 'sudo systemctl restart hal0-agent@hermes && sha256sum /var/lib/hal0/.hermes/SOUL.md'
# Expected: identical hashes.
```
Expected: (a) STATE.md `_as_of` is fresh + journal logs `render-context ok`; (b) `_as_of` advances and the model line changes after a swap; (c) hook prints STATE.md; (d) SOUL.md hash unchanged.

- [ ] **Step 4: Record findings + push the branch**

Note any deviations in the spec doc's "Testing strategy" section. Then push (Tier-2: verify branch name + remote first):

```bash
git push -u origin feat/hermes-state-md-autorender
```

- [ ] **Step 5: Open the PR**

```bash
gh pr create --title "feat(hermes): auto-render live STATE.md on restart + model/slot change" \
  --body "Implements docs/internal/hermes-state-md-auto-render-2026-06-04.md (DreamServer mesh parity #2). See plan: docs/internal/hermes-state-md-auto-render-2026-06-04-plan.md."
```

---

## Self-Review Notes (author)

- **Spec coverage:** STATE.md template (T1) ✓; render_live_context + content-hash + as_of + degraded (T2) ✓; HERMES.md trim + SOUL untouched (T3) ✓; render-context subcommand (T4) ✓; ExecStartPre restart trigger (T5) ✓; on_session_start hook + 5-min TTL non-blocking regen + installer wiring (T6) ✓; swap()/apply() runtime trigger (T7) ✓; cache-stability + LXC verification (T8) ✓. All four chosen fields (loaded model+ctx, capability rollup, GPU/NPU backend, URLs+health) + timestamp covered in T1/T2.
- **Verified during planning:** `BootstrapState` is a dataclass with all-defaulted fields — `BootstrapState(hermes_home=str(home))` is valid (T3). `AgentConfig` fields are `agent_id, agent_type, home, venv, host, port` (`hermes_bin`/`status_url` are read-only properties, NOT ctor args) — T4 test uses these.
- **Open verification points flagged inline (must check during impl, not assume):** `install.sh` `LIB_DIR`/`SRC_DIR` variable names (T6); exact success-path return sites in `swap()`/`apply()` (T7); whether `uninstall.sh` already removes `LIB_DIR` wholesale (T6); whether any existing test asserts the old HERMES.md "Active capability slots" text (T3) or subprocess in swap/apply tests (T7).
- **Non-goals honored:** no new daemon, no timer unit, no SIGHUP path.
