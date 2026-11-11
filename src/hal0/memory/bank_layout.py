"""Which engine banks a memory store is laid out in (ADR-0058 decision 7).

A new store keeps hal0's memory in two banks: ``hal0:shared`` behind the
``shared`` namespace and ``hal0:private`` behind every ``private:<agent>``
namespace (``[memory] bank`` and ``[memory] private_bank`` name them). A store
that hal0 wrote before that keeps the banks it has, until the fold-in
(decision 7a) moves its contents. Which of the two a box is on is a fact about
its store, not about its config, so hal0 keeps it in one small state file::

    /var/lib/hal0/state/memory-layout.json
        {"version": 1, "layout": "two_bank", "captured": null,
         "source": "store-absent", "recorded_at": "2026-11-11T18:02:11+00:00"}

``layout`` is ``two_bank`` or the old layout (:data:`LAYOUT_LEGACY`). A record
of the old layout also holds what the old config said, under ``captured``:
the bank behind ``shared`` (``platform_bank``) and whether private memory
shared it (``unified``, the retired ``[memory] unified_bank``). This module is
the only reader and writer of the file. Nothing here moves a store from the
old layout to ``two_bank``; that is the fold-in's last step.

First run on a box with no record
---------------------------------

:func:`determine_layout`, in this order:

1. The store's location cannot be seen
   (:func:`hal0.memory.embedder_record.store_state` says ``unavailable``), or
   hal0-api is pointed at an engine on another host: the old layout,
   captured from the config, and not written down. Nothing can be concluded
   about a store hal0 cannot look at, and the old layout is what such a box
   ran until now.
2. A store exists: the old layout, captured from the config.
3. No store, but ``hal0.toml`` still carries ``[memory] unified_bank``, a key
   only the old schema wrote: the old layout. A box that was configured for
   the old banks and whose store is elsewhere is not a new install.
4. Otherwise: ``two_bank``. No store means no bank holds anything yet.

The capture: the old platform bank is ``[memory] bank`` when the operator
set it (and not to the two-bank default, which a config save writes back on
its own), else ``shared``, the old default; ``unified`` is the old key's
value, else ``true``, the old default.

Who writes it: the updater's post-activation pass, which ``install.sh`` also
runs before the engine first starts, so a new install records ``two_bank``
and an upgraded one the old layout; and, as a fallback, the provider factory when
hal0-api boots with no record. Both use the same rules, and an existing
record is never rewritten. A record file that cannot be read is never
overwritten either: for that run the box is treated as on the old layout, captured
from the config, which is what it ran before this record existed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog

from hal0.config.paths import var_lib
from hal0.install import perms
from hal0.security import nofollow

log = structlog.get_logger(__name__)

LAYOUT_LEGACY = "legacy"  # scar-ok: the old layout's name in the record, not a shim
LAYOUT_TWO_BANK = "two_bank"
_LAYOUTS = frozenset({LAYOUT_LEGACY, LAYOUT_TWO_BANK})

#: What ``GET /api/memory/health`` reports as ``banks.layout``.
HEALTH_LEGACY_UNIFIED = "legacy_unified"
HEALTH_LEGACY_SPLIT = "legacy_split"
HEALTH_TWO_BANK = "two_bank"

#: The bank behind ``shared`` before ``[memory] bank`` existed, and its
#: default until the two-bank layout.
LEGACY_PLATFORM_BANK = "shared"
#: The two banks of a new store; ``[memory] bank`` / ``private_bank`` defaults.
TWO_BANK_PLATFORM_BANK = "hal0:shared"
TWO_BANK_PRIVATE_BANK = "hal0:private"

RECORD_VERSION = 1
#: The record's key for what the old config said.
_CAPTURED = "captured"

#: How a layout was determined (the ``source`` field).
SOURCE_STORE_ABSENT = "store-absent"
SOURCE_STORE_EXISTS = "store-exists"
SOURCE_LEGACY_CONFIG = "old-config"
SOURCE_STORE_UNAVAILABLE = "store-unavailable"
SOURCE_REMOTE_ENGINE = "remote-engine"
SOURCE_RECORD_UNREADABLE = "record-unreadable"

#: Sources that describe a store hal0 could not look at. Never written down.
_UNSETTLED_SOURCES = frozenset(
    {SOURCE_STORE_UNAVAILABLE, SOURCE_REMOTE_ENGINE, SOURCE_RECORD_UNREADABLE}
)

#: 0644: a bank layout is not a secret, and ``hal0 doctor`` run as another
#: account may read it.
_RECORD_MODE = 0o644

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class BankLayout:
    """The store's bank layout.

    ``legacy_platform_bank`` and ``legacy_unified`` are set for the old
    layout only. ``recorded_at`` is ``None`` for a layout that was determined but is
    not in the state file.
    """

    layout: str
    legacy_platform_bank: str | None = None
    legacy_unified: bool | None = None
    source: str = ""
    recorded_at: str | None = None

    @property
    def two_bank(self) -> bool:
        return self.layout == LAYOUT_TWO_BANK

    @property
    def persisted(self) -> bool:
        return self.recorded_at is not None

    @property
    def health_layout(self) -> str:
        """``legacy_unified``, ``legacy_split`` or ``two_bank``."""
        if self.two_bank:
            return HEALTH_TWO_BANK
        return HEALTH_LEGACY_SPLIT if self.legacy_unified is False else HEALTH_LEGACY_UNIFIED

    def provider_kwargs(self, memory_cfg: Any) -> dict[str, Any]:
        """The ``HindsightProvider`` keywords this layout and ``[memory]`` give."""
        if self.two_bank:
            return {
                "two_bank": True,
                "unified_bank": False,
                "bank": str(getattr(memory_cfg, "bank", None) or TWO_BANK_PLATFORM_BANK),
                "private_bank": str(
                    getattr(memory_cfg, "private_bank", None) or TWO_BANK_PRIVATE_BANK
                ),
            }
        return {
            "two_bank": False,
            "unified_bank": self.legacy_unified is not False,
            "bank": legacy_platform_bank(self, memory_cfg),
        }


class LayoutRecordError(Exception):
    """The record file exists but cannot be read or does not hold a usable record."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path} {reason}")
        self.path = path
        self.reason = reason


