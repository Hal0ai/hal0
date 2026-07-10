#!/usr/bin/env python3
"""
Suite planner — determines what's stale and needs re-running.

Pure function: no GPU, no writes. Takes suite TOMLs + registry + v2 store,
outputs a worklist of cells to run.
"""

import json
import hashlib
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ImportError:
    import tomli as tomllib

# Paths
SUITE_DIR = Path("/etc/hal0/bench/suites")
MODEL_DIR = Path("/mnt/ai-models")
V2_DIR = Path("/var/lib/hal0/benchmarks/v2")
RECORDS_PATH = V2_DIR / "records.jsonl"


def load_suites() -> list:
    """Load all suite TOMLs from /etc/hal0/bench/suites/."""
    suites = []
    if not SUITE_DIR.exists():
        return suites
    for toml_path in sorted(SUITE_DIR.glob("*.toml")):
        with open(toml_path, "rb") as f:
            suite = tomllib.load(f)
            suite["_path"] = str(toml_path)
            suites.append(suite)
    return suites


def get_installed_models() -> list:
    """Get list of installed GGUF models under /mnt/ai-models."""
    models = []
    if not MODEL_DIR.exists():
        return models
    for root, dirs, files in os.walk(MODEL_DIR):
        for f in files:
            if f.endswith(".gguf"):
                rel = os.path.relpath(os.path.join(root, f), MODEL_DIR)
                models.append(rel)
    return models


def get_latest_ok_records() -> dict:
    """
    Read records.jsonl and return a dict: cell_key -> latest ok record.
    """
    latest = {}
    if not RECORDS_PATH.exists():
        return latest
    with open(RECORDS_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get("schema") != 2:
                    continue
                cell_key = record.get("cell_key")
                if not cell_key:
                    continue
                # Keep the latest record (by run_id timestamp)
                if cell_key not in latest or record["run_id"] > latest[cell_key]["run_id"]:
                    latest[cell_key] = record
            except (json.JSONDecodeError, KeyError):
                continue
    return latest


def compute_cell_key(record: dict) -> str:
    """
    Compute a deterministic hash of the identity block.
    Two runs with the same cell_key measured the same thing.
    """
    identity = {
        "model_id": record.get("model", {}).get("id"),
        "model_sha": record.get("model", {}).get("gguf_sha256"),
        "engine_kind": record.get("engine", {}).get("kind"),
        "engine_image": record.get("engine", {}).get("image"),
        "engine_image_digest": record.get("engine", {}).get("image_digest"),
        "llamacpp_build": record.get("engine", {}).get("llamacpp_build"),
        "decode_tune": record.get("engine", {}).get("decode_tune"),
        "lane": record.get("lane"),
        "config_argv": sorted(record.get("config", {}).get("argv", [])),
        "config_env": sorted(record.get("config", {}).get("env", {}).items()),
        "config_kv": record.get("config", {}).get("kv", {}),
        "config_spec": record.get("config", {}).get("spec"),
        "config_parallel": record.get("config", {}).get("parallel"),
        "workload_kind": record.get("workload", {}).get("kind"),
        "workload_depth": record.get("workload", {}).get("depth"),
        "workload_n_prompt": record.get("workload", {}).get("n_prompt"),
        "workload_n_gen": record.get("workload", {}).get("n_gen"),
        "workload_sampler": record.get("workload", {}).get("sampler"),
        "workload_concurrency": record.get("workload", {}).get("concurrency"),
    }
    raw = json.dumps(identity, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_cells(suite: dict, installed_models: list) -> list:
    """
    Generate the full set of cells for a suite.
    Returns list of cell dicts with identity block.
    """
    cells = []
    selector = suite.get("selector", {})
    matrix = suite.get("matrix", {})
    cells_config = suite.get("cells", {})

    # Filter models by selector
    models = installed_models
    if selector.get("installed"):
        # Only installed models (already filtered)
        pass
    # caps_any filter would need registry lookup — skip for now

    # Generate cells for each model × lane × depth × sampler × kind
    lanes = matrix.get("lanes", ["default"])
    depths = matrix.get("depths", [2048])
    samplers = matrix.get("samplers", ["greedy"])
    reps = matrix.get("reps", 3)
    kinds = cells_config.get("kinds", ["pp", "tg"])

    for model_rel in models:
        for lane in lanes:
            for depth in depths:
                for sampler in samplers:
                    for kind in kinds:
                        cell = {
                            "model": model_rel,
                            "lane": lane,
                            "depth": depth,
                            "sampler": sampler,
                            "kind": kind,
                            "reps": reps,
                            "suite_id": suite.get("id"),
                            "suite_priority": suite.get("priority", 50),
                        }
                        cells.append(cell)
    return cells


def plan(
    suite_id: Optional[str] = None,
    json_output: bool = False,
) -> list:
    """
    Main planner: returns worklist of cells that need running.
    A cell is stale iff:
    1. No ok record exists for its cell_key (never measured)
    2. Newest ok record is older than suite's max_age_days
    """
    suites = load_suites()
    if suite_id:
        suites = [s for s in suites if s.get("id") == suite_id]

    installed_models = get_installed_models()
    latest_ok = get_latest_ok_records()

    worklist = []
    for suite in suites:
        cells = generate_cells(suite, installed_models)
        max_age_days = suite.get("staleness", {}).get("max_age_days", 30)
        now = datetime.now(timezone.utc)
        max_age = now - timedelta(days=max_age_days)

        for cell in cells:
            # Build identity block for this cell
            identity = {
                "model_id": cell["model"],
                "lane": cell["lane"],
                "config": {
                    "argv": [],  # Would be resolved from model defaults
                },
                "workload": {
                    "kind": cell["kind"],
                    "depth": cell["depth"],
                    "n_prompt": 2048,
                    "n_gen": 128,
                    "sampler": cell["sampler"],
                    "concurrency": 1,
                },
            }
            cell_key = compute_cell_key(identity)

            # Check staleness
            is_stale = False
            stale_reason = ""
            if cell_key not in latest_ok:
                is_stale = True
                stale_reason = "never_measured"
            else:
                record = latest_ok[cell_key]
                record_time = datetime.fromisoformat(
                    record["run_id"].split("-")[0] + "T" + record["run_id"].split("-")[1] + "Z"
                )
                if record_time < max_age:
                    is_stale = True
                    stale_reason = f"max_age_exceeded ({max_age_days}d)"

            if is_stale:
                worklist.append({
                    "cell": cell,
                    "cell_key": cell_key,
                    "stale_reason": stale_reason,
                    "suite_id": suite.get("id"),
                    "suite_priority": suite.get("priority", 50),
                })

    # Sort by: suite priority (desc), then cheap-before-expensive
    worklist.sort(key=lambda x: (-x["suite_priority"], x["cell"]["kind"]))
    return worklist


def plan_json(suite_id: Optional[str] = None) -> str:
    """Return plan as JSON string."""
    worklist = plan(suite_id=suite_id)
    return json.dumps(worklist, indent=2, default=str)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bench planner")
    parser.add_argument("--suite", help="Suite ID to plan for")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.json:
        print(plan_json(suite_id=args.suite))
    else:
        worklist = plan(suite_id=args.suite)
        print(f"Planned {len(worklist)} cells")
        for item in worklist[:10]:
            print(f"  {item['cell']['model']} / {item['cell']['lane']} / "
                  f"{item['cell']['kind']} / {item['cell']['depth']} "
                  f"({item['stale_reason']})")
        if len(worklist) > 10:
            print(f"  ... and {len(worklist) - 10} more")
