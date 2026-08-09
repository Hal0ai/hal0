"""hal0.bench.adapters — OSS load-gen/eval tool adapters (bench Phase 3).

Each module here is a self-contained adapter for one external tool: it owns
(1) building that tool's CLI argv for a cell-shaped request, (2) running it
via an injectable ``runner`` callable (tests inject fakes; nothing here shells
out to the real tool), and (3) parsing the tool's output into the same pure
result shape ``hal0.bench.parsers.Parsed`` produces, ready for
``hal0.bench.runner._assemble``. Adapters import ``schema.py``/``parsers.py``
types but do not modify them and do not touch ``runner.py``/``planner.py``/
``cli.py`` — wiring an adapter into the planner's ``KNOWN_KINDS`` is
integration's job, not the adapter's (docs/superpowers/plans/
2026-08-09-bench-phase3-oss-adapters.md).
"""
