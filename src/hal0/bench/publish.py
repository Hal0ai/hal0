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
from collections.abc import Callable, Iterable
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


def _basename(gguf: str) -> str:
    return gguf.rsplit("/", 1)[-1]


class PhysicalModels:
    """One identity resolver for "which physical model file did this record
    measure", shared by ``build_roster`` and the dashboard roster route so the
    board's rows, run counts, registry metadata and unmeasured-row dedupe all
    apply the SAME rules (#1825).

    Two records are the same physical model when any of these hold (union-find
    over gguf paths and model ids, built from every record that has a gguf):

    * they share a gguf path;
    * one path is a path-boundary suffix of exactly one longer path with the
      same basename — a v1 record's relative ``chat/Foo.gguf`` names
      ``/m/chat/Foo.gguf``;
    * they share a model ``id`` — the registry ``update`` path can move an entry
      to a new file and keep its immutable id, and the dashboard keys rows,
      caches, detail filters and queue references on that id.

    A group's key is the basename of its longest path when no other group has
    a file of that name — exactly the key the board always used, so a store
    with no collisions renders identically — and that full path otherwise
    (per-model directories store every pull as ``<dir>/model.gguf``).
    """

    def __init__(self, models: Iterable[dict[str, Any]]) -> None:
        self._parent: dict[str, str] = {}
        paths_by_base: dict[str, set[str]] = {}
        for model in models:
            gguf = model.get("gguf") or ""
            if not gguf:
                continue
            paths_by_base.setdefault(_basename(gguf), set()).add(gguf)
            self._union(_P + gguf, _P + gguf)
            if model.get("id"):
                self._union(_P + gguf, _I + model["id"])
        for paths in paths_by_base.values():
            roots: list[str] = []
            for path in sorted(paths, key=len, reverse=True):
                owners = [r for r in roots if r.endswith("/" + path)]
                if len(owners) == 1:
                    self._union(_P + path, _P + owners[0])
                else:
                    roots.append(path)

        group_paths: dict[str, list[str]] = {}
        self._group_ids: dict[str, set[str]] = {}
        for node in self._parent:
            root = self._find(node)
            if node.startswith(_P):
                group_paths.setdefault(root, []).append(node[len(_P) :])
            else:
                self._group_ids.setdefault(root, set()).add(node[len(_I) :])
        main_path = {r: min(ps, key=lambda p: (-len(p), p)) for r, ps in group_paths.items()}
        groups_by_base: dict[str, set[str]] = {}
        for root, ps in group_paths.items():
            for path in ps:
                groups_by_base.setdefault(_basename(path), set()).add(root)
        self._groups_by_base = groups_by_base
        self._key: dict[str, str] = {}
        for root, path in main_path.items():
            base = _basename(path)
            self._key[root] = base if len(groups_by_base[base]) == 1 else path
        self._paths = {p for ps in group_paths.values() for p in ps}

    def _find(self, node: str) -> str:
        parent = self._parent.setdefault(node, node)
        if parent != node:
            parent = self._parent[node] = self._find(parent)
        return parent

    def _union(self, a: str, b: str) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)

    def _root_of(self, model: dict[str, Any]) -> str | None:
        gguf = model.get("gguf") or ""
        if gguf and _P + gguf in self._parent:
            return self._find(_P + gguf)
        mid = model.get("id") or ""
        if mid and _I + mid in self._parent:
            return self._find(_I + mid)
        return None

    def key(self, model: dict[str, Any]) -> str:
        """The physical-model key for an ``identity.model`` (or roster row)
        dict. A model the store never saw with a gguf keys on its id."""
        root = self._root_of(model)
        if root is not None:
            return self._key[root]
        return model.get("id") or ""

    def _candidates(
        self, registry: Iterable[dict[str, Any]]
    ) -> list[tuple[str, int, dict[str, Any]]]:
        """Every (group root, rule rank, registry model) the join rules allow;
        lower rank is more specific."""
        regs = [r for r in registry if r.get("id") or r.get("path")]
        reg_by_base: dict[str, list[dict[str, Any]]] = {}
        for r in regs:
            if r.get("path"):
                reg_by_base.setdefault(_basename(r["path"]), []).append(r)

        out: list[tuple[str, int, dict[str, Any]]] = []
        offer = lambda root, rank, reg: out.append((root, rank, reg))  # noqa: E731

        for r in regs:
            rid, path = r.get("id") or "", r.get("path") or ""
            if rid and _I + rid in self._parent:
                offer(self._find(_I + rid), 0, r)
            if path and path in self._paths:
                offer(self._find(_P + path), 1, r)
        for p in self._paths:
            owners = [r for r in reg_by_base.get(_basename(p), []) if r["path"].endswith("/" + p)]
            if len(owners) == 1:
                offer(self._find(_P + p), 2, owners[0])
        for base, rs in reg_by_base.items():
            roots = self._groups_by_base.get(base, set())
            if len(rs) == 1 and len(roots) == 1:
                offer(next(iter(roots)), 3, rs[0])
        return out

    def match_registry(self, registry: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Map physical-model key -> the registry model it is, most specific
        rule first: exact id, exact path, a store path that is a path-boundary
        suffix of exactly one registry path, then the basename — only when it
        names ONE registry model AND one physical model in the store."""
        best: dict[str, tuple[int, dict[str, Any]]] = {}
        for root, rank, reg in self._candidates(registry):
            if root not in best or rank < best[root][0]:
                best[root] = (rank, reg)
        return {self._key[root]: reg for root, (_, reg) in best.items()}

    def on_keys(self, registry: Iterable[dict[str, Any]], keys: set[str]) -> list[dict[str, Any]]:
        """The registry models any join rule ties to one of ``keys`` — the
        ones already on the board, which must not get an unmeasured row."""
        return [reg for root, _, reg in self._candidates(registry) if self._key[root] in keys]


_P, _I = "p:", "i:"  # union-find node namespaces: gguf path / model id


def physical_model_keyer(models: Iterable[dict[str, Any]]) -> Callable[[dict[str, Any]], str]:
    """``PhysicalModels(models).key`` — see :class:`PhysicalModels`."""
    return PhysicalModels(models).key


def store_physical_models(store: Store) -> PhysicalModels:
    """:class:`PhysicalModels` over every record in ``store``, so the roster
    rows and the dashboard's run counts and registry join agree."""
    return PhysicalModels(
        (rec.get("identity") or {}).get("model") or {} for rec in store.iter_records()
    )


def build_roster(store: Store, host: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render the roster.json contract (DESIGN §9.1) from current cell values.

    One entry per model that has a current (newest ok) tg/decode record. The
    governing display number is decode t/s; prefill/accept are folded in from
    the same or sibling current cells for that model.
    """
    current = store.newest_ok_by_cell()  # cell_key -> newest ok record

    # Collapse to one representative record per PHYSICAL MODEL (not per id): the
    # same file can carry a clean registry id (from a fresh run) AND a path-like
    # id (from a v1 import) — grouping by id would show it twice. The key is the
    # gguf basename, or the full path where basenames collide (#1825; see
    # PhysicalModels). The representative is the newest tg/decode record
    # in the group (decode_ts is the headline; newest wins the id/provenance).
    _canon = store_physical_models(store).key

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
    # Order by basename first so a store with no collisions renders in exactly
    # the order it always did; colliding files then sort by full path.
    for canon, rec in sorted(by_canon.items(), key=lambda kv: (_basename(kv[0]), kv[0])):
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
