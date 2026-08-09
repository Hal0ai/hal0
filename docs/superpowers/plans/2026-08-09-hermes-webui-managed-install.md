# Hermes WebUI (hermex) Managed Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** hal0 provisions, manages, and surfaces the third-party Hermes WebUI (github.com/nesquena/hermes-webui, "hermex") as a first-class companion service installed alongside the Hermes agent, with dashboard sidebar links and deeper integration links on the Hermes agent card's flip side.

**Architecture:** A new convergent provisioning module `src/hal0/agents/hermes_webui.py` (git-pinned tree + `.env` seed + venv deps) plugs into the existing `_INSTALL_STEPS` pipeline in `hermes_provision.py`. The service registers across every existing surface: `ServiceDef` registry, systemd privilege seam + bash wrapper, `/api/services/health`, health-report/diagnosis taxonomy, doctor, and `/api/config/urls`. The UI consumes the new `hermes_webui`/`hermes_webui_enabled` URL keys in the sidebar `ServiceLinks` and threads them into the Hermes flip-card back as link pills.

**Tech Stack:** Python 3.12 / FastAPI / pydantic / typer / pytest; React 18 + Vite + TanStack Query (`ui/`, plain CSS, window-globals bridge for `dash/*.jsx`); systemd; git.

## Global Constraints

- Upstream pin: repo `https://github.com/nesquena/hermes-webui.git`, vetted SHA `deac1384fed96c07134130b5c8df45b431d0b8c3` (exactly what CT105 runs today — adoption of the existing hand install must be a byte-level no-op for the tree).
- Naming (fixed, do not vary): service id `hermes-webui` (hyphen), unit `hermes-webui.service`, seam key `hermes-webui`, config-URL keys `hermes_webui` / `hermes_webui_enabled` (underscore), env vars `HAL0_HERMES_WEBUI_PUBLIC_URL` / `HAL0_HERMES_WEBUI_PROBE_URL` / `HAL0_SKIP_HERMES_WEBUI`, diagnosis id `HAL0-HERMES-WEBUI-DOWN`, display name "Hermes WebUI". Never call it "WebUI" alone — that means Open WebUI in this codebase.
- Defaults: tree `/var/lib/hal0/hermes-webui`, bind `127.0.0.1:8787`, runs from the hermes venv `/var/lib/hal0/venvs/hermes`, user `hal0:hal0`, `.env` mode 0600.
- Operator-edit preservation: `.env` seeding only ADDS missing keys, never rewrites existing values (live box has `HERMES_WEBUI_HOST=0.0.0.0` + a password — both must survive re-provisioning). The tree converge refuses to move a dirty git tree.
- Every provisioning step must report `details["changed"]` truthfully and be a no-op on second run (enforced by `tests/agents/test_hermes_provision_idempotency.py`). Failures in this companion step are SKIP with a reason, never FAIL — webui must not break `hal0 agent install hermes`.
- Test invocation: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest <paths> -q` (HAL0_HOME override avoids /etc/hal0-perms pseudo-errors; `--extra dev` is required). UI: `cd ui && npm run typecheck && npm run lint`; e2e via `npx playwright test tests/e2e/specs/<spec>`.
- Conventional Commits, one logical change per commit. Never commit the generated password or any `.env` contents.
- Line numbers in this plan are anchors from exploration; match by symbol name if drifted.

---

### Task 1: `hermes_webui.py` module — constants + `.env` seeding

**Files:**
- Create: `src/hal0/agents/hermes_webui.py`
- Test: `tests/agents/test_hermes_webui.py`

**Interfaces:**
- Produces: `WEBUI_REPO_URL: str`, `WEBUI_PINNED_REF: str`, `VETTED_HERMES_WEBUI_REFS: frozenset[str]`, `WEBUI_TREE_DEFAULT: Path`, `WEBUI_VENV_DEFAULT: Path`, `WEBUI_DEFAULT_PORT = 8787`, `StepOutcome` dataclass (`ok: bool, changed: bool, detail: str`), `ensure_env(tree: Path, venv: Path) -> StepOutcome`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the Hermes WebUI (hermex) companion provisioning module."""
from pathlib import Path

import hal0.agents.hermes_webui as hw


def test_pinned_ref_is_vetted() -> None:
    assert hw.WEBUI_PINNED_REF in hw.VETTED_HERMES_WEBUI_REFS
    assert len(hw.WEBUI_PINNED_REF) == 40  # full SHA, not a short ref


def test_ensure_env_seeds_all_defaults_on_fresh_tree(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    tree.mkdir()
    venv = tmp_path / "venv"
    out = hw.ensure_env(tree, venv)
    assert out.ok and out.changed
    body = (tree / ".env").read_text(encoding="utf-8")
    assert "HERMES_WEBUI_HOST=127.0.0.1" in body
    assert "HERMES_WEBUI_PORT=8787" in body
    assert f"HERMES_WEBUI_PYTHON={venv}/bin/python3" in body
    # a password was generated and is non-trivial
    pw_line = next(l for l in body.splitlines() if l.startswith("HERMES_WEBUI_PASSWORD="))
    assert len(pw_line.split("=", 1)[1]) >= 24
    assert ((tree / ".env").stat().st_mode & 0o777) == 0o600


def test_ensure_env_preserves_operator_edits(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    tree.mkdir()
    existing = "# operator file\nHERMES_WEBUI_HOST=0.0.0.0\nHERMES_WEBUI_PASSWORD=operator-secret\n"
    (tree / ".env").write_text(existing, encoding="utf-8")
    out = hw.ensure_env(tree, tmp_path / "venv")
    body = (tree / ".env").read_text(encoding="utf-8")
    assert out.changed  # PORT + PYTHON were appended
    assert "HERMES_WEBUI_HOST=0.0.0.0" in body           # untouched
    assert "HERMES_WEBUI_PASSWORD=operator-secret" in body  # untouched
    assert body.startswith("# operator file")             # comments preserved
    assert "HERMES_WEBUI_PORT=8787" in body


def test_ensure_env_converges_second_run(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    tree.mkdir()
    hw.ensure_env(tree, tmp_path / "venv")
    second = hw.ensure_env(tree, tmp_path / "venv")
    assert second.ok and not second.changed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/agents/test_hermes_webui.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hal0.agents.hermes_webui'`

- [ ] **Step 3: Write the module with constants + `ensure_env`**