def layout_record_path() -> Path:
    """``/var/lib/hal0/state/memory-layout.json`` (sandboxed under ``HAL0_HOME``)."""
    return var_lib() / "state" / "memory-layout.json"


def _explicit_bank(memory_cfg: Any) -> str | None:
    """``[memory] bank`` when the operator set it to something other than the
    two-bank default, else ``None``.

    A config save writes every field back with its value, so after an upgrade
    a box that never set the key can carry ``bank = "hal0:shared"`` without
    anyone choosing it. That value is never read as an old layout's bank.
    """
    fields_set = getattr(memory_cfg, "model_fields_set", None) or set()
    if "bank" not in fields_set:
        return None
    value = str(getattr(memory_cfg, "bank", "") or "").strip()
    if not value or value == TWO_BANK_PLATFORM_BANK:
        return None
    return value


def _legacy_unified(memory_cfg: Any) -> bool:
    """The retired ``[memory] unified_bank``, which the schema keeps only as an
    extra key; ``True``, its old default, when absent or not a boolean."""
    extra = getattr(memory_cfg, "model_extra", None) or {}
    value = extra.get("unified_bank") if isinstance(extra, dict) else None
    return value if isinstance(value, bool) else True


def legacy_platform_bank(layout: BankLayout, memory_cfg: Any) -> str:
    """The bank behind ``shared`` on a box still on the old layout.

    ``[memory] bank`` when the operator set it (ADR-0052: ``hal0 memory
    migrate platform``, then set the key), else the bank the record captured.
    """
    return _explicit_bank(memory_cfg) or layout.legacy_platform_bank or LEGACY_PLATFORM_BANK


