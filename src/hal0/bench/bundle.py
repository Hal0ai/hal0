"""bundle.py — the shareable result bundle (site-pipeline DESIGN, P1).

A bundle is a SELECTION + PACKAGING layer over the store: no new measurement,
no mutation. Only ``ok`` records are eligible (Outcome contract, schema.py) —
the public board must never carry a contended or failed number. The manifest
carries a sha256 for every member file and a ``bundle_id`` derived from those
hashes, so the server can verify integrity and dedupe re-uploads without
trusting the client.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .evalrun import read_evals
from .schema import canonical_json
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


BUNDLE_SCHEMA = 1


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _redact(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for rec in records:
        rec = copy.deepcopy(rec)
        if "host" in rec:
            rec["host"]["name"] = "redacted"
        out.append(rec)
    return out


def _hal0_version() -> str:
    try:
        import hal0

        return getattr(hal0, "__version__", "") or ""
    except ImportError:
        return ""


def write_bundle(
    store: Store, spec: BundleSpec, out_path: Path | str
) -> tuple[Path, dict[str, Any]]:
    """Build a bundle tar.gz at ``out_path``; returns (path, manifest).

    Members: manifest.json + records.jsonl + optional evals.jsonl +
    profiles/*.toml + optional artifacts/<run_id>/*. ``bundle_id`` is the
    sha256 of the canonical files-hash map — content-addressed, so a re-pack
    of identical content dedupes server-side, and any member tamper breaks it.
    """
    records = select_records(store, spec)
    if not records:
        raise ValueError("no ok records match the selection — nothing to bundle")
    if spec.redact_hostname:
        records = _redact(records)

    model_ids = {r.get("identity", {}).get("model", {}).get("id") for r in records}
    evals = [e for e in read_evals() if e.get("model") in model_ids]

    members: dict[str, bytes] = {}
    members["records.jsonl"] = ("\n".join(canonical_json(r) for r in records) + "\n").encode()
    if evals:
        members["evals.jsonl"] = ("\n".join(canonical_json(e) for e in evals) + "\n").encode()
    profiles_meta = []
    for p in spec.profile_paths:
        pp = Path(p)
        data = pp.read_bytes()
        arcname = f"profiles/{pp.name}"
        members[arcname] = data
        profiles_meta.append({"name": pp.name, "sha256": _sha256(data)})
    if spec.with_artifacts:
        for rec in records:
            run_id = rec["run_id"]
            adir = store.artifacts_root / run_id
            if adir.is_dir():
                for f in sorted(adir.rglob("*")):
                    if f.is_file():
                        members[f"artifacts/{run_id}/{f.relative_to(adir)}"] = f.read_bytes()

    files = {name: _sha256(data) for name, data in sorted(members.items())}
    bundle_id = "sha256:" + _sha256(canonical_json(files).encode())

    host = copy.deepcopy(records[-1].get("host", {}))  # newest record's env
    manifest = {
        "bundle_schema": BUNDLE_SCHEMA,
        "bundle_id": bundle_id,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hal0_version": _hal0_version(),
        "title": spec.title,
        "notes": spec.notes,
        "host": host,
        "records": [
            {
                "run_id": r["run_id"],
                "cell_key": r.get("cell_key", ""),
                "kind": r.get("identity", {}).get("workload", {}).get("kind", ""),
                "model_id": r.get("identity", {}).get("model", {}).get("id", ""),
            }
            for r in records
        ],
        "profiles": profiles_meta,
        "artifacts": spec.with_artifacts,
        "files": files,
    }

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tf:
        payload = {"manifest.json": json.dumps(manifest, indent=2).encode(), **members}
        for name in sorted(payload):
            data = payload[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = (
                0  # stable member metadata; determinism lives in bundle_id, not archive bytes
            )
            tf.addfile(info, io.BytesIO(data))
    return out, manifest
