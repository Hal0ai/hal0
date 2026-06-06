# hal0 Model-Store Cleanup + Pull-Engine Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **On execution, copy this file into the hal0 repo at**
> `docs/superpowers/plans/2026-06-06-model-store-cleanup-hardening.md` and commit it
> with the code tasks (Part B).

**Goal:** Fix the model-store inconsistencies the 2026-06-06 audit found (stale Lemonade
catalog, ~0.6 GB of real-copy duplicates, one broken registry pointer) and harden the
pull path so catalog drift cannot silently recur.

**Architecture:** Two parts. **Part A** is one-time *operational* cleanup run on the
shared runtime CT 105 (10.0.1.142) — no code changes, just `hal0` CLI + filesystem ops
with verify-first discipline. **Part B** is a *code* change in the hal0 repo (developed
in a worktree on hal0-dev, PR'd, then deployed): a centralized post-mutation hook so any
registry write regenerates `server_models.json`, plus a `--check` drift guard for
cron/healthchecks. The root cause of the audit's drift is that `run_pull`,
`model register`, `scan`, and `rm` all mutate `registry.toml` but **never** call
`write_server_models` — only the install hook and a manual `hal0 capabilities sync` do.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, Typer CLI, `tomli_w`/`tomllib`,
pytest. Runtime: AMD Strix Halo CT 105, Lemonade Server, ZFS store at `/mnt/ai-models`.

**Key facts locked from source (CT 105 `/opt/hal0`):**
- `src/hal0/registry/store.py` — `ModelRegistry.add/update/remove` each hold `self._lock`,
  call `self._atomic_write(models)` then `self._invalidate()`. Exposes `self.registry_file`.
- `src/hal0/registry/pull.py` — `run_pull`/`run_flm_pull` install the file then call
  `_register_pulled`/`_register_flm_pulled` → `registry.add/update`. No catalog regen.
- `src/hal0/lemonade/server_models_gen.py` — `generate_server_models(registry_path: Path)
  -> dict`; `write_server_models(registry_path: Path, output_path: Path)` (atomic, chmod
  0644, idempotent).
- `src/hal0/cli/capabilities_commands.py` — `sync` command; defaults
  `_DEFAULT_REGISTRY_PATH=/var/lib/hal0/registry/registry.toml`,
  `_DEFAULT_SERVER_MODELS_PATH=/opt/lemonade/resources/server_models.json`. Already has
  `--dry-run`.
- Audit snapshot + reports: `/home/halo/dev/hal0-model-audit/` (`SUMMARY.md`, `out/`, `raw/`).

---

## Pre-flight (do once, before any task)

- [ ] **P1: Claim the shared runtime + confirm it's clean**

```bash
~/.claude/bin/wip hal0 status        # MUST show branch=main, no uncommitted tracked edits
~/.claude/bin/wip hal0 claim "model-store cleanup + pull-engine catalog-regen hook" \
  src/hal0/registry/store.py src/hal0/lemonade/server_models_gen.py \
  src/hal0/cli/capabilities_commands.py src/hal0/api/__init__.py
```

If `wip hal0 status` shows another session active on `/opt/hal0`, STOP and coordinate.

- [ ] **P2: Back up the two mutable artifacts on CT 105**

```bash
ssh hal0 'cp -a /var/lib/hal0/registry/registry.toml \
  /var/lib/hal0/registry/registry.toml.bak-cleanup-2026-06-06 && \
  cp -a /opt/lemonade/resources/server_models.json \
  /opt/lemonade/resources/server_models.json.bak-cleanup-2026-06-06 && echo OK'
```

- [ ] **P3: Record the baseline catalog count**

```bash
ssh hal0 'python3 -c "import json;print(len(json.load(open(\"/opt/lemonade/resources/server_models.json\"))),\"entries\")"'
# Expected baseline: 197 entries (20 hal0 models merged, dated 2026-05-26).
```

---

## PART A — One-time cleanup (runtime ops on CT 105)

### Task 1: Regenerate the stale Lemonade catalog

**Files:** none (CLI op on CT 105). Fixes the 7 registry models missing from the catalog.

- [ ] **Step 1: Dry-run the sync to preview what would be written**

```bash
ssh hal0 'cd /opt/hal0 && python -m hal0 capabilities sync --dry-run'
```
Expected: a table listing **27** model ids (the full registry) with recipes/labels.

- [ ] **Step 2: Write the regenerated catalog**

```bash
ssh hal0 'cd /opt/hal0 && python -m hal0 capabilities sync'
```
Expected: `wrote 27 entries to /opt/lemonade/resources/server_models.json.`
(Note: hal0's generator emits its curated set; if the install philosophy is "stock +
hal0", confirm the count matches `capabilities sync --dry-run` rather than assuming 27.)

- [ ] **Step 3: Verify the 7 previously-missing models are now present**

```bash
ssh hal0 'python3 - <<PY
import json,tomllib
sm=json.load(open("/opt/lemonade/resources/server_models.json"))
reg=tomllib.load(open("/var/lib/hal0/registry/registry.toml","rb"))["models"]
missing=[s for s in reg if s not in sm]
print("registry:",len(reg)," in catalog:",sum(s in sm for s in reg)," still missing:",missing)
PY'
```
Expected: `still missing: []` (every registry model now resolvable in the catalog).

- [ ] **Step 4: Confirm Lemonade picks it up without restart**

```bash
ssh hal0 'curl -s http://127.0.0.1:13305/api/v1/models | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d.get(\"data\",d)) if isinstance(d,dict) else len(d),\"models visible\")"'
```
Expected: count reflects the new catalog (Lemonade re-scans on next probe; no
`lemond.service` restart needed).

### Task 2: Replace the 2 real-copy duplicates with symlinks

**Files:** none (filesystem op on CT 105). Reclaims ~0.6 GB; makes these two models match
the symlink-into-hub pattern the other `local/` slugs use. **Verify-first (Tier 2):**
confirm byte-identity by SHA before deleting anything.

Targets from the audit:
- `qwen3.5-0.8b` — real copy `/mnt/ai-models/local/qwen3.5-0.8b/Qwen3.5-0.8B-UD-Q4_K_XL.gguf`
  vs hub blob under `models--unsloth--Qwen3.5-0.8B-GGUF`.
- `jina-reranker-v1-tiny-en-q8` — real copy at the **root**
  `/mnt/ai-models/jina-reranker-v1-tiny-en-q8/jina-reranker-v1-tiny-en.Q8_0.gguf` (the
  `local/jina-reranker-v1-tiny-en-q8/` entry is already a symlink into hub).

- [ ] **Step 1: Find the hub snapshot target + confirm SHA-256 match (qwen3.5-0.8b)**

```bash
ssh hal0 'set -e
SNAP=$(ls /mnt/ai-models/huggingface/hub/models--unsloth--Qwen3.5-0.8B-GGUF/snapshots/*/Qwen3.5-0.8B-UD-Q4_K_XL.gguf)
REAL=/mnt/ai-models/local/qwen3.5-0.8b/Qwen3.5-0.8B-UD-Q4_K_XL.gguf
echo "snap=$SNAP"; echo "real=$REAL"
sha256sum "$SNAP" "$REAL"'
```
Expected: BOTH SHA-256 hashes identical. **If they differ, STOP — not a true duplicate.**

- [ ] **Step 2: Check which path the registry entry points at**

```bash
ssh hal0 'python3 -c "import tomllib;m=tomllib.load(open(\"/var/lib/hal0/registry/registry.toml\",\"rb\"))[\"models\"];print(m[\"qwen3.5-0.8b\"][\"path\"])"'
```
Note the path. If it already points at the hub snapshot, the local real file is a pure
orphan (delete it, Step 3). If it points at the local file, replace the file in place
with a symlink (Step 3) so the registry path stays valid.

- [ ] **Step 3: Replace the real file with a symlink to the hub snapshot**

```bash
ssh hal0 'set -e
SNAP=$(ls /mnt/ai-models/huggingface/hub/models--unsloth--Qwen3.5-0.8B-GGUF/snapshots/*/Qwen3.5-0.8B-UD-Q4_K_XL.gguf)
REAL=/mnt/ai-models/local/qwen3.5-0.8b/Qwen3.5-0.8B-UD-Q4_K_XL.gguf
rm -f "$REAL" && ln -s "$SNAP" "$REAL"
ls -l "$REAL"'
```
Expected: `$REAL` now shown as a symlink `-> .../snapshots/.../Qwen3.5-0.8B-UD-Q4_K_XL.gguf`.

- [ ] **Step 4: Repeat the SHA-confirm-then-fix for jina (root copy)**

```bash
ssh hal0 'set -e
SNAP=$(ls /mnt/ai-models/huggingface/hub/models--mradermacher--jina-reranker-v1-tiny-en-GGUF/snapshots/*/jina-reranker-v1-tiny-en.Q8_0.gguf)
ROOT=/mnt/ai-models/jina-reranker-v1-tiny-en-q8/jina-reranker-v1-tiny-en.Q8_0.gguf
sha256sum "$SNAP" "$ROOT"
# Confirm the registry jina entry points at local/ (the existing symlink), NOT this root copy:
python3 -c "import tomllib;m=tomllib.load(open(\"/var/lib/hal0/registry/registry.toml\",\"rb\"))[\"models\"];print(\"registry path:\",m[\"jina-reranker-v1-tiny-en-q8\"][\"path\"])"'
```
Expected: SHAs match AND registry path = `/mnt/ai-models/local/jina-reranker-v1-tiny-en-q8/...`.

- [ ] **Step 5: Delete the unreferenced root jina copy (only if Step 4 confirmed)**

```bash
ssh hal0 'rm -rf /mnt/ai-models/jina-reranker-v1-tiny-en-q8 && echo "removed root jina copy"'
```
**If Step 4 showed the registry pointing at the root copy instead, do NOT delete — replace
with a symlink to `$SNAP` exactly as in Step 3.**

- [ ] **Step 6: Verify the affected models still resolve + load**

```bash
ssh hal0 'cd /opt/hal0 && python -m hal0 model show qwen3.5-0.8b && python -m hal0 model show jina-reranker-v1-tiny-en-q8'
ssh hal0 'df -h /mnt/ai-models | tail -1'   # confirm ~0.6 GB freed
```
Expected: both `model show` succeed with a resolvable path; available space increased.

### Task 3: Fix the kokoro pointer (moonshine cache is NOT an orphan — keep it)

**Files:** none (investigate-then-act on CT 105). The audit's `kokoro-v1` "dangling" is
probably a **false positive** (dir-based ONNX model the file-level resolver doesn't
follow).

**`voices/moonshine_voice/` is NOT an orphan — do NOT delete it.** It is the 980 MB
download cache (`download.moonshine.ai/...`) backing hal0's **Moonshine STT provider**
(`src/hal0/providers/moonshine.py`, `MoonshineProvider`, `/v1/audio/transcriptions`),
holding the STT arches (`base-en`, `small-streaming-en`, `medium-streaming-en`) plus a
bundled Kokoro TTS model. It is registry-untracked **by design** — provider-managed
caches (Moonshine, FLM, ComfyUI) live outside `registry.toml`, so `analyze.py` flags
them as "orphans" as a known false positive. Action: **keep, leave as-is.**

- [ ] **Step 1: Inspect the kokoro registry entry + on-disk target**

```bash
ssh hal0 'python3 -c "import tomllib;print(tomllib.load(open(\"/var/lib/hal0/registry/registry.toml\",\"rb\"))[\"models\"][\"kokoro-v1\"])"
ls -lL /mnt/ai-models/local/kokoro-v1/ 2>&1; echo "---"; ls -l /mnt/ai-models/local/kokoro-v1/'
```
Decision: if the registry `path` resolves to an existing dir/file (the ONNX dir), kokoro
is **fine** — no action, note it as a resolver false-positive. If the path is genuinely
broken, repoint it (Step 2).

- [ ] **Step 2: (Only if broken) repoint kokoro via the registry CLI — never hand-edit TOML**

```bash
# Determine the correct existing path first, then:
ssh hal0 'cd /opt/hal0 && python -m hal0 model show kokoro-v1'
# If a repoint is needed, use the registry/model CLI (model register / assign), NOT a text edit.
```
Per project memory `hal0_registry_toml_hand_edit_danger`: never splice `registry.toml` by
hand — a malformed file triggers a destructive auto-scan rebuild.

- [ ] **Step 3: Confirm the moonshine STT cache is intact (keep — no deletion)**

```bash
ssh hal0 'du -sh /mnt/ai-models/voices/moonshine_voice/ 2>/dev/null; \
  ls /mnt/ai-models/voices/moonshine_voice/download.moonshine.ai/model/'
```
Expected: the STT arch dirs (`base-en`, `small-streaming-en`, `medium-streaming-en`)
present. **No action — this backs the Moonshine STT provider.** Add a note to
`analyze.py`'s output (or a known-false-positives list) so provider-managed caches
(`voices/moonshine_voice`, FLM host dir, ComfyUI models) stop being reported as orphans.

- [ ] **Step 4: Re-run the audit reconciliation to confirm Part A is clean**

```bash
cd /home/halo/dev/hal0-model-audit
ssh hal0 'find /mnt/ai-models -type f -printf "%s\t%p\n"' > raw/physical_files.tsv
ssh hal0 'find /mnt/ai-models -type l -printf "%p\t%l\n"' > raw/symlinks.tsv
ssh hal0 'cat /var/lib/hal0/registry/registry.toml' > raw/registry.toml
python3 analyze.py
```
Expected: dangling = 0 (or only the confirmed kokoro false-positive); the 2 dup sets gone;
genuine orphans = 0 (after the moonshine decision).

---

## PART B — Pull-engine hardening (code, TDD, in a hal0 worktree on hal0-dev)

> Develop OFF the shared runtime. Create a worktree from `origin/main` on hal0-dev, run
> **targeted** pytest only (project memory `hal0_local_full_test_suite_hangs`: the full
> suite hangs on hal0-dev — never run `pytest tests/`).

- [ ] **B-setup: Worktree from main**

```bash
cd ~/dev/hal0 && git fetch origin
git worktree add -b feat/registry-catalog-regen ../hal0-wt-catalog-regen origin/main
cd ../hal0-wt-catalog-regen
cp /home/halo/dev/hal0-model-audit/plan-2026-06-06-model-store-cleanup-hardening.md \
   docs/superpowers/plans/2026-06-06-model-store-cleanup-hardening.md
```

### Task 4: Centralized post-mutation catalog regeneration

**Files:**
- Modify: `src/hal0/registry/store.py` (add `on_change` hook + `_notify_change`; call after add/update/remove)
- Modify: `src/hal0/api/__init__.py` (wire the closure when `model_registry` is constructed)
- Test: `tests/registry/test_store_on_change.py` (new)

- [ ] **Step 1: Write the failing test for the on_change hook**

Create `tests/registry/test_store_on_change.py`:

```python
from pathlib import Path

from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry


def _reg(tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(tmp_path / "registry.toml")


def test_on_change_fires_after_add(tmp_path):
    reg = _reg(tmp_path)
    calls = []
    reg.on_change = lambda: calls.append("x")
    reg.add(Model(id="m1", name="m1", path=str(tmp_path / "m1.gguf"),
                  size_bytes=1, capabilities=["chat"]))
    assert calls == ["x"]


def test_on_change_fires_after_update_and_remove(tmp_path):
    reg = _reg(tmp_path)
    reg.add(Model(id="m1", name="m1", path=str(tmp_path / "m1.gguf"),
                  size_bytes=1, capabilities=["chat"]))
    calls = []
    reg.on_change = lambda: calls.append("x")
    reg.update("m1", {"size_bytes": 2})
    reg.remove("m1")
    assert calls == ["x", "x"]


def test_on_change_failure_does_not_break_write(tmp_path):
    reg = _reg(tmp_path)
    def boom(): raise RuntimeError("regen failed")
    reg.on_change = boom
    # add must still succeed and persist even if the hook raises
    reg.add(Model(id="m1", name="m1", path=str(tmp_path / "m1.gguf"),
                  size_bytes=1, capabilities=["chat"]))
    assert reg.has("m1")


def test_no_hook_is_a_noop(tmp_path):
    reg = _reg(tmp_path)
    reg.add(Model(id="m1", name="m1", path=str(tmp_path / "m1.gguf"),
                  size_bytes=1, capabilities=["chat"]))  # must not raise
    assert reg.has("m1")
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd ~/dev/hal0-wt-catalog-regen && python -m pytest tests/registry/test_store_on_change.py -v`
Expected: FAIL — `AttributeError: 'ModelRegistry' object has no attribute 'on_change'`
(setting the attribute works, but it's never invoked, so the call-count asserts fail).

- [ ] **Step 3: Add the hook to `ModelRegistry`**

In `src/hal0/registry/store.py`, add the import near the top:

```python
from collections.abc import Callable
```

Add a class-level default attribute inside `class ModelRegistry` (right after the
docstring, before `__init__`):

```python
    # Optional post-mutation callback. When set (by create_app), every
    # successful add/update/remove invokes it AFTER the lock is released so
    # downstream artifacts (Lemonade server_models.json) can be regenerated.
    # Best-effort: a failing hook is logged, never propagated — a catalog
    # regen failure must not corrupt or roll back a registry write.
    on_change: "Callable[[], None] | None" = None

    def _notify_change(self) -> None:
        cb = self.on_change
        if cb is None:
            return
        try:
            cb()
        except Exception:
            log.warning("registry.on_change_failed", exc_info=True)
```

Then move the notify OUTSIDE each lock block. In `add`, change the tail so
`self._notify_change()` runs after the `with self._lock:` block:

```python
    def add(self, model: Model) -> None:
        with self._lock:
            models = dict(self._ensure_fresh())
            if model.id in models:
                raise ModelAlreadyExists(
                    f"model {model.id!r} already in registry",
                    details={"model_id": model.id},
                )
            models[model.id] = model
            self._atomic_write(models)
            self._invalidate()
        self._notify_change()
```

In `remove`, capture the result, notify only when something changed, then return:

```python
    def remove(self, model_id: str) -> bool:
        with self._lock:
            models = dict(self._ensure_fresh())
            if model_id not in models:
                return False
            del models[model_id]
            self._atomic_write(models)
            self._invalidate()
        self._notify_change()
        return True
```

In `update`, after the existing `with self._lock:` body computes and writes the new
model, capture the returned `Model` into a local, call `self._notify_change()` after the
block, then `return` the local. (Preserve whatever the current body returns — wrap the
final `return <model>` so the notify happens before it, outside the lock.)

- [ ] **Step 4: Run the test to confirm it passes**

Run: `python -m pytest tests/registry/test_store_on_change.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the existing store tests to confirm no regression**

Run: `python -m pytest tests/registry/ -v`
Expected: PASS (all pre-existing registry tests still green).

- [ ] **Step 6: Wire the regeneration closure in `create_app`**

In `src/hal0/api/__init__.py`, find where the registry is constructed:

```bash
grep -n "model_registry" src/hal0/api/__init__.py | head
```

Immediately AFTER `app.state.model_registry = ModelRegistry(...)` (use the actual
variable), insert:

```python
    import os as _os
    from pathlib import Path as _Path

    from hal0.lemonade.server_models_gen import write_server_models

    _server_models_path = _Path(
        _os.environ.get(
            "HAL0_SERVER_MODELS_PATH", "/opt/lemonade/resources/server_models.json"
        )
    )

    def _regen_server_models() -> None:
        # Regenerate Lemonade's catalog from the registry after any mutation.
        # write_server_models is atomic + idempotent; failures are logged by
        # ModelRegistry._notify_change, never raised into the request path.
        write_server_models(app.state.model_registry.registry_file, _server_models_path)

    app.state.model_registry.on_change = _regen_server_models
```

- [ ] **Step 7: Write + run an integration test for the wired closure**

Add to `tests/registry/test_store_on_change.py`:

```python
import json

from hal0.lemonade.server_models_gen import write_server_models


def test_closure_regenerates_server_models(tmp_path):
    reg = ModelRegistry(tmp_path / "registry.toml")
    out = tmp_path / "server_models.json"
    reg.on_change = lambda: write_server_models(reg.registry_file, out)
    reg.add(Model(id="qwen3-4b-q4_k_m", name="Qwen3 4B", path=str(tmp_path / "q.gguf"),
                  size_bytes=1, capabilities=["chat"],
                  hf_repo="unsloth/Qwen3-4B-GGUF", hf_filename="q.gguf"))
    assert out.exists()
    catalog = json.loads(out.read_text())
    assert any("qwen3-4b" in k.lower() for k in catalog), catalog.keys()
```

Run: `python -m pytest tests/registry/test_store_on_change.py -v`
Expected: PASS (5 tests).

- [ ] **Step 8: Commit**

```bash
git add src/hal0/registry/store.py src/hal0/api/__init__.py tests/registry/test_store_on_change.py
git commit -m "feat(registry): regenerate server_models.json on every registry mutation

Adds a best-effort on_change hook to ModelRegistry, invoked after each
add/update/remove. create_app wires it to write_server_models so pulls,
registers, scans, and removals keep Lemonade's catalog in sync. Fixes the
drift where curated models were invisible until a manual capabilities sync.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 5: `capabilities sync --check` drift guard

**Files:**
- Modify: `src/hal0/cli/capabilities_commands.py` (add `--check` flag to `sync`)
- Test: `tests/cli/test_capabilities_sync_check.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_capabilities_sync_check.py`:

```python
import json

from typer.testing import CliRunner

from hal0.cli.capabilities_commands import app

runner = CliRunner()


def _write_registry(p, model_id="qwen3-4b-q4_k_m"):
    p.write_text(
        f'[models."{model_id}"]\n'
        f'name = "Q"\npath = "/x/q.gguf"\nsize_bytes = 1\n'
        f'capabilities = ["chat"]\nhf_repo = "unsloth/Qwen3-4B-GGUF"\n'
        f'hf_filename = "q.gguf"\n'
    )


def test_check_passes_when_in_sync(tmp_path):
    reg = tmp_path / "registry.toml"; out = tmp_path / "sm.json"
    _write_registry(reg)
    runner.invoke(app, ["sync", "--registry", str(reg), "--output", str(out)])
    r = runner.invoke(app, ["sync", "--registry", str(reg), "--output", str(out), "--check"])
    assert r.exit_code == 0, r.output


def test_check_fails_on_drift(tmp_path):
    reg = tmp_path / "registry.toml"; out = tmp_path / "sm.json"
    _write_registry(reg)
    out.write_text(json.dumps({"stale": {}}))  # not what the registry would generate
    r = runner.invoke(app, ["sync", "--registry", str(reg), "--output", str(out), "--check"])
    assert r.exit_code == 1, r.output


def test_check_fails_when_output_missing(tmp_path):
    reg = tmp_path / "registry.toml"; out = tmp_path / "sm.json"
    _write_registry(reg)
    r = runner.invoke(app, ["sync", "--registry", str(reg), "--output", str(out), "--check"])
    assert r.exit_code == 1, r.output
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/cli/test_capabilities_sync_check.py -v`
Expected: FAIL — `--check` is not a known option (exit code 2 from Typer).

- [ ] **Step 3: Add the `--check` flag to `sync`**

In `src/hal0/cli/capabilities_commands.py`, add a parameter to the `sync` signature
(after `dry_run`):

```python
    check: bool = typer.Option(
        False,
        "--check",
        help="Exit non-zero if the on-disk server_models.json differs from what the "
        "registry would generate (no write). For cron/healthcheck drift detection.",
    ),
```

Insert this block immediately AFTER `catalog = generate_server_models(registry)` and
BEFORE the summary `Table(...)`:

```python
    if check:
        import json as _json

        want = _json.dumps(catalog, indent=4, sort_keys=False) + "\n"
        try:
            have = output.read_text(encoding="utf-8")
        except FileNotFoundError:
            console.print(f"[red]drift[/red] — {output} does not exist.")
            raise typer.Exit(1)
        if have != want:
            console.print(
                f"[red]drift[/red] — {output} is stale; run `hal0 capabilities sync`."
            )
            raise typer.Exit(1)
        console.print(f"[green]in sync[/green] — {output} matches the registry.")
        raise typer.Exit(0)
```

(`want` mirrors `write_server_models`'s exact byte format — `json.dumps(catalog,
indent=4, sort_keys=False) + "\n"` — so a clean sync compares equal.)

- [ ] **Step 4: Run the test to confirm it passes**

Run: `python -m pytest tests/cli/test_capabilities_sync_check.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/hal0/cli/capabilities_commands.py tests/cli/test_capabilities_sync_check.py
git commit -m "feat(cli): add 'capabilities sync --check' drift guard

Compares the on-disk server_models.json against what the registry would
generate and exits 1 on drift (or missing file). Enables a cron/healthcheck
that surfaces catalog staleness instead of it failing silently.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## PART C — Ship + verify

### Task 6: Lint, PR, deploy to CT 105, verify end-to-end

- [ ] **Step 1: Lint (CI parity — ruff format check is fatal in CI)**

Run (per project memory `feedback_hal0_ci_ruff_format_check`):
```bash
cd ~/dev/hal0-wt-catalog-regen
ruff check src/hal0/registry/store.py src/hal0/api/__init__.py src/hal0/cli/capabilities_commands.py tests/registry/test_store_on_change.py tests/cli/test_capabilities_sync_check.py
ruff format --check .
```
Expected: both clean. If `format --check` fails, run `ruff format .` and amend.

- [ ] **Step 2: Push + open PR**

```bash
git push -u origin feat/registry-catalog-regen
gh pr create --fill --title "Regenerate server_models.json on registry mutation + drift guard"
```

- [ ] **Step 3: Wait for CI green, then merge** (do NOT `--auto`)

Per project memory `hal0_gh_pr_merge_auto_merges_immediately`: `--auto` does not wait for
CI on this repo. Poll until green, then merge:
```bash
gh pr checks --watch && gh pr merge --squash --delete-branch
```

- [ ] **Step 4: Deploy to CT 105 (rebuilds UI + restarts + healthcheck)**

Per project memory `hal0_ct105_deploy_rebuilds_ui` — use the deploy script, not a bare
`git reset`:
```bash
ssh hal0 'cd /opt/hal0 && git fetch origin && git checkout main && git pull && scripts/deploy.sh'
```
Expected: deploy completes with a passing healthcheck. (Coordinate via `wip hal0 status`
first — confirm no other session is mid-deploy.)

- [ ] **Step 5: End-to-end verify the hook works on the live runtime**

```bash
# Drift guard should report in-sync right after deploy (Part A Task 1 already synced):
ssh hal0 'cd /opt/hal0 && python -m hal0 capabilities sync --check'   # expect exit 0, "in sync"
# Prove the auto-regen: a register now updates the catalog WITHOUT a manual sync.
ssh hal0 'cd /opt/hal0 && python -m hal0 capabilities sync --check && echo "AUTO-SYNC OK"'
```
Expected: `in sync` / exit 0. (Optionally, do a tiny test pull and re-run `--check` to
confirm it stays green without a manual sync.)

- [ ] **Step 6: Release the wip claim**

```bash
~/.claude/bin/wip hal0 release
~/.claude/bin/wip release
```

---

## Rollback

- **Part A:** restore the backups from P2:
  `ssh hal0 'cp -a /var/lib/hal0/registry/registry.toml.bak-cleanup-2026-06-06 /var/lib/hal0/registry/registry.toml && cp -a /opt/lemonade/resources/server_models.json.bak-cleanup-2026-06-06 /opt/lemonade/resources/server_models.json'`
  (Symlink swaps in Task 2 are reversible by re-copying the blob; the blob is never touched.)
- **Part B:** revert the merge commit (`gh pr revert` / `git revert`) and re-deploy. The
  hook is best-effort and additive — reverting only restores the manual-sync behavior.

## Self-review notes (author check)

- **Spec coverage:** Catalog drift → Task 1 (immediate) + Task 4 (permanent). Dupes →
  Task 2. kokoro/moonshine → Task 3. "Harden so drift can't recur" → Task 4 + Task 5
  drift guard. Deploy on shared runtime → Part C with wip + deploy.sh. ✅
- **No physical reorg:** deliberately excluded per the 2026-06-06 decision (overlay gain
  is cosmetic; HF cache already pub/repo-organized; ~1% space). This plan is the
  agreed-on targeted scope.
- **Concurrency/locking:** `on_change` fires OUTSIDE `self._lock`; `write_server_models`
  reads `registry.toml` from disk (not the locked in-memory map), so no re-entrancy.
- **Bulk-scan note (YAGNI, not implemented):** a multi-model scan calls `add()` N times →
  N catalog regens (each ~ms on a 14 KB TOML / 50 KB JSON). Acceptable; debounce only if
  a future scan profiles hot. Logged here so it isn't a silent surprise.
- **Type consistency:** `on_change: Callable[[], None] | None`, `_notify_change(self)`,
  `_regen_server_models()`, `registry_file` (existing property), `write_server_models(
  registry_path, output_path)` — consistent across Tasks 4–6.
```
