#!/usr/bin/env python3
"""Aggregate per-run llama-bench JSON into a single Hal0-ready index.json and a
human SUMMARY.md (ROCm vs Vulkan comparison).

Each file in <results>/runs/<name>.json is the raw JSON array emitted by
`llama-bench -o json` (one row per pp/tg test). The sibling <name>.meta.json
(written by run_benchmarks.sh) carries our labels: backend, image, context,
tag, host, gpu, timestamp. We flatten every row into a normalized record, write
<results>/index.json, and render <results>/SUMMARY.md.

Schema kept compatible with the llama-bench fields already used in the platform's
benchmark history so the datasets can later merge.

Results dir resolution: argv[1] > $HAL0_BENCH_RESULTS > /var/lib/hal0/benchmarks
"""

import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime

if len(sys.argv) > 1:
    RESULT_DIR = sys.argv[1]
else:
    RESULT_DIR = os.environ.get("HAL0_BENCH_RESULTS", "/var/lib/hal0/benchmarks")
RUNS_DIR = os.path.join(RESULT_DIR, "runs")
EMIT_V2 = "--emit-v2" in sys.argv


def test_kind(row):
    """pp (prompt processing) vs tg (token generation) for a llama-bench row."""
    if int(row.get("n_gen", 0) or 0) > 0 and int(row.get("n_prompt", 0) or 0) == 0:
        return "tg"
    if int(row.get("n_prompt", 0) or 0) > 0 and int(row.get("n_gen", 0) or 0) == 0:
        return "pp"
    return "mixed"


def load_meta(json_path):
    meta_path = json_path[: -len(".json")] + ".meta.json"
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    return {}


def normalize(row, meta, mtime_iso):
    return {
        "timestamp": meta.get("timestamp") or mtime_iso,
        "host": meta.get("host"),
        "gpu": meta.get("gpu") or row.get("gpu_info"),
        "gpu_info": row.get("gpu_info"),
        "cpu_info": row.get("cpu_info"),
        "backend": meta.get("backend"),
        "backends_reported": row.get("backends"),
        "runtime_image": meta.get("image"),
        "context": meta.get("context", "default"),
        "tag": meta.get("tag", ""),
        "llamacpp_build": {
            "commit": row.get("build_commit"),
            "number": row.get("build_number"),
        },
        "model": {
            "name": meta.get("model_rel") or row.get("model_filename"),
            "path": row.get("model_filename") or meta.get("model_path"),
            "type": row.get("model_type"),
            "size": row.get("model_size"),
            "n_params": row.get("model_n_params"),
        },
        "config": {
            "n_prompt": row.get("n_prompt"),
            "n_gen": row.get("n_gen"),
            "n_depth": row.get("n_depth", 0),
            "n_batch": row.get("n_batch"),
            "n_ubatch": row.get("n_ubatch"),
            "n_threads": row.get("n_threads"),
            "n_gpu_layers": row.get("n_gpu_layers"),
            "flash_attn": row.get("flash_attn"),
            "type_k": row.get("type_k"),
            "type_v": row.get("type_v"),
            "reps": meta.get("reps"),
        },
        "test": test_kind(row),
        "metric": {
            "avg_ts": row.get("avg_ts"),
            "stddev_ts": row.get("stddev_ts"),
            "avg_ns": row.get("avg_ns"),
            "stddev_ns": row.get("stddev_ns"),
        },
    }


def collect():
    records = []
    if not os.path.isdir(RUNS_DIR):
        return records
    for fname in sorted(os.listdir(RUNS_DIR)):
        if not fname.endswith(".json") or fname.endswith(".meta.json"):
            continue
        path = os.path.join(RUNS_DIR, fname)
        mtime_iso = datetime.fromtimestamp(os.path.getmtime(path), tz=UTC).isoformat()
        try:
            with open(path) as fh:
                rows = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"  [warn] skipping unreadable {fname}: {exc}", file=sys.stderr)
            continue
        if not isinstance(rows, list):
            rows = [rows]
        meta = load_meta(path)
        for row in rows:
            records.append(normalize(row, meta, mtime_iso))
    return records