def capture_legacy(memory_cfg: Any, *, source: str) -> BankLayout:
    """The old layout, holding what the old config said."""
    return BankLayout(
        layout=LAYOUT_LEGACY,
        legacy_platform_bank=_explicit_bank(memory_cfg) or LEGACY_PLATFORM_BANK,
        legacy_unified=_legacy_unified(memory_cfg),
        source=source,
    )


def _engine_is_remote(engine_url: str | None) -> bool:
    """True when hal0-api talks to an engine that is not on this box."""
    if not engine_url:
        return False
    host = (urlsplit(engine_url).hostname or "").lower()
    return bool(host) and host not in _LOOPBACK_HOSTS


def determine_layout(
    memory_cfg: Any,
    *,
    store: str | None = None,
    engine_url: str | None = None,
) -> BankLayout:
    """The layout of a box with no record (module docstring). Writes nothing.

    ``store`` is a :func:`hal0.memory.embedder_record.store_state` answer and
    ``engine_url`` the engine hal0-api is pointed at (``HAL0_HINDSIGHT_URL``);
    both are looked up when not given.
    """
    from hal0.memory.embedder_record import STORE_ABSENT, STORE_UNAVAILABLE, store_state

    if engine_url is None:
        engine_url = os.environ.get("HAL0_HINDSIGHT_URL")
    if _engine_is_remote(engine_url):
        return capture_legacy(memory_cfg, source=SOURCE_REMOTE_ENGINE)
    if store is None:
        store = store_state()
    if store == STORE_UNAVAILABLE:
        return capture_legacy(memory_cfg, source=SOURCE_STORE_UNAVAILABLE)
    if store != STORE_ABSENT:
        return capture_legacy(memory_cfg, source=SOURCE_STORE_EXISTS)
    extra = getattr(memory_cfg, "model_extra", None) or {}
    if isinstance(extra, dict) and "unified_bank" in extra:
        return capture_legacy(memory_cfg, source=SOURCE_LEGACY_CONFIG)
    return BankLayout(layout=LAYOUT_TWO_BANK, source=SOURCE_STORE_ABSENT)


def load_layout_record(path: Path | None = None) -> BankLayout | None:
    """The record in the state file, or ``None`` when there is no file.

    Raises :class:`LayoutRecordError` for a file that exists but cannot be
    read or does not hold a usable record.
    """
    target = path if path is not None else layout_record_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise LayoutRecordError(target, f"cannot be read ({type(exc).__name__})") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LayoutRecordError(target, "is not JSON") from exc
    if (
        not isinstance(data, dict)
        or data.get("layout") not in _LAYOUTS
        or not isinstance(data.get("recorded_at"), str)
    ):
        raise LayoutRecordError(target, "does not hold a memory layout record")
    source = data.get("source") if isinstance(data.get("source"), str) else ""
    if data["layout"] == LAYOUT_TWO_BANK:
        return BankLayout(layout=LAYOUT_TWO_BANK, source=source, recorded_at=data["recorded_at"])
    captured = data.get(_CAPTURED)
    bank = captured.get("platform_bank") if isinstance(captured, dict) else None
    unified = captured.get("unified") if isinstance(captured, dict) else None
    if not isinstance(bank, str) or not bank.strip() or not isinstance(unified, bool):
        raise LayoutRecordError(target, "names the old layout without its banks")
    return BankLayout(
        layout=LAYOUT_LEGACY,
        legacy_platform_bank=bank.strip(),
        legacy_unified=unified,
        source=source,
        recorded_at=data["recorded_at"],
    )


