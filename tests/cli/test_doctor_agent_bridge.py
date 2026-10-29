"""``hal0 doctor all`` ``agent-bridge`` row (ADR-0043).

The row says whether the bridge can take a delegated run: ``/mcp/agents`` is
served, the Hermes gateway answers, and no run has sat on an approval or a
question for over an hour. Hermes is simulated by pinning
``hermes_api.probe_status`` (the one HTTP probe the row makes); the run ledger
is a real :class:`~hal0.bridge.store.BridgeStore`.

``hal0 doctor`` runs outside hal0-api, so ``HAL0_HERMES_API_KEY__*`` is never in
its environment: the row counts keyed profiles from the keys in Hermes's own
``.env`` files (the way the ``hermes-api`` row does), and these tests write
those files and hand the row an empty environment.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from hal0.bridge.hermes_client import HermesGateway
from hal0.bridge.store import BridgeStore, RunRow
from hal0.cli import doctor_all as da

#: A key Hermes accepts (``hermes_api.api_key_for`` wants at least 16 characters).
KEY = "k" * 32


def _hermes_home(root: Path, *, keyed: tuple[str, ...], unkeyed: tuple[str, ...] = ()) -> Path:
    """A Hermes home with a ``.env`` per profile: ``keyed`` ones carry ``API_SERVER_KEY``."""
    home = root / "hermes"
    home.mkdir(parents=True, exist_ok=True)
    for profile in (*keyed, *unkeyed):
        target = home if profile == "default" else home / "profiles" / profile
        target.mkdir(parents=True, exist_ok=True)
        body = f"API_SERVER_KEY={KEY}\n" if profile in keyed else "OTHER=1\n"
        (target / ".env").write_text(body, encoding="utf-8")
    return home


@pytest.fixture
def store(tmp_path: Path) -> BridgeStore:
    opened = BridgeStore(tmp_path / "bridge.db")
    opened.open()
    return opened


@pytest.fixture
def gateway(tmp_path: Path) -> HermesGateway:
    """Keys for ``default`` and ``coder`` in Hermes's files; no ``HAL0_HERMES_API_KEY__*`` at all."""
    return HermesGateway(home=_hermes_home(tmp_path, keyed=("default", "coder")), environ={})


def _hermes_answers(monkeypatch: pytest.MonkeyPatch, *, status: int = 200) -> list[str]:
    """Pin the gateway probe; returns the probed URLs."""
    seen: list[str] = []

    def probe(url: str, *, timeout: float = 3.0) -> int:
        seen.append(url)
        return status

    monkeypatch.setattr("hal0.agents.hermes_api.probe_status", probe)
    return seen


def _run(
    store: BridgeStore, run_id: str, *, status: str, pending: str | None, age_s: float
) -> None:
    now = time.time()
    store.insert_run(
        RunRow(
            run_id=run_id,
            client="cc",
            agent="hermes",
            profile="default",
            session_key=None,
            status=status,
            created_at=now - age_s,
            updated_at=now - age_s,
            input_preview="x",
            output=None,
            error=None,
            usage_json=None,
            admission_id=None,
            execution_generation=None,
            pending_request_json=pending,
            last_seq=0,
        )
    )


def test_disabled_passes_without_probing(monkeypatch: pytest.MonkeyPatch) -> None:
    probed = _hermes_answers(monkeypatch)
    c = da.check_agent_bridge(enabled=False)
    assert (c.key, c.label, c.status, c.critical) == (
        "agent-bridge",
        "Agent bridge",
        "pass",
        False,
    )
    assert "disabled" in c.detail
    assert probed == []


def test_mount_missing_while_enabled_fails(
    monkeypatch: pytest.MonkeyPatch, store: BridgeStore
) -> None:
    probed = _hermes_answers(monkeypatch)
    c = da.check_agent_bridge(enabled=True, mounted=False, store=store)
    assert c.status == "fail"
    assert c.detail.startswith("mount missing while enabled")
    assert probed == []  # the answer is already known


def test_gateway_down_warns(
    monkeypatch: pytest.MonkeyPatch, store: BridgeStore, gateway: HermesGateway
) -> None:
    _hermes_answers(monkeypatch, status=0)
    c = da.check_agent_bridge(enabled=True, mounted=True, gateway=gateway, store=store)
    assert c.status == "warn"
    assert "Hermes gateway not answering" in c.detail