def fmt_ts(rec):
    m = rec["metric"]
    if m.get("avg_ts") is None:
        return "-"
    sd = m.get("stddev_ts") or 0
    return f"{m['avg_ts']:.1f}±{sd:.1f}"


def short_model(name):
    """Display name: drop the dir prefix and the .gguf suffix so the table
    reads as a model, not a filesystem path."""
    if not name:
        return "?"
    base = name.rsplit("/", 1)[-1]
    if base.lower().endswith(".gguf"):
        base = base[: -len(".gguf")]
    return base


# Quant families we care about for the FPX/FP4/MTP bench (finding 0.2/0.3).
_QUANT_TAGS = ("ROCMFPX", "ROCMFP8", "ROCMFP6", "ROCMFP4", "ROCMFP3")


def quant_tag(rec):
    """Short quant/speculation label from model.type + name, e.g. `ROCMFP4·MTP`.
    Empty for models outside the FPX/FP4/MTP family so the focused view can
    filter on it."""
    hay = f"{rec['model'].get('type') or ''} {rec['model'].get('name') or ''}".upper()
    parts = [t for t in _QUANT_TAGS if t in hay]
    fam = parts[0] if parts else ""
    if "MTP" in hay:
        fam = f"{fam}·MTP" if fam else "MTP"
    return fam


def write_summary(records):
    """Markdown table: rows = model x context x tag, columns = backend (pp / tg t/s)."""
    backends = sorted({r["backend"] for r in records if r.get("backend")})
    grid = defaultdict(lambda: defaultdict(dict))
    for r in records:
        mname = short_model(r["model"]["name"])
        quant = quant_tag(r)
        ctx = r.get("context", "default")
        tag = r.get("tag") or ""
        grid[(mname, quant, ctx, tag)][r.get("backend")][r["test"]] = fmt_ts(r)

    lines = []
    lines.append("# Strix Halo Benchmark Summary")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(tz=UTC).isoformat()} · "
        f"{len(records)} measurements · backends: {', '.join(backends) or 'none'}"
    )
    lines.append("")
    lines.append(
        "Throughput in tokens/sec (avg±stddev). "
        "**pp** = prompt processing, **tg** = token generation."
    )
    lines.append("")

    lines.append("## Tier A — llama-bench (single-stream kernel shape)")
    lines.append("")
    header = ["model", "quant", "context", "tag"]
    for b in backends:
        header += [f"{b} pp", f"{b} tg"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")

    for key in sorted(grid):
        mname, quant, ctx, tag = key
        cells = grid[key]
        row = [mname, quant or "-", ctx, tag or "-"]
        for b in backends:
            row.append(cells.get(b, {}).get("pp", "-"))
            row.append(cells.get(b, {}).get("tg", "-"))
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    return "\n".join(lines) + "\n"


def collect_server_ab():
    """Ingest server_ab.py output (Tier B/C: MTP draft sweeps, concurrency,
    cache-reuse). These carry the metrics Tier A can't — decode t/s under
    speculation, draft-acceptance %, and aggregate/per-stream/TTFT under
    -np>1 — which is the whole point of the FPX/FP4/MTP bench. Each file is
    `{mode, slot, provenance, results:{label:{...median...}}}`."""
    sa_dir = os.path.join(RESULT_DIR, "server-ab")
    docs = []
    if not os.path.isdir(sa_dir):
        return docs
    for fname in sorted(os.listdir(sa_dir)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(sa_dir, fname)) as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"  [warn] skipping unreadable {fname}: {exc}", file=sys.stderr)
            continue
        # Current server_ab.py wraps results in a dict; skip legacy bare-list files.
        if not isinstance(doc, dict) or "results" not in doc:
            print(f"  [warn] {fname}: legacy/unknown shape, skipped", file=sys.stderr)
            continue
        # This section covers the decode/draft/concurrency modes (the FPX/FP4/MTP
        # story). embed/rerank have a flat, non-label results shape — skip them.
        if doc.get("mode") not in ("ab", "batch", "mtp", "reuse"):
            continue
        doc["_file"] = fname
        docs.append(doc)
    return docs