def save_layout_record(layout: BankLayout, path: Path | None = None) -> BankLayout:
    """Write ``layout`` to the state file, stamped now, and return what was written.

    Atomic. A root caller (``install.sh``, a root-run update) gives the file
    the state root's owner, so the service can read it. Raises ``OSError``.
    Replaces whatever is there: :func:`ensure_layout_record` checks first.
    """
    target = path if path is not None else layout_record_path()
    stamped = replace(layout, recorded_at=datetime.now(UTC).isoformat(timespec="seconds"))
    body: dict[str, Any] = {
        "version": RECORD_VERSION,
        "layout": stamped.layout,
        _CAPTURED: (
            {
                "platform_bank": stamped.legacy_platform_bank or LEGACY_PLATFORM_BANK,
                "unified": stamped.legacy_unified is not False,
            }
            if stamped.layout == LAYOUT_LEGACY
            else None
        ),
        "source": stamped.source,
        "recorded_at": stamped.recorded_at,
    }
    nofollow.write_file(
        target,
        json.dumps(body, indent=2) + "\n",
        mode=_RECORD_MODE,
        owner=perms.state_root_owner(),
    )
    return stamped


def ensure_layout_record(
    memory_cfg: Any,
    *,
    path: Path | None = None,
    store: str | None = None,
    engine_url: str | None = None,
) -> BankLayout:
    """The box's layout: the record, else :func:`determine_layout`, written down.

    A layout determined from a store hal0 could not look at is returned and
    not written. A record file that cannot be read is left alone and the box
    is treated as on the old layout for this run. Never raises for a failed write.
    """
    try:
        existing = load_layout_record(path)
    except LayoutRecordError as exc:
        log.warning("hal0.memory.layout_record_unreadable", path=str(exc.path), reason=exc.reason)
        return capture_legacy(memory_cfg, source=SOURCE_RECORD_UNREADABLE)
    if existing is not None:
        return existing
    determined = determine_layout(memory_cfg, store=store, engine_url=engine_url)
    if determined.source in _UNSETTLED_SOURCES:
        return determined
    try:
        saved = save_layout_record(determined, path)
    except OSError as exc:
        log.warning("hal0.memory.layout_record_write_failed", error=str(exc))
        return determined
    log.info(
        "hal0.memory.layout_recorded",
        layout=saved.layout,
        source=saved.source,
        legacy_platform_bank=saved.legacy_platform_bank,
        legacy_unified=saved.legacy_unified,
    )
    return saved


def current_layout(memory_cfg: Any, *, path: Path | None = None) -> BankLayout:
    """The box's layout for a reader that must not write: the record, else
    what :func:`determine_layout` says (the old layout for a record that
    cannot be read)."""
    try:
        existing = load_layout_record(path)
    except LayoutRecordError:
        return capture_legacy(memory_cfg, source=SOURCE_RECORD_UNREADABLE)
    return existing if existing is not None else determine_layout(memory_cfg)


def platform_bank(memory_cfg: Any, *, path: Path | None = None) -> str:
    """The engine bank behind ``shared`` on this box, without writing anything."""
    return str(current_layout(memory_cfg, path=path).provider_kwargs(memory_cfg)["bank"])


def record_memory_layout() -> BankLayout | None:
    """Update hook: write down this box's layout before anything reads it.

    Reads ``[memory]`` from ``hal0.toml`` and calls
    :func:`ensure_layout_record`. ``None`` when the config cannot be read.
    Never raises.
    """
    try:
        from hal0.config.loader import load_hal0_config

        memory_cfg = load_hal0_config().memory
    except Exception as exc:
        log.warning("hal0.memory.layout_record_skipped", error=str(exc))
        return None
    try:
        return ensure_layout_record(memory_cfg)
    except Exception as exc:  # an update must not die here
        log.warning("hal0.memory.layout_record_skipped", error=str(exc))
        return None


__all__ = [
    "HEALTH_LEGACY_SPLIT",
    "HEALTH_LEGACY_UNIFIED",
    "HEALTH_TWO_BANK",
    "LAYOUT_LEGACY",
    "LAYOUT_TWO_BANK",
    "LEGACY_PLATFORM_BANK",
    "TWO_BANK_PLATFORM_BANK",
    "TWO_BANK_PRIVATE_BANK",
    "BankLayout",
    "LayoutRecordError",
    "capture_legacy",
    "current_layout",
    "determine_layout",
    "ensure_layout_record",
    "layout_record_path",
    "legacy_platform_bank",
    "load_layout_record",
    "platform_bank",
    "record_memory_layout",
    "save_layout_record",
]
