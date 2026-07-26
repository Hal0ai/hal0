# Editable Install Build Identity and Read-Only Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `--dev` (editable PEP 610) installs track the same preview base version as `pyproject.toml` while reporting install mode, git commit, branch, dirty state, and editable path separately. Read-only preview checks remain allowed; mutation, rollback, and downgrade continue to hard-refuse.

**Architecture:** Add one stdlib-only `InstallIdentity` helper under `src/hal0/release/build_identity.py` that resolves install mode and git provenance. Extend API/CLI/UI surfaces to expose it without polluting `__version__`. Wire the helper into the read-only preview update path.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, importlib.metadata, PEP 610, Rich, React 18 (existing).

## Global Constraints

- Design source: `docs/superpowers/specs/2026-07-20-official-prerelease-release-design.md` (`c8bd3999`).
- `hal0.__version__` continues to be the literal string from `importlib.metadata`; no `git describe` or local-version segments.
- Build identity must be reported through `hal0 system-info`, `/api/health`, `/api/status`, `/api/updates/state`, support bundles, and dashboard About/Updates views.
- Editable installs may run `hal0 update --channel preview --check`; they hard-refuse every mutating operation including `--allow-downgrade`.

---

## File Structure

**Create:**

- `src/hal0/release/build_identity.py`
- `tests/release/test_build_identity.py`

**Modify:**

- `src/hal0/api/routes/health.py`
- `src/hal0/cli/system_info_command.py`
- `src/hal0/cli/doctor_bundle.py`
- `src/hal0/updater/updater.py` — read-only preview path only.
- `tests/updater/test_updater.py` — editable check still hard-refuses mutating ops.
- `ui/src/dash/settings/pages/diagnostics/UpdatesPage.jsx` — surface preview deviation in editable mode.

---

### Task 1: BuildIdentity helper

**Files:**

- Create: `src/hal0/release/build_identity.py`
- Create: `tests/release/test_build_identity.py`

**Interfaces:**

- `InstallIdentity.from_distribution(channel: str) -> InstallIdentity`
- Fields: `mode`, `version`, `editable_path | None`, `git_commit | None`, `git_branch | None`, `git_dirty: bool`.

- [ ] **Step 1: Write failing tests**

Use real fixtures built with `pip install -e .` and `pip install .` inside `tmp_path` envs.

```python
def test_editable_mode_reports_git_identity(tmp_path: Path, monkeypatch):
    seed_editable_install(tmp_path, version="1.0.0-alpha.1")
    identity = InstallIdentity.from_distribution(channel="preview")
    assert identity.mode == "editable"
    assert identity.version == "1.0.0-alpha.1"
    assert identity.git_commit is not None
```

Cover installed wheel (`mode == "release"`), editable (`mode == "editable"`), source-only (`mode == "source"`), git-fhs (`mode == "git-fhs"`), and a checkout whose `pyproject.toml` is ahead of the released preview.

- [ ] **Step 2: Implement the helper**

The helper calls `importlib.metadata.distribution("hal0ai")` and:

- reads `direct_url.json` to detect editable/git-fhs/source;
- reads `RECORD` and existing dist-info to detect wheel/FHS;
- uses `subprocess.run(["git", "-C", "editable_path", "rev-parse", ...])` only when an editable root is known, and captures `--porcelain` for dirty state.

Standard library only. The helper must not throw when git is unavailable; missing git state becomes `None`/`False`. It must not require any package import beyond `importlib.metadata`, `subprocess`, and `pathlib`.