def _fnum(x):
    return "-" if x is None else (f"{x:.1f}" if isinstance(x, float) else str(x))


def _cell_metrics(val):
    """Pull the display metrics out of one label's result block, whatever the
    mode. `median` (ab/batch/mtp) or `second_call` (reuse) or the block itself."""
    empty = {
        k: None for k in ("prefill", "decode", "accept", "aggregate", "per_stream", "ttft_p95")
    }
    if not isinstance(val, dict):
        return empty
    m = val.get("median") or val.get("second_call") or val
    if not isinstance(m, dict):
        return empty
    return {
        "prefill": m.get("prompt_per_second"),
        "decode": m.get("predicted_per_second"),
        "accept": m.get("draft_acceptance_pct"),
        "aggregate": m.get("aggregate_tps"),
        "per_stream": m.get("per_stream_tps"),
        "ttft_p95": m.get("ttft_p95_s"),
    }


def write_server_ab_section(docs):
    lines = ["## Tier B/C — server (MTP · draft · concurrency)", ""]
    if not docs:
        lines.append("_No server-ab results yet._")
        lines.append("")
        return "\n".join(lines) + "\n"
    lines.append(
        "Live-server measurements: decode t/s under speculation, draft "
        "acceptance, and concurrency aggregate/per-stream/TTFT. Governing "
        "metric per §2 is **net decode t/s at the production sampler**."
    )
    lines.append("")
    for doc in docs:
        prov = doc.get("provenance") or {}
        img = prov.get("runner_image") or "?"
        depth = prov.get("depth_tokens")
        temp = (prov.get("sampler") or {}).get("temperature")
        meta = [f"mode={doc.get('mode')}"]
        if depth is not None:
            meta.append(f"depth~{depth}")
        if temp is not None:
            meta.append(f"temp {temp}")
        meta.append(f"img={img}")
        if prov.get("note"):
            meta.append(f"note={prov['note']}")
        lines.append(f"### {doc.get('slot', '?')} · " + " · ".join(meta))
        lines.append("")
        results = doc.get("results") or {}
        is_batch = doc.get("mode") == "batch" or any(
            _cell_metrics(v).get("aggregate") is not None for v in results.values()
        )
        if is_batch:
            lines.append("| cell | aggregate t/s | per-stream t/s | TTFT p95 s |")
            lines.append("|---|---|---|---|")
            for label, val in results.items():
                c = _cell_metrics(val)
                lines.append(
                    f"| {label} | {_fnum(c['aggregate'])} | "
                    f"{_fnum(c['per_stream'])} | {_fnum(c['ttft_p95'])} |"
                )
        else:
            lines.append("| cell | decode t/s | prefill t/s | draft accept % |")
            lines.append("|---|---|---|---|")
            for label, val in results.items():
                c = _cell_metrics(val)
                lines.append(
                    f"| {label} | {_fnum(c['decode'])} | "
                    f"{_fnum(c['prefill'])} | {_fnum(c['accept'])} |"
                )
        lines.append("")
    return "\n".join(lines) + "\n"