def test_a_run_waiting_over_an_hour_warns(
    monkeypatch: pytest.MonkeyPatch, store: BridgeStore, gateway: HermesGateway
) -> None:
    _hermes_answers(monkeypatch)
    pending = '{"event": "approval.request", "request_id": "r1"}'
    _run(store, "run_old", status="running", pending=pending, age_s=2 * 3600)
    _run(store, "run_old2", status="running", pending=pending, age_s=3 * 3600)
    _run(store, "run_new", status="running", pending=pending, age_s=60)
    _run(store, "run_quiet", status="running", pending=None, age_s=5 * 3600)
    _run(store, "run_done", status="completed", pending=None, age_s=9 * 3600)
    c = da.check_agent_bridge(enabled=True, mounted=True, gateway=gateway, store=store)
    assert c.status == "warn"
    assert "2 runs waiting on an approval or answer for over 1 h" in c.detail


def test_one_waiting_run_is_singular(
    monkeypatch: pytest.MonkeyPatch, store: BridgeStore, gateway: HermesGateway
) -> None:
    _hermes_answers(monkeypatch)
    _run(store, "run_old", status="running", pending='{"event": "clarify.request"}', age_s=7200)
    c = da.check_agent_bridge(enabled=True, mounted=True, gateway=gateway, store=store)
    assert "1 run waiting on an approval or answer for over 1 h" in c.detail


def test_healthy_bridge_passes_with_counts(
    monkeypatch: pytest.MonkeyPatch, store: BridgeStore, gateway: HermesGateway
) -> None:
    _hermes_answers(monkeypatch)
    _run(store, "run_a", status="running", pending=None, age_s=30)
    _run(store, "run_b", status="queued", pending=None, age_s=10)
    _run(store, "run_c", status="completed", pending=None, age_s=10)
    c = da.check_agent_bridge(enabled=True, mounted=True, gateway=gateway, store=store)
    assert c.status == "pass"
    assert c.detail == "2 profiles keyed, 2 live runs"


def test_a_profile_without_a_key_is_not_counted(
    monkeypatch: pytest.MonkeyPatch, store: BridgeStore, tmp_path: Path
) -> None:
    _hermes_answers(monkeypatch)
    home = _hermes_home(tmp_path, keyed=("default",), unkeyed=("coder",))
    gw = HermesGateway(home=home, environ={})
    c = da.check_agent_bridge(enabled=True, mounted=True, gateway=gw, store=store)
    assert c.status == "pass"
    assert c.detail == "1 profile keyed, 0 live runs"


def test_keys_are_counted_from_hermes_files_with_an_empty_environment(
    monkeypatch: pytest.MonkeyPatch, store: BridgeStore, tmp_path: Path
) -> None:
    """Regression: ``hal0 doctor`` never has ``HAL0_HERMES_API_KEY__*`` (only hal0-api sets it)."""
    _hermes_answers(monkeypatch)
    home = _hermes_home(tmp_path, keyed=("default", "coder", "writer"))
    gw = HermesGateway(home=home, environ={})
    c = da.check_agent_bridge(enabled=True, mounted=True, gateway=gw, store=store)
    assert c.status == "pass"
    assert c.detail == "3 profiles keyed, 0 live runs"


def test_the_default_gateway_does_not_read_the_process_environment(
    monkeypatch: pytest.MonkeyPatch, store: BridgeStore, tmp_path: Path
) -> None:
    """No ``gateway``: the row reads ``$HERMES_HOME``'s files, not ``os.environ``'s key variables."""
    _hermes_answers(monkeypatch)
    home = _hermes_home(tmp_path, keyed=("default", "coder"))
    monkeypatch.setenv("HERMES_HOME", str(home))
    for name in [n for n in os.environ if n.startswith("HAL0_HERMES_API_KEY__")]:
        monkeypatch.delenv(name)
    assert da.check_agent_bridge(enabled=True, mounted=True, store=store).detail == (
        "2 profiles keyed, 0 live runs"
    )
    # ...and a key that only the environment holds is not what the doctor reports.
    bare = _hermes_home(tmp_path / "bare", keyed=(), unkeyed=("default", "coder"))
    monkeypatch.setenv("HERMES_HOME", str(bare))
    monkeypatch.setenv("HAL0_HERMES_API_KEY__DEFAULT", KEY)
    monkeypatch.setenv("HAL0_HERMES_API_KEY__CODER", KEY)
    assert da.check_agent_bridge(enabled=True, mounted=True, store=store).detail == (
        "0 profiles keyed, 0 live runs"
    )


