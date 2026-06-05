"""Config loaders — read and validate TOML files at startup.

All loaders return validated pydantic models.  A ValidationError at startup
means the user has a malformed config file; the error message includes the
field path (PLAN.md §5 Tier 1: "Typos in [slot] backend = vukan raise at
startup with the field path").

Atomic writes mirror hal0.config.env.write_env_atomic: write to a tmpfile
in the same directory, fsync, then os.replace().  If the process dies
mid-write the prior file is left intact.

Port target: haloai lib/config.py (420 lines).
See PLAN.md §3 and §5 Tier 1.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from hal0.config import paths
from hal0.config.schema import (
    CURRENT_SCHEMA_VERSION,
    AgentConfig,
    Hal0Config,
    HardwareInfo,
    ProvidersConfig,
    SlotConfig,
    UpstreamsConfig,
)
from hal0.errors import Hal0Error

# ── Typed errors ──────────────────────────────────────────────────────────────


class ConfigError(Hal0Error):
    """Base class for config load/save errors."""

    code = "config.error"
    status = 500


class ConfigNotFound(ConfigError):
    """A required config file does not exist."""

    code = "config.not_found"
    status = 404


class ConfigParseError(ConfigError):
    """A config file is present but contains invalid TOML or fails validation."""

    code = "config.parse_error"
    status = 500


# ── Atomic TOML write ─────────────────────────────────────────────────────────


def write_toml_atomic(path: Path | str, data: dict[str, Any]) -> None:
    """Write a TOML file atomically.

    Mirrors hal0.config.env.write_env_atomic but for TOML payloads:
    write to a tempfile in the same directory, fsync, then os.replace().
    The rename is atomic on POSIX when src and dst share a mount; because
    the tempfile is created in the same directory, that invariant holds.

    Args:
        path: Destination path for the TOML file.
        data: Mapping that tomli_w.dump understands.

    Raises:
        OSError: If the directory cannot be created, disk full, or the
                 rename fails for a filesystem reason.
        TypeError: If data contains non-TOML-encodable values.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, path)
        tmp_path = None  # rename succeeded; don't clean up in finally
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)


# ── TOML reader ───────────────────────────────────────────────────────────────


