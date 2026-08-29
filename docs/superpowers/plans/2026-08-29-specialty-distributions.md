# Specialty Model Distributions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A declarative specialty-distribution registry (PromptForge first consumer, #1946): companion-file pull, runner capability gating with loud degraded mode, env/argv synthesis, and honest capacity booking.

**Architecture:** One new code-declared registry module (`src/hal0/registry/specialty.py`), consumed at six existing seams: `role_of` classification, `_build_plan` companion carry, pull metadata stamping, `RunnerSupports`/`RUNNER_IMAGES`, the `_resolve_llama_scalars` choke point (guard + env synthesis), and `estimate_file_size_kv_mb` capacity. Companion paths ride `Model.metadata` exactly the way `Model.mmproj` rides the model row today.

**Tech Stack:** Python 3.12/3.13, pytest, Pydantic models in `registry/model.py`, SQLite `model_file` table (free-form TEXT `role` — no migration).

**Spec:** `docs/superpowers/specs/2026-08-29-specialty-distributions-design.md`

## Global Constraints

- `model_file.role` and `FileSetEntry.role` stay free-form `str` — **no DB migration**.
- Specialty absent ⇒ every existing path byte-identical (existing suites stay green untouched).
- Degraded is **never silent** (#1888): every degraded launch stamps a structured reason and logs.
- Operator `[server].env` always wins over synthesized env (merge-under, same rule as `gpu_visibility_env`).
- Launch/preview parity: all gating and synthesis lives in `_resolve_llama_scalars`, nowhere else.
- PromptForge card facts (verbatim from #1946): sidecars FFN/GDN/Output-K8 via `PROMPTFORGE_SIDECAR`/`PROMPTFORGE_GDN_SIDECAR`/`PROMPTFORGE_MTP_OUTPUT_K8_PROXY`; default context 262144; quant marker `ActiveFPX`; HIP-only image (`GGML_VULKAN=OFF`).
- Commit messages: Conventional Commits, subject ≤ 72 chars, reference #1946.
- Run tests with `uv run pytest <path> -v` from the repo root.

---

## PR 1 — registry, classification, pull

### Task 1: `specialty.py` registry module

**Files:**
- Create: `src/hal0/registry/specialty.py`
- Test: `tests/registry/test_specialty.py`

**Interfaces:**
- Consumes: nothing (leaf module; stdlib + `re` only — must NOT import `fileset.py`, which will import it).
- Produces:
  - `CompanionSpec(role: str, pattern: re.Pattern, env: str | None, required: bool = True)` frozen dataclass
  - `SpecialtyKind(key, quant_marker, companions, mode_env, degraded_ok=True, default_ctx=None, argv_profile=None)` frozen dataclass
  - `SPECIALTY_KINDS: dict[str, SpecialtyKind]` with the `"promptforge"` entry
  - `companion_role_of(filename: str) -> str | None` — first matching companion role across all kinds
  - `detect_specialty(paths: Iterable[str], quant: str | None = None) -> str | None`
  - `kind_for_role(role: str) -> SpecialtyKind | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/registry/test_specialty.py
"""Registry of specialty model distributions — spec 2026-08-29."""

from hal0.registry.specialty import (
    SPECIALTY_KINDS,
    companion_role_of,
    detect_specialty,
    kind_for_role,
)


class TestPromptforgeKind:
    def test_promptforge_registered(self):
        kind = SPECIALTY_KINDS["promptforge"]
        assert kind.key == "promptforge"
        assert kind.quant_marker == "ActiveFPX"
        assert kind.degraded_ok is True
        assert kind.default_ctx == 262144

    def test_promptforge_companion_envs(self):
        kind = SPECIALTY_KINDS["promptforge"]
        env_by_role = {c.role: c.env for c in kind.companions}
        assert env_by_role["promptforge_ffn"] == "PROMPTFORGE_SIDECAR"
        assert env_by_role["promptforge_gdn"] == "PROMPTFORGE_GDN_SIDECAR"
        assert env_by_role["promptforge_output_k8"] == "PROMPTFORGE_MTP_OUTPUT_K8_PROXY"
        # runtime patch installs but exports no env
        assert env_by_role["runtime_patch"] is None

    def test_kind_key_is_the_capability_token(self):
        # The guard compares SpecialtyKind.key against
        # RunnerSupports.specialties — there is no separate token field.
        kind = SPECIALTY_KINDS["promptforge"]
        assert not hasattr(kind, "runner_capability")


class TestCompanionRoleOf:
    def test_pfs_files_classify(self):
        assert companion_role_of("Qwen3.8-27B-v3-FFN.pfs") == "promptforge_ffn"
        assert companion_role_of("Qwen3.8-27B-v3-GDN.pfs") == "promptforge_gdn"
        assert (
            companion_role_of("Qwen3.8-v3-Output-K8.pfs")
            == "promptforge_output_k8"
        )

    def test_runtime_patch_classifies(self):
        assert (
            companion_role_of("qwen38-v3-output-k8-runtime.patch")
            == "runtime_patch"
        )

    def test_near_miss_returns_none(self):
        assert companion_role_of("model-Q8.gguf") is None
        assert companion_role_of("notes-about-pfs.md") is None
        assert companion_role_of("some.patch.txt") is None


class TestDetectSpecialty:
    def test_quant_marker_hit(self):
        paths = ["Qwen3.8-27B-CIRU-ActiveFPX-v3-Q8.gguf"]
        assert detect_specialty(paths) == "promptforge"

    def test_quant_param_hit(self):
        assert detect_specialty(["model.gguf"], quant="ActiveFPX") == "promptforge"

    def test_companion_presence_hit(self):
        paths = ["model-Q8.gguf", "model-FFN.pfs"]
        assert detect_specialty(paths) == "promptforge"

    def test_plain_repo_no_hit(self):
        paths = ["model-Q4_K_M.gguf", "mmproj-F16.gguf", "config.json"]
        assert detect_specialty(paths) is None


class TestKindForRole:
    def test_maps_role_back_to_kind(self):
        assert kind_for_role("promptforge_ffn").key == "promptforge"
        assert kind_for_role("mmproj") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/registry/test_specialty.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hal0.registry.specialty'`

- [ ] **Step 3: Write the module**

```python
# src/hal0/registry/specialty.py
"""SPECIALTY_KINDS — the specialty-distribution registry.

Some model distributions ship more than a GGUF: runtime companion files,
required environment variables, and a runner built a particular way
(#1946's CIRU ActiveFPX + PromptForge is the first). This module is a
CODE registry, the same philosophy as :data:`hal0.runners.RUNNER_IMAGES`:
shipping support for a kind means shipping code anyway, so the truth
lives here — importable, testable, no database table.

A kind's ``key`` doubles as the capability token a runner image must
list in ``RunnerSupports.specialties`` to run the accelerated path.
Leaf module by design: stdlib-only imports, because
:mod:`hal0.registry.fileset` imports *this* for classification.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass(frozen=True, slots=True)
class CompanionSpec:
    """One companion file a specialty distribution ships."""

    role: str  # model_file.role value, e.g. "promptforge_ffn"
    pattern: re.Pattern  # filename matcher (searched, case-insensitive)
    env: str | None  # env var receiving the installed path; None = install-only
    required: bool = True  # missing => specialty incomplete => degraded


@dataclass(frozen=True, slots=True)
class SpecialtyKind:
    """One specialty distribution the platform understands."""

    key: str  # doubles as the RunnerSupports.specialties token
    quant_marker: str | None  # matched against quant / model filename
    companions: tuple[CompanionSpec, ...]
    mode_env: Mapping[str, str] = field(default_factory=dict)
    degraded_ok: bool = True  # is a plain-GGUF fallback legitimate?
    default_ctx: int | None = None
    argv_profile: str | None = None  # seed profile carrying the card argv


SPECIALTY_KINDS: dict[str, SpecialtyKind] = {
    "promptforge": SpecialtyKind(
        key="promptforge",
        quant_marker="ActiveFPX",
        companions=(
            CompanionSpec(
                role="promptforge_ffn",
                pattern=re.compile(r"ffn[^/]*\.pfs$", re.IGNORECASE),
                env="PROMPTFORGE_SIDECAR",
            ),
            CompanionSpec(
                role="promptforge_gdn",
                pattern=re.compile(r"gdn[^/]*\.pfs$", re.IGNORECASE),
                env="PROMPTFORGE_GDN_SIDECAR",
            ),
            CompanionSpec(
                role="promptforge_output_k8",
                pattern=re.compile(r"output[-_]?k8[^/]*\.pfs$", re.IGNORECASE),
                env="PROMPTFORGE_MTP_OUTPUT_K8_PROXY",
            ),
            CompanionSpec(
                role="runtime_patch",
                pattern=re.compile(r"[^/]*runtime\.patch$", re.IGNORECASE),
                env=None,  # consumed by the image build; kept for provenance
                required=False,
            ),
        ),
        # Card's mode envs ride here once validated on ct150; empty until then.
        mode_env={},
        degraded_ok=True,
        default_ctx=262_144,
        argv_profile="promptforge",
    ),
}


def companion_role_of(filename: str) -> str | None:
    """Classify one filename into a companion role, or ``None``.

    First match across all kinds wins; patterns are anchored on the
    basename so a repo path or bare name classify identically.
    """
    name = PurePosixPath(filename).name
    for kind in SPECIALTY_KINDS.values():
        for spec in kind.companions:
            if spec.pattern.search(name):
                return spec.role
    return None


def kind_for_role(role: str) -> SpecialtyKind | None:
    """Map a companion role back to its owning kind."""
    for kind in SPECIALTY_KINDS.values():
        if any(spec.role == role for spec in kind.companions):
            return kind
    return None


def detect_specialty(paths: Iterable[str], quant: str | None = None) -> str | None:
    """Detect which specialty kind (if any) a file listing belongs to.

    Two independent signals, either suffices:
    1. the kind's ``quant_marker`` appears in ``quant`` or any filename;
    2. any *required* companion pattern matches a file.
    Never guesses: no signal => ``None``.
    """
    names = [PurePosixPath(p).name for p in paths]
    for kind in SPECIALTY_KINDS.values():
        marker = kind.quant_marker
        if marker and (
            (quant and marker.lower() in quant.lower())
            or any(marker.lower() in n.lower() for n in names)
        ):
            return kind.key
        for spec in kind.companions:
            if spec.required and any(spec.pattern.search(n) for n in names):
                return kind.key
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/registry/test_specialty.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/hal0/registry/specialty.py tests/registry/test_specialty.py
git commit -m "feat(registry): specialty-distribution registry, promptforge first entry (#1946)"
```

---

### Task 2: `role_of` learns companion roles

**Files:**
- Modify: `src/hal0/registry/fileset.py:311` (`role_of`)
- Test: `tests/registry/test_specialty_fileset.py` (create)

**Interfaces:**
- Consumes: `companion_role_of` from Task 1.
- Produces: `role_of(rel) -> str` now returns companion roles (`promptforge_ffn` etc.) for matching files; everything else unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/registry/test_specialty_fileset.py
"""Specialty companions through the fileset classifier/planner."""

from hal0.registry.fileset import role_of


class TestRoleOfCompanions:
    def test_pfs_sidecars(self):
        assert role_of("Qwen3.8-27B-v3-FFN.pfs") == "promptforge_ffn"
        assert role_of("sub/dir/Qwen3.8-27B-v3-GDN.pfs") == "promptforge_gdn"
        assert role_of("Qwen3.8-v3-Output-K8.pfs") == "promptforge_output_k8"

    def test_runtime_patch(self):
        assert role_of("runtime/qwen38-v3-output-k8-runtime.patch") == "runtime_patch"

    def test_existing_roles_unchanged(self):
        # regression pins: today's classifications must not move
        assert role_of("mmproj-model-F16.gguf") == "mmproj"
        assert role_of("model-Q4_K_M.gguf") == "model"
        assert role_of("tokenizer.json") == "tokenizer"
        assert role_of("config.json") == "config"
        assert role_of("README.md") == "config"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/registry/test_specialty_fileset.py -v`
Expected: FAIL — `.pfs`/`.patch` names return `"config"`

- [ ] **Step 3: Extend `role_of`**

In `src/hal0/registry/fileset.py`, add the import near the other hal0 imports:

```python
from hal0.registry.specialty import companion_role_of
```

Then in `role_of` (line ~311), insert the companion check AFTER the mmproj
check and BEFORE the shard check (mmproj name-token rule stays highest so a
hypothetical `mmproj-*.pfs` keeps classifying as it does today):

```python
    name = PurePosixPath(rel).name
    lowered = name.lower()
    if "mmproj" in lowered:
        return "mmproj"
    companion = companion_role_of(name)
    if companion is not None:
        return companion
    if SHARD_RE.match(name):
        return "shard"
    # ... rest unchanged ...
```

Extend the docstring's role list to mention specialty companion roles from
`SPECIALTY_KINDS`.

- [ ] **Step 4: Run new + existing fileset tests**

Run: `uv run pytest tests/registry/test_specialty_fileset.py tests/registry/ -v`
Expected: PASS (new tests plus every existing registry test)

- [ ] **Step 5: Commit**

```bash
git add src/hal0/registry/fileset.py tests/registry/test_specialty_fileset.py
git commit -m "feat(registry): role_of classifies specialty companion files (#1946)"
```

---

### Task 3: `_build_plan` carries companions into the plan

**Files:**
- Modify: `src/hal0/registry/fileset.py` (`_build_plan`, ~line 370; `FileSetPlan`, line 132)
- Test: `tests/registry/test_specialty_fileset.py` (extend)

**Interfaces:**
- Consumes: companion roles from Task 2; `detect_specialty` from Task 1.
- Produces: `FileSetPlan.specialty: str | None = None` field; companion `FileSetEntry` rows in `plan.files` (counted in `total_bytes`, digest-verified by the existing pull machinery).

- [ ] **Step 1: Write the failing tests**

Append to `tests/registry/test_specialty_fileset.py`. Follow the exact
construction pattern existing `_build_plan` tests use (see
`tests/registry/` for the `RawTreeEntry` fixture shape — `path`, `size`,
`lfs_oid`, `lfs_size`):

```python
from hal0.registry.fileset import RawTreeEntry, _build_plan


def _e(path, size=100, oid=None):
    return RawTreeEntry(path=path, size=size, lfs_oid=oid, lfs_size=size if oid else None)


class TestBuildPlanCompanions:
    def test_companions_join_the_plan(self):
        entries = [
            _e("Qwen-ActiveFPX-v3-Q8.gguf", size=15_000, oid="a" * 64),
            _e("Qwen-v3-FFN.pfs", size=17_100, oid="b" * 64),
            _e("Qwen-v3-GDN.pfs", size=4_000, oid="c" * 64),
            _e("Qwen-v3-Output-K8.pfs", size=700, oid="d" * 64),
            _e("runtime/qwen38-v3-output-k8-runtime.patch", size=10),
        ]
        plan = _build_plan(entries, repo="jcbtc/qwen", revision="main")
        roles = {f.role for f in plan.files}
        assert {"model", "promptforge_ffn", "promptforge_gdn",
                "promptforge_output_k8", "runtime_patch"} <= roles
        assert plan.specialty == "promptforge"
        # companion bytes counted
        assert plan.total_bytes == 15_000 + 17_100 + 4_000 + 700 + 10

    def test_plain_repo_unaffected(self):
        entries = [_e("model-Q4_K_M.gguf", size=5_000, oid="a" * 64)]
        plan = _build_plan(entries, repo="x/y", revision="main")
        assert plan.specialty is None
        assert [f.role for f in plan.files] == ["model"]

    def test_stray_pfs_in_plain_repo_not_installed(self):
        # No quant marker, no required-companion FULL set — but one lone
        # pattern hit DOES trigger detection (companion presence is a
        # signal); the point of this test is the inverse: a repo whose only
        # oddity is a .patch (required=False) detects nothing.
        entries = [
            _e("model-Q4_K_M.gguf", size=5_000, oid="a" * 64),
            _e("build-runtime.patch", size=10),
        ]
        plan = _build_plan(entries, repo="x/y", revision="main")
        assert plan.specialty is None
        assert [f.role for f in plan.files] == ["model"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/registry/test_specialty_fileset.py -v -k Companions`
Expected: FAIL — `FileSetPlan` has no `specialty`; `.pfs` entries are dropped

- [ ] **Step 3: Implement**

1. Add to `FileSetPlan` (line ~132), after `mmproj_tiebreak_reason`:

```python
    specialty: str | None = None  # SPECIALTY_KINDS key, when detected
```

2. Extend the Task 2 import line: `from hal0.registry.specialty import companion_role_of, detect_specialty`.

3. In `_build_plan`'s classification loop, add a companion bucket. The loop
currently routes roles into `model/mmproj/tokenizer/config` lists; declare:

```python
    companion_files: list[tuple[str, RawTreeEntry]] = []  # (role, entry)
```

and in the loop, before the `else: config_files.append(e)` fallthrough:

```python
        elif role not in ("model", "shard", "mmproj", "tokenizer", "config"):
            companion_files.append((role, e))
```

4. After the tokenizer/config same-dir carry block (end of `_build_plan`'s
file assembly, ~line 530), append companions — repo-wide, NOT same-dir-only
(the card ships the patch under `runtime/`):

```python
    # ── specialty companion carry (spec 2026-08-29, #1946) ────────────────
    specialty = detect_specialty(
        [e.path for e in entries],
        quant=quant_from_filename(PurePosixPath(entry_rel).name),
    )
    if specialty is not None:
        for comp_role, e in companion_files:
            files.append(
                FileSetEntry(
                    rel=e.path,
                    size_bytes=_entry_bytes(e),
                    lfs_sha256=e.lfs_oid,
                    role=comp_role,
                    shard_index=None,
                )
            )
```

If `specialty is None` companions are NOT carried (a stray companion-shaped
file in a normal repo stays uninstalled — never guess).

5. Thread `specialty=specialty` into the `FileSetPlan(...)` constructor call
at the end of `_build_plan`. Check how `total_bytes` is computed (grep
`total_bytes` in the function/caller) — if it sums `files`, nothing more; if
it's computed earlier, recompute after the companion append.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/registry/test_specialty_fileset.py tests/registry/ -v`
Expected: PASS, existing registry suite green

- [ ] **Step 5: Commit**

```bash
git add src/hal0/registry/fileset.py tests/registry/test_specialty_fileset.py
git commit -m "feat(registry): fileset plan detects and carries specialty companions (#1946)"
```

---

### Task 4: pull stamps `metadata.specialty` + `metadata.companions` (+ sizes)

**Files:**
- Modify: `src/hal0/registry/pull.py` (`_register_pulled_fileset`, line ~1486)
- Test: `tests/registry/test_specialty_pull.py` (create)

**Interfaces:**
- Consumes: `FileSetPlan.specialty` from Task 3; companion roles in the plan's files.
- Produces: model row `metadata["specialty"] = "<kind key>"`, `metadata["companions"] = {role: absolute_dest_path}`, `metadata["companion_sizes"] = {role: size_bytes}` — the exact shapes Task 7 (env) and Task 9 (capacity) read. Mirrors how `updates["mmproj"] = str(mmproj_dest)` is stamped at `pull.py:1536`. Also fills `quant` if unset (#1890, fill-only-if-unset like `chat_template`).

- [ ] **Step 1: Write the failing test**

Find the existing `_register_pulled_fileset` test (grep
`tests/registry/ -rn "_register_pulled_fileset"`) and copy its fixture
pattern (registry construction, plan build, installed paths). New content:

```python
# tests/registry/test_specialty_pull.py
"""Pull-time stamping of specialty metadata — spec 2026-08-29."""
# fixtures copied from the existing _register_pulled_fileset test


def test_fileset_pull_stamps_specialty_metadata(tmp_path, registry_fixture):
    # plan: model + 3 pfs companions with dests under tmp_path, built with
    # specialty="promptforge" and files carrying the promptforge_* roles;
    # run the register step the way the neighboring test does.
    ...
    model = registry_fixture.get(model_id)
    assert model.metadata["specialty"] == "promptforge"
    comps = model.metadata["companions"]
    assert set(comps) == {"promptforge_ffn", "promptforge_gdn", "promptforge_output_k8"}
    for p in comps.values():
        assert p.startswith(str(tmp_path))  # absolute installed dest
    sizes = model.metadata["companion_sizes"]
    assert set(sizes) == set(comps)
    assert all(isinstance(v, int) and v > 0 for v in sizes.values())
    assert model.quant == "ActiveFPX"  # filled because it was unset


def test_plain_pull_stamps_nothing(tmp_path, registry_fixture):
    ...
    model = registry_fixture.get(model_id)
    assert "specialty" not in model.metadata
    assert "companions" not in model.metadata
```

(The `...` bodies are fixture plumbing copied verbatim from the neighboring
test; the assertions above are the complete new content.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/registry/test_specialty_pull.py -v`
Expected: FAIL — `KeyError: 'specialty'`

- [ ] **Step 3: Implement in `_register_pulled_fileset`**

Add imports: `from hal0.registry.specialty import SPECIALTY_KINDS, kind_for_role`.

The function iterates installed files to record `model_file` rows — role,
dest, and size are all in hand in that loop. Accumulate there:

```python
    companion_paths: dict[str, str] = {}
    companion_sizes: dict[str, int] = {}
    # inside the per-installed-file loop:
    #   f: the plan FileSetEntry (or InstalledFile) carrying .role/.size_bytes
    #   dest: the absolute installed path
    kind = kind_for_role(f.role)
    if kind is not None:
        spec = next(s for s in kind.companions if s.role == f.role)
        if spec.env is not None:  # runtime_patch (env=None) installs but
            companion_paths[f.role] = str(dest)  # never feeds the launcher
            companion_sizes[f.role] = int(f.size_bytes)
```

Then where `merged_meta`/`updates["metadata"]` is assembled (same block that
sets `updates["mmproj"]`, line ~1536):

```python
    if fileset.specialty is not None:
        merged_meta = {
            **merged_meta,
            "specialty": fileset.specialty,
            "companions": companion_paths,
            "companion_sizes": companion_sizes,
        }
        kind = SPECIALTY_KINDS.get(fileset.specialty)
        if kind is not None and kind.quant_marker and not existing_quant:
            updates["quant"] = kind.quant_marker  # #1890, fill-only-if-unset
```

(`existing_quant` = however the function reads the pre-existing row's fields
— follow the `chat_template` fill-only-if-unset pattern in the same
function.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/registry/test_specialty_pull.py tests/registry/ -v`
Expected: PASS, existing suite green

- [ ] **Step 5: Commit**

```bash
git add src/hal0/registry/pull.py tests/registry/test_specialty_pull.py
git commit -m "feat(registry): pull stamps specialty + companion paths on the model row (#1946)"
```

---

## PR 2 — runner key, gate, env synthesis

### Task 5: `RunnerSupports.specialties` + `promptforge` runner

**Files:**
- Modify: `src/hal0/runners/__init__.py` (`RunnerSupports` line 79, `RUNNER_IMAGES` line 119)
- Modify: `src/hal0/config/schema.py` (beside `DEFAULT_ROCMFPX_IMAGE`, line ~1030)
- Modify: `manifest.json` (`toolbox_images`)
- Test: `tests/runners/test_specialty_runner.py` (create; if `tests/runners/` does not exist, put the file where the existing `RUNNER_IMAGES` tests live — grep `tests -rn "RUNNER_IMAGES"`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `RunnerSupports.specialties: tuple[str, ...] = ()`; `RUNNER_IMAGES["promptforge"]`; `DEFAULT_PROMPTFORGE_IMAGE` in `schema.py`. Task 6's guard reads `runner.supports.specialties`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/runners/test_specialty_runner.py
from hal0.runners import RUNNER_IMAGES


def test_promptforge_runner_registered():
    r = RUNNER_IMAGES["promptforge"]
    assert r.runtime_family == "llama-server"
    assert r.device_class == "gpu"
    assert r.backend == "rocm"
    assert r.supported_backends == ("rocm",)  # HIP-only: no vulkan
    assert r.format_arch == "gguf"
    assert r.manifest_key == "promptforge"
    assert r.supports.specialties == ("promptforge",)
    assert r.supports.mtp is True


def test_existing_runners_have_empty_specialties():
    for key in ("rocmfpx", "vulkanfpx", "cuda", "cpu"):
        assert RUNNER_IMAGES[key].supports.specialties == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/runners/test_specialty_runner.py -v`
Expected: FAIL — `KeyError: 'promptforge'`

- [ ] **Step 3: Implement**

1. `src/hal0/config/schema.py`, directly under `DEFAULT_ROCMFPX_IMAGE`
(line 1030), comment block in the neighbor's voice:

```python
#: ``DEFAULT_PROMPTFORGE_IMAGE`` — the PromptForge-flavor runner (HIP-only
#: build of ciru-ai/ROCmFPX v2.3 + the card's runtime patch; GGML_VULKAN=OFF,
#: so it can never be the same image as DEFAULT_ROCMFPX_IMAGE — see #1946
#: item 4 and spec 2026-08-29). Candidate tag until the ct150 validation
#: gate (#1891) passes; the pin only moves operator-gated.
DEFAULT_PROMPTFORGE_IMAGE = "ghcr.io/hal0ai/hal0-promptforge:v2.3-qwen38"
```

2. `src/hal0/runners/__init__.py` — add to `RunnerSupports`:

```python
    #: Specialty-distribution keys this image can execute accelerated
    #: (matched against Model.metadata["specialty"]; see
    #: hal0.registry.specialty.SPECIALTY_KINDS). Empty = plain runner.
    specialties: tuple[str, ...] = ()
```

3. Import `DEFAULT_PROMPTFORGE_IMAGE` beside the `DEFAULT_ROCMFPX_IMAGE`
import; add after `"vulkanfpx"` in `RUNNER_IMAGES`:

```python
    "promptforge": Runner(
        "promptforge",
        DEFAULT_PROMPTFORGE_IMAGE,
        "llama-server",
        RunnerSupports(mtp=True, jinja=True, mmproj=True,
                       specialties=("promptforge",)),
        "gpu",
        "rocm",
        "promptforge",
        supported_backends=("rocm",),  # HIP-only build: deliberately NOT
        # vulkan — the existing (device, BINARY) fit-check refuses a
        # gpu-vulkan pairing with no new code.
        format_arch="gguf",
    ),
```

4. `manifest.json` — add to `toolbox_images` (same value shape as the
`rocm` entry): `"promptforge": "ghcr.io/hal0ai/hal0-promptforge:v2.3-qwen38"`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/runners/test_specialty_runner.py -v && uv run pytest tests -k "runner" -q`
Expected: PASS; existing runner suites green

- [ ] **Step 5: Commit**

```bash
git add src/hal0/runners/__init__.py src/hal0/config/schema.py manifest.json tests/runners/test_specialty_runner.py
git commit -m "feat(runners): promptforge runner key + RunnerSupports.specialties (#1946)"
```

---

### Task 6: `_guard_specialty_runner` — gate with loud degraded mode

**Files:**
- Modify: `src/hal0/providers/container.py` (new function beside `_guard_fpx_quant_runner:1603`; call + scalar key in `_resolve_llama_scalars:1717`)
- Test: `tests/providers/test_specialty_guard.py` (create; fixture style from `tests/providers/test_container_mmproj.py`)

**Interfaces:**
- Consumes: `SPECIALTY_KINDS` (Task 1), `runner.supports.specialties` (Task 5), `model_info["metadata"]["specialty"]` / `["companions"]` (Task 4 shape).
- Produces:
  - `_guard_specialty_runner(model_info, runner) -> dict[str, Any] | None` — `None` (accelerated OK) or degraded-reason dict `{"code": "slot.specialty_degraded", "specialty": ..., "runner": ..., "detail": ...}`; raises `UnprocessableEntity(code="slot.unsupported_specialty_for_runner")` when `degraded_ok=False`.
  - `_resolve_llama_scalars` return dict gains `"specialty_degraded": dict | None` (Task 7 skips env synthesis on it; Task 10 surfaces it).

- [ ] **Step 1: Write the failing tests**

```python
# tests/providers/test_specialty_guard.py
import pytest

from hal0.errors import UnprocessableEntity
from hal0.providers.container import _guard_specialty_runner
from hal0.runners import RUNNER_IMAGES


def _pf_model(companions=None):
    comps = companions if companions is not None else {
        "promptforge_ffn": "/var/lib/hal0/models/m/ffn.pfs",
        "promptforge_gdn": "/var/lib/hal0/models/m/gdn.pfs",
        "promptforge_output_k8": "/var/lib/hal0/models/m/k8.pfs",
    }
    return {
        "_model_key": "qwen-pf",
        "metadata": {"specialty": "promptforge", "companions": comps},
    }


def test_capable_runner_passes():
    assert _guard_specialty_runner(_pf_model(), RUNNER_IMAGES["promptforge"]) is None


def test_incapable_runner_degrades_with_reason():
    reason = _guard_specialty_runner(_pf_model(), RUNNER_IMAGES["rocmfpx"])
    assert reason["code"] == "slot.specialty_degraded"
    assert reason["specialty"] == "promptforge"
    assert reason["runner"] == "rocmfpx"


def test_missing_required_companion_degrades_even_on_capable_runner():
    model = _pf_model(companions={"promptforge_ffn": "/x/ffn.pfs"})  # gdn+k8 missing
    reason = _guard_specialty_runner(model, RUNNER_IMAGES["promptforge"])
    assert reason["code"] == "slot.specialty_degraded"
    assert "promptforge_gdn" in reason["detail"]


def test_plain_model_is_untouched():
    model = {"_model_key": "plain", "metadata": {}}
    assert _guard_specialty_runner(model, RUNNER_IMAGES["cpu"]) is None


def test_unknown_specialty_degrades_not_crashes():
    model = {"_model_key": "future", "metadata": {"specialty": "hyperdrive-v9"}}
    reason = _guard_specialty_runner(model, RUNNER_IMAGES["rocmfpx"])
    assert reason["code"] == "slot.specialty_degraded"


def test_degraded_not_ok_raises_422(monkeypatch):
    import dataclasses
    import hal0.registry.specialty as sp
    strict = dataclasses.replace(sp.SPECIALTY_KINDS["promptforge"], degraded_ok=False)
    monkeypatch.setitem(sp.SPECIALTY_KINDS, "promptforge", strict)
    with pytest.raises(UnprocessableEntity) as exc:
        _guard_specialty_runner(_pf_model(), RUNNER_IMAGES["rocmfpx"])
    assert exc.value.code == "slot.unsupported_specialty_for_runner"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_specialty_guard.py -v`
Expected: FAIL — `ImportError: cannot import name '_guard_specialty_runner'`

- [ ] **Step 3: Implement**

Add after `_guard_fpx_quant_runner` (`container.py`, after line ~1655),
docstring in its neighbor's voice:

```python
def _guard_specialty_runner(
    model_info: dict[str, Any],
    runner: Any,
) -> dict[str, Any] | None:
    """Gate a specialty-distribution model against the resolved runner.

    Spec 2026-08-29 / #1946: a specialty model (e.g. CIRU ActiveFPX +
    PromptForge) runs ACCELERATED only on a runner image that lists the
    kind in ``RunnerSupports.specialties`` AND has every required
    companion installed. Anything else is the card-blessed GGUF-only mode
    — legitimate, but NEVER silent (#1888): this returns a structured
    degraded reason the launch path stamps on the slot, shows in the
    drawer/health, and logs. Kinds that declare ``degraded_ok=False``
    hard-refuse (422) exactly like :func:`_guard_fpx_quant_runner`.

    Returns ``None`` when the accelerated path is clear, else the reason
    dict. Called from the single choke point
    (:func:`_resolve_llama_scalars`) both launch and preview share.
    """
    from hal0.registry.specialty import SPECIALTY_KINDS

    meta = (model_info or {}).get("metadata") or {}
    key = meta.get("specialty")
    if not key:
        return None
    model_key = str(model_info.get("_model_key") or model_info.get("id") or "?")
    kind = SPECIALTY_KINDS.get(key)
    if kind is None:
        # Row stamped by a newer hal0 than this one — degrade, don't crash.
        log.warning("slot launch degraded: model %s unknown specialty %r", model_key, key)
        return {
            "code": "slot.specialty_degraded",
            "specialty": str(key),
            "runner": getattr(runner, "key", None),
            "detail": f"unknown specialty kind {key!r} (newer registry?)",
        }

    def _degrade_or_raise(detail: str) -> dict[str, Any]:
        if not kind.degraded_ok:
            raise UnprocessableEntity(
                f"model {model_key!r} is a {key!r} specialty distribution with no "
                f"legitimate fallback mode; the resolved runner "
                f"{getattr(runner, 'key', '?')!r} cannot serve it — {detail}",
                code="slot.unsupported_specialty_for_runner",
                details={"model": model_key, "specialty": key,
                         "runner": getattr(runner, "key", None)},
            )
        log.warning(
            "slot launch degraded: model %s specialty %s — %s", model_key, key, detail
        )
        return {
            "code": "slot.specialty_degraded",
            "specialty": key,
            "runner": getattr(runner, "key", None),
            "detail": detail,
        }

    if key not in getattr(runner.supports, "specialties", ()):
        return _degrade_or_raise(
            f"runner {getattr(runner, 'key', '?')!r} does not list specialty "
            f"{key!r}; launching GGUF-only"
        )
    companions = meta.get("companions") or {}
    missing = [
        spec.role
        for spec in kind.companions
        if spec.required and spec.env is not None and spec.role not in companions
    ]
    if missing:
        return _degrade_or_raise(
            f"required companion files missing from the store: {missing}; "
            "re-pull the model to install them"
        )
    return None
```

Wire into `_resolve_llama_scalars` after the existing guard call
(line ~1751) — NOT gated on `for_launch` (the reason must render in preview
too; resolved values stay identical on both paths, preserving parity):

```python
    if for_launch:
        _guard_fpx_quant_runner(slot_cfg, model_info, runner)
    specialty_degraded = _guard_specialty_runner(model_info, runner)
```

and add to the return dict (line ~2019):

```python
        "specialty_degraded": specialty_degraded,
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/providers/test_specialty_guard.py tests/providers/ -q`
Expected: PASS; providers suite green

- [ ] **Step 5: Commit**

```bash
git add src/hal0/providers/container.py tests/providers/test_specialty_guard.py
git commit -m "feat(providers): specialty runner gate with loud degraded mode (#1946)"
```

---

### Task 7: env synthesis + operator-wins merge

**Files:**
- Modify: `src/hal0/registry/specialty.py` (add `specialty_env_for`)
- Modify: `src/hal0/providers/container.py` (`_resolve_llama_scalars` env assembly ~line 1902; `container_spec` merge site line 2159; check the quadlet render path ~line 3098 and `_llama_launch_plan` env param line 1121 for the same merge)
- Test: `tests/providers/test_specialty_guard.py` (extend)

**Interfaces:**
- Consumes: Task 6's `specialty_degraded` scalar; Task 4's `metadata.companions`; `CompanionSpec.env` + `SpecialtyKind.mode_env` (Task 1).
- Produces:
  - `specialty_env_for(metadata: Mapping) -> dict[str, str]` — `{env_var: path}` + `mode_env`; empty dict when no/unknown specialty or no companions.
  - `_resolve_llama_scalars` scalar `"specialty_env": dict[str, str]` (empty when degraded or plain).
  - `container_spec` merge order: `{**vis_env, **scalars["specialty_env"], **server_env}` — operator last, operator wins.

- [ ] **Step 1: Write the failing tests**

Append to `tests/providers/test_specialty_guard.py`:

```python
from hal0.registry.specialty import specialty_env_for


class TestSpecialtyEnv:
    def test_env_synthesized_from_companions(self):
        meta = _pf_model()["metadata"]
        env = specialty_env_for(meta)
        assert env["PROMPTFORGE_SIDECAR"] == "/var/lib/hal0/models/m/ffn.pfs"
        assert env["PROMPTFORGE_GDN_SIDECAR"] == "/var/lib/hal0/models/m/gdn.pfs"
        assert env["PROMPTFORGE_MTP_OUTPUT_K8_PROXY"] == "/var/lib/hal0/models/m/k8.pfs"

    def test_plain_metadata_empty(self):
        assert specialty_env_for({}) == {}
        assert specialty_env_for({"specialty": "promptforge"}) == {}  # no companions
```

Plus two integration tests in the style of the existing
`_resolve_llama_scalars` tests (find via
`grep -rn "_resolve_llama_scalars" tests/providers/`; copy their
slot_cfg/model_info/profile fixtures — the assertions below are the new
content):

```python
def test_scalars_carry_specialty_env_only_when_accelerated(...):
    # capable runner (slot binary "promptforge") -> scalars["specialty_env"]
    #   has the three PROMPTFORGE_* keys and scalars["specialty_degraded"] is None
    # incapable runner (slot binary "rocmfpx") -> scalars["specialty_env"] == {}
    #   and scalars["specialty_degraded"]["code"] == "slot.specialty_degraded"
    ...


def test_operator_server_env_wins(...):
    # slot [server].env = {"PROMPTFORGE_SIDECAR": "/operator/override.pfs"}
    # on the capable runner -> the merged plan env carries the operator value
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_specialty_guard.py -v -k "Env or env"`
Expected: FAIL — `ImportError: specialty_env_for`

- [ ] **Step 3: Implement**

1. `src/hal0/registry/specialty.py`:

```python
def specialty_env_for(metadata: Mapping[str, object]) -> dict[str, str]:
    """Synthesize the env block for a specialty model's accelerated launch.

    ``{CompanionSpec.env: installed path}`` for every companion present in
    ``metadata["companions"]``, plus the kind's static ``mode_env``.
    Empty dict for plain models, unknown kinds, or missing companions —
    the caller gates completeness via the guard, this never raises.
    """
    key = metadata.get("specialty")
    kind = SPECIALTY_KINDS.get(key) if isinstance(key, str) else None
    companions = metadata.get("companions")
    if kind is None or not isinstance(companions, Mapping) or not companions:
        return {}
    env: dict[str, str] = {}
    for spec in kind.companions:
        if spec.env is None:
            continue
        path = companions.get(spec.role)
        if isinstance(path, str) and path:
            env[spec.env] = path
    env.update(kind.mode_env)
    return env
```

2. `container.py`, in `_resolve_llama_scalars` after the `server_env` block
(line ~1902):

```python
    # ── specialty env (spec 2026-08-29, #1946) — synthesized only on the
    # accelerated path; a degraded launch gets NO specialty env so the
    # runner behaves exactly like a plain GGUF load.
    if specialty_degraded is None:
        from hal0.registry.specialty import specialty_env_for

        specialty_env = specialty_env_for((model_info or {}).get("metadata") or {})
    else:
        specialty_env = {}
```

add `"specialty_env": specialty_env,` to the return dict.

3. `container_spec` merge (line ~2159):

```python
        vis_env = gpu_visibility_env(scalars["device"], scalars["gpu_index"])
        server_env = scalars["server_env"] or {}
        merged_env = {**vis_env, **scalars["specialty_env"], **server_env}
```

Companion paths are host paths; the model store is mounted identical-path
read-only into the container (`model_mount_roots`, `container.py:1162`), so
no path translation — same reason `--mmproj` passes the host path today.
Apply the same three-way merge at every other point `server_env` is merged
(quadlet render path ~3098, `_llama_launch_plan` env at 1121 if it merges
independently — grep `server_env` in the file and cover each).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/providers/ -q`
Expected: PASS (new + full providers suite)

- [ ] **Step 5: Commit**

```bash
git add src/hal0/registry/specialty.py src/hal0/providers/container.py tests/providers/test_specialty_guard.py
git commit -m "feat(providers): synthesize specialty env under operator [server].env (#1946)"
```

---

### Task 8: seed profile + default context

**Files:**
- Modify: `src/hal0/config/schema.py` (`SEED_PROFILES` — grep `SEED_PROFILES =`; add `promptforge` beside the existing GPU seeds, mirroring a neighbor's exact `ProfileConfig` field shape)
- Modify: `src/hal0/providers/container.py` (`_resolve_context_size` chain, line ~1479)
- Test: `tests/providers/test_specialty_guard.py` (extend) + the suite that pins seed profiles (grep `tests -rn "SEED_PROFILES"`)

**Interfaces:**
- Consumes: `SpecialtyKind.default_ctx` / `argv_profile` (Task 1).
- Produces: seed profile `promptforge` (flags `-b 2048 -ub 2048 -fa on -ctk f16 -ctv f16 --no-cache-prompt`, `mtp=True`, `backend="rocm"`, `device_class="gpu"`, `quant="ActiveFPX"`, intent naming #1946); context resolution honors `default_ctx=262144` below `defaults.context_size`, above the GGUF-arch fallback, still slot-ceiling clamped.

- [ ] **Step 1: Write the failing tests**

```python
def test_promptforge_seed_profile_exists():
    from hal0.config.schema import SEED_PROFILES
    prof = SEED_PROFILES["promptforge"]
    assert "--no-cache-prompt" in prof.flags
    assert "-fa on" in prof.flags
    assert prof.mtp is True
```

Plus two context tests using the existing `resolve_effective_context_size` /
`_resolve_context_size` test fixtures (grep
`tests -rn "resolve_effective_context_size"`; assertions are the new
content):

```python
def test_specialty_default_ctx_used_when_model_has_none(...):
    # metadata.specialty=promptforge, no defaults.context_size, no
    # metadata.context_length, generous slot ceiling -> 262144
    ...


def test_model_defaults_context_size_still_wins(...):
    # defaults.context_size=8192 -> 8192, specialty ignored
    ...
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests -k "promptforge_seed or specialty_default_ctx" -v`
Expected: FAIL — `KeyError: 'promptforge'`

- [ ] **Step 3: Implement**

1. Seed profile in `schema.py` (mirror a neighboring GPU seed's exact
constructor shape; drop kwargs `ProfileConfig` lacks):

```python
    "promptforge": ProfileConfig(
        flags="-b 2048 -ub 2048 -fa on -ctk f16 -ctv f16 --no-cache-prompt",
        mtp=True,
        device_class="gpu",
        backend="rocm",
        quant="ActiveFPX",
        intent="CIRU ActiveFPX + PromptForge accelerated serving (#1946)",
    ),
```

2. Context: read `_resolve_context_size` (line ~1479) first; insert the
specialty default at the precedence point between the model-defaults read
and the arch/native fallback, reusing the existing clamp path (do NOT
duplicate clamping):

```python
    # specialty default_ctx (spec 2026-08-29): the card's intended window —
    # below an explicit defaults.context_size, above the GGUF-arch fallback.
    meta = (model_info or {}).get("metadata") or {}
    kind = SPECIALTY_KINDS.get(meta.get("specialty") or "")
    if kind is not None and kind.default_ctx:
        # feed kind.default_ctx into the chain exactly where
        # metadata.context_length would have been read
        ...
```

Use a lazy import (`from hal0.registry.specialty import SPECIALTY_KINDS`)
matching Task 6/7's pattern in this module.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/providers/ -q && uv run pytest tests -k "seed or profile" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hal0/config/schema.py src/hal0/providers/container.py tests/
git commit -m "feat(profiles): promptforge seed profile + specialty default context (#1946)"
```

---

## PR 3 — capacity, surface

### Task 9: capacity books companion bytes + specialty ctx

**Files:**
- Modify: `src/hal0/slots/capacity.py` (`estimate_file_size_kv_mb:134`, `_ctx_tokens_for:150`)
- Modify: call sites — grep `src -rn "estimate_file_size_kv_mb("` (notably `hal0/slots/preload_evict.py` and `build_per_slot`'s path 3) to pass `companion_mb=companion_bytes_mb(model_meta)`
- Test: `tests/slots/test_specialty_capacity.py` (create; fixture style from the existing capacity tests — grep `tests -rn "estimate_file_size_kv_mb"`)

**Interfaces:**
- Consumes: `metadata.specialty` / `metadata.companion_sizes` (Task 4), `SpecialtyKind.default_ctx` (Task 1).
- Produces: `companion_bytes_mb(model_meta: dict | None) -> float`; `estimate_file_size_kv_mb(model_mb, ctx_meta, *, companion_mb: float = 0.0)` (default keeps every existing caller byte-identical); `_ctx_tokens_for` honors specialty `default_ctx` between `defaults.context_size` and `metadata.context_length`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/slots/test_specialty_capacity.py
from hal0.slots.capacity import (
    _ctx_tokens_for,
    companion_bytes_mb,
    estimate_file_size_kv_mb,
)

PF_META = {
    "metadata": {
        "specialty": "promptforge",
        "companions": {
            "promptforge_ffn": "/m/ffn.pfs",
            "promptforge_gdn": "/m/gdn.pfs",
            "promptforge_output_k8": "/m/k8.pfs",
        },
        # card sizes: 17.1 GB + 4.0 GB + 0.7 GB
        "companion_sizes": {
            "promptforge_ffn": 17_100_000_000,
            "promptforge_gdn": 4_000_000_000,
            "promptforge_output_k8": 700_000_000,
        },
    }
}


def test_companion_bytes_summed():
    mb = companion_bytes_mb(PF_META)
    assert 20_000 < mb < 21_500  # 21.8e9 bytes ≈ 20790 MiB


def test_plain_model_zero():
    assert companion_bytes_mb({"metadata": {}}) == 0.0
    assert companion_bytes_mb(None) == 0.0


def test_estimate_includes_companions():
    base = estimate_file_size_kv_mb(15_000.0, PF_META["metadata"])
    with_comp = estimate_file_size_kv_mb(
        15_000.0, PF_META["metadata"], companion_mb=companion_bytes_mb(PF_META)
    )
    assert with_comp > base + 20_000


def test_specialty_ctx_default():
    assert _ctx_tokens_for(PF_META["metadata"]) == 262_144


def test_explicit_defaults_context_size_still_wins():
    meta = {"defaults": {"context_size": 8192}, **PF_META["metadata"]}
    assert _ctx_tokens_for(meta) == 8192
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/slots/test_specialty_capacity.py -v`
Expected: FAIL — `ImportError: companion_bytes_mb`

- [ ] **Step 3: Implement**

`capacity.py`:

```python
def companion_bytes_mb(model_meta: dict[str, Any] | None) -> float:
    """Total specialty-companion bytes for a model, in MiB (0.0 for plain).

    Spec 2026-08-29 / #1946 seam 6: a specialty model's runtime sidecars
    (PromptForge: +21 GiB) load alongside the GGUF; the pre-load fit math
    must book them or every ActiveFPX slot under-books by more than the
    model file itself.
    """
    if not isinstance(model_meta, dict):
        return 0.0
    meta = model_meta.get("metadata")
    meta = meta if isinstance(meta, dict) else model_meta
    sizes = meta.get("companion_sizes")
    if not isinstance(sizes, dict):
        return 0.0
    total = sum(v for v in sizes.values() if isinstance(v, (int, float)) and v > 0)
    return round(total / (1024 * 1024), 1)
```

(Accepts either the model dict or its `metadata` sub-dict — call sites hold
one or the other; the double-read above covers both.)

`estimate_file_size_kv_mb` gains the keyword (default preserves callers):

```python
def estimate_file_size_kv_mb(
    model_mb: float,
    ctx_meta: dict[str, Any] | None,
    *,
    companion_mb: float = 0.0,
) -> float:
    ...
    kv_mb = _kv_estimate_mb(_ctx_tokens_for(ctx_meta))
    return round(model_mb + companion_mb + kv_mb, 1)
```

`_ctx_tokens_for` — inside the existing `metadata` read, before
`context_length`:

```python
    meta = model_meta.get("metadata")
    if isinstance(meta, dict):
        from hal0.registry.specialty import SPECIALTY_KINDS

        kind = SPECIALTY_KINDS.get(meta.get("specialty") or "")
        if kind is not None and kind.default_ctx:
            return int(kind.default_ctx)
        cl = meta.get("context_length")
        ...
```

Then update the pre-load call sites found by the grep to pass
`companion_mb=companion_bytes_mb(<the model dict they hold>)`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/slots/ -q`
Expected: PASS (new + existing slots suite)

- [ ] **Step 5: Commit**

```bash
git add src/hal0/slots tests/slots/test_specialty_capacity.py
git commit -m "feat(capacity): book specialty companion bytes and 262K ctx (#1946)"
```

---

### Task 10: hardware route + degraded surface

**Files:**
- Modify: `src/hal0/api/routes/hardware.py:914` (runner supports block)
- Modify: the slot-status payload assembler — after Task 6 lands, grep `src -rn "specialty_degraded"` and follow the scalar from `container_spec`'s consumer to the status route (the same payload that carries the config-drift comparator's output); add the reason dict under key `specialty_degraded`, `null` when absent (additive — old UI ignores it)
- Test: extend the existing hardware-route test (find via `grep -rn "\"jinja\"" tests` on the route's test file) + one slot-status assertion in its suite

**Interfaces:**
- Consumes: `runner.supports.specialties` (Task 5), scalars `"specialty_degraded"` (Task 6).
- Produces: `GET /api/hardware` runner block gains `"specialties": [...]`; slot status payload gains `"specialty_degraded": {...} | null`.

- [ ] **Step 1: Write the failing tests**

In the hardware route's existing test (copy its fixture):

```python
def test_hardware_runner_block_lists_specialties(...):
    # for the promptforge runner row:
    assert row["specialties"] == ["promptforge"]
    # for the rocmfpx row:
    assert row_rocmfpx["specialties"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests -k hardware -q`
Expected: FAIL — `KeyError: 'specialties'`

- [ ] **Step 3: Implement**

`hardware.py:914`:

```python
                "mtp": runner.supports.mtp,
                "jinja": runner.supports.jinja,
                "mmproj": runner.supports.mmproj,
                "specialties": list(runner.supports.specialties),
```

Slot status: thread `scalars["specialty_degraded"]` into the status payload
at the point scalar-derived facts are already snapshotted.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests -k "hardware or status" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hal0/api/routes/hardware.py src/hal0 tests
git commit -m "feat(api): surface runner specialties + slot degraded reason (#1946)"
```

---

### Task 11: UI — specialty chip + degraded badge

**Files:**
- Modify: `ui/src/` slot drawer + model catalog row + runner-image picker (locate: `grep -rln "mmproj" ui/src --include='*.jsx'` — the files rendering runner capability chips and model rows; the drawer that shows the drift-comparator warning)
- Test: extend the touched components' tests if they exist (`ls ui/src/**/*.test.jsx`); otherwise verify by build

**Interfaces:**
- Consumes: `specialties` array from `/api/hardware` (Task 10); `specialty_degraded` from slot status (Task 10); `metadata.specialty` on model rows.

- [ ] **Step 1: Model catalog row — render `metadata.specialty` as a chip beside the existing quant chip (same chip component, label = the key).**
- [ ] **Step 2: Slot drawer — when slot status carries `specialty_degraded`, render a warning badge with `detail` as the text; reuse the warning-badge component the drift comparator uses.**
- [ ] **Step 3: Runner-image picker — show the `specialties` tokens on the runner row (plain text suffix).**
- [ ] **Step 4: Build clean: `cd ui && npm run build`. If the touched components have tests, add a degraded-badge render assertion and run them.**
- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat(ui): specialty chip + loud degraded badge (#1946)"
```

---

### Task 12: `packaging/runner/promptforge/` build recipe

**Files:**
- Create: `packaging/runner/promptforge/manifest.toml` (copy `packaging/runner/rocmfpx/manifest.toml`)
- Create: `packaging/runner/promptforge/build.sh` (copy `packaging/runner/rocmfpx/build.sh`, adjust)

**Interfaces:**
- Produces: the recipe the CT130 pipeline builds into `ghcr.io/hal0ai/hal0-promptforge:v2.3-qwen38`. Not runnable in this repo's CI — the deliverable is the tracked recipe.

- [ ] **Step 1: Copy the rocmfpx recipe; change in `manifest.toml`:**
  - source: `ciru-ai/ROCmFPX`, ref `qwen3.8-activefpx-promptforge-v2.3`
  - apply `runtime/qwen38-v3-output-k8-runtime.patch` (fetched from the model repo `jcbtc/Qwen3.8-27B-CIRU-ActiveFPX-PromptForge`; pin its SHA-256 from the card's digest table)
  - flags: `-DGGML_HIP=ON`, `-DGGML_HIP_FORCE_MMQ=ON`, **`-DGGML_VULKAN=OFF`** — the one flag flip vs rocmfpx; comment WHY (card requires HIP-only, #1946 item 4)
  - composable_kernel pin `fdf4bb7fcc98`; ROCm 7.15 (TheRock); target `gfx1151`
- [ ] **Step 2: Header comment in both files — candidate image; the `DEFAULT_PROMPTFORGE_IMAGE` pin only moves after the ct150 gate (#1891): Paris probe, card quality/HumanEval envelope, MTP acceptance.**
- [ ] **Step 3: Commit**

```bash
git add packaging/runner/promptforge
git commit -m "feat(packaging): promptforge runner flavor recipe — HIP-only ciru v2.3 (#1946)"
```

---

## Post-plan (not tasks — tracked on #1946)

- CT130 pipeline job + `Hal0ai/hal0-runner-images` `images.json` entry (external repo).
- ct150 validation gate before the pin/curated row (#1891); bench cells (decode + prompt 2048/3524, MTP acceptance).
- Comment on #1946 linking spec + plan; label `ready-for-agent` per PR.
