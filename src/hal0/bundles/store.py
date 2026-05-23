"""Bundle selection persistence + applier.

Once the operator clicks a tier in the first-run picker, two side
effects happen:

1. The :func:`mark_bundle_chosen` write drops a JSON marker at
   ``/var/lib/hal0/.bundle-chosen`` so the picker doesn't re-appear on
   next dashboard load.
2. The :func:`apply_bundle_to_capabilities` call walks the bundle's
   :class:`~hal0.bundles.schema.ModelEntry` list and forwards each
   row that maps onto the existing :class:`CapabilityOrchestrator`
   surface (``embed`` / ``rerank`` / ``stt`` / ``tts`` / ``img``). The
   ``chat.primary`` / ``chat.coder`` rows are recorded in the marker
   for PR-18 to pick up; the orchestrator has no chat surface in v0.2.

The applier is best-effort: per-row failures are logged but don't
abort the bundle pick. The marker is dropped regardless so the picker
doesn't loop. This matches the plan §8 framing — "Selecting a bundle
triggers model downloads in background (progress toast)" — the picker
is the commit point; the actual provisioning surfaces via the regular
slot lifecycle.

The NPU trio (FLM coresident ``agent`` / ``stt-npu`` / ``embed-npu``)
is recorded in the marker as ``npu_opt_in`` but not auto-enabled in
v0.2 even when the operator ticks the box; ADR-0010 / ADR-0004 keep
that gated behind the bundled-agent install flow.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hal0.bundles.schema import Bundle, BundleManifest, ModelEntry
from hal0.config import paths

log = logging.getLogger(__name__)


# Map a bundle ModelEntry.slot to the capability orchestrator
# (slot, child) tuple. Slots not in this map are recorded in the marker
# but not pushed through orchestrator.apply.
_SLOT_TO_CAPABILITY: dict[str, tuple[str, str]] = {
    "embed": ("embed", "embed"),
    "rerank": ("embed", "rerank"),
    "stt": ("voice", "stt"),
    "tts": ("voice", "tts"),
    "img": ("img", "img"),
}


@dataclass(frozen=True)
class BundleChoice:
    """The persisted record of a picker decision."""

    name: str
    npu_opt_in: bool
    chosen_at: str  # ISO-8601 UTC
    skipped: bool = False
    # The slot assignments recorded at pick time. Used by the dashboard
    # to show "this slot is part of the hal0-Pro bundle" lineage even
    # after the user customises individual rows.
    assignments: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "npu_opt_in": self.npu_opt_in,
            "chosen_at": self.chosen_at,
            "skipped": self.skipped,
            "assignments": list(self.assignments),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BundleChoice:
        return cls(
            name=str(data.get("name", "")),
            npu_opt_in=bool(data.get("npu_opt_in", False)),
            chosen_at=str(data.get("chosen_at", "")),
            skipped=bool(data.get("skipped", False)),
            assignments=tuple(data.get("assignments", ())),
        )


def marker_path() -> Path:
    """Return the bundle-chosen marker path (test-friendly indirection)."""

    return paths.bundle_chosen_marker()


def read_choice() -> BundleChoice | None:
    """Return the persisted choice, or None if the marker is absent."""

    path = marker_path()
    if not path.exists():
        return None
    try:
        return BundleChoice.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        # Corrupted marker — treat as missing so the picker reappears
        # rather than the operator getting stuck on a half-written file.
        log.warning("bundles.marker_unreadable", extra={"path": str(path)})
        return None


def is_picker_pending() -> bool:
    """Return True when the bundle picker should still be shown."""

    return read_choice() is None


def _utc_iso_now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")


def _write_marker(choice: BundleChoice) -> None:
    path = marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(choice.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def mark_bundle_chosen(
    name: str,
    *,
    npu_opt_in: bool = False,
    assignments: tuple[dict[str, Any], ...] = (),
) -> BundleChoice:
    """Persist a bundle pick. Atomic on a POSIX filesystem."""

    choice = BundleChoice(
        name=name,
        npu_opt_in=npu_opt_in,
        chosen_at=_utc_iso_now(),
        skipped=False,
        assignments=assignments,
    )
    _write_marker(choice)
    return choice


def mark_skipped() -> BundleChoice:
    """Persist the "Skip — configure manually" branch."""

    choice = BundleChoice(
        name="",
        npu_opt_in=False,
        chosen_at=_utc_iso_now(),
        skipped=True,
        assignments=(),
    )
    _write_marker(choice)
    return choice


async def apply_bundle_to_capabilities(
    manifest: BundleManifest,
    orchestrator: Any,
) -> list[dict[str, Any]]:
    """Forward each bundle ModelEntry to ``orchestrator.apply()`` where
    a capability mapping exists.

    Returns one record per entry with ``slot``, ``model_name``,
    ``applied`` (bool) and either ``selection`` (the orchestrator's
    echo) or ``error`` (the string message). Failures don't abort the
    walk — best-effort per ADR-0010's "progress toast" framing.
    """

    bundle: Bundle = manifest.bundle
    entries: list[ModelEntry] = []
    if bundle.primary is not None:
        entries.append(bundle.primary)
    if bundle.coder is not None:
        entries.append(bundle.coder)
    entries.extend(bundle.aux)

    results: list[dict[str, Any]] = []
    for entry in entries:
        record: dict[str, Any] = {
            "slot": entry.slot,
            "model_name": entry.model_name,
            "applied": False,
        }
        cap = _SLOT_TO_CAPABILITY.get(entry.slot)
        if cap is None:
            # chat.primary / chat.coder land here today; PR-18 owns the
            # chat surface wiring.
            record["reason"] = "no_capability_mapping"
            results.append(record)
            continue
        slot, child = cap
        try:
            selection = await orchestrator.apply(
                slot,
                child,
                {"model": entry.model_name, "enabled": True},
            )
        except Exception as exc:
            record["error"] = str(exc)
            log.warning(
                "bundles.apply_row_failed",
                extra={
                    "slot": entry.slot,
                    "model": entry.model_name,
                    "error": str(exc),
                },
            )
            results.append(record)
            continue
        record["applied"] = True
        record["selection"] = selection
        results.append(record)
    return results


__all__ = [
    "BundleChoice",
    "apply_bundle_to_capabilities",
    "is_picker_pending",
    "mark_bundle_chosen",
    "mark_skipped",
    "marker_path",
    "read_choice",
]