```python
"""Hermes WebUI (hermex) companion provisioning.

Third-party web app for the Hermes agent (github.com/nesquena/hermes-webui).
hal0 manages it as a pinned git tree under /var/lib/hal0/hermes-webui running
from the hermes venv, with a seeded-but-operator-owned ``.env``.

Deliberately import-free of :mod:`hal0.agents.hermes_provision` (which lazily
imports this module for its pipeline step) to avoid a cycle.
"""

from __future__ import annotations

import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

WEBUI_REPO_URL = "https://github.com/nesquena/hermes-webui.git"
# Vetting mirrors VETTED_HERMES_REFS: full commit SHAs that have been reviewed
# and run in production. deac1384… is the CT105 hand-install HEAD (2026-08-09),
# so adopting that box is a no-op.
WEBUI_PINNED_REF = "deac1384fed96c07134130b5c8df45b431d0b8c3"
VETTED_HERMES_WEBUI_REFS: frozenset[str] = frozenset({WEBUI_PINNED_REF})

WEBUI_TREE_DEFAULT = Path("/var/lib/hal0/hermes-webui")
WEBUI_VENV_DEFAULT = Path("/var/lib/hal0/venvs/hermes")
WEBUI_DEFAULT_PORT = 8787


@dataclass(frozen=True)
class StepOutcome:
    ok: bool
    changed: bool
    detail: str


def _env_defaults(venv: Path) -> dict[str, str]:
    return {
        "HERMES_WEBUI_HOST": "127.0.0.1",
        "HERMES_WEBUI_PORT": str(WEBUI_DEFAULT_PORT),
        "HERMES_WEBUI_PYTHON": str(venv / "bin" / "python3"),
    }


def ensure_env(tree: Path, venv: Path) -> StepOutcome:
    """Seed missing keys into ``<tree>/.env``; never rewrite existing values.

    The file is operator-owned after first write: re-provisioning only appends
    keys that are absent (the CT105 adoption case keeps its 0.0.0.0 bind and
    hand-set password). A password is generated only when the key is missing.
    """
    path = tree / ".env"
    existing = ""
    present: set[str] = set()
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        for line in existing.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                present.add(stripped.split("=", 1)[0].strip())

    wanted = _env_defaults(venv)
    missing = [(k, v) for k, v in wanted.items() if k not in present]
    if "HERMES_WEBUI_PASSWORD" not in present:
        missing.append(("HERMES_WEBUI_PASSWORD", secrets.token_urlsafe(24)))

    if not missing:
        # converge the mode even when content is current
        path.chmod(0o600)
        return StepOutcome(ok=True, changed=False, detail=".env current")

    body = existing
    if body and not body.endswith("\n"):
        body += "\n"
    if not body:
        body = "# hermes-webui server config (seeded by hal0; edits preserved)\n"
    body += "".join(f"{k}={v}\n" for k, v in missing)

    tmp = path.with_name(".env.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)
    return StepOutcome(
        ok=True, changed=True,
        detail=f"seeded {', '.join(k for k, _ in missing)}",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/agents/test_hermes_webui.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/hal0/agents/hermes_webui.py tests/agents/test_hermes_webui.py
git commit -m "feat(agents): hermes-webui provisioning module with .env seeding"
```

---

### Task 2: `ensure_tree` — pinned clone / adopt / converge

**Files:**
- Modify: `src/hal0/agents/hermes_webui.py`
- Test: `tests/agents/test_hermes_webui.py`

**Interfaces:**
- Consumes: `StepOutcome`, `WEBUI_PINNED_REF`, `WEBUI_REPO_URL` from Task 1.
- Produces: `ensure_tree(tree: Path, *, ref: str = WEBUI_PINNED_REF, repo_url: str = WEBUI_REPO_URL, run=subprocess.run) -> StepOutcome`. `run` has the `subprocess.run` signature; all calls pass `check=False, capture_output=True, text=True, timeout=…`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_hermes_webui.py`:

```python
class _FakeRun:
    """Scriptable subprocess.run stand-in keyed on the git subcommand."""

    def __init__(self, responses: dict[str, tuple[int, str, str]]):
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        key = next((a for a in ("rev-parse", "status", "fetch", "checkout", "clone") if a in argv), argv[0])
        code, out, err = self.responses.get(key, (0, "", ""))
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr=err)


def _git_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "webui"
    (tree / ".git").mkdir(parents=True)
    return tree


def test_ensure_tree_noop_when_at_pinned_ref(tmp_path: Path) -> None:
    tree = _git_tree(tmp_path)
    run = _FakeRun({"rev-parse": (0, hw.WEBUI_PINNED_REF + "\n", "")})
    out = hw.ensure_tree(tree, run=run)
    assert out.ok and not out.changed
    assert not any("fetch" in c for c in run.calls)


def test_ensure_tree_refuses_dirty_tree(tmp_path: Path) -> None:
    tree = _git_tree(tmp_path)
    run = _FakeRun({
        "rev-parse": (0, "0" * 40 + "\n", ""),
        "status": (0, " M server.py\n", ""),
    })
    out = hw.ensure_tree(tree, run=run)
    assert not out.ok and not out.changed
    assert "dirty" in out.detail


def test_ensure_tree_moves_clean_tree_to_pin(tmp_path: Path) -> None:
    tree = _git_tree(tmp_path)
    run = _FakeRun({
        "rev-parse": (0, "0" * 40 + "\n", ""),
        "status": (0, "", ""),
        "fetch": (0, "", ""),
        "checkout": (0, "", ""),
    })
    out = hw.ensure_tree(tree, run=run)
    assert out.ok and out.changed


