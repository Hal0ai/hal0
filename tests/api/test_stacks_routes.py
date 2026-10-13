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


def test_stale_seed_on_disk_lists_as_the_code_seed_and_a_write_cleans_it(
    tmp_hal0_home: str,
) -> None:
    """A stacks.toml written by an older release holds a `forge` naming retired
    profiles. The API serves the code seed, still refuses to edit it, and the
    next write drops the stale copy from disk."""
    import tomllib

    path = Path(tmp_hal0_home) / "etc" / "hal0" / "stacks.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '[stack.forge]\nname = "Forge"\n\n'
        '[[stack.forge.slots]]\nslot = "agent"\nmodel = "old"\nprofile = "rocm"\n\n'
        '[[stack.forge.slots]]\nslot = "utility"\nmodel = "old"\nprofile = "vulkan"\n',
        encoding="utf-8",
    )
    with TestClient(create_app()) as c:
        forge = next(s for s in c.get("/api/stacks").json()["stacks"] if s["slug"] == "forge")
        assert forge["seed"] is True
        assert {row["profile"] for row in forge["slots"]}.isdisjoint({"rocm", "vulkan", "rocm-moe"})
        assert c.put("/api/stacks/forge", json=_stack_body()).status_code == 409
        assert (
            c.post("/api/stacks", json={"slug": "mine", "stack": _stack_body()}).status_code == 201
        )
        slugs = [s["slug"] for s in c.get("/api/stacks").json()["stacks"]]
    assert slugs == ["saber", "forge", "pi", "mine"]
    assert set(tomllib.loads(path.read_text(encoding="utf-8"))["stack"]) == {"mine"}


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


def test_apply_dry_run_lists_no_unloads_when_commit_would_not_converge(
    app: FastAPI, tmp_hal0_home: str
) -> None:
    """#1511: commit converges only with both the slot manager and the
    capability orchestrator, so the preview reports no unloads without one."""
    from types import SimpleNamespace

    from hal0.slots.state import SlotState

    _seed_slot_toml(tmp_hal0_home, "agent", model="old-model")
    with TestClient(app) as c:
        c.post(
            "/api/stacks",
            json={
                "slug": "coding",
                "stack": _stack_body(slots=[{"slot": "agent", "model": "new-model"}]),
            },
        )
        fake_sm = AsyncMock()
        fake_sm.list = AsyncMock(
            return_value=[SimpleNamespace(name="coder", state=SlotState.READY, model_id="qwen")]
        )
        app.state.slot_manager = fake_sm
        app.state.capability_orchestrator = None

        r = c.post("/api/stacks/coding/apply", params={"dry_run": "true"})
    assert r.status_code == 200
    assert r.json()["unloads"] == []


def test_apply_dry_run_lists_running_slots_it_will_unload(app: FastAPI, tmp_hal0_home: str) -> None:
    """#1511: apply is a declarative replace, so the preview must say what it tears down."""
    from types import SimpleNamespace

    from hal0.slots.state import SlotState

    _seed_slot_toml(tmp_hal0_home, "agent", model="old-model")
    with TestClient(app) as c:
        c.post(
            "/api/stacks",
            json={
                "slug": "coding",
                "stack": _stack_body(slots=[{"slot": "agent", "model": "new-model"}]),
            },
        )
        fake_sm = AsyncMock()
        fake_sm.list = AsyncMock(
            return_value=[
                SimpleNamespace(name="agent", state=SlotState.READY, model_id="old-model"),
                SimpleNamespace(name="coder", state=SlotState.READY, model_id="qwen"),
                SimpleNamespace(name="img", state=SlotState.OFFLINE, model_id=None),
            ]
        )
        app.state.slot_manager = fake_sm
        app.state.capability_orchestrator = AsyncMock()

        r = c.post("/api/stacks/coding/apply", params={"dry_run": "true"})
    assert r.status_code == 200
    assert r.json()["unloads"] == ["coder"]
    # A preview never touches runtime.
    fake_sm.unload.assert_not_awaited()
    fake_sm.load.assert_not_awaited()


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


# ── the write boundary: a stack row is placement only (ADR-0029) ──────────────
#
# ``POST /{slug}/apply`` (non-dry-run) reaches slot TOML through
# ``StackApplyEngine`` → ``reconcile_and_guard_slot_config``, entirely
# in-process. A stack row carries placement (slot, model, device, provider,
# profile, capabilities); every tune lives on the profile it names. Schema 3
# dropped the per-row ``vision`` / ``mtp`` / ``enable_thinking`` /
# ``server_extra_args`` keys, so there is nothing tuning-shaped left to project.

_RETIRED_ROW_KEYS = ("vision", "mtp", "enable_thinking", "server_extra_args")


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
    their own name (``…-ace-saber-mtp-f16-…``), so a text search for a KEY
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


@pytest.mark.parametrize("field", _RETIRED_ROW_KEYS)
def test_stack_rows_refuse_the_retired_tuning_keys(client: TestClient, field: str) -> None:
    """A row naming a retired key is rejected on create, not silently stripped."""
    value: object = "-fa on" if field == "server_extra_args" else True
    r = client.post(
        "/api/stacks",
        json={
            "slug": "coding",
            "stack": _stack_body(slots=[{"slot": "agent", "model": "m", field: value}]),
        },
    )
    assert r.status_code == 422, r.text
    assert client.get("/api/stacks/coding").status_code == 404


