# Stacks — PR-1: Schema + Catalog — Implementation Plan

> **Status: Implemented / Shipped (v0.8.x).** `StackConfig`/catalog landed in `src/hal0/config/schema.py` + `src/hal0/stacks/` (with seed stacks saber/forge/pi). (#921, #964) Plan retained for historical context.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `StackConfig` data model and a `StacksCatalog` that reads/writes a single atomic `stacks.toml`, giving hal0 a validated, persisted, CRUD-able catalog of Stacks — the foundation all later PRs bind to.

**Architecture:** Mirror the existing Profile subsystem exactly. New Pydantic models live beside `ProfileConfig` in `config/schema.py`; a single-file `stacks.toml` (keyed by slug) is read/written through `config/loader.py` using the existing `write_toml_atomic`; a `StacksCatalog` class in a new `hal0/stacks/` package provides list/resolve/create/update/delete with seed-immutability + slug guards, copying `ProfileCatalog` method-for-method.

**Tech Stack:** Python 3.12, Pydantic v2, `tomllib`/`tomli_w`, pytest. No new dependencies.

## Global Constraints

- **Scope of this PR:** schema models + loader + catalog ONLY. No REST routes, no MCP tools, no apply engine, no export/import, no UI, no seed-stack content. Those are PRs 2–6.
- **Storage layout:** single `stacks.toml` keyed by slug (mirrors `profiles.toml`), NOT per-file. This deviates from spec §4's per-file suggestion to reuse the verified `ProfileCatalog`/`load_profiles_config` code with zero invented helpers; the storage layout is internal and does not affect the JSON export envelope (PR-3) or the apply engine (PR-2). Recorded as a deliberate deviation.
- **Pydantic config on every new model:** `model_config = {"populate_by_name": True, "extra": "forbid"}` — typos in TOML keys must raise at load, exactly like `ProfileConfig`.
- **Slug rule (verbatim from existing slot/profile policy):** `^[a-z0-9][a-z0-9_-]{0,31}$` — lowercase alphanumeric + `-`/`_`, start alphanumeric, ≤32 chars.
- **Errors:** raise `hal0.errors.NotFound` (404) and `hal0.errors.Conflict` (409) with `code=` strings under the `stacks.*` namespace; loader validation failures surface as `hal0.config.loader.ConfigParseError`.
- **Atomic writes:** all persistence goes through `hal0.config.loader.write_toml_atomic`; serialize with `cfg.model_dump(mode="python", exclude_none=True)` (mirrors `save_profiles_config`).
- **Stacks schema version:** `STACK_SCHEMA_VERSION_CURRENT = 1`, stored on `StackConfig.schema_version`. No migration is wired in this PR (v1 is the first shape); envelope migration is PR-3's concern.
- **Test runner:** `~/dev/hal0/.venv/bin/python -m pytest <path> -q`. Tests isolate the filesystem with the existing `tmp_hal0_home` fixture (root `tests/conftest.py`) or an explicit `path=` argument. **Worktree caveat:** ensure the worktree is the active editable install (`~/dev/hal0/.venv/bin/pip install -e .` from the worktree root) or run with `PYTHONPATH=src` so pytest imports the worktree's `hal0`, not the main checkout's.
- **Conventions:** test files `tests/<area>/test_*.py`; classes `Test<Feature>`; functions `test_<behavior>`; plain `assert`.

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `src/hal0/config/schema.py` | Add `StackModelMeta`, `StackCapabilityRow`, `StackSlotEntry`, `StackConfig`, `StacksConfig`, the version constant, and `SEED_STACKS` (empty until PR-6). | Modify (append a Stacks section after `ProfilesConfig`, ~line 906) |
| `src/hal0/config/paths.py` | Add `stacks_toml()` path helper. | Modify (after `profiles_toml`, ~line 252) |
| `src/hal0/config/loader.py` | Add `load_stacks_config()` / `save_stacks_config()`. | Modify (after `save_profiles_config`, ~line 449; extend the schema import) |
| `src/hal0/stacks/__init__.py` | `StacksCatalog` + `ResolvedStack` + slug/seed guards. | Create |
| `tests/config/test_stacks_schema.py` | Model validation tests. | Create |
| `tests/config/test_stacks_loader.py` | Load/save round-trip + parse-error tests. | Create |
| `tests/stacks/test_stacks_catalog.py` | Catalog CRUD + guard tests. | Create |

---

## Task 1: Stack schema models

**Files:**
- Modify: `src/hal0/config/schema.py` (append after `ProfilesConfig`, which ends ~line 906)
- Test: `tests/config/test_stacks_schema.py`

**Interfaces:**
- Consumes: `pydantic.BaseModel/Field/field_validator`, the module's existing `_VALID_DEVICES` frozenset (schema.py:52) and `ProfileConfig` (schema.py:814).
- Produces:
  - `STACK_SCHEMA_VERSION_CURRENT: int = 1`
  - `StackModelMeta(BaseModel)` — fields `id: str`, `name: str = ""`, `hf_repo: str = ""`, `hf_filename: str = ""`, `size_bytes: int = 0`, `quant: str = ""`, `capabilities: list[str] = []`, `backends: list[str] = []`, `mmproj: str | None = None`.
  - `StackCapabilityRow(BaseModel)` — `child: str`, `device: str`, `provider: str`, `model: str`, `enabled: bool = True`.
  - `StackSlotEntry(BaseModel)` — `slot: str`, `profile: str | None = None`, `model: str | None = None`, `device: str | None = None`, `provider: str | None = None`, `role: str | None = None`, `vision: bool = False`, `mtp: bool | None = None`, `enable_thinking: bool | None = None`, `server_extra_args: str | None = None`, `capabilities: list[StackCapabilityRow] = []`.
  - `StackConfig(BaseModel)` — `name: str = ""`, `description: str = ""`, `author: str = ""`, `icon: str = ""`, `tags: list[str] = []`, `schema_version: int = STACK_SCHEMA_VERSION_CURRENT`, `hal0_version: str = ""`, `slots: list[StackSlotEntry] = []`, `profiles: dict[str, ProfileConfig] = {}`, `models: dict[str, StackModelMeta] = {}`.
  - `StacksConfig(BaseModel)` — `stack: dict[str, StackConfig] = {}`.
  - `SEED_STACKS: dict[str, StackConfig] = {}`.

- [ ] **Step 1: Write the failing test**

Create `tests/config/test_stacks_schema.py`:

```python
"""Unit tests for the Stack schema models.

Targeted file run:
    ~/dev/hal0/.venv/bin/python -m pytest tests/config/test_stacks_schema.py -q
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import (
    STACK_SCHEMA_VERSION_CURRENT,
    StackCapabilityRow,
    StackConfig,
    StackModelMeta,
    StackSlotEntry,
    StacksConfig,
)


class TestStackModelMeta:
    def test_minimal_requires_id(self) -> None:
        m = StackModelMeta(id="chadrock-35b-ace-saber")
        assert m.id == "chadrock-35b-ace-saber"
        assert m.size_bytes == 0
        assert m.capabilities == []
        assert m.mmproj is None

    def test_empty_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            StackModelMeta(id="   ")

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            StackModelMeta(id="m1", path="/mnt/ai-models/x.gguf")  # path is machine-specific, excluded


class TestStackCapabilityRow:
    def test_valid_row(self) -> None:
        r = StackCapabilityRow(child="embed", device="npu", provider="flm", model="bge-m3")
        assert r.enabled is True

    def test_bad_device_raises(self) -> None:
        with pytest.raises(ValidationError):
            StackCapabilityRow(child="embed", device="quantum", provider="flm", model="bge-m3")


class TestStackSlotEntry:
    def test_minimal_requires_slot(self) -> None:
        e = StackSlotEntry(slot="agent")
        assert e.slot == "agent"
        assert e.vision is False
        assert e.capabilities == []

    def test_bad_slot_name_raises(self) -> None:
        with pytest.raises(ValidationError):
            StackSlotEntry(slot="Agent Slot!")

    def test_bad_device_raises(self) -> None:
        with pytest.raises(ValidationError):
            StackSlotEntry(slot="agent", device="gpu-quantum")


class TestStackConfig:
    def test_defaults(self) -> None:
        s = StackConfig()
        assert s.name == ""
        assert s.schema_version == STACK_SCHEMA_VERSION_CURRENT
        assert s.slots == []
        assert s.profiles == {}
        assert s.models == {}

    def test_full_round_trip_through_dict(self) -> None:
        s = StackConfig(
            name="Saber",
            description="high-speed agentic MoE",
            slots=[StackSlotEntry(slot="agent", model="chadrock-35b-ace-saber")],
            models={"chadrock-35b-ace-saber": StackModelMeta(id="chadrock-35b-ace-saber")},
        )
        dumped = s.model_dump(mode="python", exclude_none=True)
        again = StackConfig.model_validate(dumped)
        assert again.slots[0].slot == "agent"
        assert again.models["chadrock-35b-ace-saber"].id == "chadrock-35b-ace-saber"

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            StackConfig(surprise="nope")


class TestStacksConfig:
    def test_empty_default(self) -> None:
        c = StacksConfig()
        assert c.stack == {}

    def test_keyed_by_slug(self) -> None:
        c = StacksConfig(stack={"saber": StackConfig(name="Saber")})
        assert c.stack["saber"].name == "Saber"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/dev/hal0/.venv/bin/python -m pytest tests/config/test_stacks_schema.py -q`
Expected: FAIL with `ImportError: cannot import name 'StackConfig' from 'hal0.config.schema'`

- [ ] **Step 3: Add the models to `schema.py`**

Append immediately after the `ProfilesConfig` class (which ends ~line 906) in `src/hal0/config/schema.py`. The `_VALID_DEVICES` frozenset and `ProfileConfig` are already defined above in this module; no new imports are needed (`re` is imported lazily inside the validator, matching `name_valid` at schema.py:622).

```python
# ── Stacks ────────────────────────────────────────────────────────────────────
# A Stack is a named, portable bundle of slots + their profiles + model
# assignments + capability selections. Stored single-file in stacks.toml keyed
# by slug, mirroring profiles.toml. See docs/superpowers/specs/2026-06-19-stacks-design.md.

# Stacks carry their own schema version (independent of hal0.toml meta.schema_version),
# stamped on every StackConfig and on the export envelope (PR-3).
STACK_SCHEMA_VERSION_CURRENT = 1

_STACK_NAME_RE = r"^[a-z0-9][a-z0-9_-]{0,31}$"


class StackModelMeta(BaseModel):
    """Transport-safe metadata subset of a registry ``Model``.

    Embedded in a stack so an importer on another machine can resolve or
    pull a referenced model by id. Deliberately excludes the machine-specific
    ``path`` and any host-local fields — see spec §3/§6.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    id: str = Field(..., description="Registry model id this entry describes.")
    name: str = Field(default="", description="Human-readable display name.")
    hf_repo: str = Field(default="", description="HuggingFace repo id, for resolve-and-pull on import.")
    hf_filename: str = Field(default="", description="Filename within the HF repo.")
    size_bytes: int = Field(default=0, description="Total model size in bytes; 0 = unknown.")
    quant: str = Field(default="", description="Quantization label shown on cards (e.g. 'FP4', 'Q4_K_M').")
    capabilities: list[str] = Field(default_factory=list, description="Capability strings, e.g. ['chat','vision'].")
    backends: list[str] = Field(default_factory=list, description="Runnable backends, e.g. ['rocm','vulkan'].")
    mmproj: str | None = Field(default=None, description="mmproj sidecar marker (presence flag); never a host path on import.")

    @field_validator("id")
    @classmethod
    def id_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("stack model meta id must not be empty")
        return v


class StackCapabilityRow(BaseModel):
    """One (slot, child) capability selection carried by a stack slot entry.

    Mirrors the fields of ``hal0.capabilities.config.CapabilitySelection`` that
    are portable; the apply engine (PR-2) translates these into real
    CapabilitySelection rows at apply time.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    child: str = Field(..., description="Capability child key, e.g. 'embed', 'rerank', 'stt', 'tts', 'vision'.")
    device: str = Field(..., description="Device target for this child.")
    provider: str = Field(..., description="Provider name for this child.")
    model: str = Field(..., description="Model id bound to this child.")
    enabled: bool = Field(default=True, description="Whether this child is active in the stack.")

    @field_validator("device")
    @classmethod
    def device_valid(cls, v: str) -> str:
        if v not in _VALID_DEVICES:
            raise ValueError(f"device {v!r}: must be one of {sorted(_VALID_DEVICES)}")
        return v


class StackSlotEntry(BaseModel):
    """One slot's contribution to a stack: which model/profile/caps it carries.

    References models and profiles by name/id; the embedded ``profiles`` and
    ``models`` maps on the parent ``StackConfig`` carry the metadata needed to
    resolve those references on another machine.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    slot: str = Field(..., description="Slot name this entry configures (kebab-case).")
    profile: str | None = Field(default=None, description="Profile name reference (resolved against StackConfig.profiles).")
    model: str | None = Field(default=None, description="Model id reference (resolved against StackConfig.models).")
    device: str | None = Field(default=None, description="Device override for the slot.")
    provider: str | None = Field(default=None, description="Provider override for the slot.")
    role: str | None = Field(default=None, description="Normalization role hint, e.g. 'primary'.")
    vision: bool = Field(default=False, description="Enable the mmproj vision sidecar for this slot.")
    mtp: bool | None = Field(default=None, description="Per-slot MTP override (inherits profile default when None).")
    enable_thinking: bool | None = Field(default=None, description="Per-slot reasoning override.")
    server_extra_args: str | None = Field(default=None, description="Freeform llama-server CLI flags for this slot.")
    capabilities: list[StackCapabilityRow] = Field(default_factory=list, description="Capability child selections.")

    @field_validator("slot")
    @classmethod
    def slot_valid(cls, v: str) -> str:
        import re

        if not v or not v.strip():
            raise ValueError("slot name must not be empty")
        if not re.match(_STACK_NAME_RE, v):
            raise ValueError(
                f"slot name {v!r}: use lowercase alphanumeric, hyphens, underscores; "
                f"start with alphanumeric; max 32 chars"
            )
        return v

    @field_validator("device")
    @classmethod
    def device_valid(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_DEVICES:
            raise ValueError(f"device {v!r}: must be one of {sorted(_VALID_DEVICES)}")
        return v


class StackConfig(BaseModel):
    """One ``[stack.<slug>]`` entry in stacks.toml.

    A curated bundle of slots + embedded profiles + embedded model metadata.
    The slug is the dict key (validated by StacksCatalog on create), not a
    field here — mirroring ProfileConfig. ``name`` is the human display label.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    name: str = Field(default="", description="Human display label (falls back to slug in the UI).")
    description: str = Field(default="", description="What this stack is for.")
    author: str = Field(default="", description="Author/provenance, for the future directory.")
    icon: str = Field(default="", description="Accent token or emoji shown on the card.")
    tags: list[str] = Field(default_factory=list, description="Freeform tags for listing/filtering.")
    schema_version: int = Field(
        default=STACK_SCHEMA_VERSION_CURRENT,
        description="Stack schema version, stamped for forward-compat / envelope migration.",
    )
    hal0_version: str = Field(default="", description="hal0 version that produced this stack (provenance).")
    slots: list[StackSlotEntry] = Field(default_factory=list, description="Slots this stack configures.")
    profiles: dict[str, ProfileConfig] = Field(
        default_factory=dict,
        description="Embedded profiles referenced by slots, so the stack is self-contained.",
    )
    models: dict[str, StackModelMeta] = Field(
        default_factory=dict,
        description="Embedded model metadata (no weights) for referenced model ids.",
    )


class StacksConfig(BaseModel):
    """Parsed stacks.toml — top-level ``[stack]`` table, keyed by slug."""

    model_config = {"populate_by_name": True, "extra": "forbid"}

    stack: dict[str, StackConfig] = Field(default_factory=dict)


# Built-in seed stacks (immutable, clone-only). Empty until PR-6 fills it with
# saber/forge/pi. StacksCatalog consults this for the seed-immutability guard.
SEED_STACKS: dict[str, StackConfig] = {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/dev/hal0/.venv/bin/python -m pytest tests/config/test_stacks_schema.py -q`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/halo/dev/wt/stacks-spec
git add src/hal0/config/schema.py tests/config/test_stacks_schema.py
git commit -m "feat(stacks): StackConfig schema models + validation"
```

---

## Task 2: Path helper + loader (load/save stacks.toml)

**Files:**
- Modify: `src/hal0/config/paths.py` (after `profiles_toml`, ~line 252)
- Modify: `src/hal0/config/loader.py` (extend the `hal0.config.schema` import block ~line 29; add functions after `save_profiles_config`, ~line 449)
- Test: `tests/config/test_stacks_loader.py`

**Interfaces:**
- Consumes: `write_toml_atomic` (loader.py:69), `_read_toml` (loader.py:117), `ConfigParseError` (loader.py:62), `paths.etc()` (paths.py:55), and `StacksConfig`/`SEED_STACKS` from Task 1.
- Produces:
  - `paths.stacks_toml() -> Path` → `etc() / "stacks.toml"`.
  - `loader.load_stacks_config(path: Path | None = None) -> StacksConfig` — absent file returns `StacksConfig.model_validate({"stack": SEED_STACKS})`; malformed/invalid raises `ConfigParseError`.
  - `loader.save_stacks_config(cfg: StacksConfig, path: Path | None = None) -> None` — atomic write via `write_toml_atomic` with `model_dump(mode="python", exclude_none=True)`.

- [ ] **Step 1: Write the failing test**

Create `tests/config/test_stacks_loader.py`:

```python
"""Unit tests for the stacks.toml loader/saver.

Targeted file run:
    ~/dev/hal0/.venv/bin/python -m pytest tests/config/test_stacks_loader.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config.loader import ConfigParseError, load_stacks_config, save_stacks_config
from hal0.config.schema import StackConfig, StackSlotEntry, StacksConfig


class TestLoadStacksConfig:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        cfg = load_stacks_config(path=tmp_path / "nonexistent.toml")
        assert cfg.stack == {}

    def test_round_trip_save_then_load(self, tmp_path: Path) -> None:
        target = tmp_path / "stacks.toml"
        cfg = StacksConfig(
            stack={
                "saber": StackConfig(
                    name="Saber",
                    description="high-speed agentic MoE",
                    slots=[StackSlotEntry(slot="agent", model="chadrock-35b-ace-saber")],
                )
            }
        )
        save_stacks_config(cfg, path=target)
        assert target.exists()
        loaded = load_stacks_config(path=target)
        assert "saber" in loaded.stack
        assert loaded.stack["saber"].name == "Saber"
        assert loaded.stack["saber"].slots[0].model == "chadrock-35b-ace-saber"

    def test_invalid_toml_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "stacks.toml"
        p.write_bytes(b"[stack\nbad toml <<<")
        with pytest.raises(ConfigParseError):
            load_stacks_config(path=p)

    def test_unknown_field_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "stacks.toml"
        p.write_bytes(b'[stack.x]\nname = "X"\nnot_a_field = "surprise"\n')
        with pytest.raises(ConfigParseError):
            load_stacks_config(path=p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/dev/hal0/.venv/bin/python -m pytest tests/config/test_stacks_loader.py -q`
Expected: FAIL with `ImportError: cannot import name 'load_stacks_config' from 'hal0.config.loader'`

- [ ] **Step 3a: Add the path helper**

In `src/hal0/config/paths.py`, after `profiles_toml()` (~line 252), add:

```python
def stacks_toml() -> Path:
    """Return the stack catalog path (/etc/hal0/stacks.toml).

    The file is optional — :func:`hal0.config.loader.load_stacks_config`
    returns the built-in seed stacks (empty until they ship) when absent.

    FHS:       /etc/hal0/stacks.toml
    HAL0_HOME: $HAL0_HOME/etc/hal0/stacks.toml
    """
    return etc() / "stacks.toml"
```

- [ ] **Step 3b: Extend the loader's schema import**

In `src/hal0/config/loader.py`, the import from `hal0.config.schema` (~line 29-39) currently lists `ProfileConfig, ProfilesConfig, ...`. Add `SEED_STACKS` and `StacksConfig` to that import list (alphabetical placement within the existing parenthesized import):

```python
from hal0.config.schema import (
    CURRENT_SCHEMA_VERSION,
    SEED_PROFILES,
    SEED_STACKS,
    AgentConfig,
    Hal0Config,
    HardwareInfo,
    ProfileConfig,
    ProfilesConfig,
    ProvidersConfig,
    SlotConfig,
    StacksConfig,
    UpstreamsConfig,
)
```

- [ ] **Step 3c: Add the loader functions**

In `src/hal0/config/loader.py`, after `save_profiles_config` (~line 449), add:

```python
# ── stacks.toml ───────────────────────────────────────────────────────────────


def load_stacks_config(path: Path | None = None) -> StacksConfig:
    """Load and validate /etc/hal0/stacks.toml.

    Returns the built-in seed stacks (``SEED_STACKS``, empty until they ship)
    when the file is absent, so the catalog is always well-formed on a fresh
    install. When the file is present, only its contents are returned — seeds
    are NOT merged in (load REPLACES, mirroring ``load_profiles_config``).

    Raises:
        ConfigParseError: If the TOML is malformed or fails validation.
    """
    target = path if path is not None else paths.stacks_toml()
    if not Path(target).exists():
        return StacksConfig.model_validate({"stack": SEED_STACKS})
    raw = _read_toml(Path(target))
    try:
        return StacksConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate stacks.toml at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc


def save_stacks_config(cfg: StacksConfig, path: Path | None = None) -> None:
    """Atomically write the full stack catalog to stacks.toml.

    The written file is the single source of truth; callers must pass the
    COMPLETE catalog (start from ``load_stacks_config()``, then add/modify)
    so existing stacks survive the round trip. ``exclude_none=True`` keeps
    tomli_w from raising on optional None fields, mirroring
    :func:`save_profiles_config`.
    """
    target = Path(path) if path is not None else paths.stacks_toml()
    write_toml_atomic(target, cfg.model_dump(mode="python", exclude_none=True))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/dev/hal0/.venv/bin/python -m pytest tests/config/test_stacks_loader.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/halo/dev/wt/stacks-spec
git add src/hal0/config/paths.py src/hal0/config/loader.py tests/config/test_stacks_loader.py
git commit -m "feat(stacks): stacks.toml path helper + atomic loader/saver"
```

---

## Task 3: StacksCatalog (CRUD + guards)

**Files:**
- Create: `src/hal0/stacks/__init__.py`
- Test: `tests/stacks/test_stacks_catalog.py`

**Interfaces:**
- Consumes: `load_stacks_config`/`save_stacks_config` (Task 2); `StackConfig`/`StackSlotEntry`/`StackModelMeta`/`ProfileConfig` (for `ResolvedStack` typing) and the `schema.SEED_STACKS` module attribute (Task 1); `NotFound`/`Conflict` (`hal0.errors`); `paths.stacks_toml`. Defines its own compiled `_STACK_NAME_RE` (does not import the schema string version).
- Produces:
  - `ResolvedStack` dataclass — `slug: str`, `name: str`, `description: str`, `author: str`, `icon: str`, `tags: tuple[str, ...]`, `slots: list[StackSlotEntry]`, `profiles: dict[str, ProfileConfig]`, `models: dict[str, StackModelMeta]`, `schema_version: int`, `hal0_version: str`, `seed: bool`.
  - `StacksCatalog` class with `list() -> list[ResolvedStack]`, `resolve(slug) -> ResolvedStack`, `create(slug, StackConfig) -> ResolvedStack`, `update(slug, StackConfig) -> ResolvedStack` (full replace), `delete(slug) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/stacks/test_stacks_catalog.py`:

```python
"""Unit tests for StacksCatalog CRUD + guards.

Targeted file run:
    ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_stacks_catalog.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config import schema
from hal0.config.schema import StackConfig, StackSlotEntry
from hal0.errors import Conflict, NotFound
from hal0.stacks import ResolvedStack, StacksCatalog


@pytest.fixture
def catalog(tmp_path: Path) -> StacksCatalog:
    return StacksCatalog(path=tmp_path / "stacks.toml")


def _saber() -> StackConfig:
    return StackConfig(
        name="Saber",
        description="high-speed agentic MoE",
        slots=[StackSlotEntry(slot="agent", model="chadrock-35b-ace-saber")],
    )


class TestCreateAndRead:
    def test_create_then_resolve(self, catalog: StacksCatalog) -> None:
        created = catalog.create("saber", _saber())
        assert isinstance(created, ResolvedStack)
        assert created.slug == "saber"
        assert created.seed is False
        got = catalog.resolve("saber")
        assert got.name == "Saber"
        assert got.slots[0].slot == "agent"

    def test_create_then_list(self, catalog: StacksCatalog) -> None:
        catalog.create("saber", _saber())
        slugs = [r.slug for r in catalog.list()]
        assert slugs == ["saber"]

    def test_create_duplicate_raises_conflict(self, catalog: StacksCatalog) -> None:
        catalog.create("saber", _saber())
        with pytest.raises(Conflict):
            catalog.create("saber", _saber())

    def test_create_invalid_slug_raises_conflict(self, catalog: StacksCatalog) -> None:
        with pytest.raises(Conflict):
            catalog.create("Saber Slot!", _saber())

    def test_resolve_missing_raises_not_found(self, catalog: StacksCatalog) -> None:
        with pytest.raises(NotFound):
            catalog.resolve("ghost")


class TestUpdateAndDelete:
    def test_update_replaces(self, catalog: StacksCatalog) -> None:
        catalog.create("saber", _saber())
        updated = catalog.update("saber", StackConfig(name="Saber v2"))
        assert updated.name == "Saber v2"
        assert updated.slots == []

    def test_update_missing_raises_not_found(self, catalog: StacksCatalog) -> None:
        with pytest.raises(NotFound):
            catalog.update("ghost", _saber())

    def test_delete(self, catalog: StacksCatalog) -> None:
        catalog.create("saber", _saber())
        catalog.delete("saber")
        assert catalog.list() == []

    def test_delete_missing_raises_not_found(self, catalog: StacksCatalog) -> None:
        with pytest.raises(NotFound):
            catalog.delete("ghost")


class TestSeedGuard:
    def test_seed_stack_is_immutable(self, catalog: StacksCatalog, monkeypatch: pytest.MonkeyPatch) -> None:
        # Inject a seed entry so the guard has something to protect (SEED_STACKS
        # is empty until PR-6). The catalog reads SEED_STACKS from the schema
        # module. No create() call: update()/delete() run _guard_custom() FIRST,
        # so they raise on a seed slug regardless of whether it is on disk —
        # and load_stacks_config already surfaces seeds when the file is absent.
        monkeypatch.setitem(schema.SEED_STACKS, "saber", _saber())
        with pytest.raises(Conflict):
            catalog.update("saber", StackConfig(name="hijack"))
        with pytest.raises(Conflict):
            catalog.delete("saber")


class TestPersistence:
    def test_persists_across_catalog_instances(self, tmp_path: Path) -> None:
        path = tmp_path / "stacks.toml"
        StacksCatalog(path=path).create("saber", _saber())
        # Fresh catalog instance reads the written file.
        assert any(r.slug == "saber" for r in StacksCatalog(path=path).list())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_stacks_catalog.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hal0.stacks'`

- [ ] **Step 3: Implement the catalog**

Create `src/hal0/stacks/__init__.py`:

```python
"""StacksCatalog — read and mutate the stack catalog through one interface.

A stack is a named bundle of slots + embedded profiles + embedded model
metadata. This module concentrates the stack interface, mirroring
``hal0.profiles.ProfileCatalog``:

* full-catalog reads and atomic full-catalog writes (single stacks.toml);
* seed immutability and duplicate-slug checks;
* slug validation.

Routes (PR-4) and the apply engine (PR-2) are adapters over this module.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from hal0.config import paths, schema
from hal0.config.loader import load_stacks_config, save_stacks_config
from hal0.config.schema import (
    ProfileConfig,
    StackConfig,
    StackModelMeta,
    StackSlotEntry,
)
from hal0.errors import Conflict, NotFound

log = logging.getLogger(__name__)

_STACK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class ResolvedStack:
    """A stack plus derived fields (slug + seed flag) for API/UI consumption."""

    slug: str
    name: str
    description: str
    author: str
    icon: str
    tags: tuple[str, ...]
    slots: list[StackSlotEntry]
    profiles: dict[str, ProfileConfig]
    models: dict[str, StackModelMeta]
    schema_version: int
    hal0_version: str
    seed: bool


class StacksCatalog:
    """Read and mutate the stack catalog through one interface."""

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _path_or_default(self) -> Path:
        return self._path or paths.stacks_toml()

    def list(self) -> list[ResolvedStack]:
        cfg = load_stacks_config(self._path)
        return [self._resolve_item(slug, stack) for slug, stack in cfg.stack.items()]

    def resolve(self, slug: str) -> ResolvedStack:
        cfg = load_stacks_config(self._path)
        stack = cfg.stack.get(slug)
        if stack is None:
            raise NotFound(
                f"stack {slug!r} not found",
                code="stacks.not_found",
                details={"stack": slug, "available": sorted(cfg.stack)},
            )
        return self._resolve_item(slug, stack)

    def create(self, slug: str, stack: StackConfig) -> ResolvedStack:
        self._validate_name(slug)
        with self._lock:
            catalog = load_stacks_config(self._path)
            if slug in catalog.stack:
                raise Conflict(
                    f"stack {slug!r} already exists",
                    code="stacks.exists",
                    details={"stack": slug},
                )
            catalog.stack[slug] = stack
            save_stacks_config(catalog, self._path)
        return self._resolve_item(slug, stack)

    def update(self, slug: str, stack: StackConfig) -> ResolvedStack:
        """Replace the stack body wholesale (PUT semantics)."""
        self._guard_custom(slug)
        with self._lock:
            catalog = load_stacks_config(self._path)
            if slug not in catalog.stack:
                raise NotFound(
                    f"stack {slug!r} not found",
                    code="stacks.not_found",
                    details={"stack": slug},
                )
            catalog.stack[slug] = stack
            save_stacks_config(catalog, self._path)
        return self._resolve_item(slug, stack)

    def delete(self, slug: str) -> None:
        self._guard_custom(slug)
        with self._lock:
            catalog = load_stacks_config(self._path)
            if slug not in catalog.stack:
                raise NotFound(
                    f"stack {slug!r} not found",
                    code="stacks.not_found",
                    details={"stack": slug},
                )
            del catalog.stack[slug]
            save_stacks_config(catalog, self._path)

    def _resolve_item(self, slug: str, stack: StackConfig) -> ResolvedStack:
        return ResolvedStack(
            slug=slug,
            name=stack.name,
            description=stack.description,
            author=stack.author,
            icon=stack.icon,
            tags=tuple(stack.tags),
            slots=stack.slots,
            profiles=stack.profiles,
            models=stack.models,
            schema_version=stack.schema_version,
            hal0_version=stack.hal0_version,
            seed=slug in schema.SEED_STACKS,
        )

    def _guard_custom(self, slug: str) -> None:
        if slug in schema.SEED_STACKS:
            raise Conflict(
                f"stack {slug!r} is a seed stack — seed stacks are immutable; "
                "clone under a new name",
                code="stacks.seed_immutable",
                details={"stack": slug},
            )

    def _validate_name(self, slug: str) -> None:
        if not _STACK_NAME_RE.match(slug):
            raise Conflict(
                "stack slug must be kebab-case (a-z0-9_-), ≤32 chars, start with alphanumeric",
                code="stacks.invalid_name",
                details={"stack": slug},
            )
```

Note: the seed guard reads `schema.SEED_STACKS` (module attribute access, not a captured import) so the test's `monkeypatch.setitem(schema.SEED_STACKS, ...)` is observed.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_stacks_catalog.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Run the full new-test set + a sanity import**

Run: `~/dev/hal0/.venv/bin/python -m pytest tests/config/test_stacks_schema.py tests/config/test_stacks_loader.py tests/stacks/test_stacks_catalog.py -q`
Expected: PASS (28 tests total)

Run: `~/dev/hal0/.venv/bin/python -c "from hal0.stacks import StacksCatalog; from hal0.config.loader import load_stacks_config; print('ok')"`
Expected: prints `ok`

- [ ] **Step 6: Commit**

```bash
cd /home/halo/dev/wt/stacks-spec
git add src/hal0/stacks/__init__.py tests/stacks/test_stacks_catalog.py
git commit -m "feat(stacks): StacksCatalog CRUD with slug + seed guards"
```

---

## PR-1 Done — Definition of Done

- `StackConfig` and friends validate and round-trip through TOML.
- `stacks.toml` loads/saves atomically; absent file yields the (empty) seed set; malformed file raises `ConfigParseError`.
- `StacksCatalog` does list/resolve/create/update/delete with duplicate, slug, seed, and not-found guards, and persists across instances.
- 28 new tests pass; no existing test regressed (`~/dev/hal0/.venv/bin/python -m pytest tests/config tests/stacks -q`).
- No routes/MCP/UI/apply/export yet — those are PRs 2–6.

---

## Roadmap — remaining PRs (each gets its own plan, written against PR-1's merged code)

- **PR-2 — Apply engine + drift.** Translate a `StackConfig` into a `SlotConfigStore` `ChangeSet` (slot TOML + capabilities.toml + embedded profiles), expose dry-run (compute-only) vs commit (atomic, rollback) + Phase-B `SlotManager` lifecycle convergence (load named, unload rest), and add the active-stack pointer (`/var/lib/hal0/stacks/state.json`) + content-hash drift status (`clean`/`modified`/`none`). Plan written against the real `SlotConfigStore.apply/commit/revert` and `CapabilityOrchestrator.apply` signatures.
- **PR-3 — Export / import.** `.hal0stack.json` envelope (`kind`, `schema_version`, `hal0_version`, `exported_at`, `checksum`, `stack`); build `StackModelMeta` from registry `Model`s on export; on import, run the schema-version walk, then the resolve-pass (present / pullable via `registry/pull.py` / unresolvable) and snapshot-from-live.
- **PR-4 — REST + MCP.** `src/hal0/api/routes/stacks.py` mirroring `routes/profiles.py` (list/create/get/update/delete + apply?dry_run + export + import?dry_run + snapshot); register on the app; MCP admin tools `stack_list`/`stack_status` (autonomous) + `stack_apply`/`stack_import`/`stack_delete` (gated via ApprovalQueue). Route tests mirror `tests/api/test_profiles_*`.
- **PR-5 — Dashboard UI.** `#slots/stacks` sub-page (nav child in `chrome.jsx` + `renderView` case in `main.jsx`); `ui/src/api/hooks/useStacks.ts` + `endpoints.ts` constants copying `useProfiles.ts`; card grid (reuse `DCard`/`StatusDot`) with Active ribbon + drift badge; editor `Drawer` slot-picker; diff-preview modal; import file-drop with resolve report + Pull buttons.
- **PR-6 — Seed stacks + docs.** Fill `SEED_STACKS` with `saber`/`forge`/`pi` (exact registry ids per spec §10) as installer seed TOML under `installer/etc-hal0/`; add a Stacks doc page in `hal0-web`.
