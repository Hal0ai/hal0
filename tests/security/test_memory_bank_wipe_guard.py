"""Issue #1457: the #1024 incident class, one route over.

#1024 was ``DELETE /api/memory/banks/{bank_id}`` reached with a single
unauthenticated curl. Its hardening — an echoed-``?confirm=<bank_id>``
gate plus a dry-run blast-radius preview (#1028) and a ``record_action``
audit row (#1030) — was applied by special-casing that one path out of the
generic ``_FORWARDS`` passthrough table into a hand-written handler.

``DELETE /api/memory/banks/{bank_id}/memories`` stayed in the table. It is
the same irreversible operation with a narrower name: Hindsight documents
``DELETE /v1/default/banks/{bank_id}/memories`` as "Delete memory units for
a memory bank … a destructive operation that cannot be undone". It had the
audit row and nothing else — no confirm echo, no preview, no gate. On the
live default posture (``auth_required=false``) that is a one-call wipe of
every memory unit in the bank, 1629 nodes / 315 documents on the box the
audit ran against.

Two claims, and a structural one:

1. The route rejects without a matching echoed ``?confirm=<bank_id>``, with
   the same preview payload ``delete_bank`` returns, and forwards nothing.
2. It still forwards on a match, still audits, still passes filters through.
3. ``CONFIRM_GUARDED_MEMORY_ROUTES`` pins *which* destructive routes must
   carry the echo, and this file asserts every pinned route really does —
   so the next irreversible route added to the memory surface has to be
   argued about in a diff rather than inheriting the passthrough by
   default. That is the gap #1457 is: the ADMIN classification in
   ``DESTRUCTIVE_MEMORY_ROUTES`` already covered this path and was inert,
   because classification is not a guard.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hal0.activity import AuditStore
from hal0.security.exposure import (
    CONFIRM_GUARDED_MEMORY_ROUTES,
    DESTRUCTIVE_MEMORY_ROUTES,
)
from tests.api.test_memory_admin_routes import (
    _build_app,
    _HindsightStubProvider,
    _Recorder,
    client,  # noqa: F401 — fixture
    recorder,  # noqa: F401 — fixture
)

WIPE = "/api/memory/banks/scratch/memories"


# ── 1. the guard ─────────────────────────────────────────────────────────────


def test_bank_memories_wipe_rejected_without_confirm(
    client: TestClient,  # noqa: F811
    recorder: _Recorder,  # noqa: F811
) -> None:
    """The #1457 repro: this returned 200 and wiped the bank."""
    r = client.delete(WIPE)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "memory.confirm_required"
    assert err["details"]["bank_id"] == "scratch"
    assert err["details"]["requires_confirm"] is True
    assert not any(rq["method"] == "DELETE" for rq in recorder.requests)


def test_bank_memories_wipe_rejected_on_mismatched_confirm(
    client: TestClient,  # noqa: F811
    recorder: _Recorder,  # noqa: F811
) -> None:
    r = client.delete(f"{WIPE}?confirm=other")
    assert r.status_code == 400
    assert not any(rq["method"] == "DELETE" for rq in recorder.requests)


def test_bank_memories_wipe_rejection_carries_the_blast_radius_preview(
    client: TestClient,  # noqa: F811
    recorder: _Recorder,  # noqa: F811
) -> None:
    """Same dry-run preview ``delete_bank`` returns — the operator must see
    *how much* the wipe destroys before echoing the id back."""
    recorder.respond(
        "GET",
        "/v1/default/banks/scratch/stats",
        200,
        {"memory_count": 1629, "document_count": 315, "entity_count": 12},
    )
    preview = client.delete(WIPE).json()["error"]["details"]["preview"]
    assert preview["stats_available"] is True
    assert preview["item_count"] == 1629
    assert preview["counts"]["document_count"] == 315


def test_bank_memories_wipe_preview_failsoft_still_rejects(
    recorder: _Recorder,  # noqa: F811
) -> None:
    """A preview is a convenience, never a gate."""
    import httpx

    from hal0.memory.hindsight_client import HindsightRestClient

    recorder.fail_connect = True
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder.handler), base_url="http://127.0.0.1:9177"
    )
    app = _build_app(_HindsightStubProvider(HindsightRestClient(http_client=http, api_key="x")))
    with TestClient(app) as c:
        r = c.delete(WIPE)
    assert r.status_code == 400
    assert r.json()["error"]["details"]["preview"]["stats_available"] is False


