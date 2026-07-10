"""publish.py — the public roster contract (DESIGN §9).

``build_roster`` renders the ``roster.json`` schema-1 contract (DESIGN §9.1)
from the store: one entry per roster model with the current summary numbers PLUS
the per-run ``detail`` block the upgraded docs table now shows (measured-on date,
lane, image/build, depth/sampler/reps/stddev, TTFT, argv digest, and the
history sparkline series). ``write_roster`` persists it under the state root;
``emit_site_ts`` is the (stubbed) generator for the site repo's data file.

WHY roster.json is the interface (not a live endpoint): publishing stays a
diffable, revertible PR to the website repo (DESIGN §9.2) — the deliberate
scale-down from a live leaderboard. ``build_roster`` is a pure read over the
store so `publish --check` can diff without writing.

The host block carries ``hal0`` (the hal0 version, DESIGN §9.1) so the public
methodology aside can render "measured on hal0 X.Y.Z" from data instead of prose
that drifts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .store import Store

ROSTER_SCHEMA = 1


def _detail_from_record(rec: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    """The per-model ``detail`` block (DESIGN §9.1) from a current record + its
    trend history."""
    identity = rec.get("identity", {})
    engine = identity.get("engine", {})
    workload = identity.get("workload", {})
    summary = rec.get("summary", {})
    host = rec.get("host", {})
    return {
        "run_id": rec.get("run_id"),
        "measured": _date(rec.get("run_id")),
        "lane": identity.get("lane"),
        "image": engine.get("image"),
        "llamacpp_build": engine.get("llamacpp_build"),
        "hal0": host.get("hal0_version"),
        "depth": workload.get("depth"),
        "sampler": (workload.get("sampler") or {}).get("mode"),
        "reps": len(rec.get("reps") or []),
        "stddev": summary.get("decode_ts_stddev"),
        "ttft_ms_p50": summary.get("ttft_ms_p50"),
        "argv_digest": rec.get("cell_key"),  # cell_key already content-addresses argv+all identity
        "history": [
            {"date": _date(h.get("run_id")), "decode_ts": h.get("decode_ts_med")} for h in history
        ],
    }


def build_roster(store: Store, host: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render the roster.json contract (DESIGN §9.1) from current cell values.

    One entry per model that has a current (newest ok) tg/decode record. The
    governing display number is decode t/s; prefill/accept are folded in from
    the same or sibling current cells for that model.
    """
    current = store.newest_ok_by_cell()  # cell_key -> newest ok record

    # Collapse to one representative record per PHYSICAL MODEL, keyed by the gguf
    # BASENAME (not the id): the same file can carry a clean registry id (from a
    # fresh run) AND a path-like id (from a v1 import) — grouping by id would show
    # it twice. The representative is the newest tg/decode record in the group
    # (decode_ts is the headline; newest wins the id/provenance shown).
    def _canon(model: dict[str, Any]) -> str:
        gguf = model.get("gguf") or ""
        return gguf.rsplit("/", 1)[-1] or model.get("id") or ""

    def _rank(rec: dict[str, Any]) -> tuple[int, str]:
        kind = ((rec.get("identity") or {}).get("workload") or {}).get("kind")
        return (1 if kind == "tg" else 0, rec.get("run_id") or "")

    by_canon: dict[str, dict[str, Any]] = {}
    prefill_by_canon: dict[str, tuple[str, float]] = {}  # canon -> (run_id, prefill)
    for rec in current.values():
        identity = rec.get("identity", {})
        model = identity.get("model", {})
        # The roster board is a board of MODELS. v1 server-ab records only knew a
        # slot name (agent/code/embed/rerank) and carry no gguf — skip them so a
        # slot never appears as a "model" (only real model files show).
        if not model.get("gguf"):
            continue
        canon = _canon(model)
        if not canon:
            continue
        kind = (identity.get("workload") or {}).get("kind")
        summary = rec.get("summary", {})
        if kind == "pp" and summary.get("prefill_ts_med") is not None:
            prev = prefill_by_canon.get(canon)
            rid = rec.get("run_id") or ""
            if prev is None or rid > prev[0]:  # newest pp's prefill
                prefill_by_canon[canon] = (rid, summary["prefill_ts_med"])
        cur = by_canon.get(canon)
        if cur is None or _rank(rec) > _rank(cur):
            by_canon[canon] = rec

    models: list[dict[str, Any]] = []
    for canon, rec in sorted(by_canon.items()):
        identity = rec.get("identity", {})
        model = identity.get("model", {})
        mid = model.get("id")
        config = identity.get("config", {})
        summary = rec.get("summary", {})
        history = store.history(cell_key=rec.get("cell_key"))
        kv = config.get("kv") or {}
        pf = prefill_by_canon.get(canon)
        models.append(
            {
                "id": mid,
                "gguf": model.get("gguf"),
                "decode_ts": summary.get("decode_ts_med"),
                "prefill_ts": pf[1] if pf else None,
                "accept": summary.get("accept_med"),
                "caps": model.get("caps") or [],
                "spec": (config.get("spec") or {}).get("type") if config.get("spec") else None,
                "kv": f"{kv.get('main_k', '?')}/{kv.get('main_v', '?')}" if kv else None,
                "size_gb": round(int(model.get("size_bytes", 0) or 0) / 1e9, 1) or None,
                "detail": _detail_from_record(rec, history),
            }
        )

    return {
        "schema": ROSTER_SCHEMA,
        "generated": _today(),
        "host": host or _default_host(current),
        "models": models,
    }


