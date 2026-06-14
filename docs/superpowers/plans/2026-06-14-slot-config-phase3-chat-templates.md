# Slot Config Phase 3 (Chat Templates) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Make a slot's chat template configurable without hand-editing TOML — a model-level default plus a per-slot override, backed by a small template library, and actually applied by the container llama-server.

**Architecture:** Templates live in a single dir under the model store: `<store>/chat-templates/<id>.jinja`. The store is mounted **identical-path, read-only** into every slot container (`container.py:380`), so the container reads templates at the same path the host writes them — no path translation. Bundled templates ship in the package and seed that dir on startup (absent-only, like SEED_PROFILES). `chat_template` is a new field on `ModelDefaults` (model recipe) and `SlotConfig` (slot override); `resolve_chat_template(slot_cfg, model_info)` picks the effective id (slot > model > none=auto). The container provider appends `--chat-template-file <store>/chat-templates/<id>.jinja` when an id is set.

**Tech Stack:** Python (pydantic, pytest), FastAPI, React (.jsx), Playwright e2e.

**Base branch:** `afk/slot-config-phase3-templates` (stacked on Phase 2; retarget PR to `main` now that #800 is merged). Spec: `docs/superpowers/specs/2026-06-14-slot-config-grouping-mtp-templates-design.md`.

**⚠️ CT105 validation:** the in-container template *load* (llama-server actually honoring `--chat-template-file`) cannot be GPU/container-tested on this VM. Unit + e2e tests here validate the wiring; the PR must be flagged for CT105 validation of real loading.

**Verify:** backend → `PYTHONPATH=/home/halo/dev/hal0-tmpl/src /home/halo/dev/hal0/.venv/bin/python -m pytest <file> -v` (run only the specific file; the full suite hangs). UI → `npm run typecheck` (0), `npm run build` (0), `npx playwright test <spec> --project=chromium`. Lint gate (CI-fatal): `ruff check src tests` and `ruff format --check src tests` must pass — run `ruff check --fix` + `ruff format` before committing.

---

### Task 1: Config fields + resolver

**Files:** `src/hal0/registry/model.py` (`ModelDefaults.chat_template`), `src/hal0/config/schema.py` (`SlotConfig.chat_template` + `resolve_chat_template`), test `tests/config/test_chat_template_resolve.py`.

- [ ] **Step 1 — failing test:**
```python
from hal0.config.schema import resolve_chat_template

def test_slot_override_wins():
    assert resolve_chat_template({"chat_template": "qwen3"}, {"defaults": {"chat_template": "chatml"}}) == "qwen3"

def test_model_default_used_when_slot_absent():
    assert resolve_chat_template({}, {"defaults": {"chat_template": "chatml"}}) == "chatml"

def test_auto_returns_none():
    assert resolve_chat_template({"chat_template": "auto"}, {}) is None
    assert resolve_chat_template({}, {}) is None
```
- [ ] **Step 2 — run, confirm FAIL** (`resolve_chat_template` missing).
- [ ] **Step 3 — implement:**
  - `ModelDefaults` (registry/model.py): add `chat_template: str | None = Field(default=None, description="Chat template id from /api/chat-templates, or 'auto'/None for the GGUF-embedded template.")`.
  - `SlotConfig` (schema.py): add `chat_template: str | None = Field(default=None, description="Per-slot chat template override (id or 'auto'). Wins over the model's default. See resolve_chat_template.")`.
  - Add to schema.py:
```python
def resolve_chat_template(slot_cfg: dict, model_info: dict) -> str | None:
    """Effective chat-template id: slot override > model default > None (auto).

    'auto' (or empty/None) means use the GGUF-embedded template (no
    --chat-template-file). Returns the template id otherwise.
    """
    for val in (slot_cfg.get("chat_template"),
                (model_info.get("defaults") or {}).get("chat_template")):
        if val and val != "auto":
            return str(val)
    return None
```
- [ ] **Step 4 — run, confirm 4 passed.**
- [ ] **Step 5 — ruff fix+format, commit** `feat(slots): chat_template config fields + resolver`.

---

### Task 2: Template library + catalog endpoint

**Files:** new `src/hal0/templates/chat/chatml.jinja` (+ `qwen3.jinja`), new `src/hal0/templates/__init__.py` (seeding helper), `src/hal0/api/routes/` new `chat_templates.py` route mounted under `/api/chat-templates`, test `tests/api/test_chat_templates.py`.

The store dir is `<model_store_root()>/chat-templates/`. Seeding copies bundled `*.jinja` there if absent (skip silently if the store is read-only). Catalog = listing of `<store>/chat-templates/*.jinja` (id = filename stem) + the synthetic `auto` entry. Custom paste = `POST /api/chat-templates {id, content}` → write `<store>/chat-templates/<id>.jinja`.

- [ ] **Step 1 — failing test** (`tests/api/test_chat_templates.py`): use a `tmp_path` store via `monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))`. Assert `GET /api/chat-templates` returns the seeded bundled ids + `auto`; assert `POST` writes a custom file and it then appears in the listing. (Use the app factory + TestClient as other `tests/api/` tests do — inspect one for the fixture pattern.)
- [ ] **Step 2 — run, confirm FAIL.**
- [ ] **Step 3 — implement:**
  - Ship `chatml.jinja` (standard ChatML) and `qwen3.jinja` under `src/hal0/templates/chat/`. (Pull canonical content; chatml = `<|im_start|>{role}\n{content}<|im_end|>` loop + generation prompt.)
  - `seed_chat_templates()` in `src/hal0/templates/__init__.py`: `dst = model_store_root()/"chat-templates"`; `mkdir(parents=True, exist_ok=True)` guarded by try/except (read-only store → log+return); copy each bundled `*.jinja` if not present.
  - Call `seed_chat_templates()` from app startup (where SEED_PROFILES / store init happens — grep for the startup seed and mirror it).
  - Route `chat_templates.py`: `GET` → `[{"id":"auto","label":"Auto (GGUF embedded)"}] + [{"id": p.stem, "label": p.stem} for p in sorted((store/"chat-templates").glob("*.jinja"))]`; `POST {id, content}` → validate id matches `^[a-z0-9][a-z0-9_-]{0,40}$`, write `<store>/chat-templates/<id>.jinja`, return the new entry. Mount it in the API app (grep where other routers are `include_router`'d).
- [ ] **Step 4 — run, confirm pass.**
- [ ] **Step 5 — ruff fix+format, commit** `feat(api): chat-template catalog endpoint + bundled library + store seeding`.

---

### Task 3: Container plumbing — emit `--chat-template-file`

**Files:** `src/hal0/providers/container.py` (`_llama_launch_plan` + `container_spec`), test `tests/providers/test_container_chat_template.py`.

- [ ] **Step 1 — failing test:** build a slot_cfg+model_info with a resolved template id and assert the rendered command/plan contains `--chat-template-file <store>/chat-templates/<id>.jinja`; assert it's ABSENT when the effective id is None/auto. (Inspect `container_spec`/`_llama_launch_plan` test patterns in existing `tests/providers/` for how plans are built and asserted.)
- [ ] **Step 2 — run, confirm FAIL.**
- [ ] **Step 3 — implement:**
  - Add `chat_template_path: str | None = None` to `_llama_launch_plan(...)`; when set, append `["--chat-template-file", chat_template_path]` to `command` (after model/ctx, with flags — placement isn't critical, but keep it before `extra_tokens` so a hand override in extra_args still wins).
  - In `container_spec`, after resolving model/profile: `tmpl_id = resolve_chat_template(slot_cfg, model_info)`; if `tmpl_id`: `tmpl_path = str(model_store_root() / "chat-templates" / f"{tmpl_id}.jinja")` and pass it to the plan builder; else pass None. (Import `resolve_chat_template`, `model_store_root`.)
- [ ] **Step 4 — run, confirm pass.**
- [ ] **Step 5 — ruff fix+format, commit** `feat(slots): container emits --chat-template-file from resolved chat_template`.

---

### Task 4: UI — model recipe "Chat template" field

**Files:** `ui/src/dash/model-modals.jsx` (recipe editor `onSave`/form), a `useChatTemplates` hook (`ui/src/api/hooks/`) + endpoint const, test `ui/tests/e2e/specs/` new `model-recipe-template-v3.spec.ts`.

The recipe editor (model update) writes `{ defaults: {...} }` via `useModelUpdate` (`PUT /api/models/{id}`). Add a "Chat template" `<select>` (Auto + catalog ids) that writes `defaults.chat_template`. Source options from `GET /api/chat-templates`.

- [ ] **Step 1 — failing e2e** (mock `GET /api/chat-templates` via `page.route` — NOT allowlisted, so route works; open recipe editor, select a template, Save, assert `PUT /api/models/{id}` body `defaults.chat_template` === selected). Confirm allowlist status by checking `src/api/mock.ts` first; if `/api/chat-templates` is forced-mock, seed via `HAL0_DATA` instead.
- [ ] **Step 2 — run, confirm FAIL.**
- [ ] **Step 3 — implement** the `useChatTemplates` hook (`useQuery(['chat-templates'], GET ENDPOINTS.chatTemplates)`), add `ENDPOINTS.chatTemplates = '/api/chat-templates'`, and the select in the recipe form seeding from `model.defaults?.chat_template ?? 'auto'`, writing it into the `defaults` object in `onSave`.
- [ ] **Step 4 — pass + typecheck 0 + build 0.**
- [ ] **Step 5 — commit** `feat(models): chat-template field in the recipe editor`.

---

### Task 5: UI — slot Template-row `[Override]` picker

**Files:** `ui/src/dash/slot-modals.jsx` (the Model group's Template row from Phase 1 shows the model template read-only — add `[Override]`), test in `slot-drawer-profile-v3.spec.ts`.

- [ ] **Step 1 — failing e2e:** seed a slot + `GET /api/chat-templates`; the drawer Template row shows the model's template read-only with an `[Override]` control; clicking it reveals a `<select>`; choosing a template + Save sends `PUT /config` with `chat_template`. (`mtp`-style: the value seeds from `slot.chat_template`, surfaced via slot_view — add `entry["chat_template"] = cfg.get("chat_template")` in `slot_view/__init__.py` alongside `mtp`, with a slot_view test, same as Phase 2.)
- [ ] **Step 2 — run, confirm FAIL.**
- [ ] **Step 3 — implement** the Template row override: a toggle that swaps the read-only display for a `<select>` (Auto + catalog), and include `chat_template` in `onSaveClick`'s `slotBody` only when overridden/changed (dirty-track to avoid clobber, like profile). Surface `chat_template` in `slot_view` + add the slot_view assertion.
- [ ] **Step 4 — pass (slot specs + slot_view pytest) + typecheck 0 + build 0.**
- [ ] **Step 5 — ruff (for the slot_view py change) + commit** `feat(slots): per-slot chat-template override in the edit drawer`.

---

## Self-Review

**Spec coverage (Phase 3):** model-level field → Task 1/4; slot override → Task 1/5; template library + catalog → Task 2; container application → Task 3; "Auto = GGUF-embedded" → resolver returns None → no flag (Tasks 1/3). ✓

**Placeholder scan:** the bundled jinja *content* (Task 2 Step 3) and the existing test-fixture/router-mount patterns are delegated with explicit "inspect an existing X and mirror it" instructions — acceptable since they're locate-and-match against real code, not invented APIs.

**Type/name consistency:** `resolve_chat_template(slot_cfg, model_info)` defined Task 1, consumed Task 3; `chat_template` field name consistent across `ModelDefaults`/`SlotConfig`/slot_view/UI; `<store>/chat-templates/<id>.jinja` path identical in Tasks 2 and 3 (and matches the identical-path store mount).

**CT105 gap:** real in-container load is unverifiable here — flagged in the header and to be flagged on the PR.