def _read_toml(path: Path) -> dict[str, Any]:
    """Read a TOML file and return its contents as a dict.

    Raises:
        ConfigNotFound: If the file does not exist.
        ConfigParseError: If the file cannot be parsed as TOML.
    """
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError as exc:
        raise ConfigNotFound(
            f"config file not found: {path}",
            details={"path": str(path)},
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigParseError(
            f"failed to parse TOML at {path}: {exc}",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


# ── hal0.toml ─────────────────────────────────────────────────────────────────


def load_hal0_config(path: Path | None = None) -> Hal0Config:
    """Load and validate hal0.toml.

    Args:
        path: Override path.  If None, uses hal0.config.paths.hal0_toml().

    Returns:
        A validated Hal0Config.  If the file does not exist, returns the
        default config (all defaults, schema_version=CURRENT_SCHEMA_VERSION).

    Raises:
        ConfigParseError: If the TOML is malformed or fails validation.
    """
    target = path if path is not None else paths.hal0_toml()
    if not Path(target).exists():
        return Hal0Config()
    raw = _read_toml(Path(target))
    try:
        return Hal0Config.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate hal0 config at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc


def save_hal0_config(cfg: Hal0Config, path: Path | None = None) -> None:
    """Atomically write hal0.toml.

    Args:
        cfg: Validated Hal0Config to persist.
        path: Override path.  If None, uses hal0.config.paths.hal0_toml().
    """
    target = path if path is not None else paths.hal0_toml()
    # ``exclude_none=True`` keeps tomli_w happy — None has no TOML
    # representation and tomli_w raises TypeError on it. Pydantic
    # re-supplies the default on load, so dropping None on write is
    # safe for any field whose default is None (e.g. the ADR-0014
    # ``memory.graph.upstream`` block when the user picks a local
    # route).
    data = cfg.model_dump(mode="python", exclude_none=True)
    write_toml_atomic(target, data)


# ── slots/<name>.toml ─────────────────────────────────────────────────────────


def load_slot_config(slot_name: str, path: Path | None = None) -> SlotConfig:
    """Load and validate /etc/hal0/slots/<slot_name>.toml.

    The on-disk shape (per haloai lib/config.py) nests fields under
    [slot], [model], etc.  We normalise that into the flat SlotConfig
    shape: [slot] keys hoist to the top level, [model] stays nested,
    everything else lands in ``extra``.

    Args:
        slot_name: e.g. "primary", "embed", "stt", "tts".
        path: Override path.  If None, uses
              hal0.config.paths.slots_config_dir() / f"{slot_name}.toml".

    Returns:
        A validated SlotConfig.

    Raises:
        ConfigNotFound: If the slot TOML doesn't exist.
        ConfigParseError: If the TOML is malformed or fails validation.
    """
    target = path if path is not None else paths.slots_config_dir() / f"{slot_name}.toml"
    raw = _read_toml(Path(target))
    flattened = _flatten_slot_toml(raw, slot_name=slot_name)
    try:
        return SlotConfig.model_validate(flattened)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate slot config {slot_name!r} at {target}: {exc}",
            details={"path": str(target), "slot": slot_name, "reason": str(exc)},
        ) from exc


def save_slot_config(cfg: SlotConfig, path: Path | None = None) -> None:
    """Atomically write a slot config TOML.

    The pydantic SlotConfig is flat; we re-nest into the on-disk shape
    that haloai writes ([slot] / [model] sections) so hand-edits stay
    readable.

    Args:
        cfg: Validated SlotConfig to persist.
        path: Override path.  If None, derives from
              paths.slots_config_dir() / f"{cfg.name}.toml".
    """
    target = path if path is not None else paths.slots_config_dir() / f"{cfg.name}.toml"
    data = _unflatten_slot_toml(cfg)
    write_toml_atomic(target, data)


def list_slots() -> list[str]:
    """Return all configured slot names (stems of /etc/hal0/slots/*.toml)."""
    d = paths.slots_config_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.toml"))


# ── slot TOML shape helpers ──────────────────────────────────────────────────


def _flatten_slot_toml(raw: dict[str, Any], slot_name: str) -> dict[str, Any]:
    """Hoist [slot] keys to top-level so SlotConfig.model_validate works.

    The on-disk shape (haloai-compatible) is::

        [slot]
        name = "primary"
        port = 8081
        backend = "vulkan"

        [model]
        default = "qwen3-4b-q4_k_m"

        [defaults]
        threads = 12
        ...

    SlotConfig expects ``name`` and ``port`` at the top level, ``model``
    as a nested key, and stashes anything else under ``extra``.
    """
    slot_section = raw.get("slot", {}) if isinstance(raw, dict) else {}
    if not isinstance(slot_section, dict):
        slot_section = {}

    # Hoist [slot] keys; fall back to the on-disk filename for `name` if
    # the TOML omits it.
    out: dict[str, Any] = {**slot_section}
    out.setdefault("name", slot_name)

    # Nested [model] section.
    model_section = raw.get("model")
    if isinstance(model_section, dict):
        out["model"] = model_section

    # Anything else (e.g. [defaults], [server], custom sections) lands in
    # `extra` so we don't lose it on round-trip.
    extra: dict[str, Any] = {}
    for k, v in raw.items():
        if k in ("slot", "model"):
            continue
        extra[k] = v
    if extra:
        out["extra"] = extra

    return out


def _unflatten_slot_toml(cfg: SlotConfig) -> dict[str, Any]:
    """Inverse of _flatten_slot_toml — produce the on-disk shape.

    Round-trips both ``backend`` (deprecated) and ``device`` (v0.2) so a
    SlotConfig promoted from a legacy TOML doesn't silently lose its
    deprecated field. ``backend`` will be dropped in v0.3 once the
    deprecation window closes.
    """
    data = cfg.model_dump(mode="python", exclude_none=False)
    out: dict[str, Any] = {
        "slot": {
            "name": data["name"],
            "port": data["port"],
            "backend": data["backend"],
            "device": data["device"],
            "provider": data["provider"],
            "enabled": data["enabled"],
            "workers": data["workers"],
            "idle_timeout_s": data["idle_timeout_s"],
        },
        "model": data["model"],
    }
    extra = data.get("extra") or {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            # NOTE: keep user-authored sections (`[defaults]`, etc.) at
            # their original top-level position on round-trip.
            if k in ("slot", "model"):
                continue
            out[k] = v
    return out


# ── providers.toml ────────────────────────────────────────────────────────────


def load_providers_config(path: Path | None = None) -> ProvidersConfig:
    """Load and validate providers.toml.

    Returns an empty ProvidersConfig if the file does not exist.
    """
    target = path if path is not None else paths.etc() / "providers.toml"
    if not Path(target).exists():
        return ProvidersConfig()
    raw = _read_toml(Path(target))
    try:
        return ProvidersConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate providers.toml at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc


def save_providers_config(cfg: ProvidersConfig, path: Path | None = None) -> None:
    """Atomically write providers.toml."""
    target = path if path is not None else paths.etc() / "providers.toml"
    write_toml_atomic(target, cfg.model_dump(mode="python"))


# ── upstreams.toml ────────────────────────────────────────────────────────────


def load_upstreams_config(path: Path | None = None) -> UpstreamsConfig:
    """Load and validate upstreams.toml.

    Returns an empty UpstreamsConfig if the file does not exist.
    """
    target = path if path is not None else paths.etc() / "upstreams.toml"
    if not Path(target).exists():
        return UpstreamsConfig()
    raw = _read_toml(Path(target))
    try:
        return UpstreamsConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate upstreams.toml at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc


def save_upstreams_config(cfg: UpstreamsConfig, path: Path | None = None) -> None:
    """Atomically write upstreams.toml."""
    target = path if path is not None else paths.etc() / "upstreams.toml"
    write_toml_atomic(target, cfg.model_dump(mode="python"))


# ── agents/<name>.toml (ADR-0013) ─────────────────────────────────────────────


def load_agent_config(agent_name: str, path: Path | None = None) -> AgentConfig:
    """Load and validate ``/etc/hal0/agents/<agent_name>.toml`` (ADR-0013).

    Raises:
        ConfigNotFound: file missing.
        ConfigParseError: bad TOML or schema-validation failure.
    """
    target = path if path is not None else paths.agents_config_dir() / f"{agent_name}.toml"
    raw = _read_toml(Path(target))
    try:
        return AgentConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate agent config {agent_name!r} at {target}: {exc}",
            details={"path": str(target), "agent": agent_name, "reason": str(exc)},
        ) from exc


def save_agent_config(cfg: AgentConfig, path: Path | None = None) -> None:
    """Atomically write an agent config TOML (ADR-0013).

    ``exclude_none=True`` keeps tomli_w from choking on optional blocks
    (``auth.env``, ``server.url`` on builtins).
    """
    target = path if path is not None else paths.agents_config_dir() / f"{cfg.agent.name}.toml"
    data = cfg.model_dump(mode="python", exclude_none=True)
    write_toml_atomic(target, data)


def list_agent_configs() -> list[str]:
    """Return every configured agent name (stem of /etc/hal0/agents/*.toml)."""
    d = paths.agents_config_dir()
    if not d.exists():
        return []
    return sorted(f.stem for f in d.glob("*.toml") if f.is_file())


# ── hardware.json (JSON, not TOML) ────────────────────────────────────────────


def load_hardware_info(path: Path | None = None) -> HardwareInfo:
    """Load /etc/hal0/hardware.json and return a validated HardwareInfo.

    Returns the all-defaults HardwareInfo if the file is absent — the
    hardware module owns probing; callers that need real data should run
    `hal0 probe` first.

    Raises:
        ConfigParseError: If the JSON cannot be parsed or fails validation.
    """
    import json

    target = path if path is not None else paths.hardware_json()
    if not Path(target).exists():
        return HardwareInfo()
    try:
        raw = json.loads(Path(target).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigParseError(
            f"failed to parse hardware.json at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc
    try:
        return HardwareInfo.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate hardware.json at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc


def save_hardware_info(info: HardwareInfo, path: Path | None = None) -> None:
    """Atomically write hardware.json.

    Uses the same tmpfile+fsync+rename pattern as write_toml_atomic, but
    against JSON (hardware.json is human-readable JSON per PLAN.md §2).
    """
    import json

    target = Path(path if path is not None else paths.hardware_json())
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(info.model_dump(mode="python"), indent=2, sort_keys=True) + "\n"

    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, target)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)


# ── manifest.json (toolbox image digests) ─────────────────────────────────────


def _find_manifest_path() -> Path | None:
    """Locate the release manifest.

    Resolution order:
      1. ``paths.manifest_json()`` — /etc/hal0/manifest.json (installed).
      2. Repo root sibling of /usr/lib/hal0/current — dev installs that
         keep the manifest next to the source tree.  Only consulted when
         ``HAL0_HOME`` is NOT set, so unit tests with isolated
         tmp_hal0_home don't accidentally pick up the repo-root copy.

    Returns the first existing path, or None if neither is found.  The
    loader's callers fall back to ":v1" tag pulls in that case.
    """
    candidates: list[Path] = []
    installed = paths.manifest_json()
    candidates.append(installed)
    # Repo-root candidate: src/hal0/config/loader.py → ../../../manifest.json
    # Skip when HAL0_HOME is set — that env var means "isolated test home,
    # don't fall back to the source tree".
    if not os.environ.get("HAL0_HOME"):
        here = Path(__file__).resolve()
        candidates.append(here.parents[3] / "manifest.json")
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    """Load the release manifest.

    `scripts/update-toolbox-digests.sh` patches `toolbox_images.<name>.digest`
    with the published image's content digest (run before a release).
    Callers (notably the providers when constructing ContainerSpec.image)
    use the digest to pin pulls, falling back to the `tag` when digest is
    null/missing (see PLAN.md §12 and §17 Risks).

    Schema (see manifest.json at repo root for the canonical comment):
      {
        "_schema": "hal0.manifest.v1",
        "version": "...",
        "channel": "...",
        "toolbox_images": {
          "<name>": {"tag": "ghcr.io/.../:v1", "digest": "sha256:..." | null},
          ...
        }
      }

    Args:
        path: Explicit manifest path. Defaults to the FHS-aware resolver.

    Returns:
        Parsed manifest as a dict. Empty dict if no manifest is present
        (the runtime treats this as "pull by tag").

    Raises:
        ConfigParseError: The manifest file exists but is not valid JSON.
    """
    import json

    resolved = path if path is not None else _find_manifest_path()
    if resolved is None or not Path(resolved).is_file():
        return {}
    try:
        with open(resolved, "rb") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigParseError(
            f"failed to parse manifest at {resolved}: {exc}",
            details={"path": str(resolved), "reason": str(exc)},
        ) from exc


def manifest_image_ref(
    name: str,
    *,
    manifest: dict[str, Any] | None = None,
) -> str | None:
    """Return the pinned image reference for a toolbox image, if any.

    Resolution:
      - If `toolbox_images[name].digest` is a non-empty sha256:..., return
        the registry-qualified ref ``<tag-without-:v1-suffix>@<digest>``.
      - If only `tag` is present, return the tag as-is.
      - Else None.

    The runtime callers wire this into the existing
    ``HAL0_TOOLBOX_IMAGE_<BACKEND>`` env-var override pattern (see
    llama_server.py:image_ref) so no provider code needs to read the
    manifest directly — the installer materialises env vars per slot.

    Args:
        name: Short image key (vulkan, rocm, flm, moonshine, kokoro).
        manifest: Optional pre-loaded manifest dict. Loaded on demand
                  if omitted.

    Returns:
        Pull-ready image reference, or None if the manifest doesn't list
        this image.
    """
    if manifest is None:
        manifest = load_manifest()
    images = manifest.get("toolbox_images") or {}
    entry = images.get(name)
    if not isinstance(entry, dict):
        return None
    tag = entry.get("tag")
    digest = entry.get("digest")
    if digest and isinstance(digest, str) and digest.startswith("sha256:"):
        if tag and "@" not in str(tag):
            # Strip any :tag suffix from the registry ref before appending @digest.
            ref_no_tag = str(tag).rsplit(":", 1)[0] if ":" in str(tag).split("/")[-1] else str(tag)
            return f"{ref_no_tag}@{digest}"
        return str(tag) if tag else None
    return str(tag) if tag else None


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "ConfigError",
    "ConfigNotFound",
    "ConfigParseError",
    "list_agent_configs",
    "list_slots",
    "load_agent_config",
    "load_hal0_config",
    "load_hardware_info",
    "load_manifest",
    "load_providers_config",
    "load_slot_config",
    "load_upstreams_config",
    "manifest_image_ref",
    "save_agent_config",
    "save_hal0_config",
    "save_hardware_info",
    "save_providers_config",
    "save_slot_config",
    "save_upstreams_config",
    "write_toml_atomic",
]