def write_roster(store: Store, roster: dict[str, Any] | None = None) -> Path:
    """Write ``roster.json`` under the state root (DESIGN §3.1 layout). Returns
    the path. Building it here (if not passed) keeps the CLI one call."""
    store.ensure_dirs()
    roster = roster if roster is not None else build_roster(store)
    path = store.root / "roster.json"
    path.write_text(json.dumps(roster, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def emit_site_ts(roster: dict[str, Any], out_path: Path | str) -> Path:
    """Generate the site repo's ``data/model-roster.ts`` from roster.json
    (DESIGN §9.2 stage 1). ``model-roster.ts`` stays the interface; this emits it.

    P2: render the EXACT site format the repo's ModelRoster.astro imports
    (const roster: ModelRosterEntry[] = [...] with ROSTER_DATE, expandable-row
    detail fields). The shape below is a faithful placeholder — valid TS that
    round-trips the data — pending the site repo's real type export.
    """
    out = Path(out_path)
    # P2: replace with the site's real ModelRosterEntry[] shape + type import.
    body = json.dumps(roster.get("models", []), indent=2, ensure_ascii=False)
    ts = (
        "// AUTO-GENERATED by `hal0 bench publish` — do not edit by hand.\n"
        f"export const ROSTER_DATE = {json.dumps(roster.get('generated'))};\n"
        f"export const ROSTER_HOST = {json.dumps(roster.get('host'))};\n"
        f"export const roster = {body} as const;\n"
    )
    out.write_text(ts, encoding="utf-8")
    return out


# -- small date/host helpers ------------------------------------------------- #


def _date(run_id: str | None) -> str | None:
    if not run_id:
        return None
    return run_id.split("T")[0] if "T" in run_id else run_id


def _today() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%d")


def _default_host(current: dict[str, Any]) -> dict[str, Any]:
    """Derive the roster host block from a current record's host (DESIGN §9.1
    host: gpu/mem_gb/hal0). Prefer a record with a populated ``hal0_version`` —
    v1-imported records carry an empty host, and picking one of those would show
    a blank "measured on hal0 …" on the public methodology aside. Falls back to
    any record's host, else empty."""
    best: dict[str, Any] | None = None
    for rec in current.values():
        h = rec.get("host") or {}
        block = {"gpu": h.get("gpu"), "mem_gb": h.get("mem_gb"), "hal0": h.get("hal0_version")}
        if h.get("hal0_version"):
            return block  # a real, attributable host — use it
        best = best or block
    return best or {"gpu": None, "mem_gb": None, "hal0": None}
