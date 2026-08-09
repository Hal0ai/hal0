"""suites.py — the declarative suite layer (DESIGN §4).

A suite is *what to measure* x *under what config* x *when it's stale*, declared
once as TOML under ``/etc/hal0/bench/suites/*.toml`` and loaded here into typed
``Suite`` dataclasses. WHY TOML + stdlib ``tomllib``: the format is the operator/
agent contract and must parse with zero deps on the box (tomllib is stdlib on
3.11+, which is exactly why this package pins ``requires-python >=3.11`` and does
NOT depend on tomli — DESIGN task note).

The five tables mirror DESIGN §4 one-to-one: ``[suite]`` (identity + budget),
``[selector]`` (which models, resolved against the registry at plan time — the
planner does the resolving, not this loader), ``[matrix]`` (the axes),
``[cells]`` (measurement kinds), ``[staleness]`` (max age). Unknown keys are
ignored, not fatal, so a newer suite file stays loadable by an older lab.

A suite's ``[suite].schedule`` key never existed as a real hook: it was parsed
into a ``Suite.schedule`` field nothing ever read — no systemd timer or worker
consulted it. It has been removed outright (Phase 4), not merely ignored. The
actual cadence: ``installer/systemd/hal0-bench.timer`` fires weekly and always
runs the "roster" suite (``hal0 bench run --suite roster --scheduled``, gated
by ``run --scheduled``'s own politeness window — ``window.toml``,
:func:`window_file`); every other suite (``lane-matrix``, ``smoke``, ad-hoc
ones) is on-demand only, and the model-pull trigger ``smoke.toml`` used to
advertise has no wired hook at all (see that file).
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from hal0.config import paths as _paths


def suite_dir() -> Path:
    """The operator suite-TOML directory. ONE resolver for every surface —
    the CLI and the API route used to carry separate copies ("keep in sync"),
    and only the API honoured ``$HAL0_BENCH_SUITE_DIR`` (#1526). Precedence:
    ``$HAL0_BENCH_SUITE_DIR`` > ``/etc/hal0/bench/suites`` (HAL0_HOME-aware via
    :func:`hal0.config.paths.etc`, so sandboxed installs never read the real
    box's suites — #1518)."""
    env = os.environ.get("HAL0_BENCH_SUITE_DIR")
    if env:
        return Path(env)
    return _paths.etc() / "bench" / "suites"


def window_file() -> Path:
    """The --scheduled maintenance-window policy file (HAL0_HOME-aware)."""
    return _paths.etc() / "bench" / "window.toml"


@dataclass
class Selector:
    """Which models a suite targets. Resolved against the registry at plan time
    (planner.py), so this is just the declaration. ``caps_any`` matches registry
    capability tags; ``installed`` restricts to models present on the box;
    explicit include/exclude lists override.

    ``include_only = true`` makes the include list the ONLY selection source:
    an empty include selects NOTHING. Without it, an empty include falls back
    to the caps/installed filters — which for an operator-curated suite like
    lane-matrix meant its commented-out include list silently selected every
    installed GGUF (a multi-day accidental sweep)."""

    caps_any: list[str] = field(default_factory=list)
    installed: bool = True
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    include_only: bool = False


def _default_configs() -> list[dict]:
    return [{"label": "default", "flags": {}}]


@dataclass
class Matrix:
    """The measurement axes (DESIGN §4 [matrix]). ``lanes = ["default"]`` means
    each model's preferred profile lane; identity fields not listed here are
    pinned to model defaults by the planner.

    ``configs`` is the flag-tuning axis: a list of ``{label, flags}`` variants,
    each a set of whitelisted llama-bench tuning flags (``-b -ub -ngl -fa -ctk
    -ctv -t -mmp -pg``) the seam accepts. Every variant becomes its OWN cell
    (its flags land in the resolved argv, which feeds cell_key), so a suite can
    A/B batch size, flash-attn, KV quant, etc. and compare them side by side.
    Default = one ``"default"`` variant with no extra flags (llama-bench
    defaults) — so a suite that omits ``configs`` behaves exactly as before."""

    lanes: list[str] = field(default_factory=lambda: ["default"])
    depths: list[int] = field(default_factory=lambda: [2048])
    samplers: list[str] = field(default_factory=lambda: ["greedy"])
    reps: int = 3
    configs: list[dict] = field(default_factory=_default_configs)


@dataclass
class Cells:
    """Which measurement kinds run (DESIGN §4 [cells]): pp/tg/chat/batch/embed/
    rerank/reuse. MTP acceptance is implied when a selected model has the mtp
    cap (planner attaches it), so it is not listed as a kind."""

    kinds: list[str] = field(default_factory=lambda: ["pp", "tg"])


@dataclass
class Staleness:
    """When a cell's newest record is too old regardless of provenance drift
    (DESIGN §4 [staleness], §6 clause 2)."""

    max_age_days: int = 30


@dataclass
class Suite:
    """A fully-parsed suite. ``id`` is the stable handle used across the CLI and
    stamped into every record's ``suite`` field."""

    id: str
    description: str = ""
    budget_min: int = 240
    exclusive: bool = True
    priority: int = 50
    selector: Selector = field(default_factory=Selector)
    matrix: Matrix = field(default_factory=Matrix)
    cells: Cells = field(default_factory=Cells)
    staleness: Staleness = field(default_factory=Staleness)
    source_path: str = ""  # where it was loaded from (or "" for a virtual seed)


def suite_from_dict(data: dict, source_path: str = "") -> Suite:
    """Build a Suite from a parsed-TOML dict. Missing tables fall back to their
    dataclass defaults so a minimal suite file (just ``[suite]``) is valid."""
    suite_tbl = data.get("suite", {})
    sel = data.get("selector", {})
    mtx = data.get("matrix", {})
    cel = data.get("cells", {})
    stale = data.get("staleness", {})

    if "id" not in suite_tbl:
        raise ValueError(f"suite file {source_path or '<dict>'} missing [suite].id")

    return Suite(
        id=suite_tbl["id"],
        description=suite_tbl.get("description", ""),
        budget_min=int(suite_tbl.get("budget_min", 240)),
        exclusive=bool(suite_tbl.get("exclusive", True)),
        priority=int(suite_tbl.get("priority", 50)),
        selector=Selector(
            caps_any=list(sel.get("caps_any", [])),
            installed=bool(sel.get("installed", True)),
            include=list(sel.get("include", [])),
            exclude=list(sel.get("exclude", [])),
            include_only=bool(sel.get("include_only", False)),
        ),
        matrix=Matrix(
            lanes=list(mtx.get("lanes", ["default"])),
            depths=[int(d) for d in mtx.get("depths", [2048])],
            samplers=list(mtx.get("samplers", ["greedy"])),
            reps=int(mtx.get("reps", 3)),
            configs=_normalize_configs(mtx.get("configs")),
        ),
        cells=Cells(kinds=list(cel.get("kinds", ["pp", "tg"]))),
        staleness=Staleness(max_age_days=int(stale.get("max_age_days", 30))),
        source_path=source_path,
    )


def _normalize_configs(raw) -> list[dict]:
    """Normalise the ``[matrix].configs`` TOML into ``[{label, flags}]``.

    Accepts a list of tables (``[[matrix.configs]]`` with ``label`` + optional
    inline ``flags = { "-b" = 1024 }``). Missing/empty → the single ``default``
    variant. Flag values are stringified so they hash + shell-quote consistently."""
    if not raw:
        return _default_configs()
    out: list[dict] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        label = str(c.get("label") or f"cfg{len(out)}")
        flags = {str(k): str(v) for k, v in (c.get("flags") or {}).items()}
        out.append({"label": label, "flags": flags})
    return out or _default_configs()


def load_suite_file(path: Path | str) -> Suite:
    """Parse one ``*.toml`` suite file into a Suite."""
    p = Path(path)
    with p.open("rb") as fh:  # tomllib requires binary mode
        data = tomllib.load(fh)
    return suite_from_dict(data, source_path=str(p))


def load_suites(directory: Path | str) -> dict[str, Suite]:
    """Load every ``*.toml`` in a directory into an ``{id: Suite}`` map.

    A missing directory yields an empty map (the box may have only virtual seed
    suites, DESIGN §4 — seeds live in code, operator TOML overrides live here).
    A file that fails to parse is skipped with the error surfaced to stderr
    rather than aborting the whole load, so one bad override does not hide every
    other suite.
    """
    d = Path(directory)
    suites: dict[str, Suite] = {}
    if not d.is_dir():
        return suites
    for path in sorted(d.glob("*.toml")):
        try:
            suite = load_suite_file(path)
        except (tomllib.TOMLDecodeError, ValueError, OSError) as exc:
            # stderr, not stdout: several callers emit JSON on stdout and a
            # warning line there corrupts the stream.
            print(f"[suites] skipping {path}: {exc}", file=sys.stderr)
            continue
        suites[suite.id] = suite
    return suites
