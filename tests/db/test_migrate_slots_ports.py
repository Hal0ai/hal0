"""Packaged migration 004_slots_ports.sql (rework §11.1 + §11.2).

Applies cleanly on top of 001/002/003, creates the slot + slot_link +
port_claim tables, enforces the live-uniqueness invariant, and is
idempotent against the real package.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hal0.db.connection import connect
from hal0.db.migrate import migrate


class TestPackagedSlotsPortsMigration:
    def test_004_applies_on_top_of_prior(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            applied = migrate(conn)
            # 004 lands after the registry/metrics/store migrations.
            assert 4 in applied
            assert applied == sorted(applied)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert {"slot", "slot_link", "port_claim"} <= tables

    def test_004_is_idempotent_against_the_real_package(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            migrate(conn)
            assert migrate(conn) == []

    def test_slot_name_is_unique(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            migrate(conn)
            conn.execute("INSERT INTO slot (name, slot_type) VALUES ('a', 'llm')")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO slot (name, slot_type) VALUES ('a', 'llm')")

    def test_slot_id_autoincrements_and_survives_delete(self, tmp_path: Path) -> None:
        """AUTOINCREMENT never reuses an id, even after a delete — the
        stable-identity guarantee §11.1 rests on."""
        with connect(tmp_path / "t.db") as conn:
            migrate(conn)
            cur = conn.execute("INSERT INTO slot (name, slot_type) VALUES ('a', 'llm')")
            first_id = cur.lastrowid
            conn.execute("DELETE FROM slot WHERE id = ?", (first_id,))
            cur = conn.execute("INSERT INTO slot (name, slot_type) VALUES ('b', 'llm')")
            assert cur.lastrowid > first_id

    def test_port_claim_live_uniqueness(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            migrate(conn)
            conn.execute(
                "INSERT INTO port_claim (port, owner_kind, owner_label) "
                "VALUES (8081, 'slot', 'slot:1')"
            )
            # A second LIVE claim on the same port is rejected.
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO port_claim (port, owner_kind, owner_label) "
                    "VALUES (8081, 'slot', 'slot:2')"
                )
            # But once released, the same port can be claimed again — the
            # partial index only covers released_at IS NULL.
            conn.execute(
                "UPDATE port_claim SET released_at = strftime('%s','now') WHERE port = 8081"
            )
            conn.execute(
                "INSERT INTO port_claim (port, owner_kind, owner_label) "
                "VALUES (8081, 'slot', 'slot:2')"
            )
            live = conn.execute(
                "SELECT COUNT(*) FROM port_claim WHERE port = 8081 AND released_at IS NULL"
            ).fetchone()[0]
            assert live == 1

    def test_slot_link_cascades_on_slot_delete(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            migrate(conn)
            p = conn.execute(
                "INSERT INTO slot (name, slot_type) VALUES ('anchor', 'llm')"
            ).lastrowid
            c = conn.execute(
                "INSERT INTO slot (name, slot_type) VALUES ('shadow', 'transcription')"
            ).lastrowid
            conn.execute(
                "INSERT INTO slot_link (parent_id, child_id, kind) VALUES (?, ?, 'served_by')",
                (p, c),
            )
            conn.execute("DELETE FROM slot WHERE id = ?", (p,))
            assert conn.execute("SELECT COUNT(*) FROM slot_link").fetchone()[0] == 0

    def test_port_claim_slot_id_set_null_on_slot_delete(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            migrate(conn)
            sid = conn.execute("INSERT INTO slot (name, slot_type) VALUES ('a', 'llm')").lastrowid
            conn.execute(
                "INSERT INTO port_claim (port, slot_id, owner_kind, owner_label) "
                "VALUES (8081, ?, 'slot', 'slot:a')",
                (sid,),
            )
            conn.execute("DELETE FROM slot WHERE id = ?", (sid,))
            row = conn.execute("SELECT slot_id FROM port_claim WHERE port = 8081").fetchone()
            # Audit trail preserved; ownership dropped to NULL.
            assert row["slot_id"] is None
