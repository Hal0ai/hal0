"""Tests for the /api/stacks route surface (PR-4).

Covers catalog CRUD, declarative apply (dry-run diff + commit→converge),
export → import round-trip, snapshot, and seed-immutability — exercised through
the real ``create_app()`` + ``TestClient`` so the lifespan-wired registry /
slot-manager / orchestrator are in play.

Run targeted:
    PYTHONPATH=src .venv-test/bin/python -m pytest tests/api/test_stacks_routes.py -q
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.config import schema
from hal0.config.schema import StackConfig

# ── helpers ────────────────────────────────────────────────────────────────────


def _seed_slot_toml(home: str, name: str, *, model: str = "", port: int = 8090) -> Path:
    """Write a minimal slot TOML so the apply engine has a file to reconcile."""
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    body = f'[slot]\nname = "{name}"\nport = {port}\n'
    if model:
        body += f'\n[model]\ndefault = "{model}"\n'
    path.write_text(body, encoding="utf-8")
    return path


def _stack_body(name: str = "Coding", slots: list[dict] | None = None) -> dict:
    return {"name": name, "description": "test stack", "slots": slots or []}


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def app(tmp_hal0_home: str) -> FastAPI:
    """Fresh app; tmp_hal0_home means no stacks.toml → empty catalog."""
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# ── GET (list) ─────────────────────────────────────────────────────────────────


def test_list_ships_seed_catalog(client: TestClient) -> None:
    # Fresh install (no stacks.toml) → the built-in seed stacks (PR-6).
    r = client.get("/api/stacks")
    assert r.status_code == 200
    body = r.json()
    assert body["active"] is None
    assert body["drift"] == "none"
    slugs = {s["slug"] for s in body["stacks"]}
    assert {"saber", "forge", "pi"} <= slugs
    assert all(s["seed"] is True for s in body["stacks"] if s["slug"] in {"saber", "forge", "pi"})


# ── POST (create) ──────────────────────────────────────────────────────────────


def test_create_201_and_listed(client: TestClient) -> None:
    r = client.post("/api/stacks", json={"slug": "coding", "stack": _stack_body()})
    assert r.status_code == 201
    body = r.json()
    assert body["slug"] == "coding"
    assert body["name"] == "Coding"
    assert body["seed"] is False
    assert body["active"] is False
    listed = client.get("/api/stacks").json()["stacks"]
    assert any(s["slug"] == "coding" for s in listed)


def test_create_persists_across_reload(tmp_hal0_home: str) -> None:
    with TestClient(create_app()) as c1:
        assert (
            c1.post("/api/stacks", json={"slug": "persist", "stack": _stack_body()}).status_code
            == 201
        )
    with TestClient(create_app()) as c2:
        listed = c2.get("/api/stacks").json()["stacks"]
    assert any(s["slug"] == "persist" for s in listed)


def test_create_duplicate_409(client: TestClient) -> None:
    client.post("/api/stacks", json={"slug": "coding", "stack": _stack_body()})
    r = client.post("/api/stacks", json={"slug": "coding", "stack": _stack_body()})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "stacks.exists"


def test_create_invalid_slug_409(client: TestClient) -> None:
    r = client.post("/api/stacks", json={"slug": "Bad Slug!", "stack": _stack_body()})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "stacks.invalid_name"


def test_create_unknown_body_field_422(client: TestClient) -> None:
    r = client.post(
        "/api/stacks",
        json={"slug": "coding", "stack": _stack_body(), "bogus": 1},
    )
    assert r.status_code == 422


# ── GET (detail) ───────────────────────────────────────────────────────────────


def test_get_detail_200(client: TestClient) -> None:
    client.post("/api/stacks", json={"slug": "coding", "stack": _stack_body()})
    r = client.get("/api/stacks/coding")
    assert r.status_code == 200
    assert r.json()["name"] == "Coding"


def test_get_missing_404(client: TestClient) -> None:
    r = client.get("/api/stacks/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "stacks.not_found"


# ── PUT (update) ───────────────────────────────────────────────────────────────


def test_update_200_persists(client: TestClient) -> None:
    client.post("/api/stacks", json={"slug": "coding", "stack": _stack_body()})
    r = client.put("/api/stacks/coding", json=_stack_body(name="Coding v2"))
    assert r.status_code == 200
    assert r.json()["name"] == "Coding v2"
    assert client.get("/api/stacks/coding").json()["name"] == "Coding v2"


def test_update_missing_404(client: TestClient) -> None:
    r = client.put("/api/stacks/nope", json=_stack_body())
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "stacks.not_found"


# ── DELETE ─────────────────────────────────────────────────────────────────────


def test_delete_204(client: TestClient) -> None:
    client.post("/api/stacks", json={"slug": "coding", "stack": _stack_body()})
    assert client.delete("/api/stacks/coding").status_code == 204
    slugs = {s["slug"] for s in client.get("/api/stacks").json()["stacks"]}
    assert "coding" not in slugs  # seeds remain; the custom stack is gone


def test_delete_missing_404(client: TestClient) -> None:
    r = client.delete("/api/stacks/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "stacks.not_found"


# ── seed immutability (monkeypatched seed registry) ────────────────────────────


def test_seed_immutable_put_and_delete_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(schema.SEED_STACKS, "saber", StackConfig(name="Saber"))
    put = client.put("/api/stacks/saber", json=_stack_body())
    assert put.status_code == 409
    assert put.json()["error"]["code"] == "stacks.seed_immutable"
    delete = client.delete("/api/stacks/saber")
    assert delete.status_code == 409
    assert delete.json()["error"]["code"] == "stacks.seed_immutable"


# ── apply (dry-run) ────────────────────────────────────────────────────────────


def test_apply_dry_run_shows_diff(tmp_hal0_home: str) -> None:
    _seed_slot_toml(tmp_hal0_home, "agent", model="old-model")
    with TestClient(create_app()) as c:
        c.post(
            "/api/stacks",
            json={
                "slug": "coding",
                "stack": _stack_body(slots=[{"slot": "agent", "model": "new-model"}]),
            },
        )
        r = c.post("/api/stacks/coding/apply", params={"dry_run": "true"})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    row = next(x for x in body["changes"] if x["slot"] == "agent")
    assert row["before_model"] == "old-model"
    assert row["after_model"] == "new-model"
    assert row["changed"] is True


# ── apply (commit + converge with injected fakes) ──────────────────────────────


def test_apply_commit_converges_and_sets_active(app: FastAPI, tmp_hal0_home: str) -> None:
    _seed_slot_toml(tmp_hal0_home, "agent", model="old-model")
    with TestClient(app) as c:
        c.post(
            "/api/stacks",
            json={
                "slug": "coding",
                "stack": _stack_body(slots=[{"slot": "agent", "model": "new-model"}]),
            },
        )
        # Inject fakes so converge() drives no real containers: empty live
        # snapshot → the agent slot is "loaded".
        fake_sm = AsyncMock()
        fake_sm.list = AsyncMock(return_value=[])
        app.state.slot_manager = fake_sm
        app.state.capability_orchestrator = AsyncMock()

        r = c.post("/api/stacks/coding/apply")
        assert r.status_code == 200
        body = r.json()
        assert body["dry_run"] is False
        assert "agent" in body["converged"]["loaded"]
        fake_sm.load.assert_awaited()

        # Active pointer + clean drift (live toml == applied projection).
        listed = c.get("/api/stacks").json()
        assert listed["active"] == "coding"
        assert listed["drift"] == "clean"
        active_item = next(s for s in listed["stacks"] if s["slug"] == "coding")
        assert active_item["active"] is True
        assert active_item["drift"] == "clean"


# ── create-on-apply (slots that don't exist yet) ───────────────────────────────


def test_apply_dry_run_lists_slots_to_create(client: TestClient) -> None:
    # "quick" has no slot TOML → dry-run flags it under `creates`.
    client.post(
        "/api/stacks",
        json={
            "slug": "coding",
            "stack": _stack_body(slots=[{"slot": "quick", "model": "m", "device": "gpu-vulkan"}]),
        },
    )
    r = client.post("/api/stacks/coding/apply", params={"dry_run": "true"})
    assert r.status_code == 200
    assert r.json()["creates"] == ["quick"]


def test_apply_commit_creates_missing_slot(app: FastAPI, tmp_hal0_home: str) -> None:
    with TestClient(app) as c:
        c.post(
            "/api/stacks",
            json={
                "slug": "coding",
                "stack": _stack_body(
                    slots=[
                        {"slot": "quick", "model": "m", "device": "gpu-vulkan", "profile": "vulkan"}
                    ]
                ),
            },
        )
        fake_sm = AsyncMock()
        fake_sm.list = AsyncMock(return_value=[])
        app.state.slot_manager = fake_sm
        app.state.capability_orchestrator = AsyncMock()

        r = c.post("/api/stacks/coding/apply")
        assert r.status_code == 200
        body = r.json()
        assert body["created"] == ["quick"]
        # The slot was created through the live slot-create path before converge.
        fake_sm.create.assert_awaited()
        created_name = fake_sm.create.await_args.args[0]
        assert created_name == "quick"
        # A port was auto-assigned by _normalize_create_body.
        created_body = fake_sm.create.await_args.args[1]
        assert isinstance(created_body.get("port"), int) and created_body["port"] > 0


# ── the write boundary: stack apply may not cross the slot/model partition ─────
#
# ``POST /{slug}/apply`` (non-dry-run) reaches slot TOML through
# ``StackApplyEngine`` → ``reconcile_and_guard_slot_config``, entirely
# in-process: it never passes the ``routes/slots`` handlers where
# ``reject_model_owned_slot_keys`` lives. Before the fix that pipeline checked
# only NPU-exclusivity and default-uniqueness, so — ``SlotConfig`` being
# ``extra="allow"`` — an apply silently persisted ``mtp``/``vision``/
# ``enable_thinking`` onto an EXISTING slot's TOML that
# ``PUT /api/slots/{name}/config`` 400s on, reintroducing the pre-partition
# on-disk shape (spec-hw-slot-ownership §1). Nothing anywhere covered this.


def _apply_with_fakes(app: FastAPI, c: TestClient, slug: str) -> dict:
    """Commit-apply ``slug`` with converge fakes so no container is touched."""
    fake_sm = AsyncMock()
    fake_sm.list = AsyncMock(return_value=[])
    app.state.slot_manager = fake_sm
    app.state.capability_orchestrator = AsyncMock()
    r = c.post(f"/api/stacks/{slug}/apply")
    assert r.status_code == 200, r.text
    return r.json()


def _read_slot_toml(home: str, name: str) -> dict:
    """Parse a slot's on-disk TOML.

    Parsed, not substring-matched: several real model ids contain ``mtp`` in
    their own name (``…-ace-saber-mtp-f16-…``), so a text search for the KEY
    reports a false positive on the VALUE.
    """
    import tomllib

    with open(Path(home) / "etc" / "hal0" / "slots" / f"{name}.toml", "rb") as f:
        data = tomllib.load(f)
    # The on-disk [slot] table is hoisted to the top level on load.
    hoisted = data.pop("slot", None)
    if isinstance(hoisted, dict):
        data.update(hoisted)
    return data


@pytest.mark.parametrize(
    ("field", "value"),
    [("vision", True), ("mtp", True), ("enable_thinking", True)],
)
def test_apply_never_lands_model_owned_keys_on_an_existing_slot(
    app: FastAPI, tmp_hal0_home: str, field: str, value: bool
) -> None:
    """A stack row's model-owned capability must not reach the slot's TOML.

    This is the regression test for the stacks-apply bypass. The row still
    CARRIES the field (``StackSlotEntry`` keeps all three for back-compat —
    seed stacks declare ``mtp = true``), and the apply still succeeds and still
    swaps the model; the capability simply never projects onto the slot.
    """
    _seed_slot_toml(tmp_hal0_home, "agent", model="old-model")
    with TestClient(app) as c:
        c.post(
            "/api/stacks",
            json={
                "slug": "coding",
                "stack": _stack_body(slots=[{"slot": "agent", "model": "new-model", field: value}]),
            },
        )
        body = _apply_with_fakes(app, c, "coding")

        # The apply did its real work...
        assert body["dry_run"] is False
        on_disk = _read_slot_toml(tmp_hal0_home, "agent")
        assert on_disk["model"]["default"] == "new-model", "the model swap must still land"
        # ...but the model-owned key is nowhere on disk.
        assert field not in on_disk, (
            f"stack apply persisted model-owned {field!r} to slot TOML: {on_disk}"
        )
        # And the same key is still refused at the HTTP slot-config boundary, so
        # the two write surfaces now agree instead of one being a back door.
        r = c.put("/api/slots/agent/config", json={field: value})
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "slot.model_owned_key_denied"


def test_apply_dry_run_never_projects_model_owned_keys(
    client: TestClient, tmp_hal0_home: str
) -> None:
    """The dry-run preview must not PROMISE a write the commit won't make.

    ``plan()`` is the same code path apply commits, so a dry-run that showed
    ``vision`` in its after-state would either be lying or the guard would only
    be half-applied.
    """
    _seed_slot_toml(tmp_hal0_home, "agent", model="old-model")
    client.post(
        "/api/stacks",
        json={
            "slug": "coding",
            "stack": _stack_body(
                slots=[
                    {
                        "slot": "agent",
                        "model": "new-model",
                        "vision": True,
                        "mtp": True,
                        "enable_thinking": True,
                    }
                ]
            ),
        },
    )
    r = client.post("/api/stacks/coding/apply", params={"dry_run": "true"})
    assert r.status_code == 200, r.text
    body = r.json()
    row = next(x for x in body["changes"] if x["slot"] == "agent")
    assert row["after_model"] == "new-model"
    assert row["changed"] is True
    # Nothing was rejected — the keys are simply not projected, so a legitimate
    # stack that declares them still applies cleanly.
    assert not any("rejected" in line for line in body["summary"]), body["summary"]


def test_seed_stacks_declaring_mtp_still_apply_cleanly(app: FastAPI, tmp_hal0_home: str) -> None:
    """The shipped seed stacks carry ``mtp = true`` — they must not self-reject.

    ``config/data/seed_stacks.toml`` declares ``mtp = true`` on the saber /
    forge / pi agent rows. Had the fix kept projecting the key and merely added
    the guard, applying any shipped seed stack would fail its own guard on every
    box. Uses ``saber``'s real slot names via the seed catalog.
    """
    _seed_slot_toml(tmp_hal0_home, "agent", model="old-model")
    with TestClient(app) as c:
        saber = c.get("/api/stacks/saber")
        assert saber.status_code == 200, saber.text
        agent_row = next(e for e in saber.json()["slots"] if e["slot"] == "agent")
        assert agent_row["mtp"] is True, "fixture assumption: seed saber declares mtp"
        body = _apply_with_fakes(app, c, "saber")
    assert not any("rejected" in line for line in body["summary"]), body["summary"]
    on_disk = _read_slot_toml(tmp_hal0_home, "agent")
    assert "mtp" not in on_disk, on_disk
    assert on_disk["model"]["default"].startswith("qwen3-6-35b"), "the seed model still landed"


# ── the write boundary: freeform [server].extra_args is screened ───────────────


def test_apply_rejects_hardware_flags_in_server_extra_args(
    app: FastAPI, tmp_hal0_home: str
) -> None:
    """``-ngl`` in a stack row's extra_args is refused per-slot, not persisted.

    The slot surface had no freeform-flag screen on ANY in-process writer: the
    model and profile surfaces screen at their HTTP routes, ``SlotManager``
    screens nowhere, and the launch path only screens against
    MANAGED_ARGS_DENYLIST at load time. A stack could therefore park a
    grid-owned hardware flag in slot TOML permanently.

    Report-don't-raise: the offending slot keeps ``after == before`` and the
    reason lands in ``summary`` — one bad row must not 400 a multi-slot apply.
    """
    slot_path = _seed_slot_toml(tmp_hal0_home, "agent", model="old-model")
    before = slot_path.read_text(encoding="utf-8")
    with TestClient(app) as c:
        c.post(
            "/api/stacks",
            json={
                "slug": "coding",
                "stack": _stack_body(
                    slots=[
                        {
                            "slot": "agent",
                            "model": "new-model",
                            "server_extra_args": "-ngl 99 --jinja",
                        }
                    ]
                ),
            },
        )
        body = _apply_with_fakes(app, c, "coding")

    assert any("rejected" in line for line in body["summary"]), body["summary"]
    assert any("-ngl" in line for line in body["summary"]), body["summary"]
    assert slot_path.read_text(encoding="utf-8") == before, "the rejected slot is untouched"


def test_apply_rejects_managed_flags_in_server_extra_args(app: FastAPI, tmp_hal0_home: str) -> None:
    """``--port`` in extra_args would rebind the slot's listener — refused."""
    slot_path = _seed_slot_toml(tmp_hal0_home, "agent", model="old-model")
    with TestClient(app) as c:
        c.post(
            "/api/stacks",
            json={
                "slug": "coding",
                "stack": _stack_body(
                    slots=[
                        {
                            "slot": "agent",
                            "model": "new-model",
                            "server_extra_args": "--port 9999",
                        }
                    ]
                ),
            },
        )
        body = _apply_with_fakes(app, c, "coding")

    assert any("rejected" in line for line in body["summary"]), body["summary"]
    assert "9999" not in slot_path.read_text(encoding="utf-8")


