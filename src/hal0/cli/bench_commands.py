"""`hal0 bench` — benchmarking CLI (design §5), thin mount over hal0.bench.cli.

The bench CLI is argparse-based (stdlib-only, same precedent as server_ab.py),
so rather than re-declare every verb as a typer command, main.py registers
``bench`` as a single passthrough command (allow_extra_args +
ignore_unknown_options + no --help interception) and the raw argv goes straight
to the argparse parser. `hal0 bench plan --suite roster` behaves exactly like
the design's `hal0 bench plan [--suite ID|PATH]`.

Verbs (see hal0.bench.cli): plan, run, worker, status, results, history,
reindex, devices, publish, eval.
"""

from __future__ import annotations

import typer

# main.py passes these to app.command("bench", ...) — the passthrough only
# works with all three (extra args collected, unknown -flags kept, and --help
# forwarded to argparse instead of typer eating it).
BENCH_CONTEXT_SETTINGS = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
    "help_option_names": [],
}

BENCH_HELP = "Benchmarks — plan/run suites, query results, drain the run queue."


def bench(ctx: typer.Context) -> None:
    from hal0.bench.cli import main as bench_main

    raise typer.Exit(bench_main(ctx.args or ["--help"]))
