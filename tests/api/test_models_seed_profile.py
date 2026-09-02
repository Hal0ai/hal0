"""Tests for POST /api/models/{model_id}/seed-profile — profile stamp route.

Server-side twin of the old model-drawer template select: resolves a named
profile and stamps its flags onto ``defaults.extra_args``/``defaults.profile``,
re-screening through ``screen_model_write`` so a stale/hand-edited profile can
never smuggle a managed flag into the stamp.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app

# ── isolated app fixture (mirrors test_models_feasibility.py) ───────────────


@pytest.fixture
def crud_app(tmp_hal0_home: str) -> FastAPI:
    extra_root = Path(tmp_hal0_home) / "crud-models"
    extra_root.mkdir(parents=True)
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        f'[models]\nroots = ["{extra_root}"]\nauto_scan_on_start = false\n',
        encoding="utf-8",
    )
    return create_app()


@pytest.fixture
def crud_client(crud_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(crud_app) as c:
        yield c


@pytest.fixture
def crud_models_root(tmp_hal0_home: str) -> Path:
    return Path(tmp_hal0_home) / "crud-models"


def _create_minimal_model(client: TestClient, models_root: Path) -> str:
    """Register a minimal model and return its id."""
    fpath = models_root / "seed-profile-test.gguf"
    fpath.write_bytes(b"\x00" * 64)
    mid = "seed-profile-test"
    r = client.post("/api/models", json={"id": mid, "path": str(fpath)})
    assert r.status_code == 201, r.text
    return mid


def _events_since(client: TestClient, since: int, type_glob: str | None = None) -> list[dict]:
    """Mirrors test_models_crud.py's event-polling idiom."""
    params = f"?since={since}&limit=1000"
    if type_glob:
        params += f"&type={type_glob}"
    return client.get(f"/api/events{params}").json().get("events", [])


def _max_event_id(client: TestClient) -> int:
    body = client.get("/api/events?limit=1000").json()
    return max((ev["id"] for ev in body.get("events", [])), default=0)


# ── tests ─────────────────────────────────────────────────────────────────


def test_seed_profile_stamps_flags_and_provenance(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    mid = _create_minimal_model(crud_client, crud_models_root)
    # "chat" is a seed profile shipped in every install (seed_profiles.toml).
    r = crud_client.post(f"/api/models/{mid}/seed-profile", json={"profile": "chat"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["defaults"]["profile"] == "chat"
    assert (
        "--jinja" in body["defaults"]["extra_args"]
    )  # chat's flags: -fa auto --jinja -b 2048 -ub 512


def test_seed_profile_emits_model_updated_with_changed_fields(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    mid = _create_minimal_model(crud_client, crud_models_root)
    pre = _max_event_id(crud_client)
    r = crud_client.post(f"/api/models/{mid}/seed-profile", json={"profile": "chat"})
    assert r.status_code == 200, r.text

    events = _events_since(crud_client, pre, "model.updated")
    assert events, "expected a model.updated event"
    payload = next(ev for ev in events if ev["data"].get("id") == mid)
    assert payload["data"]["changed_fields"] == ["defaults.extra_args", "defaults.profile"]


def test_seed_profile_merge_preserves_sibling_defaults_keys(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    """``defaults`` is a 1-level merged subtable (registry/store.py) — a stamp
    that only names ``extra_args``/``profile`` must leave a sibling default
    (here ``context_size``) that was set earlier untouched."""
    mid = _create_minimal_model(crud_client, crud_models_root)
    r = crud_client.put(f"/api/models/{mid}", json={"defaults": {"context_size": 4096}})
    assert r.status_code == 200, r.text
    assert r.json()["defaults"]["context_size"] == 4096

    r = crud_client.post(f"/api/models/{mid}/seed-profile", json={"profile": "chat"})
    assert r.status_code == 200, r.text

    body = crud_client.get(f"/api/models/{mid}").json()
    assert body["defaults"]["profile"] == "chat"
    assert "--jinja" in body["defaults"]["extra_args"]
    assert body["defaults"]["context_size"] == 4096


def test_seed_profile_unknown_model_404(crud_client: TestClient) -> None:
    r = crud_client.post("/api/models/no-such-model/seed-profile", json={"profile": "chat"})
    assert r.status_code == 404


def test_seed_profile_unknown_profile_404(crud_client: TestClient, crud_models_root: Path) -> None:
    mid = _create_minimal_model(crud_client, crud_models_root)
    r = crud_client.post(f"/api/models/{mid}/seed-profile", json={"profile": "no-such"})
    assert r.status_code == 404


def test_seed_profile_requires_profile_name(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    mid = _create_minimal_model(crud_client, crud_models_root)
    r = crud_client.post(f"/api/models/{mid}/seed-profile", json={})
    assert r.status_code == 400


def test_seed_profile_rescreens_managed_flags(
    crud_client: TestClient, crud_models_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A stale/hand-edited profile whose stored flags smuggle a managed arg
    # must be rejected at stamp time (spec decision 1).
    mid = _create_minimal_model(crud_client, crud_models_root)
    from hal0 import profiles as profiles_mod

    real_resolve = profiles_mod.ProfileCatalog.resolve

    def bad_resolve(self: profiles_mod.ProfileCatalog, name: str) -> profiles_mod.ResolvedProfile:
        rp = real_resolve(self, "chat")
        return dataclasses.replace(rp, flags="--port 9999 -fa auto")

    monkeypatch.setattr(profiles_mod.ProfileCatalog, "resolve", bad_resolve)
    r = crud_client.post(f"/api/models/{mid}/seed-profile", json={"profile": "chat"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "slot.managed_arg_denied"
    # And nothing persisted (a freshly-created model has no defaults at all):
    after_defaults = crud_client.get(f"/api/models/{mid}").json().get("defaults") or {}
    assert after_defaults.get("profile") != "chat"