def test_apply_keeps_clean_server_extra_args(app: FastAPI, tmp_hal0_home: str) -> None:
    """Sanity floor: a legitimate tuning flag still persists."""
    slot_path = _seed_slot_toml(tmp_hal0_home, "agent", model="old-model")
    with TestClient(app) as c:
        c.post(
            "/api/stacks",
            json={
                "slug": "coding",
                "stack": _stack_body(
                    slots=[
                        {
                            "slot": "agent",
                            "model": "new-model",
                            "server_extra_args": "--jinja -fa on",
                        }
                    ]
                ),
            },
        )
        body = _apply_with_fakes(app, c, "coding")

    assert not any("rejected" in line for line in body["summary"]), body["summary"]
    assert "--jinja" in slot_path.read_text(encoding="utf-8")


def test_create_on_apply_screens_server_extra_args(app: FastAPI, tmp_hal0_home: str) -> None:
    """The sibling CREATE path is screened too, and reports per-slot.

    ``_create_missing_slots`` calls ``SlotManager.create()`` in-process, which
    has no screen of its own — so the guard has to run at the stacks call site
    for a slot born from an apply.
    """
    with TestClient(app) as c:
        c.post(
            "/api/stacks",
            json={
                "slug": "coding",
                "stack": _stack_body(
                    slots=[
                        {
                            "slot": "quick",
                            "model": "m",
                            "device": "gpu-vulkan",
                            "profile": "vulkan",
                            "server_extra_args": "--threads 8",
                        }
                    ]
                ),
            },
        )
        fake_sm = AsyncMock()
        fake_sm.list = AsyncMock(return_value=[])
        app.state.slot_manager = fake_sm
        app.state.capability_orchestrator = AsyncMock()
        r = c.post("/api/stacks/coding/apply")
        assert r.status_code == 200, r.text
        body = r.json()

    assert body["created"] == [], "a screened-out slot must not be reported created"
    fake_sm.create.assert_not_awaited()
    errors = body["converged"]["errors"]
    assert any(e["target"] == "quick" and "--threads" in e["error"] for e in errors), errors