def test_apply_lands_placement_only_on_an_existing_slot(app: FastAPI, tmp_hal0_home: str) -> None:
    """The apply swaps the model and re-points the profile; it writes no tune."""
    _seed_slot_toml(tmp_hal0_home, "agent", model="old-model")
    with TestClient(app) as c:
        c.post(
            "/api/stacks",
            json={
                "slug": "coding",
                "stack": _stack_body(
                    slots=[{"slot": "agent", "model": "new-model", "profile": "coding"}]
                ),
            },
        )
        body = _apply_with_fakes(app, c, "coding")

        assert body["dry_run"] is False
        on_disk = _read_slot_toml(tmp_hal0_home, "agent")
        assert on_disk["model"]["default"] == "new-model", "the model swap must still land"
        assert on_disk["profile"] == "coding"
        assert schema.slot_tuning_keys(on_disk) == [], on_disk
        # The tuning keys are refused at the HTTP slot-config boundary too, so
        # the two write surfaces agree: a tune belongs on a profile.
        for field in ("mtp", "vision", "enable_thinking"):
            r = c.put("/api/slots/agent/config", json={field: True})
            assert r.status_code == 400, r.text
            assert r.json()["error"]["code"] == "slot.tuning_key_denied"


def test_apply_dry_run_previews_the_profile_repoint(client: TestClient, tmp_hal0_home: str) -> None:
    """The dry-run is the same plan the commit applies: model + profile only."""
    _seed_slot_toml(tmp_hal0_home, "agent", model="old-model")
    client.post(
        "/api/stacks",
        json={
            "slug": "coding",
            "stack": _stack_body(
                slots=[{"slot": "agent", "model": "new-model", "profile": "coding"}]
            ),
        },
    )
    r = client.post("/api/stacks/coding/apply", params={"dry_run": "true"})
    assert r.status_code == 200, r.text
    body = r.json()
    row = next(x for x in body["changes"] if x["slot"] == "agent")
    assert row["after_model"] == "new-model"
    assert row["changed"] is True
    assert not any("rejected" in line for line in body["summary"]), body["summary"]


def test_seed_stacks_apply_cleanly_and_carry_placement_only(
    app: FastAPI, tmp_hal0_home: str
) -> None:
    """The shipped seed stacks name a profile per row and nothing else tuning-shaped.

    Uses ``saber``'s real slot names via the seed catalog: the agent row names
    the ``agent`` seed profile (which carries the 64K window), and applying it
    lands model + profile without a tuning key."""
    _seed_slot_toml(tmp_hal0_home, "agent", model="old-model")
    with TestClient(app) as c:
        saber = c.get("/api/stacks/saber")
        assert saber.status_code == 200, saber.text
        agent_row = next(e for e in saber.json()["slots"] if e["slot"] == "agent")
        assert not set(_RETIRED_ROW_KEYS) & set(agent_row), agent_row
        assert agent_row["profile"] == "agent"
        body = _apply_with_fakes(app, c, "saber")
    assert not any("rejected" in line for line in body["summary"]), body["summary"]
    on_disk = _read_slot_toml(tmp_hal0_home, "agent")
    assert on_disk["model"]["default"] == "Qwen3.6-35B-A3B-MTP-GGUF", "the seed model still landed"
    assert on_disk["profile"] == "agent"
    assert schema.slot_tuning_keys(on_disk) == [], on_disk


def test_create_on_apply_writes_no_tuning_key(app: FastAPI, tmp_hal0_home: str) -> None:
    """A slot born from an apply carries placement + profile and no tune.

    ``_create_missing_slots`` calls ``SlotManager.create()`` in-process, so the
    body it builds is the whole contract: no ``server`` table, no window."""
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
                            "profile": "utility",
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

    assert body["created"] == ["quick"]
    created_body = fake_sm.create.await_args.args[1]
    assert created_body["profile"] == "utility"
    assert "server" not in created_body
    assert schema.slot_tuning_keys(created_body) == [], created_body


def test_import_screens_an_embedded_profile_with_hardware_flags(client: TestClient) -> None:
    """A stack envelope cannot back-door a flag the profile routes refuse: the
    embedded profile is screened before anything is written."""
    env = client.post(
        "/api/stacks/import",
        json={
            "dry_run": True,
            "envelope": {
                "kind": "hal0.stack",
                "schema_version": 3,
                "stack": {
                    "name": "S",
                    "schema_version": 3,
                    "slots": [{"slot": "agent", "model": "m", "profile": "bad-prof"}],
                    "profiles": {"bad-prof": {"flags": "-fa on -ngl 99"}},
                },
            },
        },
    )
    assert env.status_code == 200, env.text  # a dry run validates shape only

    r = client.post(
        "/api/stacks/import",
        json={
            "slug": "from-env",
            "envelope": {
                "kind": "hal0.stack",
                "schema_version": 3,
                "stack": {
                    "name": "S",
                    "schema_version": 3,
                    "slots": [{"slot": "agent", "model": "m", "profile": "bad-prof"}],
                    "profiles": {"bad-prof": {"flags": "-fa on -ngl 99"}},
                },
            },
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.hardware_flag_denied"
    assert client.get("/api/stacks/from-env").status_code == 404
    assert client.get("/api/profiles/bad-prof").status_code == 404


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


@pytest.mark.parametrize("dry_run", [True, False])
def test_import_schema_2_envelope_refused_400(client: TestClient, dry_run: bool) -> None:
    """A 1.4-era stack envelope (schema 2, per-row vision/mtp/...) is refused
    with a clear message — dry run and commit agree, nothing is created."""
    env = {
        "kind": "hal0.stack",
        "schema_version": 2,
        "stack": {
            "name": "Old",
            "schema_version": 2,
            "slots": [{"slot": "agent", "model": "m", "mtp": True, "vision": False}],
        },
    }
    body: dict = {"envelope": env, "dry_run": dry_run}
    if not dry_run:
        body["slug"] = "old"
    r = client.post("/api/stacks/import", json=body)
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "stacks.envelope_schema_unsupported"
    assert "re-export" in err["message"]
    assert client.get("/api/stacks/old").status_code == 404


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