def test_bank_memories_wipe_accepts_a_body_confirm(
    client: TestClient,  # noqa: F811
    recorder: _Recorder,  # noqa: F811
) -> None:
    """``delete_bank`` reads the echo from the query string or the body;
    the sibling must not diverge on that detail."""
    r = client.request("DELETE", WIPE, json={"confirm": "scratch"})
    assert r.status_code == 200
    assert recorder.requests[-1]["method"] == "DELETE"


# ── 2. the golden path is intact ─────────────────────────────────────────────


def test_confirmed_bank_memories_wipe_forwards(
    client: TestClient,  # noqa: F811
    recorder: _Recorder,  # noqa: F811
) -> None:
    r = client.delete(f"{WIPE}?confirm=scratch")
    assert r.status_code == 200
    assert recorder.requests[-1]["method"] == "DELETE"
    assert recorder.requests[-1]["path"] == "/v1/default/banks/scratch/memories"


def test_confirmed_wipe_forwards_filters_but_not_the_confirm_token(
    client: TestClient,  # noqa: F811
    recorder: _Recorder,  # noqa: F811
) -> None:
    """Upstream filter params still ride; ``confirm`` is hal0's own gate and
    must not leak into the engine call."""
    r = client.delete(f"{WIPE}?confirm=scratch&types=observation")
    assert r.status_code == 200
    params = recorder.requests[-1]["params"]
    assert params.get("types") == "observation"
    assert "confirm" not in params


def test_bank_id_grammar_still_enforced_before_the_guard(
    client: TestClient,  # noqa: F811
) -> None:
    r = client.delete("/api/memory/banks/bad..id/memories?confirm=bad..id")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "memory.invalid_bank"


def test_confirmed_wipe_is_audited_and_rejection_is_not(tmp_path) -> None:
    """The audit row survived the rewrite (it was the one guard this route
    already had), and a rejected wipe must NOT produce one — a 400 is not a
    destructive op."""
    import httpx

    from hal0.memory.hindsight_client import HindsightRestClient
    from tests.api.test_memory_admin_routes import _build_app_with_audit

    rec = _Recorder()
    audit = AuditStore(tmp_path / "audit.db")
    audit.init_schema()
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(rec.handler), base_url="http://127.0.0.1:9177"
    )
    rest = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
    app = _build_app_with_audit(_HindsightStubProvider(rest), audit)

    with TestClient(app) as c:
        assert c.delete(WIPE).status_code == 400
        assert audit.query(action="memory.memories.delete") == []
        assert (
            c.delete(f"{WIPE}?confirm=scratch", headers={"X-hal0-Agent": "hermes"}).status_code
            == 200
        )

    rows = audit.query(action="memory.memories.delete")
    assert rows, "expected a memory.memories.delete audit row"
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["actor"] == "mcp:hermes"
    assert rows[0]["target"] == "scratch"


# ── 3. the pin: confirm-guarded routes are named, and really are guarded ─────


def test_confirm_guarded_routes_are_a_subset_of_the_destructive_set() -> None:
    assert CONFIRM_GUARDED_MEMORY_ROUTES <= DESTRUCTIVE_MEMORY_ROUTES


def test_confirm_guarded_set_covers_both_bank_wipes() -> None:
    assert (
        frozenset(
            {
                ("DELETE", "/api/memory/banks/{bank_id}"),
                ("DELETE", "/api/memory/banks/{bank_id}/memories"),
            }
        )
        == CONFIRM_GUARDED_MEMORY_ROUTES
    )


@pytest.mark.parametrize(("method", "template"), sorted(CONFIRM_GUARDED_MEMORY_ROUTES))
def test_every_confirm_guarded_route_actually_rejects_without_the_echo(
    method: str,
    template: str,
    client: TestClient,  # noqa: F811
    recorder: _Recorder,  # noqa: F811
) -> None:
    """The claim the constant makes, checked against the live app: naming a
    route here without wiring the guard fails this test."""
    path = template.replace("/api/memory", "").format(bank_id="scratch")
    r = client.request(method, f"/api/memory{path}")
    assert r.status_code == 400, f"{method} {path} was not confirm-guarded"
    assert r.json()["error"]["code"] == "memory.confirm_required"
    assert not any(rq["method"] == "DELETE" for rq in recorder.requests)