# ── export / import round-trip ─────────────────────────────────────────────────


def test_export_import_round_trip(client: TestClient) -> None:
    client.post("/api/stacks", json={"slug": "coding", "stack": _stack_body()})
    env = client.post("/api/stacks/coding/export").json()
    assert env["kind"] == "hal0.stack"
    assert env["checksum"].startswith("sha256:")

    # dry-run import validates + checksum-verifies, creates nothing.
    dry = client.post("/api/stacks/import", json={"dry_run": True, "envelope": env})
    assert dry.status_code == 200
    assert dry.json()["valid"] is True
    assert dry.json()["checksum_ok"] is True

    # commit creates a clone under a new slug.
    commit = client.post("/api/stacks/import", json={"slug": "coding-copy", "envelope": env})
    assert commit.status_code == 200
    assert commit.json()["stack"]["slug"] == "coding-copy"
    assert any(s["slug"] == "coding-copy" for s in client.get("/api/stacks").json()["stacks"])


def test_import_commit_without_slug_400(client: TestClient) -> None:
    client.post("/api/stacks", json={"slug": "coding", "stack": _stack_body()})
    env = client.post("/api/stacks/coding/export").json()
    r = client.post("/api/stacks/import", json={"envelope": env})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "stacks.import_no_slug"


def test_import_bad_envelope_400(client: TestClient) -> None:
    r = client.post("/api/stacks/import", json={"dry_run": True, "envelope": {"kind": "nope"}})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "stacks.bad_envelope"


# ── snapshot ───────────────────────────────────────────────────────────────────


def test_snapshot_returns_unsaved_config(tmp_hal0_home: str) -> None:
    _seed_slot_toml(tmp_hal0_home, "agent", model="some-model")
    with TestClient(create_app()) as c:
        r = c.post("/api/stacks/snapshot", json={"name": "from-live"})
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is False
    assert body["stack"]["name"] == "from-live"
    assert any(s["slot"] == "agent" for s in body["stack"]["slots"])


def test_snapshot_with_slug_persists(tmp_hal0_home: str) -> None:
    _seed_slot_toml(tmp_hal0_home, "agent", model="some-model")
    with TestClient(create_app()) as c:
        r = c.post("/api/stacks/snapshot", json={"name": "snap", "slug": "snap-1"})
        assert r.status_code == 200
        assert r.json()["created"] is True
        assert any(s["slug"] == "snap-1" for s in c.get("/api/stacks").json()["stacks"])