def test_unknown_mount_state_warns_but_still_reports(
    monkeypatch: pytest.MonkeyPatch, store: BridgeStore, gateway: HermesGateway
) -> None:
    """hal0-api not answering: the row cannot say the mount is missing, so it is a warning."""
    _hermes_answers(monkeypatch)
    monkeypatch.setattr(da, "_get_any", lambda *_a, **_k: None)
    c = da.check_agent_bridge(enabled=True, gateway=gateway, store=store)
    assert c.status == "warn"
    assert "mount is unchecked" in c.detail and "keyed" in c.detail


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"servers": [{"id": "hal0-admin"}, {"id": "hal0-agents"}]}, True),
        ({"servers": [{"id": "hal0-admin"}]}, False),
        (None, None),
    ],
)
def test_the_mount_is_read_from_the_server_listing(
    monkeypatch: pytest.MonkeyPatch, payload: object, expected: bool | None
) -> None:
    seen: list[str] = []

    def fake_get(path: str, base: str | None, **_kw: object) -> object:
        seen.append(path)
        return payload

    monkeypatch.setattr(da, "_get_any", fake_get)
    assert da._agent_mount_present("http://x") is expected
    assert seen == ["/api/mcp/servers"]


def test_default_ledger_is_read_only_and_never_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "var" / "bridge.db"
    monkeypatch.setattr(da.paths, "bridge_db", lambda: db)
    assert da._live_bridge_runs(None) == (0, 0)
    assert not db.exists() and not db.parent.exists()
    opened = BridgeStore(db)
    opened.open()
    _run(opened, "run_a", status="running", pending='{"event": "approval.request"}', age_s=7200)
    _run(opened, "run_b", status="completed", pending=None, age_s=7200)
    assert da._live_bridge_runs(None) == (1, 1)


def test_an_unreadable_ledger_is_unknown_not_zero(
    monkeypatch: pytest.MonkeyPatch, gateway: HermesGateway, tmp_path: Path
) -> None:
    """``bridge.db`` is 0600: a doctor run as another user must say so, not report "0 live runs"."""
    _hermes_answers(monkeypatch)
    db = tmp_path / "var" / "bridge.db"
    opened = BridgeStore(db)
    opened.open()
    _run(opened, "run_stuck", status="running", pending='{"event": "approval.request"}', age_s=7200)
    monkeypatch.setattr(da.paths, "bridge_db", lambda: db)
    real_access = os.access
    monkeypatch.setattr(
        da.os,
        "access",
        lambda p, mode, **kw: False if Path(p) == db else real_access(p, mode, **kw),
    )
    assert da._live_bridge_runs(None) is None
    c = da.check_agent_bridge(enabled=True, mounted=True, gateway=gateway)
    assert c.status == "warn"
    assert "run ledger unreadable (run as hal0 or root)" in c.detail
    assert "0 live runs" not in c.detail and "live runs unknown" in c.detail


def test_a_ledger_that_is_not_sqlite_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "bridge.db"
    db.write_bytes(b"not a database" * 100)
    monkeypatch.setattr(da.paths, "bridge_db", lambda: db)
    assert da._live_bridge_runs(None) is None


def test_a_ledger_in_a_directory_the_user_cannot_enter_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "var" / "bridge.db"
    monkeypatch.setattr(da.paths, "bridge_db", lambda: db)
    real_stat = os.stat

    def deny(path: object, *a: object, **kw: object) -> os.stat_result:
        if Path(str(path)) == db:
            raise PermissionError(13, "Permission denied")
        return real_stat(path, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(da.os, "stat", deny)
    assert da._live_bridge_runs(None) is None


def test_the_row_is_registered_after_the_store_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """``build_all_checks`` runs the row, right after ``bridge-store``."""
    from hal0.cli.doctor_verify import Check

    monkeypatch.setattr(
        da,
        "gather_payloads",
        lambda _b: {
            k: {} for k in ("health", "urls", "system", "capabilities", "memory", "services")
        },
    )
    monkeypatch.setattr(da, "build_checks", lambda **_k: [])
    monkeypatch.setattr(da, "_get_any", lambda *_a, **_k: None)
    order: list[str] = []
    for name in [n for n in dir(da) if n.startswith("check_") and callable(getattr(da, n))]:
        monkeypatch.setattr(
            da, name, lambda *_a, _n=name, **_k: (order.append(_n), Check(_n, _n, "pass", ""))[1]
        )
    da.build_all_checks()
    assert order.index("check_agent_bridge") == order.index("check_bridge_store") + 1
