#!/usr/bin/env python3
"""Summarise install-matrix results as a Markdown table.

    python3 -I report.py <results-dir> [--latest-per-shape]

Reads every ``<results-dir>/*/result.json`` (schema ``hal0.install-matrix.v1``)
and prints one row per result in shape row order (``shapes.json``). A shape
that is not in ``shapes.json`` is kept, after the known ones. A file that
cannot be read or has another schema is skipped with one line on stderr. A
missing value prints as ``-``. A second section lists, per failed run, the last
step reached and the first failed unit. ``--latest-per-shape`` keeps only the
run with the newest ``started_at`` for each shape.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "hal0.install-matrix.v1"
SHAPES_FILE = Path(__file__).resolve().with_name("shapes.json")
HEADERS = (
    "Shape", "Source / version", "Exit", "Total", "Slowest step", "Disk GB", "Downloaded GB",
    "Questions", "Slots", "Doctor ok/warn/critical", "Dashboard s", "First chat token / model",
    "Memory write/read", "Hermes reply s",
)  # fmt: skip


def shape_rows() -> dict[str, int]:
    try:
        data = json.loads(SHAPES_FILE.read_text(encoding="utf-8"))
        return {s["id"]: int(s["row"]) for s in data["shapes"]}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def load_results(root: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(root.glob("*/result.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(
                f"report: skipping {path}: unreadable ({exc.__class__.__name__})", file=sys.stderr
            )
            continue
        if (
            not isinstance(data, dict)
            or data.get("schema") != SCHEMA
            or not isinstance(data.get("shape"), str)
        ):
            print(f"report: skipping {path}: not a {SCHEMA} result", file=sys.stderr)
            continue
        out.append(data)
    return out


def started(res: dict) -> str:
    value = res.get("started_at")
    return value if isinstance(value, str) else ""


def is_num(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def d(value: object) -> str:
    return "-" if value is None or value == "" else str(value).replace("|", "/").replace("\n", " ")


def secs(value: object) -> str:
    if not is_num(value):
        return "-"
    return f"{value:g}"


def gb(value: object) -> str:
    return f"{value / 1e9:.1f}" if is_num(value) else "-"


def mss(value: object) -> str:
    if not is_num(value):
        return "-"
    total = round(value)
    return f"{total // 60}:{total % 60:02d}"


def slowest(durations: object) -> str:
    if not isinstance(durations, dict):
        return "-"
    timed = [(k, v) for k, v in durations.items() if is_num(v)]
    if not timed:
        return "-"
    name, value = max(timed, key=lambda kv: kv[1])
    return f"{name} {secs(value)}s"


def sub(res: dict, key: str) -> dict:
    value = res.get(key)
    return value if isinstance(value, dict) else {}


def okfail(value: object) -> str:
    return "-" if not isinstance(value, bool) else ("ok" if value else "fail")


def row(res: dict) -> list[str]:
    doctor, chat = sub(res, "doctor"), sub(res, "chat")
    memory, hermes = sub(res, "memory"), sub(res, "hermes")
    if doctor and any(is_num(doctor.get(k)) for k in ("ok", "warn", "critical")):
        doc = "/".join(d(doctor.get(k)) for k in ("ok", "warn", "critical"))
    else:
        doc = "-"
    token, model = chat.get("first_token_seconds"), chat.get("model")
    chat_cell = (
        "-"
        if token is None and not model
        else f"{secs(token) + 's' if is_num(token) else '-'} {d(model)}"
    )
    mem = "-" if not memory else f"{okfail(memory.get('write_ok'))}/{okfail(memory.get('read_ok'))}"
    slots = res.get("slot_count")
    return [
        d(res.get("shape")),
        f"{d(res.get('source'))} {d(res.get('hal0_version'))}",
        d(res.get("install_exit_code")),
        mss(res.get("elapsed_seconds")),
        slowest(res.get("step_durations_seconds")),
        gb(res.get("disk_used_bytes")),
        gb(res.get("bytes_downloaded")),
        d(res.get("questions_asked")),
        d(slots),
        doc,
        secs(res.get("api_health_seconds")),
        chat_cell,
        mem,
        secs(hermes.get("reply_seconds")),
    ]  # fmt: skip


def why_failed(res: dict) -> str | None:
    """Why a run counts as failed, or None when it does not."""
    code = res.get("install_exit_code")
    if res.get("aborted") is not None:
        detail = res.get("abort_detail")
        return f"aborted: {d(res.get('aborted'))}" + (f" ({d(detail)})" if detail else "")
    if res.get("idle_timed_out"):
        return "idle timeout"
    if res.get("timed_out"):
        return "timeout"
    if not is_num(code):
        return "no driver result"
    if code != 0:
        return f"exit {code}"
    if res.get("failed_units"):
        return "failed units"
    return None


def render(results: list[dict]) -> str:
    lines = ["| " + " | ".join(HEADERS) + " |", "|" + "|".join(["---"] * len(HEADERS)) + "|"]
    lines += ["| " + " | ".join(row(r)) + " |" for r in results]
    bad = [r for r in results if why_failed(r)]
    lines += ["", "## Failed runs", ""]
    if not bad:
        lines.append("None.")
    for res in bad:
        units = res.get("failed_units")
        first = units[0] if isinstance(units, list) and units else None
        why = why_failed(res)
        lines.append(
            f"- {d(res.get('shape'))} ({started(res) or '-'}): {why}; last step: {d(res.get('last_step'))}; "
            f"first failed unit: {d(first)}"
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("results_dir")
    ap.add_argument("--latest-per-shape", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.results_dir)
    if not root.is_dir():
        print(f"report: {root} is not a directory", file=sys.stderr)
        return 2
    results = load_results(root)
    if args.latest_per_shape:
        newest: dict[str, dict] = {}
        for res in results:
            cur = newest.get(res["shape"])
            if cur is None or started(res) > started(cur):
                newest[res["shape"]] = res
        results = list(newest.values())
    rows = shape_rows()
    results.sort(
        key=lambda r: (
            rows.get(r["shape"], len(rows) + 1),
            r["shape"] if r["shape"] not in rows else "",
            started(r),
        )
    )
    print(render(results))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
