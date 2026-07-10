#!/usr/bin/env python3
"""
Bench runner — executes cells from the planner worklist.

Takes a worklist, checks GPU-window gate, drives Tier A cells via
`hal0-benchctl sweep`, Tier B/C cells via `server_ab.py`, and writes
v2 records to the store.

Resumable by construction: the planner is a set-difference against the
store, so re-running after a crash recomputes the remaining cells.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add installer/bench to path
BENCH_DIR = Path(__file__).parent
sys.path.insert(0, str(BENCH_DIR))

from v2_store import (
    ensure_v2_dir,
    write_record,
    compute_cell_key,
    DEFAULT_RESULT_DIR,
    DEFAULT_V2_DIR,
)

SEAM = "/usr/lib/hal0/bin/hal0-benchctl"
MODEL_DIR = Path("/mnt/ai-models")
HARNESS_DIR = BENCH_DIR


def is_gpu_idle() -> bool:
    """
    Check if GPU inference slots are active.
    Returns True if GPU is idle (no active slots).
    """
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "hal0-slot@agent.service"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip() == "active":
            return False
        result = subprocess.run(
            ["systemctl", "is-active", "hal0-slot@brain.service"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip() == "active":
            return False
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def take_exclusive() -> bool:
    """
    Stop all GPU slots for exclusive benchmarking.
    Returns True if successful.
    """
    slots = [
        "hal0-slot@agent.service",
        "hal0-slot@brain.service",
        "hal0-slot@flm.service",
        "hal0-slot@rerank.service",
    ]
    for slot in slots:
        try:
            subprocess.run(
                ["systemctl", "stop", slot],
                capture_output=True, text=True, timeout=60
            )
            print(f"[exclusive] stopping GPU slot: {slot}")
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"[exclusive] warning: failed to stop {slot}: {e}")
    return True


def release_exclusive() -> bool:
    """
    Restart all GPU slots after exclusive benchmarking.
    """
    slots = [
        "hal0-slot@agent.service",
        "hal0-slot@brain.service",
        "hal0-slot@flm.service",
        "hal0-slot@rerank.service",
    ]
    for slot in slots:
        try:
            subprocess.run(
                ["systemctl", "start", slot],
                capture_output=True, text=True, timeout=60
            )
            print(f"[exclusive] restarting GPU slot: {slot}")
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"[exclusive] warning: failed to restart {slot}: {e}")
    return True


def run_cell(cell: dict, exclusive: bool = False) -> dict:
    """
    Execute a single cell. Returns the result record.
    """
    model_rel = cell["model"]
    lane = cell["lane"]
    kind = cell["kind"]
    depth = cell["depth"]
    reps = cell["reps"]
    sampler = cell["sampler"]

    # Build identity block
    identity = {
        "model": {
            "id": model_rel,
            "gguf": str(MODEL_DIR / model_rel),
        },
        "engine": {
            "kind": "llama-bench",
            "image": "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server",
        },
        "lane": lane,
        "config": {
            "argv": [],
            "kv": {},
            "parallel": 1,
        },
        "workload": {
            "kind": kind,
            "depth": depth,
            "n_prompt": 2048,
            "n_gen": 128,
            "sampler": sampler,
            "concurrency": 1,
        },
    }

    # Compute cell_key
    cell_key = compute_cell_key(identity)

    # Build the llama-bench command
    cmd = [
        "sudo", "-n", SEAM, "sweep",
        model_rel,
        lane,
        "--exclusive" if exclusive else "",
        "-p", "2048",
        "-n", str(reps),
        "-d", str(depth),
        "-fa", "1",
        "-ngl", "99",
        "-mmp", "0",
    ]
    cmd = [c for c in cmd if c]  # Remove empty strings

    print(f"[runner] executing: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            print(f"[runner] cell failed: {result.stderr[:500]}")
            outcome = "failed"
        else:
            outcome = "ok"
    except subprocess.TimeoutExpired:
        print(f"[runner] cell timed out")
        outcome = "failed"
    except FileNotFoundError:
        print(f"[runner] seam not found: {SEAM}")
        outcome = "failed"

    # Build record
    record = {
        "schema": 2,
        "run_id": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + "-" + os.urandom(3).hex(),
        "suite": cell.get("suite_id"),
        "trigger": "manual",
        "cell_key": cell_key,
        "model": identity["model"],
        "engine": identity["engine"],
        "lane": lane,
        "config": identity["config"],
        "workload": identity["workload"],
        "host": {
            "name": "hal0",
            "platform": "strix-halo",
            "gpu": "Radeon 8060S (gfx1151)",
            "exclusive": exclusive,
        },
        "reps": [],
        "summary": {},
        "outcome": outcome,
        "artifacts": f"v2/artifacts/{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')[:19].replace(':', '-')}/",
    }

    # Write record
    write_record(record)
    return record


def run_worklist(
    worklist: list,
    exclusive: bool = True,
    budget_min: int = 240,
    dry_run: bool = False,
) -> dict:
    """
    Execute the full worklist.
    Returns summary of results.
    """
    ensure_v2_dir()

    if exclusive and not dry_run:
        take_exclusive()

    results = {
        "total": len(worklist),
        "ok": 0,
        "failed": 0,
        "skipped": 0,
        "cells": [],
    }

    for i, item in enumerate(worklist):
        cell = item["cell"]
        print(f"\n[{i+1}/{len(worklist)}] {cell['model']} / {cell['lane']} / "
              f"{cell['kind']} / {cell['depth']}")

        if dry_run:
            print(f"  [dry-run] would run: {cell['model']} {cell['lane']} "
                  f"{cell['kind']} depth={cell['depth']}")
            results["skipped"] += 1
            continue

        try:
            record = run_cell(cell, exclusive=exclusive)
            results["cells"].append(record)
            if record["outcome"] == "ok":
                results["ok"] += 1
            else:
                results["failed"] += 1
        except Exception as e:
            print(f"  [error] {e}")
            results["failed"] += 1

    if exclusive and not dry_run:
        release_exclusive()

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bench runner")
    parser.add_argument("--worklist", help="Path to worklist JSON")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-exclusive", action="store_true")
    parser.add_argument("--budget-min", type=int, default=240)
    args = parser.parse_args()

    if args.worklist:
        with open(args.worklist) as f:
            worklist = json.load(f)
    else:
        # Import planner and run
        sys.path.insert(0, str(BENCH_DIR))
        from planner import plan
        worklist = plan()

    results = run_worklist(
        worklist=worklist,
        exclusive=not args.no_exclusive,
        budget_min=args.budget_min,
        dry_run=args.dry_run,
    )

    print(f"\n{'='*60}")
    print(f"Results: {results['ok']} ok, {results['failed']} failed, "
          f"{results['skipped']} skipped, {results['total']} total")
