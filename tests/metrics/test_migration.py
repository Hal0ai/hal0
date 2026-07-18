"""002_metrics.sql migration tests -- applies cleanly on top of 001, in order."""

from __future__ import annotations

from pathlib import Path

from hal0.db.connection import connect
from hal0.db.migrate import migrate


class TestMetricsMigration:
    def test_002_applies_after_001_creates_expected_tables(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            applied = migrate(conn)
            # 002 applies after 001, in order. Later migrations (003_store, ...)
            # tack on after; assert the 001->002 prefix rather than an exact set.
            assert applied[:2] == [1, 2]
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert {
                "request_metric",
                "slot_sample",
                "slot_event",
                "bench_run",
                "metric_rollup",
            } <= tables

    def test_002_is_idempotent(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            migrate(conn)
            assert migrate(conn) == []

    def test_partial_apply_only_picks_up_002_when_001_already_applied(self, tmp_path: Path) -> None:
        """A DB that already has 001 applied (e.g. by SqliteModelRegistry)
        picks up only 002 on the next migrate() call."""
        db = tmp_path / "t.db"
        with connect(db) as conn:
            # Simulate a prior process having only applied 001. Imported as
            # `from hal0.db.migrate import ...` rather than
            # `import hal0.db.migrate as x` -- `hal0/db/__init__.py` does
            # `from hal0.db.migrate import migrate`, which shadows the
            # `migrate` submodule *attribute* on the `hal0.db` package with
            # the function of the same name, so `import hal0.db.migrate as
            # x` (attribute-chain resolution) would bind `x` to that
            # function instead of the submodule.
            from hal0.db.connection import tx
            from hal0.db.migrate import (
                _discover_migrations,
                _ensure_migrations_table,
                _split_statements,
            )

            migrations = _discover_migrations(None)
            only_001 = [(v, f, s) for v, f, s in migrations if v == 1]
            assert only_001, "expected 001_registry.sql to exist in the package"
            _ensure_migrations_table(conn)

            with tx(conn):
                for stmt in _split_statements(only_001[0][2]):
                    conn.execute(stmt)
                conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (1, 'x')")

        with connect(db) as conn:
            applied = migrate(conn)
            # 001 already applied -> resumes at 002. Later migrations follow;
            # assert 001 is skipped and 002 leads, not an exact set.
            assert 1 not in applied
            assert applied[0] == 2

    def test_request_metric_row_round_trips(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            migrate(conn)
            conn.execute(
                "INSERT INTO request_metric "
                "(ts, request_id, slot_id, model_id, ok, decode_tps, tps_source) "
                "VALUES ('2026-01-01T00:00:00Z', 'r1', 'primary', 'qwen3-4b', 1, 42.5, 'exact')"
            )
            row = conn.execute("SELECT * FROM request_metric").fetchone()
            assert row["slot_id"] == "primary"
            assert row["decode_tps"] == 42.5
            assert row["tps_source"] == "exact"

    def test_slot_sample_composite_primary_key(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            migrate(conn)
            conn.execute(
                "INSERT INTO slot_sample (ts, slot_id, state) VALUES "
                "('2026-01-01T00:00:00Z', 'primary', 'ready')"
            )
            conn.execute(
                "INSERT INTO slot_sample (ts, slot_id, state) VALUES "
                "('2026-01-01T00:00:00Z', '__fleet__', 'n/a')"
            )
            rows = conn.execute("SELECT slot_id FROM slot_sample ORDER BY slot_id").fetchall()
            assert [r[0] for r in rows] == ["__fleet__", "primary"]
