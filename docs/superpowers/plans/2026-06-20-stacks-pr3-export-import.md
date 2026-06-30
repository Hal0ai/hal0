# Stacks — PR-3: Export / Import / Snapshot — Implementation Plan

> **Status: Implemented / Shipped (v0.8.x).** Export/import/snapshot (`.hal0stack.json` envelope + model resolve pass) landed in `src/hal0/stacks/portable.py`; `stack_import`/`stack_apply` exposed via MCP + REST. (#926, #964) Plan retained for historical context.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a stack portable. **Export** a `StackConfig` to a self-contained `.hal0stack.json` envelope that embeds referenced profiles + model *metadata* (never weights/paths) with a content checksum. **Import** an envelope: validate, run a resolve pass classifying each model ref as present / pullable / unresolvable, reconcile embedded profiles, and create the stack. **Snapshot** the live config (slots + capabilities) into a new `StackConfig`.

**Architecture:** A new pure-function module `src/hal0/stacks/portable.py`. Export embeds references from the live `ModelRegistry` + profile catalog; the envelope is a thin dict with a sha256 over the canonical stack body. Import is the inverse: a Pydantic `StackEnvelope` validates the wire shape (the inner `StackConfig` already forbids extras), `resolve_models` diffs refs against the registry, and `import_stack` reconciles profiles + calls `StacksCatalog.create`. Snapshot reuses the export embedder over a `StackConfig` built from `list_slots()`/`load_slot_config`/`load_capabilities_config`.

**Tech Stack:** Python 3.12, Pydantic v2, `hashlib`/`json`, pytest. No new dependencies. **No network** — actual model pulls are a later UI/REST action; PR-3 only *reports* pullable.

**Branch:** `feat/stacks-export`, off `feat/stacks-spec` (PR #921) — **parallel to the apply line (#923→#925)**, since export/import depends only on PR-1 (StackConfig/catalog + registry), not on the apply engine. Its PR targets `feat/stacks-spec`.

## Global Constraints

- **Scope of THIS PR (3):** export + import-resolve + snapshot ONLY — all in `src/hal0/stacks/portable.py`. NO REST/MCP (PR-4), NO actual model pulling (network), NO apply/converge/drift (those are #923/#925), NO dashboard.
- **Transport safety (spec §3/§6):** the envelope carries model **ids + metadata** (`StackModelMeta`) and embedded `ProfileConfig`s — never GGUF weights and never a host path. On export, `mmproj` is reduced to a presence marker (`"present"`/`None`), never the absolute sidecar path. Secrets never appear (the inference-surface scope never holds them).
- **Envelope shape:** `{"kind": "hal0.stack", "schema_version": <int>, "hal0_version": <str>, "exported_at": <ISO str, caller-stamped>, "checksum": "sha256:<hex>", "stack": <StackConfig dict>}`. Checksum is sha256 over `json.dumps(stack_body, sort_keys=True, default=str)` — deterministic, independent of `exported_at`.
- **Determinism / no clock:** `exported_at` is a **caller-supplied parameter** (the REST handler stamps it in PR-4) — `portable.py` never calls `datetime.now()`, so functions are pure and testable.
- **Import validation:** reject anything whose top-level `kind != "hal0.stack"` or that fails `StackEnvelope` validation, with `hal0.errors.BadRequest` (code `stacks.bad_envelope`). Reject `schema_version` greater than `STACK_SCHEMA_VERSION_CURRENT` (a newer format we can't read) with `stacks.envelope_too_new`. Older versions are accepted (only v1 exists today; the migration hook is a documented seam).
- **Resolve classification (per referenced model id):** `present` if `registry.has(id)`; else `pullable` if the embedded `StackModelMeta` has BOTH `hf_repo` and `hf_filename`; else `unresolvable`.
- **Reuse:** registry via `ModelRegistry` (`get`/`has`, `registry_dir=` injection); profiles via `load_profiles_config`/`save_profiles_config`; live config via `list_slots`/`load_slot_config`/`load_capabilities_config`; persistence via `StacksCatalog.create`. `hal0_version` from `hal0.__version__`.
- **Test runner:** `cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest <path> -q`. Filesystem isolation via `tmp_hal0_home` (root conftest); registry via `ModelRegistry(registry_dir=tmp_path/"registry")` (pattern from `tests/registry/test_store.py`).
- **Conventions:** test files `tests/stacks/test_*.py`; classes `Test<Feature>`; functions `test_<behavior>`; plain `assert`.

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `src/hal0/stacks/portable.py` | `embed_references` + `export_envelope` (T1); `StackEnvelope` + `parse_envelope` + `verify_checksum` + `ModelResolution`/`ResolveReport` + `resolve_models` + `import_stack` (T2); `snapshot_live_stack` (T3); shared `_referenced_model_ids`/`_referenced_profile_names`. | Create |
| `tests/stacks/test_export.py` | embedding + envelope + checksum tests. | Create (T1) |
| `tests/stacks/test_import.py` | parse/validate + resolve matrix + import-create + profile reconcile. | Create (T2) |
| `tests/stacks/test_snapshot.py` | snapshot-from-live tests. | Create (T3) |

---

## Task 1: Export — embed references + envelope

**Files:**
- Create: `src/hal0/stacks/portable.py`
- Test: `tests/stacks/test_export.py`

**Interfaces:**
- Consumes: `hal0.config.schema.{StackConfig, StackModelMeta}`; `hal0.config.loader.load_profiles_config`; `hal0.registry.store.ModelRegistry`; `hal0.__version__`.
- Produces:
  - `ENVELOPE_KIND = "hal0.stack"`.
  - `_referenced_model_ids(stack) -> set[str]`, `_referenced_profile_names(stack) -> set[str]`.
  - `embed_references(stack, *, registry, profiles_path=None) -> StackConfig` — returns a copy with `models`/`profiles` populated + `hal0_version` stamped.
  - `export_envelope(stack, *, exported_at, registry, profiles_path=None) -> dict` — the full envelope dict with checksum.

- [ ] **Step 1: Write the failing test**

Create `tests/stacks/test_export.py`:

```python
"""Tests for stack export: reference embedding + envelope + checksum.

Targeted file run:
    cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_export.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config.loader import save_profiles_config
from hal0.config.schema import (
    ProfileConfig,
    ProfilesConfig,
    StackCapabilityRow,
    StackConfig,
    StackSlotEntry,
)
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry
from hal0.stacks.portable import ENVELOPE_KIND, embed_references, export_envelope


@pytest.fixture
def reg(tmp_path: Path) -> ModelRegistry:
    r = ModelRegistry(registry_dir=tmp_path / "registry")
    r.add(Model(id="ace-saber", path="/models/ace.gguf", name="Ace Saber", hf_repo="jcbtc/ace", hf_filename="ace.gguf", size_bytes=19_000_000_000, capabilities=["chat", "vision"], backends=["rocm"], mmproj="/models/ace-mmproj.gguf"))
    return r


def _stack() -> StackConfig:
    return StackConfig(
        name="Saber",
        slots=[
            StackSlotEntry(slot="agent", model="ace-saber", profile="rocm"),
            StackSlotEntry(slot="embed", capabilities=[StackCapabilityRow(child="embed", device="npu", provider="flm", model="bge-m3")]),
        ],
    )


class TestEmbedReferences:
    def test_embeds_registry_model_metadata(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        out = embed_references(_stack(), registry=reg)
        assert "ace-saber" in out.models
        meta = out.models["ace-saber"]
        assert meta.hf_repo == "jcbtc/ace" and meta.hf_filename == "ace.gguf"
        assert meta.size_bytes == 19_000_000_000
        assert "vision" in meta.capabilities

    def test_mmproj_is_presence_marker_not_path(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        out = embed_references(_stack(), registry=reg)
        assert out.models["ace-saber"].mmproj == "present", "host mmproj path must not leak"

    def test_missing_model_embedded_as_bare_id(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        out = embed_references(_stack(), registry=reg)
        # bge-m3 (a capability model) is not in the registry → bare ref
        assert out.models["bge-m3"].id == "bge-m3"
        assert out.models["bge-m3"].hf_repo == ""

    def test_embeds_referenced_profile(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        save_profiles_config(ProfilesConfig(profile={"rocm": ProfileConfig(image="ghcr.io/x:y", quant="FP4")}))
        out = embed_references(_stack(), registry=reg)
        assert "rocm" in out.profiles
        assert out.profiles["rocm"].image == "ghcr.io/x:y"

    def test_stamps_hal0_version(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        from hal0 import __version__

        out = embed_references(_stack(), registry=reg)
        assert out.hal0_version == __version__


class TestExportEnvelope:
    def test_envelope_shape(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        env = export_envelope(_stack(), exported_at="2026-06-20T00:00:00Z", registry=reg)
        assert env["kind"] == ENVELOPE_KIND
        assert env["schema_version"] >= 1
        assert env["exported_at"] == "2026-06-20T00:00:00Z"
        assert env["checksum"].startswith("sha256:")
        assert env["stack"]["models"]["ace-saber"]["hf_repo"] == "jcbtc/ace"

    def test_checksum_is_deterministic_and_ignores_exported_at(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        a = export_envelope(_stack(), exported_at="2026-06-20T00:00:00Z", registry=reg)
        b = export_envelope(_stack(), exported_at="2099-01-01T00:00:00Z", registry=reg)
        assert a["checksum"] == b["checksum"], "checksum must cover the stack body only, not exported_at"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_export.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hal0.stacks.portable'`

- [ ] **Step 3: Implement export in `portable.py`**

Create `src/hal0/stacks/portable.py`:

```python
"""Portable stacks — export/import/snapshot (spec §3/§4/§6).

Export embeds a stack's referenced profiles + model METADATA (never weights,
never host paths) into a self-contained ``.hal0stack.json`` envelope with a
content checksum. Import validates + classifies model refs (present / pullable
/ unresolvable) + reconciles profiles + creates the stack. Snapshot reads the
live config into a StackConfig. Pure functions — the caller stamps ``exported_at``
and injects the registry, so there is no clock or hidden global here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from hal0 import __version__
from hal0.config.loader import load_profiles_config
from hal0.config.schema import StackConfig, StackModelMeta
from hal0.registry.store import ModelRegistry

ENVELOPE_KIND = "hal0.stack"


def _referenced_model_ids(stack: StackConfig) -> set[str]:
    """Every model id a stack references — slot primaries + capability rows."""
    ids: set[str] = set()
    for entry in stack.slots:
        if entry.model:
            ids.add(entry.model)
        for row in entry.capabilities:
            if row.model:
                ids.add(row.model)
    return ids


def _referenced_profile_names(stack: StackConfig) -> set[str]:
    """Every profile name a stack's slots reference."""
    return {entry.profile for entry in stack.slots if entry.profile}


def embed_references(
    stack: StackConfig,
    *,
    registry: ModelRegistry,
    profiles_path: Path | None = None,
) -> StackConfig:
    """Return a copy of ``stack`` with ``models``/``profiles`` populated.

    Model metadata is the transport-safe subset of the registry ``Model``;
    ``mmproj`` is reduced to a presence marker so a host path never travels.
    Models absent from the registry are embedded as a bare-id ``StackModelMeta``
    so the importer still sees the reference (and reports it unresolvable).
    Referenced profiles are embedded verbatim from the live profile catalog.
    ``hal0_version`` is stamped for provenance.
    """
    models: dict[str, StackModelMeta] = {}
    for mid in sorted(_referenced_model_ids(stack)):
        if registry.has(mid):
            m = registry.get(mid)
            models[mid] = StackModelMeta(
                id=m.id,
                name=m.name,
                hf_repo=m.hf_repo,
                hf_filename=m.hf_filename,
                size_bytes=m.size_bytes,
                capabilities=list(m.capabilities),
                backends=list(m.backends),
                mmproj="present" if m.mmproj else None,
            )
        else:
            models[mid] = StackModelMeta(id=mid)

    pcfg = load_profiles_config(profiles_path)
    profiles = {
        name: pcfg.profile[name]
        for name in sorted(_referenced_profile_names(stack))
        if name in pcfg.profile
    }

    return stack.model_copy(
        update={"profiles": profiles, "models": models, "hal0_version": __version__}
    )


def _checksum(stack_body: dict[str, Any]) -> str:
    """sha256 over the canonical stack body — deterministic, order-independent."""
    payload = json.dumps(stack_body, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def export_envelope(
    stack: StackConfig,
    *,
    exported_at: str,
    registry: ModelRegistry,
    profiles_path: Path | None = None,
) -> dict[str, Any]:
    """Build the ``.hal0stack.json`` envelope dict for ``stack``.

    ``exported_at`` is caller-supplied (no clock here). The checksum covers the
    embedded stack body only — re-exporting the same stack yields the same
    checksum regardless of ``exported_at``.
    """
    embedded = embed_references(stack, registry=registry, profiles_path=profiles_path)
    body = embedded.model_dump(mode="python", exclude_none=True)
    return {
        "kind": ENVELOPE_KIND,
        "schema_version": embedded.schema_version,
        "hal0_version": embedded.hal0_version,
        "exported_at": exported_at,
        "checksum": _checksum(body),
        "stack": body,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_export.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/halo/dev/wt/stacks-export
git add src/hal0/stacks/portable.py tests/stacks/test_export.py
git commit -m "feat(stacks): export — embed refs + .hal0stack.json envelope + checksum"
```

---

## Task 2: Import — parse, resolve, create

**Files:**
- Modify: `src/hal0/stacks/portable.py`
- Test: `tests/stacks/test_import.py`

**Interfaces:**
- Consumes: `embed_references`/`_referenced_model_ids`/`ENVELOPE_KIND` (T1); `hal0.config.schema.{StackConfig, STACK_SCHEMA_VERSION_CURRENT}`; `hal0.config.loader.{load_profiles_config, save_profiles_config}`; `hal0.stacks.StacksCatalog`; `hal0.errors.BadRequest`.
- Produces:
  - `StackEnvelope` (Pydantic, `extra="ignore"`, `stack: StackConfig`).
  - `parse_envelope(data) -> StackEnvelope`; `verify_checksum(envelope_dict) -> bool`.
  - `ModelResolution` + `ResolveReport` (dataclasses); `resolve_models(stack, registry) -> ResolveReport`.
  - `import_stack(data, slug, catalog, *, registry, profiles_path=None) -> tuple[ResolvedStack, ResolveReport]`.

- [ ] **Step 1: Write the failing test**

Create `tests/stacks/test_import.py`:

```python
"""Tests for stack import: parse/validate + resolve matrix + create.

Targeted file run:
    cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_import.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config.schema import StackCapabilityRow, StackConfig, StackSlotEntry
from hal0.errors import BadRequest
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry
from hal0.stacks import StacksCatalog
from hal0.stacks.portable import (
    export_envelope,
    import_stack,
    parse_envelope,
    resolve_models,
    verify_checksum,
)


@pytest.fixture
def reg(tmp_path: Path) -> ModelRegistry:
    r = ModelRegistry(registry_dir=tmp_path / "registry")
    r.add(Model(id="present-model", path="/m/p.gguf", hf_repo="x/p", hf_filename="p.gguf"))
    return r


def _stack() -> StackConfig:
    return StackConfig(
        name="S",
        slots=[
            StackSlotEntry(slot="agent", model="present-model"),
            StackSlotEntry(slot="chat", model="pullable-model"),
            StackSlotEntry(slot="util", model="ghost-model"),
        ],
    )


def _envelope(reg: ModelRegistry) -> dict:
    # Stack references 3 models; embed_references bare-ids the two absent ones.
    # Inject hf metadata for pullable-model so the resolve pass classifies it pullable.
    env = export_envelope(_stack(), exported_at="t", registry=reg)
    env["stack"]["models"]["pullable-model"]["hf_repo"] = "y/pull"
    env["stack"]["models"]["pullable-model"]["hf_filename"] = "pull.gguf"
    return env


class TestParseEnvelope:
    def test_valid_envelope_parses(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        env = export_envelope(_stack(), exported_at="t", registry=reg)
        parsed = parse_envelope(env)
        assert parsed.kind == "hal0.stack"
        assert parsed.stack.name == "S"

    def test_wrong_kind_rejected(self, tmp_hal0_home: str) -> None:
        with pytest.raises(BadRequest):
            parse_envelope({"kind": "not-a-stack", "stack": {}})

    def test_non_dict_rejected(self, tmp_hal0_home: str) -> None:
        with pytest.raises(BadRequest):
            parse_envelope("nope")  # type: ignore[arg-type]

    def test_too_new_schema_rejected(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        env = export_envelope(_stack(), exported_at="t", registry=reg)
        env["stack"]["schema_version"] = 9999
        with pytest.raises(BadRequest):
            import_stack(env, "s", StacksCatalog(path=Path(tmp_hal0_home) / "etc/hal0/stacks.toml"), registry=reg)


class TestVerifyChecksum:
    def test_intact_checksum_verifies(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        env = export_envelope(_stack(), exported_at="t", registry=reg)
        assert verify_checksum(env) is True

    def test_tampered_body_fails(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        env = export_envelope(_stack(), exported_at="t", registry=reg)
        env["stack"]["name"] = "TAMPERED"
        assert verify_checksum(env) is False


class TestResolveModels:
    def test_resolve_matrix(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        parsed = parse_envelope(_envelope(reg))
        report = resolve_models(parsed.stack, reg)
        by_id = {r.model_id: r.status for r in report.resolutions}
        assert by_id["present-model"] == "present"
        assert by_id["pullable-model"] == "pullable"
        assert by_id["ghost-model"] == "unresolvable"

    def test_report_buckets(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        report = resolve_models(parse_envelope(_envelope(reg)).stack, reg)
        assert report.present == ["present-model"]
        assert report.pullable == ["pullable-model"]
        assert report.unresolvable == ["ghost-model"]


class TestImportStack:
    def test_import_creates_stack_and_returns_report(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc/hal0/stacks.toml")
        resolved, report = import_stack(_envelope(reg), "saber", catalog, registry=reg)
        assert resolved.slug == "saber"
        assert any(r.slug == "saber" for r in catalog.list())
        assert report.pullable == ["pullable-model"]

    def test_import_reconciles_embedded_profile(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        from hal0.config.loader import load_profiles_config
        from hal0.config.schema import ProfileConfig

        env = export_envelope(_stack(), exported_at="t", registry=reg)
        env["stack"]["profiles"] = {"custom-x": ProfileConfig(image="ghcr.io/c:x").model_dump(mode="python")}
        env["stack"]["slots"][0]["profile"] = "custom-x"
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc/hal0/stacks.toml")
        import_stack(env, "s2", catalog, registry=reg)
        assert "custom-x" in load_profiles_config().profile, "embedded profile must be reconciled into profiles.toml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_import.py -q`
Expected: FAIL with `ImportError: cannot import name 'import_stack' from 'hal0.stacks.portable'`

- [ ] **Step 3: Add import to `portable.py`**

Append to `src/hal0/stacks/portable.py`. First extend the imports at the top — add ONLY these new names (do not re-list names Task 1 already imports):
- Add `save_profiles_config` to the existing `from hal0.config.loader import load_profiles_config` line → `from hal0.config.loader import load_profiles_config, save_profiles_config`.
- Add `STACK_SCHEMA_VERSION_CURRENT` to the existing `from hal0.config.schema import StackConfig, StackModelMeta` line → `from hal0.config.schema import STACK_SCHEMA_VERSION_CURRENT, StackConfig, StackModelMeta`.
- Add three new import lines:

```python
from dataclasses import dataclass, field

from pydantic import BaseModel

from hal0.errors import BadRequest
```

Then append the import machinery:

```python
# ── import ───────────────────────────────────────────────────────────────────


class StackEnvelope(BaseModel):
    """Parsed ``.hal0stack.json`` wire shape. ``extra="ignore"`` keeps a newer
    producer's extra envelope keys from breaking import; the inner StackConfig
    still forbids unknown fields."""

    model_config = {"extra": "ignore"}

    kind: str
    schema_version: int = STACK_SCHEMA_VERSION_CURRENT
    hal0_version: str = ""
    exported_at: str = ""
    checksum: str = ""
    stack: StackConfig


def parse_envelope(data: Any) -> StackEnvelope:
    """Validate the wire shape. Raises BadRequest on a non-envelope/invalid input."""
    if not isinstance(data, dict) or data.get("kind") != ENVELOPE_KIND:
        raise BadRequest(
            "not a hal0.stack envelope",
            code="stacks.bad_envelope",
            details={"kind": (data.get("kind") if isinstance(data, dict) else None)},
        )
    try:
        return StackEnvelope.model_validate(data)
    except Exception as exc:
        raise BadRequest(
            f"invalid stack envelope: {exc}",
            code="stacks.bad_envelope",
            details={"reason": str(exc)},
        ) from exc


def verify_checksum(envelope: dict[str, Any]) -> bool:
    """True when the envelope's checksum matches its stack body."""
    body = envelope.get("stack")
    if not isinstance(body, dict):
        return False
    return envelope.get("checksum") == _checksum(body)


@dataclass(frozen=True)
class ModelResolution:
    """How one referenced model id resolves against the local registry."""

    model_id: str
    status: str  # "present" | "pullable" | "unresolvable"
    hf_repo: str = ""
    hf_filename: str = ""


@dataclass
class ResolveReport:
    """Per-model resolution + convenience buckets for the import UI."""

    resolutions: list[ModelResolution] = field(default_factory=list)

    @property
    def present(self) -> list[str]:
        return [r.model_id for r in self.resolutions if r.status == "present"]

    @property
    def pullable(self) -> list[str]:
        return [r.model_id for r in self.resolutions if r.status == "pullable"]

    @property
    def unresolvable(self) -> list[str]:
        return [r.model_id for r in self.resolutions if r.status == "unresolvable"]


def resolve_models(stack: StackConfig, registry: ModelRegistry) -> ResolveReport:
    """Classify each referenced model id: present / pullable / unresolvable."""
    resolutions: list[ModelResolution] = []
    for mid in sorted(_referenced_model_ids(stack)):
        if registry.has(mid):
            resolutions.append(ModelResolution(mid, "present"))
            continue
        meta = stack.models.get(mid)
        if meta is not None and meta.hf_repo and meta.hf_filename:
            resolutions.append(ModelResolution(mid, "pullable", meta.hf_repo, meta.hf_filename))
        else:
            resolutions.append(ModelResolution(mid, "unresolvable"))
    return ResolveReport(resolutions)


def _reconcile_profiles(stack: StackConfig, profiles_path: Path | None = None) -> None:
    """Add the stack's embedded profiles that don't already exist locally.

    Name collisions keep the LOCAL profile (the importer never silently
    overwrites a profile the user already tuned).
    """
    if not stack.profiles:
        return
    pcfg = load_profiles_config(profiles_path)
    changed = False
    for name, profile in stack.profiles.items():
        if name not in pcfg.profile:
            pcfg.profile[name] = profile
            changed = True
    if changed:
        save_profiles_config(pcfg, profiles_path)


def import_stack(
    data: Any,
    slug: str,
    catalog: Any,
    *,
    registry: ModelRegistry,
    profiles_path: Path | None = None,
) -> tuple[Any, ResolveReport]:
    """Validate, reconcile profiles, create the stack, and report model resolution.

    ``catalog`` is a StacksCatalog (duck-typed: needs ``create(slug, StackConfig)``).
    Raises BadRequest for a bad/too-new envelope; the catalog raises Conflict on a
    duplicate slug.
    """
    env = parse_envelope(data)
    if env.stack.schema_version > STACK_SCHEMA_VERSION_CURRENT:
        raise BadRequest(
            f"stack schema v{env.stack.schema_version} is newer than supported "
            f"v{STACK_SCHEMA_VERSION_CURRENT}",
            code="stacks.envelope_too_new",
            details={"got": env.stack.schema_version, "supported": STACK_SCHEMA_VERSION_CURRENT},
        )
    # (forward-compat seam: older schema_version would migrate here; only v1 exists.)
    _reconcile_profiles(env.stack, profiles_path)
    resolved = catalog.create(slug, env.stack)
    report = resolve_models(env.stack, registry)
    return resolved, report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_import.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/halo/dev/wt/stacks-export
git add src/hal0/stacks/portable.py tests/stacks/test_import.py
git commit -m "feat(stacks): import — parse/validate + resolve pass + create"
```

---

## Task 3: Snapshot from live config

**Files:**
- Modify: `src/hal0/stacks/portable.py`
- Test: `tests/stacks/test_snapshot.py`

**Interfaces:**
- Consumes: `embed_references` (T1); `hal0.config.loader.{list_slots, load_slot_config}`; `hal0.capabilities.config.load_capabilities_config`; `hal0.config.schema._VALID_DEVICES`; `StackConfig`/`StackSlotEntry`/`StackCapabilityRow`.
- Produces: `snapshot_live_stack(*, name="", description="", registry, profiles_path=None) -> StackConfig`.

- [ ] **Step 1: Write the failing test**

Create `tests/stacks/test_snapshot.py`:

```python
"""Tests for snapshot-from-live: read slots + capabilities → a StackConfig.

Targeted file run:
    cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_snapshot.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.registry.store import ModelRegistry
from hal0.stacks.portable import snapshot_live_stack


@pytest.fixture
def reg(tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(registry_dir=tmp_path / "registry")


def _write_slot(home: str, name: str, body: list[str]) -> None:
    d = Path(home) / "etc" / "hal0" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.toml").write_text("\n".join(body) + "\n", encoding="utf-8")


def _write_caps(home: str, text: str) -> None:
    p = Path(home) / "etc" / "hal0" / "capabilities.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class TestSnapshot:
    def test_captures_primary_slot(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        _write_slot(tmp_hal0_home, "agent", ['name = "agent"', "port = 8087", 'device = "gpu-rocm"', 'provider = "llama-server"', "[model]", 'default = "ace-saber"'])
        stack = snapshot_live_stack(registry=reg, name="Live")
        agent = next(e for e in stack.slots if e.slot == "agent")
        assert agent.model == "ace-saber"
        assert agent.device == "gpu-rocm"
        assert stack.name == "Live"

    def test_captures_capability_rows(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        _write_slot(tmp_hal0_home, "embed", ['name = "embed"', "port = 8082", "[model]", 'default = ""'])
        _write_caps(
            tmp_hal0_home,
            "\n".join(["schema_version = 2", "[selections.embed.embed]", 'device = "npu"', 'provider = "flm"', 'model = "bge-m3"', "enabled = true", ""]),
        )
        stack = snapshot_live_stack(registry=reg)
        embed = next(e for e in stack.slots if e.slot == "embed")
        assert any(r.child == "embed" and r.model == "bge-m3" and r.device == "npu" for r in embed.capabilities)

    def test_empty_slot_is_skipped(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        # A seeded slot with no model and no capabilities should not bloat the snapshot.
        _write_slot(tmp_hal0_home, "tts", ['name = "tts"', "port = 8085", "[model]", 'default = ""'])
        stack = snapshot_live_stack(registry=reg)
        assert not any(e.slot == "tts" for e in stack.slots)

    def test_unset_capability_device_is_skipped(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        # A blank-picker selection (device == "") must not produce an invalid row.
        _write_slot(tmp_hal0_home, "vision", ['name = "vision"', "port = 8086", "[model]", 'default = "v"'])
        _write_caps(tmp_hal0_home, "\n".join(["schema_version = 2", "[selections.vision.vision]", 'device = ""', 'provider = ""', 'model = ""', "enabled = false", ""]))
        stack = snapshot_live_stack(registry=reg)
        vision = next(e for e in stack.slots if e.slot == "vision")
        assert vision.capabilities == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_snapshot.py -q`
Expected: FAIL with `ImportError: cannot import name 'snapshot_live_stack' from 'hal0.stacks.portable'`

- [ ] **Step 3: Add snapshot to `portable.py`**

Extend the top imports — add ONLY these new names:
- Add `list_slots, load_slot_config` to the existing `from hal0.config.loader import …` line.
- Add `_VALID_DEVICES, StackCapabilityRow, StackSlotEntry` to the existing `from hal0.config.schema import …` line.
- Add one new line: `from hal0.capabilities.config import load_capabilities_config`.

Append:

```python
# ── snapshot from live ───────────────────────────────────────────────────────


def snapshot_live_stack(
    *,
    name: str = "",
    description: str = "",
    registry: ModelRegistry,
    profiles_path: Path | None = None,
) -> StackConfig:
    """Build a StackConfig from the current on-disk slots + capabilities.

    Reads ``/etc/hal0/slots/*.toml`` and ``capabilities.toml`` (HAL0_HOME-aware)
    and projects each configured slot into a StackSlotEntry. Empty seeded slots
    (no model, no capability rows) are skipped so the snapshot stays clean.
    Blank-picker capability selections (device unset) are dropped — they would
    fail StackCapabilityRow validation and carry no real config. The result is
    run through :func:`embed_references` so it is self-contained.
    """
    caps = load_capabilities_config()
    entries: list[StackSlotEntry] = []

    for slot_name in list_slots():
        try:
            sc = load_slot_config(slot_name)
        except Exception:
            # A malformed slot TOML never breaks the whole snapshot.
            continue

        rows: list[StackCapabilityRow] = []
        for child, sel in caps.selections.get(slot_name, {}).items():
            if sel.device not in _VALID_DEVICES:
                continue  # unset / blank-picker selection
            rows.append(
                StackCapabilityRow(
                    child=child,
                    device=sel.device,
                    provider=sel.provider,
                    model=sel.model,
                    enabled=sel.enabled,
                )
            )

        model = sc.model.default or None
        if model is None and not rows:
            continue  # empty seeded slot

        entries.append(
            StackSlotEntry(
                slot=slot_name,
                model=model,
                device=sc.device,
                provider=sc.provider,
                role=sc.role,
                vision=sc.vision,
                mtp=sc.mtp,
                enable_thinking=sc.enable_thinking,
                server_extra_args=sc.server.extra_args,
                profile=sc.profile,
                capabilities=rows,
            )
        )

    stack = StackConfig(name=name, description=description, slots=entries)
    return embed_references(stack, registry=registry, profiles_path=profiles_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_snapshot.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full PR-3 set + regression sweep**

Run: `cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks -q`
Expected: PASS (PR-1's catalog/schema/loader tests + the 21 new PR-3 tests).

Run: `cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/registry tests/config tests/capabilities -q`
Expected: PASS — confirms the reused registry/config/capabilities readers are untouched.

- [ ] **Step 6: Commit**

```bash
cd /home/halo/dev/wt/stacks-export
git add src/hal0/stacks/portable.py tests/stacks/test_snapshot.py
git commit -m "feat(stacks): snapshot live config into a StackConfig"
```

---

## PR-3 Done — Definition of Done

- `export_envelope` produces a `.hal0stack.json` dict: embedded profiles + model metadata (no weights, `mmproj` reduced to a marker, no host paths), deterministic checksum over the stack body.
- `parse_envelope` rejects non-envelopes / invalid shapes / too-new schema; `verify_checksum` detects tampering; `resolve_models` classifies present / pullable / unresolvable; `import_stack` reconciles profiles + creates the stack + returns the report.
- `snapshot_live_stack` captures configured slots + capability rows, skips empty slots and blank-picker selections, and self-embeds.
- 21 new tests pass; PR-1 + `tests/registry` + `tests/config` + `tests/capabilities` unregressed.
- No REST/MCP, no network pulls, no apply/converge — those are PR-4 / out of scope.

## Next — PR-4 (REST + MCP) wires everything

`POST /api/stacks/{slug}/export` → `export_envelope`. `POST /api/stacks/import?dry_run=true` → `parse_envelope` + `resolve_models` (report only). `POST …/import` → `import_stack`. `POST …/snapshot` → `snapshot_live_stack` + `catalog.create`. Plus the apply endpoints wiring `plan`/`apply_config`/`converge`/`record_active` (#923/#925) and the gated MCP tools — injecting `app.state.model_registry`/`slot_manager`/`capability_orchestrator`, and stamping `exported_at` with the real clock at the handler boundary.