def test_ensure_tree_refuses_unmanaged_nonempty_dir(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    tree.mkdir()
    (tree / "junk.txt").write_text("x")
    out = hw.ensure_tree(tree, run=_FakeRun({}))
    assert not out.ok
    assert "unmanaged" in out.detail


def test_ensure_tree_clones_when_absent(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    run = _FakeRun({"clone": (0, "", ""), "checkout": (0, "", "")})
    out = hw.ensure_tree(tree, run=run)
    assert out.ok and out.changed
    assert any("clone" in c for c in run.calls)


def test_ensure_tree_rejects_unvetted_ref(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HAL0_ALLOW_UNVETTED_HERMES_WEBUI", raising=False)
    out = hw.ensure_tree(tmp_path / "webui", ref="f" * 40, run=_FakeRun({}))
    assert not out.ok
    assert "unvetted" in out.detail
```

Add `import subprocess` to the test file's imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/agents/test_hermes_webui.py -q`
Expected: new tests FAIL with `AttributeError: … has no attribute 'ensure_tree'`

- [ ] **Step 3: Implement `ensure_tree`**

Append to `src/hal0/agents/hermes_webui.py` (add `import os` to imports):

```python
_GIT_TIMEOUT = 120


def ensure_tree(
    tree: Path,
    *,
    ref: str = WEBUI_PINNED_REF,
    repo_url: str = WEBUI_REPO_URL,
    run=subprocess.run,
) -> StepOutcome:
    """Converge ``tree`` to the vetted pinned ref.

    Adoption-safe: an existing checkout already at the pin is untouched; a
    dirty checkout or a non-git directory is refused (ok=False) rather than
    clobbered — the caller downgrades that to a SKIP with this detail.
    """
    if ref not in VETTED_HERMES_WEBUI_REFS and not os.environ.get(
        "HAL0_ALLOW_UNVETTED_HERMES_WEBUI", ""
    ).strip():
        return StepOutcome(ok=False, changed=False, detail=f"unvetted ref {ref[:12]}")

    def _git(*args: str, cwd: Path | None = None):
        argv = ["git", *(("-C", str(cwd)) if cwd else ()), *args]
        return run(argv, check=False, capture_output=True, text=True, timeout=_GIT_TIMEOUT)

    if (tree / ".git").exists():
        head = _git("rev-parse", "HEAD", cwd=tree)
        if head.returncode == 0 and head.stdout.strip() == ref:
            return StepOutcome(ok=True, changed=False, detail=f"tree at pinned ref {ref[:12]}")
        dirty = _git("status", "--porcelain", cwd=tree)
        if dirty.returncode != 0 or dirty.stdout.strip():
            return StepOutcome(ok=False, changed=False,
                               detail="tree dirty or unreadable; refusing to move ref")
        fetch = _git("fetch", "origin", ref, cwd=tree)
        if fetch.returncode != 0:
            return StepOutcome(ok=False, changed=False,
                               detail=f"git fetch failed: {(fetch.stderr or '').strip()[-200:]}")
        co = _git("checkout", "--detach", ref, cwd=tree)
        if co.returncode != 0:
            return StepOutcome(ok=False, changed=False,
                               detail=f"git checkout failed: {(co.stderr or '').strip()[-200:]}")
        return StepOutcome(ok=True, changed=True, detail=f"checked out pinned ref {ref[:12]}")

    if tree.exists() and any(tree.iterdir()):
        return StepOutcome(
            ok=False, changed=False,
            detail="unmanaged non-git tree present; move it aside or set HAL0_SKIP_HERMES_WEBUI=1",
        )

    clone = run(["git", "clone", "--filter=blob:none", repo_url, str(tree)],
                check=False, capture_output=True, text=True, timeout=600)
    if clone.returncode != 0:
        return StepOutcome(ok=False, changed=False,
                           detail=f"git clone failed: {(clone.stderr or '').strip()[-200:]}")
    co = _git("checkout", "--detach", ref, cwd=tree)
    if co.returncode != 0:
        return StepOutcome(ok=False, changed=False,
                           detail=f"git checkout failed: {(co.stderr or '').strip()[-200:]}")
    return StepOutcome(ok=True, changed=True, detail=f"cloned at pinned ref {ref[:12]}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/agents/test_hermes_webui.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add src/hal0/agents/hermes_webui.py tests/agents/test_hermes_webui.py
git commit -m "feat(agents): hermes-webui pinned-ref tree converge with adoption safety"
```

---

### Task 3: `ensure_deps` + `provision()` + pipeline step in `hermes_provision.py`

**Files:**
- Modify: `src/hal0/agents/hermes_webui.py`
- Modify: `src/hal0/agents/hermes_provision.py` (`_INSTALL_STEPS` tuple near line 5230; new `_phase_hermes_webui` next to the other `_phase_*` functions)
- Modify: `tests/agents/_hermes_fakes.py` (`sandbox_hermes_paths`, near line 132)
- Test: `tests/agents/test_hermes_webui.py`

**Interfaces:**
- Consumes: `ensure_tree`, `ensure_env`, `StepOutcome` (Tasks 1–2); `PhaseResult`, `PhaseStatus`, `_StepCtx`, `_INSTALL_STEPS` from `hermes_provision.py`.
- Produces: `ensure_deps(venv: Path, *, run=subprocess.run) -> StepOutcome`; `provision(*, tree: Path, venv: Path, run=subprocess.run) -> dict` with keys `{"status": "ok"|"skip", "changed": bool, "reason": str|None, "details": dict}`; pipeline step name `"hermes_webui"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_hermes_webui.py`:

```python
def test_ensure_deps_noop_when_importable(tmp_path: Path) -> None:
    run = _FakeRun({})  # returncode 0 for the import probe
    out = hw.ensure_deps(tmp_path / "venv", run=run)
    assert out.ok and not out.changed
    assert len(run.calls) == 1  # probe only, no pip install


def test_ensure_deps_installs_when_missing(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        code = 1 if "-c" in argv else 0  # import probe fails, pip succeeds
        return subprocess.CompletedProcess(argv, code, stdout="", stderr="")

    out = hw.ensure_deps(tmp_path / "venv", run=run)
    assert out.ok and out.changed
    assert any("install" in c for c in calls)


def test_provision_skips_gracefully_on_tree_failure(tmp_path: Path) -> None:
    tree = tmp_path / "webui"
    tree.mkdir()
    (tree / "junk.txt").write_text("x")  # unmanaged dir → ensure_tree refuses
    out = hw.provision(tree=tree, venv=tmp_path / "venv", run=_FakeRun({}))
    assert out["status"] == "skip"
    assert "unmanaged" in out["reason"]


def test_provision_ok_and_convergent(tmp_path: Path) -> None:
    tree = _git_tree(tmp_path)
    run = _FakeRun({"rev-parse": (0, hw.WEBUI_PINNED_REF + "\n", "")})
    first = hw.provision(tree=tree, venv=tmp_path / "venv", run=run)
    assert first["status"] == "ok" and first["changed"]  # .env seeded
    second = hw.provision(tree=tree, venv=tmp_path / "venv", run=run)
    assert second["status"] == "ok" and not second["changed"]


def test_pipeline_contains_hermes_webui_step_and_skip_env(monkeypatch) -> None:
    import hal0.agents.hermes_provision as hp

    names = [name for name, _fn in hp._INSTALL_STEPS]
    assert "hermes_webui" in names
    assert names.index("hermes_webui") < names.index("smoke_tests")

    monkeypatch.setenv("HAL0_SKIP_HERMES_WEBUI", "1")
    ctx = hp._StepCtx(state=hp.BootstrapState())
    result = hp._phase_hermes_webui(ctx)
    assert result.status == hp.PhaseStatus.SKIP
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/agents/test_hermes_webui.py -q`
Expected: FAIL — `ensure_deps` / `provision` / `_phase_hermes_webui` missing

- [ ] **Step 3: Implement `ensure_deps` and `provision` in `hermes_webui.py`**

```python
def ensure_deps(venv: Path, *, run=subprocess.run) -> StepOutcome:
    """pyyaml + cryptography into the hermes venv (probe-first, so a
    provisioned box costs one interpreter start, not a pip run)."""
    python = venv / "bin" / "python3"
    probe = run([str(python), "-c", "import yaml, cryptography"],
                check=False, capture_output=True, text=True, timeout=30)
    if probe.returncode == 0:
        return StepOutcome(ok=True, changed=False, detail="deps present")
    pip = venv / "bin" / "pip"
    inst = run([str(pip), "install", "pyyaml>=6.0", "cryptography>=42.0"],
               check=False, capture_output=True, text=True, timeout=600)
    if inst.returncode != 0:
        return StepOutcome(ok=False, changed=False,
                           detail=f"pip install failed: {(inst.stderr or '').strip()[-200:]}")
    return StepOutcome(ok=True, changed=True, detail="installed pyyaml + cryptography")


def provision(*, tree: Path, venv: Path, run=subprocess.run) -> dict:
    """Full companion converge: tree → .env → deps.

    Any refusal (dirty tree, offline clone, pip failure) maps to
    ``status="skip"`` with the blocking detail as ``reason`` — the Hermes
    agent install must never fail because its companion couldn't converge.
    """
    tree_out = ensure_tree(tree, run=run)
    details: dict = {"tree": tree_out.detail}
    if not tree_out.ok:
        return {"status": "skip", "changed": False, "reason": tree_out.detail, "details": details}

    env_out = ensure_env(tree, venv)
    details["env"] = env_out.detail
    deps_out = ensure_deps(venv, run=run)
    details["deps"] = deps_out.detail
    if not deps_out.ok:
        return {"status": "skip", "changed": tree_out.changed or env_out.changed,
                "reason": deps_out.detail, "details": details}

    changed = tree_out.changed or env_out.changed or deps_out.changed
    return {"status": "ok", "changed": changed, "reason": None, "details": details}
```

- [ ] **Step 4: Add the pipeline step to `hermes_provision.py`**

Place next to the other `_phase_*` functions (e.g. just above `_phase_smoke_tests`):

```python
def _phase_hermes_webui(ctx: _StepCtx) -> PhaseResult:
    """Converge the Hermes WebUI (hermex) companion — opt-out, never fatal."""
    if os.environ.get("HAL0_SKIP_HERMES_WEBUI", "").strip() in ("1", "true", "yes"):
        return PhaseResult(status=PhaseStatus.SKIP, reason="HAL0_SKIP_HERMES_WEBUI set")
    from hal0.agents import hermes_webui as hw

    out = hw.provision(tree=hw.WEBUI_TREE_DEFAULT, venv=Path(ctx.state.venv), run=ctx.io.run)
    status = PhaseStatus.OK if out["status"] == "ok" else PhaseStatus.SKIP
    return PhaseResult(
        status=status,
        details={**out["details"], "changed": out["changed"]},
        reason=out["reason"],
    )
```

Then add `("hermes_webui", _phase_hermes_webui),` to `_INSTALL_STEPS` **after** `gateway_secrets_wire` and **before** `smoke_tests`.

- [ ] **Step 5: Keep the sandboxed full-pipeline tests hermetic**

In `tests/agents/_hermes_fakes.py::sandbox_hermes_paths` add (with the other monkeypatches):

```python
    # hermes-webui companion: full-pipeline tests must never touch git/network.
    monkeypatch.setenv("HAL0_SKIP_HERMES_WEBUI", "1")
    monkeypatch.setattr(
        "hal0.agents.hermes_webui.WEBUI_TREE_DEFAULT",
        tmp_path / "var" / "lib" / "hal0" / "hermes-webui",
        raising=True,
    )
```

- [ ] **Step 6: Run the module tests plus the convergence contract**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/agents/test_hermes_webui.py tests/agents/test_hermes_provision_idempotency.py tests/agents/test_hermes_provision.py -q`
Expected: all passed (SKIP step reports `changed=False`, so `r2.converged` holds)

- [ ] **Step 7: Commit**

```bash
git add src/hal0/agents/hermes_webui.py src/hal0/agents/hermes_provision.py \
        tests/agents/test_hermes_webui.py tests/agents/_hermes_fakes.py
git commit -m "feat(agents): wire hermes-webui converge into the hermes install pipeline"
```

---

### Task 4: systemd unit + installer/uninstaller wiring

**Files:**
- Create: `installer/systemd/hermes-webui.service`
- Modify: `installer/install.sh` (webui block after the hermes enable block, near line 2941)
- Modify: `installer/uninstall.sh` (unit list, near line 341)
- Modify: `src/hal0/api/routes/installer.py` (`_REPAIRABLE_UNITS`, near line 551)
- Test: `tests/api/test_installer_repairable.py` behavior lives in existing installer tests — extend `tests/api/test_services_page.py`-adjacent installer test if present, else add assertion test below.

- [ ] **Step 1: Write the unit file**

`installer/systemd/hermes-webui.service` — deliberately mirrors the proven CT105 hand unit (same ExecStart/EnvironmentFile, so adopting it is a config-compatible swap), plus low-risk hardening only. No `ProtectSystem=strict` yet: the upstream app's state-writing paths are unaudited and the working directory must stay writable.

```ini
[Unit]
Description=Hermes WebUI (hermex)
Documentation=https://github.com/nesquena/hermes-webui
After=network-online.target hal0-agent@hermes.service
Wants=network-online.target

[Service]
Type=simple
User=hal0
Group=hal0
WorkingDirectory=/var/lib/hal0/hermes-webui
EnvironmentFile=/var/lib/hal0/hermes-webui/.env
ExecStart=/var/lib/hal0/venvs/hermes/bin/python3 /var/lib/hal0/hermes-webui/server.py
Restart=on-failure
RestartSec=10
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Wire install.sh**

Insert directly after the `systemctl enable --now hal0-agent@hermes.service` block (near line 2941), matching the surrounding style:

```sh
# ── Hermes WebUI (hermex) companion ──────────────────────────────────────────
# Managed pinned checkout + .env live under /var/lib/hal0/hermes-webui.
# `agent webui-sync` re-execs itself as hal0 when run as root, and is the
# same converge the `hal0 agent install hermes` pipeline runs — safe on both
# fresh installs and updates. Opt out with HAL0_SKIP_HERMES_WEBUI=1.
if [[ "${HAL0_SKIP_HERMES_WEBUI:-0}" != "1" ]]; then
    install -m 0644 "${REPO_ROOT}/installer/systemd/hermes-webui.service" \
        "${UNIT_DIR}/hermes-webui.service"
    if [[ -x /var/lib/hal0/venvs/hermes/bin/hermes ]]; then
        "${HAL0_BIN}" agent webui-sync \
            || echo "[warn] hermes-webui sync failed (non-fatal)"
    fi
    if [[ -f /var/lib/hal0/hermes-webui/server.py ]]; then
        systemctl daemon-reload
        systemctl enable --now hermes-webui.service \
            || echo "[warn] hermes-webui enable failed (non-fatal)"
    fi
fi
```

Note: `agent webui-sync` is added in Task 5; install.sh and the CLI land in adjacent commits on this branch, so ordering within the branch is fine.

- [ ] **Step 3: Wire uninstall.sh**

Add `hermes-webui.service` to the unit teardown list near line 341 (same disable/rm treatment as `hal0-openwebui.service`). Do NOT delete `/var/lib/hal0/hermes-webui` — state removal follows the same policy as other `/var/lib/hal0` trees.

- [ ] **Step 4: Add to repairable units**

In `src/hal0/api/routes/installer.py` `_REPAIRABLE_UNITS` (near line 551) add `"hermes-webui.service",`.

- [ ] **Step 5: Test the repairable-units surface**

Append to `tests/agents/test_hermes_webui.py`:

```python
def test_hermes_webui_unit_is_repairable() -> None:
    from hal0.api.routes.installer import _REPAIRABLE_UNITS

    assert "hermes-webui.service" in _REPAIRABLE_UNITS


def test_unit_file_matches_managed_paths() -> None:
    unit = Path("installer/systemd/hermes-webui.service").read_text(encoding="utf-8")
    assert "WorkingDirectory=/var/lib/hal0/hermes-webui" in unit
    assert "EnvironmentFile=/var/lib/hal0/hermes-webui/.env" in unit
    assert "User=hal0" in unit
```

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/agents/test_hermes_webui.py -q` → all passed. Also `bash -n installer/install.sh && bash -n installer/uninstall.sh` → exit 0.

- [ ] **Step 6: Commit**

```bash
git add installer/systemd/hermes-webui.service installer/install.sh installer/uninstall.sh \
        src/hal0/api/routes/installer.py tests/agents/test_hermes_webui.py
git commit -m "feat(installer): ship and enable managed hermes-webui.service"
```

---

### Task 5: CLI — `--webui/--no-webui` flag + `hal0 agent webui-sync`

**Files:**
- Modify: `src/hal0/cli/agent_commands.py` (`agent_install` near line 58, `_install_hermes` near line 131; new command at module level)
- Test: `tests/cli/test_agent_install_hermes.py`

**Interfaces:**
- Consumes: `provision`, `WEBUI_TREE_DEFAULT`, `WEBUI_VENV_DEFAULT` from `hermes_webui.py`.
- Produces: typer option `--webui/--no-webui` (default `True`) on `agent install`; typer command `webui-sync`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/cli/test_agent_install_hermes.py` (follow that file's existing runner/fixture conventions for invoking the typer app; the assertions below are the contract):

```python
def test_no_webui_flag_sets_skip_env(monkeypatch) -> None:
    from hal0.cli import agent_commands as ac

    monkeypatch.delenv("HAL0_SKIP_HERMES_WEBUI", raising=False)
    captured: dict = {}

    def fake_install_hermes(**kwargs):
        captured["env"] = os.environ.get("HAL0_SKIP_HERMES_WEBUI")

    monkeypatch.setattr(ac, "_install_hermes", lambda **kw: fake_install_hermes(**kw))
    runner = CliRunner()
    runner.invoke(ac.app, ["install", "hermes", "--no-webui"])
    assert captured["env"] == "1"


def test_webui_sync_invokes_provision(monkeypatch) -> None:
    from hal0.cli import agent_commands as ac
    import hal0.agents.hermes_webui as hw

    monkeypatch.setattr(ac.os, "geteuid", lambda: 1000)
    called: dict = {}
    monkeypatch.setattr(
        hw, "provision",
        lambda **kw: called.update(kw) or {"status": "ok", "changed": False, "reason": None, "details": {}},
    )
    runner = CliRunner()
    result = runner.invoke(ac.app, ["webui-sync"])
    assert result.exit_code == 0
    assert called["tree"] == hw.WEBUI_TREE_DEFAULT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/cli/test_agent_install_hermes.py -q`
Expected: FAIL — no `--no-webui` option / no `webui-sync` command

- [ ] **Step 3: Implement**

In `agent_commands.py`, add to `agent_install`'s signature (next to the existing `gateway` option, near line 70):

```python
    webui: bool = typer.Option(
        True, "--webui/--no-webui",
        help="Also provision the Hermes WebUI (hermex) companion.",
    ),
```

At the top of the hermes branch of `agent_install` (before `_install_hermes(...)` is called):

```python
    if not webui:
        os.environ["HAL0_SKIP_HERMES_WEBUI"] = "1"
```

(The env var flows into the re-exec'd `agent bootstrap hermes` subprocess, where `_phase_hermes_webui` honors it — same mechanism as `HAL0_SKIP_HERMES` in install.sh.)

New command in the same typer app:

```python
@app.command("webui-sync")
def agent_webui_sync() -> None:
    """Converge the Hermes WebUI (hermex) companion: pinned tree, .env, deps.

    Root re-execs as the hal0 user so everything under /var/lib/hal0 is born
    hal0:hal0 (same posture as `agent bootstrap hermes`).
    """
    if os.geteuid() == 0:
        os.execvp("sudo", ["sudo", "-u", "hal0", "-H", sys.argv[0], "agent", "webui-sync"])
    from hal0.agents import hermes_webui as hw

    out = hw.provision(tree=hw.WEBUI_TREE_DEFAULT, venv=hw.WEBUI_VENV_DEFAULT)
    if out["status"] == "ok":
        typer.echo(f"hermes-webui: converged (changed={out['changed']}) — {out['details']}")
    else:
        typer.echo(f"hermes-webui: skipped — {out['reason']}")
        raise typer.Exit(code=0)  # explicitly non-fatal
```

(Add `import sys` if the module lacks it. Match the module's existing command-registration pattern — if agent commands hang off a subcommand group rather than `app`, register there and adjust the test invocation path identically.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/cli/test_agent_install_hermes.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add src/hal0/cli/agent_commands.py tests/cli/test_agent_install_hermes.py
git commit -m "feat(cli): agent install --webui flag and agent webui-sync command"
```

---

### Task 6: service registry + privilege seam + bash wrapper

**Files:**
- Modify: `src/hal0/services/registry.py` (append to `SERVICES` tuple, near line 114)
- Modify: `src/hal0/system/seam.py` (`COMPANION_SERVICE_UNITS`, near line 90)
- Modify: `installer/wrappers/hal0-systemctl` (case arm near lines 185-192)
- Modify: `tests/api/test_services_page.py` (`_EXPECTED_IDS`, line 33)
- Test: `tests/system/test_seam.py` (extend)

- [ ] **Step 1: Update the expected-ids test first (failing)**

In `tests/api/test_services_page.py:33`:

```python
_EXPECTED_IDS = {"openwebui", "comfyui", "hermes", "hindsight", "hermes-webui"}
```

Append a seam-routing test to `tests/system/test_seam.py`, copying that file's existing companion-unit test shape for `hal0-openwebui.service`, with unit `"hermes-webui.service"` and expected seam argv `["svc-restart", "hermes-webui"]`.

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/api/test_services_page.py tests/system/test_seam.py -q`
Expected: FAIL (registry missing the id; seam map missing the unit)

- [ ] **Step 2: Add the registry entry**

Append to `SERVICES` in `src/hal0/services/registry.py`:

```python
    ServiceDef(
        id="hermes-webui",
        name="Hermes WebUI",
        description="Chat web app for the Hermes agent (hermex; loopback :8787 by default).",
        unit="hermes-webui.service",
        public_url_env="HAL0_HERMES_WEBUI_PUBLIC_URL",
        probe="http",
        probe_url="http://127.0.0.1:8787/",
        probe_url_env="HAL0_HERMES_WEBUI_PROBE_URL",
        actions=_FULL,
        loopback_port=8787,
        hints=(
            "Binds loopback by default; edit /var/lib/hal0/hermes-webui/.env "
            "(HERMES_WEBUI_HOST) or set HAL0_HERMES_WEBUI_PUBLIC_URL for a proxy URL.",
        ),
    ),
```

(`port=None` intentionally: loopback-default services get no host:port browser fallback, mirroring hermes. The generic `_probe_http_env` path in `services.py:96` handles `probe="http"` for non-special-cased ids — no `services.py` change needed.)

- [ ] **Step 3: Extend both privilege maps (they MUST stay in sync)**

`src/hal0/system/seam.py` (near line 90):

```python
COMPANION_SERVICE_UNITS: dict[str, str] = {
    "hal0-openwebui.service": "openwebui",
    "hindsight-api.service": "hindsight",
    "hermes-webui.service": "hermes-webui",
}
```

`installer/wrappers/hal0-systemctl` (case arm near line 187):

```sh
      openwebui)     unit="hal0-openwebui.service" ;;
      hindsight)     unit="hindsight-api.service" ;;
      hermes-webui)  unit="hermes-webui.service" ;;
```

Also update the wrapper's usage text (near line 222) to list `hermes-webui`.

- [ ] **Step 4: Run tests**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/api/test_services_page.py tests/system/test_seam.py tests/system/test_seam_agent_units.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add src/hal0/services/registry.py src/hal0/system/seam.py installer/wrappers/hal0-systemctl \
        tests/api/test_services_page.py tests/system/test_seam.py
git commit -m "feat(services): register hermes-webui service with lifecycle seam routing"
```

---

### Task 7: `/api/services/health` fourth block

**Files:**
- Modify: `src/hal0/api/routes/services_health.py`
- Modify: `tests/api/test_services_health.py` (count assertion `len == 3` → 4, id set)

- [ ] **Step 1: Update the count/id tests first (failing)**

In `tests/api/test_services_health.py`: change the `len(body["services"]) == 3` assertion (near line 81) to `== 4` and add `"hermes-webui"` to its expected-id set. Add a probe test following the file's existing `_probe_openwebui` mock pattern:

```python
def test_hermes_webui_not_installed_reports_honest_down(svc_health_client, monkeypatch) -> None:
    # unit_state "unknown" == unit file absent → detail "not installed", up False
    ...  # patch hal0.api.routes.services_health.svc_systemd.unit_state → {"unit_file_state": "unknown", ...}
    entry = next(s for s in body["services"] if s["id"] == "hermes-webui")
    assert entry["up"] is False
    assert entry["detail"] == "not installed"
```

(Fill the patch/client plumbing from the file's existing tests — same fixtures, same `AsyncMock` style as `_stub_all_down` in `test_services_page.py`.)

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/api/test_services_health.py -q` → FAIL

- [ ] **Step 2: Implement the block**

In `services_health.py`, module constants (next to `_OPENWEBUI_PROBE_URL`, near line 44):

```python
_HERMES_WEBUI_UNIT = "hermes-webui.service"
_HERMES_WEBUI_PROBE_URL = os.environ.get(
    "HAL0_HERMES_WEBUI_PROBE_URL", "http://127.0.0.1:8787/"
)


def _hermes_webui_url() -> str | None:
    url = os.environ.get("HAL0_HERMES_WEBUI_PUBLIC_URL", "").strip().rstrip("/")
    return url or None
```

Add `from hal0.services import systemd as svc_systemd` if not already imported. Probe (next to `_probe_openwebui`, near line 108):

```python
async def _probe_hermes_webui() -> tuple[bool, str]:
    """Companion probe. An absent unit file is 'not installed' (opt-out box),
    which the doctor classifier maps to PASS — distinct from installed-but-down."""
    state = await svc_systemd.unit_state(_HERMES_WEBUI_UNIT)
    if (state.get("unit_file_state") or "unknown") in ("", "unknown"):
        return False, "not installed"
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            resp = await client.get(_HERMES_WEBUI_PROBE_URL)
    except httpx.HTTPError as exc:
        return False, f"unreachable ({type(exc).__name__})"
    if 200 <= resp.status_code < 400:
        return True, "reachable — probe ok"
    return False, f"unhealthy (HTTP {resp.status_code})"
```

Entry block after the openwebui block (mirroring lines 188-204):

```python
    # ── hermes-webui ──────────────────────────────────────────────────────────
    try:
        hw_up, hw_detail = await _probe_hermes_webui()
    except Exception as exc:
        log.warning("services_health.hermes_webui_probe_error", exc=repr(exc))
        hw_up, hw_detail = False, type(exc).__name__

    services.append(
        {"id": "hermes-webui", "name": "Hermes WebUI", "up": hw_up,
         "detail": hw_detail, "url": _hermes_webui_url(), "stat": None}
    )
```

- [ ] **Step 3: Run tests**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/api/test_services_health.py -q` → all passed

- [ ] **Step 4: Commit**

```bash
git add src/hal0/api/routes/services_health.py tests/api/test_services_health.py
git commit -m "feat(api): hermes-webui entry in /api/services/health"
```

---

### Task 8: health report check + diagnosis taxonomy + doctor exports

**Files:**
- Modify: `src/hal0/health_report.py` (`check_hermes_webui`, `build_checks` near line 223, `_CHECK_ID_MAP` near line 251, `__all__`)
- Modify: `src/hal0/diagnostics.py` (`DIAGNOSIS_IDS` near line 147)
- Modify: `src/hal0/cli/doctor_verify.py` (re-export near line 43-58, `__all__`)
- Modify: `tests/cli/test_diagnosis.py` (`_EXPECTED_TAXONOMY` near lines 149-181, key loop near line 141)
- Test: `tests/cli/test_doctor_verify.py`

Layering rule: `health_report.py` and `diagnostics.py` must not import `hal0.cli` (enforced by `tests/diagnostics/test_layering.py`).

- [ ] **Step 1: Write the failing classifier tests**

Append to `tests/cli/test_doctor_verify.py` (pure-function style, matching lines 22-28):

```python
def test_check_hermes_webui_up() -> None:
    c = dv.check_hermes_webui(
        {"services": [{"id": "hermes-webui", "up": True, "detail": "reachable — probe ok"}]}
    )
    assert c.status == "pass"


def test_check_hermes_webui_not_installed_is_pass() -> None:
    c = dv.check_hermes_webui(
        {"services": [{"id": "hermes-webui", "up": False, "detail": "not installed"}]}
    )
    assert c.status == "pass"
    assert "not installed" in c.detail


def test_check_hermes_webui_down_is_warn() -> None:
    c = dv.check_hermes_webui(
        {"services": [{"id": "hermes-webui", "up": False, "detail": "unreachable (ConnectError)"}]}
    )
    assert c.status == "warn"
```

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/cli/test_doctor_verify.py -q` → FAIL

- [ ] **Step 2: Implement in `health_report.py`**

Next to `check_hermes` (near line 203):

```python
def check_hermes_webui(services: dict | None) -> Check:
    """Hermes WebUI companion. Opt-out boxes report 'not installed' → PASS
    (absence is a choice, not a failure — memory-engine precedent)."""
    if isinstance(services, dict):
        entry = next(
            (s for s in services.get("services", []) if s.get("id") == "hermes-webui"),
            None,
        )
        if entry is not None and entry.get("detail") == "not installed":
            return Check(key="hermes-webui", label="Hermes WebUI",
                         status="pass", detail="not installed")
    return _service_check(services, "hermes-webui", "Hermes WebUI")
```

Add `check_hermes_webui(services),` to the end of the `build_checks` list (after `check_hermes(services)`), add `"hermes-webui": "HAL0-HERMES-WEBUI-DOWN",` to `_CHECK_ID_MAP` (KeyError in `to_diagnosis` otherwise), and add `"check_hermes_webui"` to `__all__`.

Note: verify `_service_check`'s produced `Check.key` — it must equal `"hermes-webui"` (the sid argument). If `_service_check` derives the key differently, pass whatever argument makes the key `"hermes-webui"` so the `_CHECK_ID_MAP` lookup holds.

- [ ] **Step 3: Extend the taxonomy**

`src/hal0/diagnostics.py`: add `"HAL0-HERMES-WEBUI-DOWN",` to `DIAGNOSIS_IDS` next to `"HAL0-HERMES-DOWN"`. `tests/cli/test_diagnosis.py`: add the same id to `_EXPECTED_TAXONOMY` and to the key loop near line 141.

- [ ] **Step 4: Re-export in `doctor_verify.py`**

Add `check_hermes_webui` to the `from hal0.health_report import (…)` block and to `__all__`.

- [ ] **Step 5: Run the affected suites**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest tests/cli/test_doctor_verify.py tests/cli/test_diagnosis.py tests/api/test_doctor_route.py tests/cli/test_doctor_all.py tests/diagnostics/test_layering.py -q`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add src/hal0/health_report.py src/hal0/diagnostics.py src/hal0/cli/doctor_verify.py \
        tests/cli/test_doctor_verify.py tests/cli/test_diagnosis.py
git commit -m "feat(doctor): hermes-webui health check with HAL0-HERMES-WEBUI-DOWN diagnosis"
```

---

### Task 9: `/api/config/urls` keys + doctor URL rendering

**Files:**
- Modify: `src/hal0/api/routes/config.py` (`get_urls` near line 169 — ALL THREE return branches + the docstring contract block near lines 174-182)
- Modify: `src/hal0/cli/doctor_verify.py` (`_render_urls` near lines 126-131)
- Test: `tests/api/` — extend the existing config-urls tests (find via `grep -rn "openwebui_enabled" tests/`)

**Interfaces:**
- Produces: response keys `hermes_webui: str` and `hermes_webui_enabled: bool` on `GET /api/config/urls`. Resolution: `HAL0_HERMES_WEBUI_PUBLIC_URL` wins; else if the unit is active AND the `.env` bind host is non-loopback → `http://<resolved-host>:<port>`; else `""` (loopback bind leaks no dead link — same posture as hermes).

- [ ] **Step 1: Write the failing tests**

In the existing config-urls test module, add (adapting client/fixture plumbing from its neighbors):

```python
def test_urls_include_hermes_webui_public_env(client, monkeypatch) -> None:
    monkeypatch.setenv("HAL0_HERMES_WEBUI_PUBLIC_URL", "https://webui.example.dev/")
    body = client.get("/api/config/urls").json()
    assert body["hermes_webui"] == "https://webui.example.dev"
    assert body["hermes_webui_enabled"] is True


def test_urls_hermes_webui_loopback_bind_disabled(client, monkeypatch) -> None:
    monkeypatch.delenv("HAL0_HERMES_WEBUI_PUBLIC_URL", raising=False)
    # no .env present in the sandboxed HAL0_HOME → defaults to loopback → no link
    body = client.get("/api/config/urls").json()
    assert body["hermes_webui"] == ""
    assert body["hermes_webui_enabled"] is False


def test_urls_hermes_webui_lan_bind_gets_host_fallback(client, monkeypatch, tmp_path) -> None:
    envdir = tmp_path / "hermes-webui"
    envdir.mkdir(parents=True)
    (envdir / ".env").write_text("HERMES_WEBUI_HOST=0.0.0.0\nHERMES_WEBUI_PORT=8787\n")
    monkeypatch.setattr("hal0.api.routes.config._hermes_webui_env_path", lambda: envdir / ".env")
    monkeypatch.setattr("hal0.api.routes.config._hermes_webui_is_active",
                        AsyncMock(return_value=True))
    body = client.get("/api/config/urls").json()
    assert body["hermes_webui"].endswith(":8787")
    assert body["hermes_webui_enabled"] is True
```

Run the module → FAIL (missing keys).

- [ ] **Step 2: Implement in `config.py`**

Helpers (near `_comfyui_link`, line 147):

```python
_HERMES_WEBUI_UNIT = "hermes-webui.service"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _hermes_webui_env_path() -> Path:
    from hal0.config import paths

    return paths.var_lib() / "hermes-webui" / ".env"


def _hermes_webui_bind() -> tuple[str, str]:
    """(host, port) from the webui .env; loopback defaults on any failure."""
    host, port = "127.0.0.1", "8787"
    try:
        for line in _hermes_webui_env_path().read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("HERMES_WEBUI_HOST="):
                host = stripped.split("=", 1)[1].strip()
            elif stripped.startswith("HERMES_WEBUI_PORT="):
                port = stripped.split("=", 1)[1].strip()
    except OSError:
        pass
    return host, port


async def _hermes_webui_is_active() -> bool:
    from hal0.services import systemd as svc_systemd

    return await svc_systemd.unit_is_active(_HERMES_WEBUI_UNIT)


async def _hermes_webui_link(request: Request) -> str:
    """Public env wins; LAN-bound + active gets a host:port fallback;
    loopback-bound (the secure default) gets no link — mirror of hermes."""
    public = os.environ.get("HAL0_HERMES_WEBUI_PUBLIC_URL", "").strip().rstrip("/")
    if public:
        return public
    if not await _hermes_webui_is_active():
        return ""
    host, port = _hermes_webui_bind()
    if host in _LOOPBACK_HOSTS:
        return ""
    return f"http://{_host_without_port(_resolve_host(request))}:{port}"
```

In `get_urls`: compute `hermes_webui = await _hermes_webui_link(request)` once before the branching, then add to **all three** return dicts:

```python
        "hermes_webui": hermes_webui,
        "hermes_webui_enabled": bool(hermes_webui),
```

Update the docstring key-contract block (lines 174-182) to list both new keys — the dashboard treats every key as always-present.

- [ ] **Step 3: Doctor URL line**

In `doctor_verify.py::_render_urls`, after the Hermes line:

```python
    if isinstance(urls, dict) and urls.get("hermes_webui_enabled") and urls.get("hermes_webui"):
        lines.append(Text.from_markup(f"  Hermes WebUI [cyan]{urls['hermes_webui']}[/cyan]"))
```

- [ ] **Step 4: Run tests**

Run the config-urls test module + `tests/cli/test_doctor_verify.py` → all passed.

- [ ] **Step 5: Commit**

```bash
git add src/hal0/api/routes/config.py src/hal0/cli/doctor_verify.py tests/
git commit -m "feat(api): hermes_webui keys in /api/config/urls with bind-aware fallback"
```

---

### Task 10: UI — sidebar link + `useConfigUrls` type + services page icon

**Files:**
- Modify: `ui/src/api/hooks/useConfigUrls.ts` (`ConfigUrls` type, lines 18-28)
- Modify: `ui/src/dash/chrome.jsx` (`ServiceLinks`, lines 601-652)
- Modify: `ui/src/dash/services.jsx` (`SIcons`, lines 30-45)
- Modify: `ui/tests/e2e/specs/sidebar-accordion-v3.spec.ts`
- Modify: `ui/tests/e2e/specs/services-v3.spec.ts`

- [ ] **Step 1: Extend the type**

In `useConfigUrls.ts` add to the `ConfigUrls` interface:

```ts
  hermes_webui: string;
  hermes_webui_enabled: boolean;
```

- [ ] **Step 2: Sidebar row**

In `chrome.jsx::ServiceLinks`, next to the existing gates (lines 603-604):

```jsx
const webui = cfg.data?.hermes_webui_enabled ? (cfg.data.hermes_webui || "") : "";
```

After the Hermes row (line 648), same shape as the Hermes/OpenWebUI anchors:

```jsx
{webui && (
  <a
    className="sb-row sb-svc"
    data-testid={testPrefix + "hermes-webui"}
    href={webui}
    target="_blank"
    rel="noopener noreferrer"
    onClick={() => onLaunch && onLaunch()}
    title="Open the Hermes WebUI (hermex)"
  >
    {Icons.chat}
    <span className="lbl">Hermes WebUI</span>
    <span className="sb-svc-ext">{Icons.ext}</span>
  </a>
)}
```

(`Icons.chat` already exists in the `Icons` export, line ~99-152. Row renders in both desktop sidebar and mobile drawer via `testPrefix` for free.)

- [ ] **Step 3: Services page icon**

In `services.jsx::SIcons` add a quoted key (hyphen), reusing the existing hermes glyph style:

```jsx
"hermes-webui": (
  <SIc>
    <rect x="2" y="3" width="12" height="9" rx="1.5" />
    <path d="M5 15h6M8 12v3M5 6.5h6M5 9h4" />
  </SIc>
),
```

(`svcIcon(id)` falls back to `SIcons.default` for unknown ids, so this is cosmetic-only — but add it.)

- [ ] **Step 4: e2e specs**

`sidebar-accordion-v3.spec.ts`:
- Extend the `/api/config/urls` mock (lines 28-31) with `hermes_webui: 'http://hal0.example:8787', hermes_webui_enabled: true`.
- In the links test (line 70), assert `svc-hermes-webui` has the mocked href, `target="_blank"`, `rel` matching `/noopener/`.
- In the omission test (line 100), set `hermes_webui_enabled: false` in that test's mock and assert `expect(svc.locator('[data-testid="svc-hermes-webui"]')).toHaveCount(0)`.

`services-v3.spec.ts`: add a fixture entry to the mocked `/api/services` payload:

```ts
{
  id: 'hermes-webui', name: 'Hermes WebUI',
  description: 'Chat web app for the Hermes agent (hermex; loopback :8787 by default).',
  managed: true, unit: 'hermes-webui.service',
  unit_state: { active_state: 'active', sub_state: 'running', unit_file_state: 'enabled', since: null },
  up: true, detail: 'reachable — probe ok', stat: null,
  url: null, mdns_url: null, loopback_port: 8787,
  actions: ['start', 'stop', 'restart', 'enable', 'disable'],
  mdns_capable: false, hints: [],
},
```

and extend the "all cards render" assertion with `svcp-card-hermes-webui`.

- [ ] **Step 5: Verify**

Run: `cd ui && npm run typecheck && npm run lint`
Expected: clean.
Run: `cd ui && npx playwright test tests/e2e/specs/sidebar-accordion-v3.spec.ts tests/e2e/specs/services-v3.spec.ts`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add ui/src/api/hooks/useConfigUrls.ts ui/src/dash/chrome.jsx ui/src/dash/services.jsx \
        ui/tests/e2e/specs/sidebar-accordion-v3.spec.ts ui/tests/e2e/specs/services-v3.spec.ts
git commit -m "feat(ui): hermes-webui sidebar link and services page entry"
```

---

### Task 11: UI — Hermes flip-card back integration links

**Files:**
- Modify: `ui/src/dash/agents/agents-hook-bridge.ts` (republish `useConfigUrls` on `window`)
- Modify: `ui/src/dash/agents/agent-card.jsx` (`LiveAgentCard` back-face actions zone, lines 225-253)
- Modify: `ui/src/dash/agents/agents-overview.jsx` (thread `links` prop, near lines 172-178 and 253-263)
- Modify: `ui/src/dash/chrome.jsx` (ensure `GLYPHS` has `ext`, `board`, `chat` entries)
- Modify: `ui/tests/e2e/specs/agent-view-v3.spec.ts`

Constraint reminder: `dash/*.jsx` files may NOT use ES imports across dash modules — hooks reach them only via the window-globals bridge (`agent-card.jsx:16-20` states the contract).

- [ ] **Step 1: Bridge the hook**

In `agents-hook-bridge.ts`, alongside the existing `__hal0UseAgents` publications:

```ts
import { useConfigUrls } from "@/api/hooks/useConfigUrls";
// …in the existing Object.assign(window, { … }) block:
  __hal0UseConfigUrls: useConfigUrls,
```

- [ ] **Step 2: Glyph check**

In `chrome.jsx`, the flip card uses the string-name `<Icon name="…"/>` form which resolves through `GLYPHS` (fallback `GLYPHS.dot`). Verify `GLYPHS` contains `ext`, `board`, and `chat` keys; for any that are missing, copy the corresponding `<path>`/`<rect>` children verbatim from the pre-rendered `Icons.ext` / `Icons.board` / `Icons.chat` entries (lines ~99-152) into `GLYPHS` under those names.

- [ ] **Step 3: Card back links row**

In `agent-card.jsx`:
- Signature: `function LiveAgentCard({ agent, health, statusCls, statusLabel, restart, onLogs, onPersona, links })`.
- Update the gate (line 123): `const hasActions = !!(onLogs || onPersona || restart || (links && links.length));`
- Inside the `fcb-actions` div, ABOVE the existing logs/persona row:

```jsx
{links && links.length > 0 && (
  <div className="fcb-act-row">
    {links.map((l) =>
      l.href ? (
        <a
          key={l.id}
          className="fc-act"
          data-testid={"agent-link-" + l.id}
          href={l.href}
          target="_blank"
          rel="noopener noreferrer"
          onClick={stop}
        >
          <Icon name={l.icon} size={12} sw={1.5} />
          {l.label}
        </a>
      ) : (
        <button
          key={l.id}
          className="fc-act"
          data-testid={"agent-link-" + l.id}
          onClick={(e) => { stop(e); l.onClick(); }}
        >
          <Icon name={l.icon} size={12} sw={1.5} />
          {l.label}
        </button>
      )
    )}
  </div>
)}
```

(`.fc-act` is class-based CSS — anchors inherit the pill look; no CSS change needed.)

- [ ] **Step 4: Thread links from the overview**

In `agents-overview.jsx`, next to the other window-hook resolutions (lines 172-178):

```jsx
const useConfigUrlsHook = window.__hal0UseConfigUrls;
const cfgUrls = useConfigUrlsHook ? useConfigUrlsHook() : { data: null };
```

Where the Hermes `LiveAgentCard` is rendered (lines 253-263), build and pass:

```jsx
const hermesLinks = [];
if (cfgUrls.data?.hermes_webui_enabled && cfgUrls.data.hermes_webui) {
  hermesLinks.push({ id: "webui", label: "WebUI", icon: "chat", href: cfgUrls.data.hermes_webui });
}
if (cfgUrls.data?.hermes_enabled && cfgUrls.data.hermes) {
  hermesLinks.push({ id: "dash", label: "Dash", icon: "ext", href: cfgUrls.data.hermes });
}
hermesLinks.push({
  id: "board", label: "Kanban", icon: "board",
  onClick: () => { window.location.hash = "#board"; },
});
```

…and add `links={hermesLinks}` to that `<LiveAgentCard …/>`. Do NOT pass `links` to the Pi card.

- [ ] **Step 5: e2e**

In `agent-view-v3.spec.ts`, extend the mocks with `/api/config/urls` (`hermes: 'http://hal0.example:9119', hermes_enabled: true, hermes_webui: 'http://hal0.example:8787', hermes_webui_enabled: true`, plus the existing keys) and add:

```ts
test('Hermes card back carries WebUI / Dash / Kanban integration links', async ({ page }) => {
  // …same setup/mocks as the existing flip test…
  await page.locator('[data-testid="agent-card-hermes"]').click();
  const card = page.locator('[data-testid="agent-card-hermes"]');
  await expect(card.locator('[data-testid="agent-link-webui"]')).toHaveAttribute('href', 'http://hal0.example:8787');
  await expect(card.locator('[data-testid="agent-link-dash"]')).toHaveAttribute('href', 'http://hal0.example:9119');
  await card.locator('[data-testid="agent-link-board"]').click();
  await expect(page).toHaveURL(/#board/);
});
```

Scope all locators to the card — the flip is opacity-toggled, not unmounted (per the Pi flip test note at lines 91-106).

- [ ] **Step 6: Verify**

Run: `cd ui && npm run typecheck && npm run lint && npx playwright test tests/e2e/specs/agent-view-v3.spec.ts`
Expected: clean + all passed. Also re-run the existing flip/restart test in that spec — the restart action must still work with links present.

- [ ] **Step 7: Commit**

```bash
git add ui/src/dash/agents/agents-hook-bridge.ts ui/src/dash/agents/agent-card.jsx \
        ui/src/dash/agents/agents-overview.jsx ui/src/dash/chrome.jsx \
        ui/tests/e2e/specs/agent-view-v3.spec.ts
git commit -m "feat(ui): integration links on the hermes flip-card back"
```

---

### Task 12: Full verification + changelog

**Files:**
- Modify: `CHANGELOG.md` (unreleased section)

- [ ] **Step 1: Full backend suite**

Run: `HAL0_HOME=$(mktemp -d) uv run --extra dev pytest -q`
Expected: no new failures vs the branch base (`git stash` compare if unsure). Pay attention to: `test_hermes_provision_idempotency.py`, `test_services_page.py`, `test_services_health.py`, `test_diagnosis.py`, `test_doctor_route.py`, `test_seam*.py`, `test_layering.py`.

- [ ] **Step 2: Full UI verification**

Run: `cd ui && npm run typecheck && npm run lint && npx vitest run && npm run test:e2e`
Expected: clean. (If unrelated e2e specs are flaky/red on the base branch, note them; do not chase.)

- [ ] **Step 3: Shell syntax**

Run: `bash -n installer/install.sh && bash -n installer/uninstall.sh && bash -n installer/wrappers/hal0-systemctl`
Expected: exit 0.

- [ ] **Step 4: Changelog entry**

Under the unreleased heading, matching existing entry style:

```markdown
- feat(agents): the Hermes WebUI (hermex) companion is now hal0-managed — pinned
  checkout under /var/lib/hal0/hermes-webui, seeded .env (loopback :8787 by
  default, generated password, operator edits preserved), hermes-webui.service
  unit, lifecycle actions from the Services page, doctor/health coverage
  (HAL0-HERMES-WEBUI-DOWN), hermes_webui keys on /api/config/urls, a sidebar
  link, and integration links on the Hermes agent card. Opt out with
  `hal0 agent install hermes --no-webui` or HAL0_SKIP_HERMES_WEBUI=1.
```

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): hermes-webui managed companion"
```

---

## Deployment / adoption notes (not part of the task sequence)

- **CT105 adoption is designed to be a no-op:** live checkout is clean at exactly `deac1384fed96c07134130b5c8df45b431d0b8c3` (the pin), `.env` seeding preserves its `HERMES_WEBUI_HOST=0.0.0.0` and password, and the shipped unit's `ExecStart`/`EnvironmentFile` match the hand unit. Installing over it replaces the unit file (adds `Documentation=`, `NoNewPrivileges`, `PrivateTmp`) and restarts the service once.
- After deploy, `hermes.thinmint.dev`-style public exposure for the webui is a gateway concern (Traefik on CT200) — set `HAL0_HERMES_WEBUI_PUBLIC_URL` in the hal0-api unit env when a proxy route exists; out of scope here.
- Bumping the upstream pin later = new SHA in `VETTED_HERMES_WEBUI_REFS` + `WEBUI_PINNED_REF` after review — same duty as the hermes-agent requirement line.
