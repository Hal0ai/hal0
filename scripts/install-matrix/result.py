#!/usr/bin/env python3
"""Assemble one install-matrix result.json from the pieces of a run.

    python3 -I result.py assemble --shape ID --source S --vmid N \
        --pre FILE --post FILE --probe FILE --driver FILE --summary FILE \
        --prereqs LIST --started ISO --out result.json \
        [--install-sh PATH]

``--pre``, ``--post`` and ``--probe`` are ``key=value`` files printed by
collect.sh. ``--driver`` is the JSON tty_drive.py
(or run-shape.sh, for an unattended run) wrote. ``--summary`` is the installer's
``--summary-json`` file. Any input may be missing or unreadable: its fields
come out as null. A number collect.sh could not measure (-1) is also null.

``last_step`` is the last ``ui_step "<title>"`` title, in the order of the
installer's source, that has a duration in the summary. The summary's own key
order is a bash hash order and is never used.

The file is written mode 0600. Standard library only.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

SCHEMA = "hal0.install-matrix.v1"
HERE = Path(__file__).resolve().parent
DEFAULT_INSTALL_SH = HERE.parents[1] / "installer" / "install.sh"
_STEP = re.compile(r'^ui_step\s+"([^"]+)"', re.MULTILINE)


def read_kv(path: str | None) -> dict[str, str] | None:
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


def read_json(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def num(kv: dict[str, str] | None, key: str, *, as_float: bool = False):
    """A measured number, or None when absent, unparsable or -1 (unknown)."""
    if kv is None or key not in kv:
        return None
    try:
        value = float(kv[key]) if as_float else int(kv[key])
    except ValueError:
        return None
    return None if value < 0 else value


def flag(kv: dict[str, str] | None, key: str):
    if kv is None or key not in kv:
        return None
    return kv[key] == "1"


def text(kv: dict[str, str] | None, key: str):
    if kv is None or key not in kv:
        return None
    return kv[key]


def names(kv: dict[str, str] | None, key: str):
    value = text(kv, key)
    if value is None:
        return None
    return [n for n in value.split(",") if n]


def delta(post: dict[str, str] | None, pre: dict[str, str] | None, key: str):
    a, b = num(post, key), num(pre, key)
    if a is None or b is None:
        return None
    return a - b


def installer_steps(install_sh: Path) -> list[str]:
    try:
        return _STEP.findall(install_sh.read_text(encoding="utf-8"))
    except OSError:
        return []


def last_step(durations: object, order: list[str]):
    if not isinstance(durations, dict):
        return None
    reached = [title for title in order if title in durations]
    return reached[-1] if reached else None


def shape_row(shape_id: str):
    try:
        spec = importlib.util.spec_from_file_location("_matrix_shape", HERE / "shape.py")
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for shape in module.load(module.DEFAULT_FILE):
            if shape["id"] == shape_id:
                return shape["row"]
    except Exception:
        return None
    return None


def assemble(args: argparse.Namespace) -> dict:
    pre, post, probe = read_kv(args.pre), read_kv(args.post), read_kv(args.probe)
    driver, summary = read_json(args.driver), read_json(args.summary)
    summary = summary or {}
    durations = summary.get("step_durations_seconds")
    prereqs = [p for p in re.split(r"[,\s]+", args.prereqs or "") if p]
    return {
        "schema": SCHEMA,
        "shape": args.shape,
        "row": shape_row(args.shape),
        "source": args.source,
        "hal0_version": summary.get("hal0_version"),
        "started_at": args.started,
        "install_exit_code": driver.get("exit_code") if driver else None,
        "timed_out": driver.get("timed_out") if driver else None,
        "idle_timed_out": driver.get("idle_timed_out") if driver else None,
        "questions_asked": driver.get("questions_answered") if driver else None,
        "elapsed_seconds": summary.get("elapsed_seconds"),
        "step_durations_seconds": durations if isinstance(durations, dict) else None,
        "last_step": last_step(durations, installer_steps(Path(args.install_sh))),
        "disk_used_bytes": delta(post, pre, "disk_used_bytes"),
        "bytes_downloaded": delta(post, pre, "net_rx_bytes"),
        "slot_count": num(post, "slot_count"),
        "slots": names(post, "slots"),
        "doctor": None
        if probe is None
        else {
            "ok": num(probe, "doctor_ok"),
            "warn": num(probe, "doctor_warn"),
            "critical": num(probe, "doctor_critical"),
            "exit": num(probe, "doctor_exit"),
        },
        "failed_units": names(post, "failed_units"),
        "api_health_seconds": num(probe, "api_health_s", as_float=True),
        "chat": None
        if probe is None
        else {
            "ok": flag(probe, "chat_ok"),
            "first_token_seconds": num(probe, "chat_first_token_s", as_float=True),
            "model": text(probe, "chat_model") or None,
        },
        "memory": None
        if probe is None
        else {
            "write_ok": flag(probe, "memory_write_ok"),
            "read_ok": flag(probe, "memory_read_ok"),
        },
        "hermes": None
        if probe is None
        else {
            "reply_ok": flag(probe, "hermes_reply_ok"),
            "reply_seconds": num(probe, "hermes_reply_s", as_float=True),
        },
        "harness_prereqs": prereqs,
        "warnings": summary.get("warnings"),
        "errors": summary.get("errors"),
        "components": summary.get("components"),
    }


def write_private(path: str, payload: dict) -> None:
    target = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:  # mkstemp makes it 0600
            fh.write(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("assemble")
    a.add_argument("--shape", required=True)
    a.add_argument("--source", required=True)
    a.add_argument("--vmid", type=int, required=True)
    for name in ("pre", "post", "probe", "driver", "summary"):
        a.add_argument(f"--{name}", default=None)
    a.add_argument("--prereqs", default="")
    a.add_argument("--started", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--install-sh", default=str(DEFAULT_INSTALL_SH))
    args = ap.parse_args(argv)
    write_private(args.out, assemble(args))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
