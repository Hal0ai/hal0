"""bundle.py — the shareable result bundle (site-pipeline DESIGN, P1).

A bundle is a SELECTION + PACKAGING layer over the store: no new measurement,
no mutation. Only ``ok`` records are eligible (Outcome contract, schema.py) —
the public board must never carry a contended or failed number. The manifest
carries a sha256 for every member file and a ``bundle_id`` derived from those
hashes, so the server can verify integrity and dedupe re-uploads without
trusting the client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .store import Store


@dataclass
class BundleSpec:
    """What goes into a bundle. All selectors AND together; an empty spec
    selects every ok record (explicit upload is still a separate verb, so an
    over-wide bundle is inspectable before it goes anywhere)."""

    run_ids: list[str] | None = None
    suite: str | None = None
    since: str | None = None  # ISO lower bound, compared against run_id's stamp
    title: str = ""
    notes: str = ""
    with_artifacts: bool = False
    redact_hostname: bool = True
    profile_paths: list[str] = field(default_factory=list)


def _record_ts(rec: dict[str, Any]) -> str:
    run_id = rec.get("run_id") or ""
    return run_id.split("Z-")[0] + "Z" if "Z-" in run_id else run_id


def select_records(store: Store, spec: BundleSpec) -> list[dict[str, Any]]:
    """Filter records.jsonl down to the bundle's contents, append order kept."""
    wanted = set(spec.run_ids) if spec.run_ids else None
    out: list[dict[str, Any]] = []
    for rec in store.iter_records():
        if rec.get("outcome") != "ok":
            continue
        if wanted is not None and rec.get("run_id") not in wanted:
            continue
        if spec.suite and rec.get("suite") != spec.suite:
            continue
        if spec.since and _record_ts(rec) < spec.since:
            continue
        out.append(rec)
    return out
