# Runner-Image Catalogue v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the runner-images page reliably surface freshly pushed images (tag tracking), add per-family operator default overrides, move the page under Slots (Profiles under Models), and harden stacks state/config permissions.

**Architecture:** Four independently mergeable PRs: (A) `images.json` completeness in hal0-runner-images; (B) stacks permission hardening + typed error; (C) backend — sync tag-tracking, row enrichment, `[slots.default_images]` override map wired into `_resolve_image_ref`; (D) UI — IA moves + Defaults strip + tag picker + set-default flow, built against C's contract.

**Tech Stack:** Python 3.12 / FastAPI / pydantic (backend); React 18 mixed .jsx/.ts, Vite, vitest, TanStack Query (ui/); bash wrapper (installer); GHCR v2 registry API (anonymous token).

**Spec:** `docs/superpowers/specs/2026-08-24-runner-image-catalogue-v2-design.md` (committed on `feat/runner-catalogue-v2`).

## Global Constraints

- All hal0 work happens on thinmint (`ssh thinmint`, BatchMode) in per-task worktrees: `git -C /mnt/mintdev/repos/hal0 worktree add /mnt/mintdev/worktrees/hal0/<slug> -b <branch> github/main`. Venv per worktree: `uv sync --all-extras && uv pip install -e .`.
- Edit workflow from the Mac: `scp` file local → Edit tool → `scp` back; or heredoc via ssh for new files. Never edit over NFS paths.
- Tests: `.venv/bin/pytest <targets> -q`; lint `.venv/bin/ruff check` + `ruff format --check` on touched files; mypy must add zero NEW errors vs main. `tests/mcp/` fails locally on thinmint (root-owned /etc/hal0/hal0.toml) — environmental, ignore.
- UI: `cd ui && npm run typecheck && npx vitest run <targets>`; γ-suite runs in CI only.
- Conventional Commits; commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; squash-merge; PR bodies explain the why and cite the spec path.
- TDD red-first for every behavior change; watch each test fail before implementing.
- Live reference box ct105 (`root@10.0.1.142`, rc7+hotpatch) exists but tasks in this plan MUST NOT touch it.

## Cross-task contract (C produces, D consumes — frozen now)

`GET /api/runner-images` / `POST /api/runner-images/sync` rows gain:

```json
{
  "available_tags": ["0824", "0822"],
  "is_default": {"family": "rocmfpx", "source": "override"},
  "in_use_by": ["agent", "utility"]
}
```

- `available_tags`: newest-first (sort: date-shaped numeric desc → semver desc → registry order); always present, may be `[]` on probe failure; headline `tag` unchanged semantics.
- `is_default`: `null` when no family default resolves to this row's `image` (any tag); `source` is `"override"` (from `[slots.default_images]`) or `"release"` (baked constant).
- `in_use_by`: slot names whose *rendered unit* (`/etc/containers/systemd` snapshot via existing provider introspection, or resolved config on non-installed dev boxes) references `image:tag`; `[]` when unknown.

Settings knob (C): `hal0.toml` `[slots] default_images = { rocmfpx = "ghcr.io/hal0ai/hal0-combined:0824" }`. Family keys = the keys of `hal0.runners.RUNNER_IMAGES` (verify exact constant; ~`rocmfpx`, `cpu`, `vulkan`, `flm`, `kokoro`, `moonshine`, `qwen3tts`, `comfyui`). Write/clear via existing `PUT /api/settings` deep-merge: `{"slots": {"default_images": {"rocmfpx": "<ref>"}}}`; clearing = value `null` (settings route must treat explicit null as key removal — verify existing deep-merge null semantics and follow them; if deep-merge has no delete idiom, add `""` = clear and document it).

---

### Task A: images.json completeness (repo Hal0ai/hal0-runner-images)

**Files:**
- Modify: `images.json` (repo root, thinmint checkout `/mnt/mintdev/repos/hal0-runner-images`)

**Interfaces:**
- Produces: an `images` array entry with `id: "rocmfpx-combined"` and `image: "ghcr.io/hal0ai/hal0-combined"` that hal0's `sync_runner_images` will pick up unchanged (row id = `id`, probe repo = `image` sans host).