def main():
    records = collect()
    server_ab = collect_server_ab()
    os.makedirs(RESULT_DIR, exist_ok=True)

    index_path = os.path.join(RESULT_DIR, "index.json")
    out = {
        "generated": datetime.now(tz=UTC).isoformat(),
        "count": len(records),
        "records": records,
        "server_ab": server_ab,
    }
    with open(index_path, "w") as fh:
        json.dump(out, fh, indent=2)

    summary_path = os.path.join(RESULT_DIR, "SUMMARY.md")
    with open(summary_path, "w") as fh:
        fh.write(write_summary(records))
        fh.write("\n")
        fh.write(write_server_ab_section(server_ab))

    print(f"Wrote {index_path} ({len(records)} measurements, {len(server_ab)} server-ab files)")
    print(f"Wrote {summary_path}")

    # Emit v2 records (new benchmark system)
    if EMIT_V2:
        from pathlib import Path

        V2_DIR = Path(RESULT_DIR) / "v2"
        V2_DIR.mkdir(parents=True, exist_ok=True)
        RECORDS_PATH = V2_DIR / "records.jsonl"
        import hashlib

        for rec in records:
            # Build identity block for cell_key
            identity = {
                "model_id": rec["model"]["name"],
                "engine_kind": "llama-bench",
                "engine_image": rec.get("runtime_image"),
                "llamacpp_build": f"{rec['llamacpp_build'].get('commit', '')}-{rec['llamacpp_build'].get('number', '')}",
                "lane": rec.get("backend"),
                "config": {
                    "argv": [
                        f"-n_prompt={rec['config']['n_prompt']}",
                        f"-n_gen={rec['config']['n_gen']}",
                        f"-n_depth={rec['config']['n_depth']}",
                        f"-n_batch={rec['config']['n_batch']}",
                        f"-n_ubatch={rec['config']['n_ubatch']}",
                        f"-n_threads={rec['config']['n_threads']}",
                        f"-ngl={rec['config']['n_gpu_layers']}",
                        f"-fa={'1' if rec['config']['flash_attn'] else '0'}",
                        f"-ctk={rec['config']['type_k']}",
                        f"-ctv={rec['config']['type_v']}",
                    ],
                    "kv": {"main_k": rec["config"]["type_k"], "main_v": rec["config"]["type_v"]},
                    "parallel": 1,
                },
                "workload": {
                    "kind": rec["test"],
                    "depth": rec["config"]["n_depth"],
                    "n_prompt": rec["config"]["n_prompt"],
                    "n_gen": rec["config"]["n_gen"],
                    "sampler": "greedy",
                    "concurrency": 1,
                },
            }
            raw = json.dumps(identity, sort_keys=True, default=str)
            cell_key = hashlib.sha256(raw.encode()).hexdigest()

            v2_record = {
                "schema": 2,
                "run_id": f"v1-imported-{rec.get('timestamp', '')}-{rec.get('backend', '')}",
                "suite": "imported",
                "trigger": "import-v1",
                "cell_key": cell_key,
                "model": {"id": rec["model"]["name"], "gguf": rec["model"]["path"]},
                "engine": {
                    "kind": "llama-bench",
                    "image": rec.get("runtime_image"),
                    "llamacpp_build": f"{rec['llamacpp_build'].get('commit', '')}-{rec['llamacpp_build'].get('number', '')}",
                },
                "lane": rec.get("backend"),
                "config": identity["config"],
                "workload": identity["workload"],
                "host": {
                    "name": "hal0",
                    "platform": "strix-halo",
                    "gpu": rec.get("gpu"),
                    "exclusive": True,
                },
                "reps": [{"t_s": rec["metric"]["avg_ts"]}],
                "summary": {
                    "decode_ts_med": rec["metric"]["avg_ts"] if rec["test"] == "tg" else None,
                    "prefill_ts_med": rec["metric"]["avg_ts"] if rec["test"] == "pp" else None,
                },
                "outcome": "ok",
                "artifacts": "",
            }
            with open(RECORDS_PATH, "a") as f:
                f.write(json.dumps(v2_record, default=str) + "\n")

        print(f"Wrote {RECORDS_PATH} ({len(records)} v2 records)")


if __name__ == "__main__":
    main()