- [ ] **Step 3: Run tests and confirm GREEN**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/release/test_build_identity.py -q
uv run ruff check src/hal0/release/build_identity.py tests/release/test_build_identity.py
uv run mypy src/hal0/release/build_identity.py
```

- [ ] **Step 4: Commit**

```bash
git add src/hal0/release/build_identity.py tests/release/test_build_identity.py
git commit -m "feat(build-identity): report editable, git, and source provenance"
```

---

### Task 2: Expose build identity via CLI and API

**Files:**

- Modify: `src/hal0/cli/system_info_command.py`
- Modify: `src/hal0/api/routes/health.py`
- Modify: `src/hal0/api/routes/updater.py` (state payload)
- Modify: `src/hal0/cli/doctor_bundle.py`
- Modify: tests

- [ ] **Step 1: Failing tests**

Add `system-info` JSON test asserting the `install` block includes mode/version/git fields. Add API tests for `/api/health`, `/api/status`, and `/api/updates/state` payloads exposing `install_mode`, `git_commit`, `git_branch`, `git_dirty`. Assert support bundle includes the same block under `version/build` keys.

- [ ] **Step 2: Wire the helper into each surface**

Add a single `BuildIdentity.snapshot()` helper in `system_info_command.build_system_info()` and reuse it across the API. Ensure the public `hal0 --version` keeps printing only the base version.

- [ ] **Step 3: Verify**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/api/test_health.py tests/cli/test_system_info_command.py \
  tests/cli/test_doctor_bundle.py -q
uv run ruff check src/hal0/
```

- [ ] **Step 4: Commit**

```bash
git add src/hal0/cli/system_info_command.py src/hal0/api/routes/health.py \
  src/hal0/api/routes/updater.py src/hal0/cli/doctor_bundle.py \
  tests/api/test_health.py tests/cli/test_system_info_command.py \
  tests/cli/test_doctor_bundle.py
git commit -m "feat(install-identity): expose provenance in system-info, health, and bundles"
```

---

### Task 3: Editable read-only preview check

**Files:**

- Modify: `src/hal0/updater/updater.py:1399-1462`
- Modify: `tests/updater/test_updater.py`

- [ ] **Step 1: Failing tests**

```python
def test_check_reports_preview_deviation_for_editable(monkeypatch, synthetic_preview_manifest):
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: True)
    result = asyncio.run(Updater(channel="preview").check())
    assert result["deviation"] in {"behind", "equal", "ahead"}
```

Cover rejection of `Updater.apply/commit/rollback` and `Updater.downgrade_to` for editable installs while keeping `Updater.check()` allowed.

- [ ] **Step 2: Run tests and confirm RED**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/updater/test_updater.py -k 'editable or check' -q
```

Expected: apply/commit/rollback still refuse (good), but check returns no deviation field yet.

- [ ] **Step 3: Implement deviation**

`Updater.check()` returns:

```python
{
    "current": "1.0.0-alpha.1",
    "available": "1.0.0-beta.2",
    "channel": "preview",
    "install_mode": "editable",
    "git_commit": "e21d03d8",
    "git_branch": "rework",
    "git_dirty": False,
    "deviation": "behind",
    "next_steps": [
        "git fetch --tags",
        "git checkout v1.0.0-beta.2",
        "pip install -e .",
        "npm --prefix ui run build",
    ],
}
```

Preserve `_raise_if_editable_install()` for apply/commit/rollback. Hard-refuse `downgrade_to` on editable installs with the same safe-update next steps.

- [ ] **Step 4: Verify**

```bash
HAL0_HOME=$(mktemp -d) uv run pytest tests/updater/test_updater.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/hal0/updater/updater.py tests/updater/test_updater.py
git commit -m "feat(updater): preview deviation and safe-edit next steps"
```

---

### Task 4: Dashboard preview surface

**Files:**

- Modify: `ui/src/dash/settings/pages/diagnostics/UpdatesPage.jsx`

- [ ] **Step 1: Surface preview deviation and editable mode**

Render `install_mode` next to the channel pill. When `install_mode === "editable"`, hide the apply button and show the safe-update instructions verbatim (Git fetch/checkout, `pip install -e .`, `npm --prefix ui run build`).

- [ ] **Step 2: Verify**

```bash
cd ui && npm exec eslint -- src/dash/settings/pages/diagnostics/UpdatesPage.jsx
cd ui && npm run typecheck
```

- [ ] **Step 3: Commit**

```bash
git add ui/src/dash/settings/pages/diagnostics/UpdatesPage.jsx
git commit -m "feat(ui): editable preview deviation on updates page"
```