- [ ] **Step 1: branch** `git -C /mnt/mintdev/repos/hal0-runner-images fetch origin && git checkout -b feat/combined-catalogue origin/main`.
- [ ] **Step 2: edit `images.json`**: add to `images` (mirror neighboring entries' shape):

```json
{
  "id": "rocmfpx-combined",
  "image": "ghcr.io/hal0ai/hal0-combined",
  "ownership": "owned",
  "publish": "external",
  "notes": "Canonical ROCmFPX+Vulkan combined runner (llama-server). Built from Hal0ai/hal0 packaging/runner/rocmfpx/build.sh; tag pinned app-side (DEFAULT_ROCMFPX_IMAGE). No pinned tag here on purpose: the hal0 catalogue tracks tags/list so fresh pushes surface on sync."
}
```

  Deliberately NO `"tag"` key (tag-tracking headline = newest) and NO `"manifest_key"` (stays out of manifest.json toolbox_images — the app-side pin remains authoritative, per the file's own `_comment`).
- [ ] **Step 3: retire the stale row**: find the existing entry with `"image": "ghcr.io/hal0ai/hal0-rocmfpx"` whose id is `rocmfpx` (currently implying tag `ade07ba`, the #1888-broken runner) and DELETE it. Keep `rocmfpx-hy3` (deliberate special runner). Do not touch other entries.
- [ ] **Step 4: validate**: `python3 -c "import json; d=json.load(open('images.json')); assert isinstance(d['images'], list); print(len(d['images']))"`; if the repo has CI/schema checks (`ls .github/workflows`), run whatever validation exists locally.
- [ ] **Step 5: commit** `fix(images): catalogue hal0-combined, retire stale rocmfpx ade07ba row` (body: why — hal0 catalogue could never show the flagship runner; ade07ba row advertised the #1888-broken image). Push, open PR to Hal0ai/hal0-runner-images with `gh`.

### Task B: stacks permission hardening + typed unreadable error (repo hal0)

**Files:**
- Modify: `src/hal0/stacks/state.py` (`write_stack_state_atomic`, `read_stack_state`)
- Modify: `src/hal0/stacks/__init__.py` and/or `src/hal0/config/loader.py:_read_toml` call-site in `load_stacks_config` (PermissionError mapping)
- Test: `tests/stacks/test_state_permissions.py` (new; put beside existing stacks tests — `ls tests/stacks` first and follow its naming)

**Interfaces:**
- Produces: `StacksStateUnreadable` (subclass of the stacks error family or `Hal0Error` directly) with `code="stacks.state_unreadable"`, `status=500`, message naming the unreadable path; raised instead of raw `PermissionError` from both the state.json read and the stacks.toml load path.

- [ ] **Step 1: red tests** (all three, watch them fail):

```python
import os, stat
from pathlib import Path

import pytest

from hal0.stacks.state import StackStateRecord, read_stack_state, write_stack_state_atomic


def test_atomic_write_leaves_group_readable_state(tmp_path):
    """A root-run CLI wrote 0600 state.json (mkstemp default) and the hal0-user
    API 500'd on every /api/stacks since (ct105, 2026-07-12→08-24). The atomic
    writer must chmod the temp file before replace."""
    path = tmp_path / "state.json"
    write_stack_state_atomic(path, StackStateRecord(active_slug="s", applied_at=1.0,
                                                    content_hash="x", slots={}))
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode & 0o060 == 0o060, f"group rw missing: {oct(mode)}"


def test_read_state_permission_error_is_typed(tmp_path):
    from hal0.stacks.state import StacksStateUnreadable
    path = tmp_path / "state.json"
    path.write_text("{}")
    path.chmod(0o000)
    if os.geteuid() == 0:
        pytest.skip("root reads through 0o000")
    with pytest.raises(StacksStateUnreadable) as exc:
        read_stack_state(path)
    assert "state.json" in str(exc.value)


def test_load_stacks_config_permission_error_is_typed(tmp_path):
    from hal0.config.loader import load_stacks_config
    from hal0.stacks.state import StacksStateUnreadable
    p = tmp_path / "stacks.toml"
    p.write_text("")
    p.chmod(0o000)
    if os.geteuid() == 0:
        pytest.skip("root reads through 0o000")
    with pytest.raises(StacksStateUnreadable) as exc:
        load_stacks_config(path=p)
    assert "stacks.toml" in str(exc.value)
```

  Adjust `StackStateRecord` construction to its real signature (read the dataclass first); adjust `load_stacks_config(path=...)` kwarg to its real name (loader.py:898-911).
- [ ] **Step 2: implement**: in `write_stack_state_atomic`, after `os.fsync` and before `os.replace`: `os.fchmod(fd, 0o664)` (do it while fd open; note in comment: mkstemp is 0600 by design, this file is service-shared state under a setgid `hal0` dir — the ct105 outage cite). In `read_stack_state`, add `except PermissionError as exc: raise StacksStateUnreadable(...)`. Define `StacksStateUnreadable` following the existing stacks/slot error family pattern (grep `class SlotError` / stacks error classes; `code="stacks.state_unreadable"`). In `load_stacks_config`'s `_read_toml` call path, catch `PermissionError` and raise the same typed error naming the toml path (message must include the path and the fix hint `chgrp hal0 && chmod 640`).
- [ ] **Step 3: green** targeted tests, then `tests/stacks tests/config -q`, ruff on touched files.
- [ ] **Step 4: commit + PR** `fix(stacks): root-written state must stay service-readable; type the unreadable error` — body cites the live ct105 incident (state.json 0600 root since Jul 12, /api/stacks 500 `system.internal`), the spec §4, and the hot-fix already applied to the box.

### Task C: backend — tag tracking, enrichment, default_images override (repo hal0)

**Files:**
- Modify: `src/hal0/registry/runner_image_sync.py` (tag list + sort + `available_tags`)
- Modify: `src/hal0/registry/runner_image.py` (`RunnerImage` model: `available_tags: list[str] = []`; enrichment fields are response-side, not stored, unless the model already carries derived fields — inspect and follow)
- Modify: `src/hal0/api/routes/runner_images.py` (list/sync responses gain `is_default`/`in_use_by` enrichment)
- Modify: `src/hal0/config/schema.py` (`SlotsConfig.default_images: dict[str, str] = {}` with validator: keys must be known family keys, values non-empty image refs)
- Modify: `src/hal0/providers/container.py::_resolve_image_ref` (override tier between profile image and baked default)
- Test: `tests/registry/test_runner_image_sync.py` (extend), `tests/providers/test_image_resolution.py` (extend), `tests/config/test_schema.py` (extend), new `tests/registry/test_tag_sort.py`
- **Read first:** `src/hal0/runners.py` (or wherever `RUNNER_IMAGES` lives) for the family keys; `_resolve_image_ref` docstring tiers; how routes access config (`request.app.state`…).

**Interfaces:**
- Consumes: nothing from other tasks (A's images.json entry improves data but code must work with or without it).
- Produces: the frozen cross-task contract above, plus `hal0.registry.runner_image_sync.sort_tags_newest_first(tags: list[str]) -> list[str]` (pure, exported for tests) and `SlotsConfig.default_images`.

- [ ] **Step 1: red — tag sort** (`tests/registry/test_tag_sort.py`):

```python
from hal0.registry.runner_image_sync import sort_tags_newest_first

def test_date_shaped_numerics_beat_everything_and_sort_desc():
    assert sort_tags_newest_first(["0822", "latest", "0824", "v1"])[:2] == ["0824", "0822"]

def test_semver_sorts_desc_after_numerics():
    out = sort_tags_newest_first(["v1.2.0", "v1.10.0", "latest"])
    assert out[:2] == ["v1.10.0", "v1.2.0"]

def test_registry_order_is_last_resort_and_stable():
    assert sort_tags_newest_first(["alpha", "beta"]) == ["alpha", "beta"]

def test_empty_ok():
    assert sort_tags_newest_first([]) == []
```

- [ ] **Step 2: implement `sort_tags_newest_first`** — three buckets: all-digit tags (int desc), semver-shaped (`v?\d+(\.\d+)+`, tuple desc), rest (original order); buckets concatenate numeric→semver→rest. Wire into `probe_ghcr_package`: always call `_ghcr_list_tags`, set `available_tags=sort_tags_newest_first(tags)`; unpinned headline tag = first of the sorted list, falling back to `latest`; pinned entries keep pin as headline but still store `available_tags`. Extend existing sync tests: pinned entry keeps `tag` but gains `available_tags`; `tags/list` HTTP failure degrades to `available_tags=[]` without failing the row (extend the existing respx/mock harness in `tests/registry/test_runner_image_sync.py` — read how it mocks GHCR and follow that pattern exactly).
- [ ] **Step 3: red — SlotsConfig.default_images**: valid map accepted; unknown family key rejected with the schema's error idiom; empty-string value rejected. Then implement field + validator (family keys from `RUNNER_IMAGES`).
- [ ] **Step 4: red — resolution override** (`tests/providers/test_image_resolution.py`): with no pin/profile-image and `default_images={"rocmfpx": "ghcr.io/x/y:z"}`, `_resolve_image_ref` returns the override; with a slot `image_pin` set, pin still wins; with the key absent, baked default unchanged (existing tests must stay green). Implement: read override map from the loaded config at the point `_resolve_image_ref` already receives config context (inspect its signature — thread the map the same way its existing tiers get their data, do NOT add global state).
- [ ] **Step 5: red — route enrichment**: API test (follow `tests/api/` runner-images route tests if present, else the store-level pattern): a store row whose `image:tag` equals the effective default for family `rocmfpx` reports `is_default={"family":"rocmfpx","source":"release"}`; with an override set, `source=="override"`; a row referenced by a slot's resolved config lists that slot in `in_use_by`. Implement enrichment in `runner_images.py` route helpers (pure function `enrich_row(image, *, defaults, slot_usage)` so it unit-tests without the app).
- [ ] **Step 6: full suites** `tests/registry tests/providers tests/config tests/api -q`, ruff, mypy-delta.
- [ ] **Step 7: commit + PR** `feat(runner-images): tag-tracked catalogue, default_images override, honest row enrichment` — body: spec path, the three root causes, contract block verbatim.

### Task D: UI — IA moves + Defaults strip + tag picker + set-default (repo hal0)

**Files:**
- Modify: `ui/src/dash/chrome.jsx` (nav: `slots/runner-images` sub-link under iGPU Slots; move `Profiles` sub-link under Models → `models/profiles`; line ~412 title map + ~495 sub-link registry)
- Modify: `ui/src/dash/command-palette.jsx` (BOTH nav sections — the known stale duplicate at ~281-292 AND the primary list)
- Modify: `ui/src/dash/models.jsx` (drop the `runner-images` tab; add `profiles` tab hosting the existing profiles view; keep tab state idiom)
- Modify: `ui/src/dash/slots.jsx` / wherever `slots/profiles` route renders today (route `slots/runner-images` → `RunnerImagesView`; `models/profiles` → profiles view; follow the existing sub-route dispatch — read how `slots/profiles` resolves first)
- Modify: `ui/src/dash/runner-images.jsx` (Defaults strip, tag picker, per-tag Pull + Set-as-default, newer-tag chip)
- Modify: `ui/src/api/hooks/useRunnerImages.ts` (row type gains `available_tags`, `is_default`, `in_use_by`; `useSetDefaultImage` mutation → `PUT /api/settings` body `{"slots":{"default_images":{[family]: ref}}}`, clear = null per Task C's verified semantics)
- Test: `ui/src/dash/__tests__/runner-images-view.test.tsx` (new), extend nav tests if any exist (`grep -rl "chrome" ui/src/dash/__tests__`)

**Interfaces:**
- Consumes: Task C's frozen contract (build against it even before C merges — mock rows in tests carry the new fields).
- Produces: nav ids `slots/runner-images`, `models/profiles` (γ-suite and palette reference nav ids — grep `tests/e2e` for `slots/profiles` and update those assertions in the same PR).

- [ ] **Step 1: red — pure pieces first** (vitest): `defaultsStripRows(images, families)` pure helper → rows `{family, ref, source}` from enriched images; `newerTagAvailable(row)` → true when `available_tags[0]` differs from headline `tag`. Write tests with contract-shaped fixtures, watch fail, implement in `runner-images.jsx` (export the helpers).
- [ ] **Step 2: IA moves**: nav registries (all THREE lists: chrome.jsx two spots, palette two spots), route dispatch, models.jsx tab swap. Grep the whole `ui/src` + `tests/e2e` for `slots/profiles` and `runner-images` to catch every reference; γ-suite specs asserting the old locations must be updated in this PR.
- [ ] **Step 3: page features**: Defaults strip at top (family → `effective ref` + `release default`/`override` badge + clear button, confirm dialog); per-row tag `<select>` (headline default) + Pull (existing pull mutation takes image id — pulling a non-headline tag needs the pull route's tag handling: read `runner_pull_jobs` first; if pulls are id-keyed to the headline tag only, scope per-tag pull to headline + the newest tag via row refresh after set-default, and say so in the PR body rather than silently faking it); Set-as-default button per row/tag with confirm dialog naming `in_use_by` slots that will drift.
- [ ] **Step 4: typecheck + vitest full + eslint** on touched files.
- [ ] **Step 5: commit + PR** `feat(ui): runner images under Slots, profiles under Models, defaults strip + tag picker` — body: spec path, nav-id changes called out for reviewers, contract dependency on Task C PR noted (“merge after backend PR #…”).

### Merge order & wave mechanics

- A, B, C run fully parallel; D develops parallel but its PR merges after C (API fields must exist for γ-suite against real backend; unit tests are mock-based and green regardless).
- Each PR: independent reviewer pass (cavecrew-reviewer), findings applied, CI green (8 checks; `python (3.12)` ~25 min, γ ~12 min), squash-merge, delete branch, remove worktree.
- After the last hal0 merge: run one full local suite on merged main in a scratch worktree; update the spec's status line; sync handoff notes to `/mnt/mintdev/artifacts/`.
