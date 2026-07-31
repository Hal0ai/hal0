"""Tests for Profile CRUD API — POST/PUT/DELETE /api/profiles.

Run targeted:
    ~/dev/wt-phase-c/.venv/bin/python -m pytest tests/api/test_profiles_crud.py -q
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.config.schema import MTP_FLAG_BUNDLE, SEED_PROFILES

# ── helpers ────────────────────────────────────────────────────────────────────


def _seed_slot_toml(home: str, name: str, profile: str, port: int = 8090) -> Path:
    """Write a minimal slot TOML that references *profile*."""
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text(
        f'[slot]\nname = "{name}"\nport = {port}\nprofile = "{profile}"\n',
        encoding="utf-8",
    )
    return path


def _seed_flat_slot_toml(home: str, name: str, profile: str, port: int = 8090) -> Path:
    """Write a flat (top-level, no [slot] table) slot TOML (#1087)."""
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text(
        f'name = "{name}"\nport = {port}\nprofile = "{profile}"\ndevice = "gpu-vulkan"\n',
        encoding="utf-8",
    )
    return path


def _seed_profiles_toml(home: str, name: str, flags: str, **fields: str) -> Path:
    """Write a custom profile straight into profiles.toml, bypassing the API.

    The only way to stage state the write-path screens would refuse — i.e. the
    pre-guard profiles #1411 is about, authored before spec-hw-slot-ownership §5
    shipped. Mirrors the ``[profile.<name>]`` table shape the loader reads.
    """
    root = Path(home) / "etc" / "hal0"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "profiles.toml"
    extras = "".join(f'{k} = "{v}"\n' for k, v in fields.items())
    path.write_text(f'[profile.{name}]\nflags = "{flags}"\n{extras}', encoding="utf-8")
    return path


def _seed_corrupt_slot_toml(home: str, name: str) -> Path:
    """Write a slot TOML that fails to parse."""
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text("[slot\nthis is not = valid toml {{{", encoding="utf-8")
    return path


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def app(tmp_hal0_home: str) -> FastAPI:
    """Fresh app; tmp_hal0_home means no profiles.toml → seeds returned."""
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# ── POST /api/profiles ─────────────────────────────────────────────────────────


def test_create_profile_201_and_listed(client: TestClient) -> None:
    r = client.post(
        "/api/profiles",
        json={
            "name": "my-vulkan",
            "flags": "-fa on",
            "mtp": False,
            "device_class": "gpu",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "my-vulkan"
    assert body["flags"] == "-fa on"
    assert body["mtp"] is False
    assert body["device_class"] == "gpu"
    assert "resolved_flags" in body
    assert body["seed"] is False
    listed = client.get("/api/profiles").json()
    assert any(p["name"] == "my-vulkan" for p in listed)


# ── spec-hw-slot-ownership §5: profile flags reject slot-hardware flags ────────


def test_create_profile_rejects_slot_hardware_flag(client: TestClient) -> None:
    """A profile is a device-agnostic tune template — its flags must not carry a
    grid-owned hardware flag (--threads/-ngl/--device). The create hard-rejects
    with the "belongs on the slot" envelope and persists nothing."""
    r = client.post(
        "/api/profiles",
        json={
            "name": "hw-profile",
            "flags": "-fa on --threads 8",
            "device_class": "gpu",
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.hardware_flag_denied"
    assert "slot" in r.json()["error"]["message"].lower()
    listed = client.get("/api/profiles").json()
    assert not any(p["name"] == "hw-profile" for p in listed)


def test_update_profile_rejects_slot_hardware_flag(client: TestClient) -> None:
    """PUT screens the same partition — a clean profile edited to carry -ngl is
    rejected."""
    created = client.post(
        "/api/profiles",
        json={"name": "edit-me", "flags": "-fa on"},
    )
    assert created.status_code == 201, created.text
    r = client.put("/api/profiles/edit-me", json={"flags": "-b 2048 -ngl 99"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.hardware_flag_denied"


def test_create_profile_rejects_managed_flag(client: TestClient) -> None:
    """§21.7: -c/--ctx-size (like --port/--model/--host/--alias) is hal0-managed
    — a profile that persists it only explodes later when stamped onto a model.
    The create hard-rejects with slot.managed_arg_denied and persists nothing."""
    r = client.post(
        "/api/profiles",
        json={
            "name": "ctx-profile",
            "flags": "-fa on -c 131072",
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.managed_arg_denied"
    listed = client.get("/api/profiles").json()
    assert not any(p["name"] == "ctx-profile" for p in listed)


def test_update_profile_rejects_managed_flag(client: TestClient) -> None:
    """PUT screens the managed denylist too — a clean profile edited to carry
    --port is rejected."""
    created = client.post(
        "/api/profiles",
        json={"name": "edit-ctx", "flags": "-fa on"},
    )
    assert created.status_code == 201, created.text
    r = client.put("/api/profiles/edit-ctx", json={"flags": "-b 2048 --port 9999"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.managed_arg_denied"


# ── #1411: the §5 screen must not brick profiles authored before it ────────────


def test_update_grandfathers_stored_hardware_flag_on_rename_only_put(
    tmp_hal0_home: str,
) -> None:
    """A profile authored before §5 shipped must stay editable (#1411).

    Saving it back verbatim — exactly what the drawer does, since it round-trips
    the flag text from GET — used to 400 on the profile's OWN stored flags, so
    every pre-guard custom profile was read-only through the API. The screen
    now looks at what the PUT *introduces*, not what it inherits.
    """
    _seed_profiles_toml(
        tmp_hal0_home,
        "legacy",
        "-fa on -dev ROCm0 -ctk q8_0 -ctv q8_0 -b 2048 --threads 8",
        intent="pre-guard tune",
    )
    with TestClient(create_app()) as client:
        stored = client.get("/api/profiles/legacy")
        assert stored.status_code == 200, stored.text
        flags = stored.json()["flags"]
        assert "-dev" in flags and "--threads" in flags

        # Rename-only intent change, re-sending the stored flags verbatim.
        r = client.put("/api/profiles/legacy", json={"flags": flags, "intent": "retuned"})
        assert r.status_code == 200, r.text
        assert r.json()["intent"] == "retuned"
        assert r.json()["flags"] == flags


def test_update_without_flags_key_never_screens(tmp_hal0_home: str) -> None:
    """A patch that doesn't name `flags` at all must not be screened against the
    stored ones (#1411)."""
    _seed_profiles_toml(tmp_hal0_home, "legacy2", "-fa on -dev ROCm0")
    with TestClient(create_app()) as client:
        r = client.put("/api/profiles/legacy2", json={"intent": "still editable"})
        assert r.status_code == 200, r.text
        assert r.json()["intent"] == "still editable"


def test_update_still_rejects_a_newly_added_hardware_flag(tmp_hal0_home: str) -> None:
    """Grandfathering is per-flag, not a blanket amnesty: a legacy `-dev` does
    not buy the right to ALSO add `--threads` (#1411)."""
    _seed_profiles_toml(tmp_hal0_home, "legacy3", "-fa on -dev ROCm0")
    with TestClient(create_app()) as client:
        r = client.put("/api/profiles/legacy3", json={"flags": "-fa on -dev ROCm0 --threads 8"})
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "slot.hardware_flag_denied"
        assert r.json()["error"]["details"]["flags"] == ["--threads"]


def test_update_can_drop_the_legacy_hardware_flag(tmp_hal0_home: str) -> None:
    """The migration path stays open — removing the legacy flag is accepted and
    the grandfather no longer applies afterwards (#1411)."""
    _seed_profiles_toml(tmp_hal0_home, "legacy4", "-fa on -dev ROCm0")
    with TestClient(create_app()) as client:
        cleaned = client.put("/api/profiles/legacy4", json={"flags": "-fa on"})
        assert cleaned.status_code == 200, cleaned.text
        assert cleaned.json()["flags"] == "-fa on"
        # Now that the stored text is clean, re-adding -dev is a NEW reach.
        r = client.put("/api/profiles/legacy4", json={"flags": "-fa on -dev ROCm0"})
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "slot.hardware_flag_denied"


def test_create_is_never_grandfathered(client: TestClient) -> None:
    """POST has no stored baseline, so §5 stays a hard reject there (#1411)."""
    r = client.post("/api/profiles", json={"name": "fresh", "flags": "-fa on -dev ROCm0"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.hardware_flag_denied"


def test_create_profile_accepts_device_agnostic_tune(client: TestClient) -> None:
    """A real tune template (batch/flash-attn/KV-quant, no hardware) is accepted."""
    r = client.post(
        "/api/profiles",
        json={
            "name": "clean-tune",
            "flags": "-b 2048 -ub 512 -fa on -ctk q8_0",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["flags"] == "-b 2048 -ub 512 -fa on -ctk q8_0"


def test_create_persists_across_reload(tmp_hal0_home: str) -> None:
    """Second app/client constructed after the POST sees the written file."""
    app1 = create_app()
    with TestClient(app1) as c1:
        r = c1.post(
            "/api/profiles",
            json={"name": "persist-me"},
        )
        assert r.status_code == 201

    # New app reads profiles.toml from disk — must include the new profile.
    app2 = create_app()
    with TestClient(app2) as c2:
        listed = c2.get("/api/profiles").json()
    assert any(p["name"] == "persist-me" for p in listed)


def test_create_duplicate_name_409(client: TestClient) -> None:
    """Duplicate against existing custom profile → 409 profiles.exists."""
    client.post(
        "/api/profiles",
        json={"name": "my-vulkan"},
    )
    r = client.post(
        "/api/profiles",
        json={"name": "my-vulkan"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "profiles.exists"


def test_create_seed_name_409(client: TestClient) -> None:
    """Duplicate against a seed profile name → 409 profiles.exists."""
    seed_name = next(iter(SEED_PROFILES))
    r = client.post(
        "/api/profiles",
        json={"name": seed_name},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "profiles.exists"


def test_create_mtp_false_resolved_flags_equals_flags(client: TestClient) -> None:
    """Custom profile with mtp=False: resolved_flags == flags (stripped)."""
    r = client.post(
        "/api/profiles",
        json={"name": "no-mtp", "flags": "-fa on", "mtp": False},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["resolved_flags"] == body["flags"].strip()
    assert "--spec-type" not in body["resolved_flags"]


def test_create_mtp_true_resolved_flags_no_longer_injects_bundle(client: TestClient) -> None:
    """Custom profile with mtp=True: since ML-5, ``profile.mtp`` is informational
    only — MTP is a per-MODEL capability gated by ``runner.supports.mtp`` and
    injected at launch (in ``_resolve_llama_scalars``), NOT by
    ``resolve_profile_flags`` from the profile. So the profile-CRUD preview's
    ``resolved_flags`` no longer carries the bundle purely from ``profile.mtp``
    (showing it here would mislead — launch won't add it unless the model+runner
    support it)."""
    r = client.post(
        "/api/profiles",
        json={"name": "with-mtp", "flags": "-fa on", "mtp": True},
    )
    assert r.status_code == 201
    body = r.json()
    assert MTP_FLAG_BUNDLE not in body["resolved_flags"]
    # the profile's own flags are still resolved verbatim.
    assert body["resolved_flags"] == body["flags"].strip()
    assert "-fa on" in body["resolved_flags"]


def test_create_invalid_bad_device_class_422(client: TestClient) -> None:
    r = client.post(
        "/api/profiles",
        json={"name": "valid-name", "device_class": "badvalue"},
    )
    assert r.status_code == 422


def test_create_invalid_uppercase_name_422(client: TestClient) -> None:
    r = client.post(
        "/api/profiles",
        json={"name": "MyProfile"},
    )
    assert r.status_code == 422


def test_create_invalid_name_with_spaces_422(client: TestClient) -> None:
    r = client.post(
        "/api/profiles",
        json={"name": "my profile"},
    )
    assert r.status_code == 422


# ── PUT /api/profiles/{name} ───────────────────────────────────────────────────


def test_update_custom_profile_200(tmp_hal0_home: str) -> None:
    """PUT updates the profile and the change persists across reload."""
    app1 = create_app()
    with TestClient(app1) as c1:
        c1.post(
            "/api/profiles",
            json={"name": "my-vulkan", "flags": "-fa on"},
        )
        r = c1.put("/api/profiles/my-vulkan", json={"flags": "-fa off"})
        assert r.status_code == 200
        assert r.json()["flags"] == "-fa off"

    # Verify persisted.
    app2 = create_app()
    with TestClient(app2) as c2:
        listed = c2.get("/api/profiles").json()
    updated = next(p for p in listed if p["name"] == "my-vulkan")
    assert updated["flags"] == "-fa off"


def test_update_only_device_class_preserves_other_fields(client: TestClient) -> None:
    """PUT with ONLY device_class set updates it and preserves the rest."""
    client.post(
        "/api/profiles",
        json={
            "name": "my-vulkan",
            "flags": "-fa on",
            "mtp": True,
            "device_class": "gpu",
        },
    )
    r = client.put("/api/profiles/my-vulkan", json={"device_class": "cpu"})
    assert r.status_code == 200
    body = r.json()
    assert body["device_class"] == "cpu"
    assert body["flags"] == "-fa on"
    assert body["mtp"] is True
    # Persisted view agrees.
    listed = client.get("/api/profiles").json()
    item = next(p for p in listed if p["name"] == "my-vulkan")
    assert item["device_class"] == "cpu"
    assert item["flags"] == "-fa on"
    assert item["mtp"] is True


def test_update_missing_404(client: TestClient) -> None:
    r = client.put("/api/profiles/does-not-exist", json={"flags": "-fa off"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "profiles.not_found"


def test_seed_immutable_put_409(client: TestClient) -> None:
    seed_name = next(iter(SEED_PROFILES))
    r = client.put("/api/profiles/" + seed_name, json={"flags": "-fa off"})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "profiles.seed_immutable"
    assert "clone" in err["message"]


# ── DELETE /api/profiles/{name} ────────────────────────────────────────────────


def test_delete_custom_204(client: TestClient) -> None:
    client.post(
        "/api/profiles",
        json={"name": "my-vulkan"},
    )
    r = client.delete("/api/profiles/my-vulkan")
    assert r.status_code == 204
    listed = client.get("/api/profiles").json()
    assert not any(p["name"] == "my-vulkan" for p in listed)


def test_delete_missing_404(client: TestClient) -> None:
    r = client.delete("/api/profiles/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "profiles.not_found"


def test_seed_immutable_delete_409(client: TestClient) -> None:
    seed_name = next(iter(SEED_PROFILES))
    r = client.delete("/api/profiles/" + seed_name)
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "profiles.seed_immutable"
    assert "clone" in err["message"]


def test_delete_in_use_409(tmp_hal0_home: str) -> None:
    """DELETE a profile that a slot TOML references → 409 profiles.in_use."""
    # Seed slot TOML referencing my-vulkan BEFORE building the app so
    # the slot is on-disk when the route scans list_slots().
    _seed_slot_toml(tmp_hal0_home, "gpu-slot", "my-vulkan")

    app = create_app()
    with TestClient(app) as c:
        # Create the custom profile (seeds are the starting catalog).
        c.post(
            "/api/profiles",
            json={"name": "my-vulkan"},
        )
        r = c.delete("/api/profiles/my-vulkan")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "profiles.in_use"
    assert "gpu-slot" in err["details"]["slots"]


def test_delete_in_use_409_flat_slot_toml(tmp_hal0_home: str) -> None:
    """Flat-shape slot TOML references a profile → DELETE blocked (#1087).

    Regression: profile in-use scanning must load flat top-level slot files
    (no [slot] table), not just the legacy nested shape, so the in-use guard
    still fires and no profiles.in_use_scan_error is logged.
    """
    _seed_flat_slot_toml(tmp_hal0_home, "gpu-slot", "my-vulkan")

    app = create_app()
    with TestClient(app) as c:
        c.post(
            "/api/profiles",
            json={"name": "my-vulkan"},
        )
        listed = c.get("/api/profiles").json()
        r = c.delete("/api/profiles/my-vulkan")
    # In-use guard fires for the flat slot.
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "profiles.in_use"
    assert "gpu-slot" in err["details"]["slots"]
    # used_by is populated from the flat slot in the listing too.
    item = next(p for p in listed if p["name"] == "my-vulkan")
    assert "gpu-slot" in item["used_by"]


def test_delete_in_use_409_despite_corrupt_sibling_toml(tmp_hal0_home: str) -> None:
    """Corrupt slot TOML next to a valid referencing slot: DELETE still 409."""
    _seed_corrupt_slot_toml(tmp_hal0_home, "broken-slot")
    _seed_slot_toml(tmp_hal0_home, "gpu-slot", "my-vulkan", port=8091)

    app = create_app()
    with TestClient(app) as c:
        c.post(
            "/api/profiles",
            json={"name": "my-vulkan"},
        )
        r = c.delete("/api/profiles/my-vulkan")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "profiles.in_use"
    assert "gpu-slot" in err["details"]["slots"]


def test_delete_succeeds_with_only_corrupt_toml_and_warns(
    tmp_hal0_home: str,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only a corrupt slot TOML on disk: DELETE succeeds; scan warning fires.

    structlog output routing is GLOBAL and order-dependent across the test
    suite: standalone, this app renders via PrintLogger to stdout (capsys);
    under the full suite another test may bridge structlog into stdlib
    logging (caplog). Assert across BOTH channels — the contract is that
    the warning fires, not where it lands. (Full-suite flake, Phase C gate.)
    """
    _seed_corrupt_slot_toml(tmp_hal0_home, "broken-slot")

    app = create_app()
    with TestClient(app) as c:
        c.post(
            "/api/profiles",
            json={"name": "my-vulkan"},
        )
        capsys.readouterr()  # drain startup noise
        with caplog.at_level("WARNING"):
            r = c.delete("/api/profiles/my-vulkan")
        captured = capsys.readouterr()
    assert r.status_code == 204
    log_text = captured.out + captured.err + caplog.text
    assert "profiles.in_use_scan_error" in log_text
    assert "broken-slot" in log_text


# ── cloned_from provenance ─────────────────────────────────────────────────────


def test_create_with_cloned_from_201_and_listed(client: TestClient) -> None:
    r = client.post(
        "/api/profiles",
        json={
            "name": "vulkan-custom",
            "cloned_from": "vulkan",
        },
    )
    assert r.status_code == 201
    assert r.json()["cloned_from"] == "vulkan"

    listed = client.get("/api/profiles").json()
    item = next(p for p in listed if p["name"] == "vulkan-custom")
    assert item["cloned_from"] == "vulkan"


def test_seed_profiles_have_null_cloned_from(client: TestClient) -> None:
    listed = client.get("/api/profiles").json()
    assert listed and all(p["cloned_from"] is None for p in listed)


# ── 1c: the inert fit hints are not stamped with a hardware claim ─────────────


def test_create_without_device_class_leaves_it_unset(client: TestClient) -> None:
    """`ProfileBody.device_class` defaulted to `"gpu"`, so every profile created
    without one silently acquired a hardware claim it never asked for — on a
    field that (before 81e1e206) gated real /dev/kfd passthrough. A tuning-only
    profile is device-AGNOSTIC; the default is now None, matching ProfileConfig
    and all 16 seeds."""
    r = client.post("/api/profiles", json={"name": "agnostic", "flags": "-fa on"})
    assert r.status_code == 201, r.text
    assert r.json()["device_class"] is None
    # and it survives the round-trip through the on-disk catalog
    listed = client.get("/api/profiles").json()
    entry = next(p for p in listed if p["name"] == "agnostic")
    assert entry["device_class"] is None


def test_create_with_explicit_device_class_still_honored(client: TestClient) -> None:
    """The hint is inert, not forbidden — an operator may still declare a fit
    hint, and the published chat-long-context artifact carries one."""
    r = client.post(
        "/api/profiles",
        json={"name": "gpu-hinted", "flags": "-fa on", "device_class": "gpu"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["device_class"] == "gpu"


def test_cuda_backend_accepted_on_create_and_update(client: TestClient) -> None:
    """`ProfileConfig.backend` accepts rocm|vulkan|cuda but the route DTOs only
    accepted rocm|vulkan, so a CUDA profile could be imported yet 422'd on every
    subsequent PUT — un-editable through the API that created it."""
    r = client.post(
        "/api/profiles",
        json={"name": "cuda-tune", "flags": "-fa on", "device_class": "gpu", "backend": "cuda"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["backend"] == "cuda"
    r2 = client.put("/api/profiles/cuda-tune", json={"backend": "cuda", "intent": "NVIDIA"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["backend"] == "cuda"
    assert r2.json()["intent"] == "NVIDIA"


# ── #1411: a pre-1.0 custom profile must stay editable ────────────────────────


def _seed_legacy_profiles_toml(home: str, name: str, flags: str) -> Path:
    """Write a custom profile carrying flags that today's §5 screen rejects.

    This is what a 0.9.8 box has on disk: `-dev`/`--threads` were legal on a
    profile before spec-hw-slot-ownership §5. It must be written directly —
    the API (correctly) refuses to create one.
    """
    root = Path(home) / "etc" / "hal0"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "profiles.toml"
    path.write_text(
        f'[profile.{name}]\nflags = "{flags}"\nmtp = false\nintent = "Legacy"\nquant = ""\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def legacy_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    """Client over a box that already has a pre-1.0 custom profile on disk."""
    _seed_legacy_profiles_toml(tmp_hal0_home, "old-custom", "-fa on -dev Vulkan0 --threads 8")
    with TestClient(create_app()) as c:
        yield c


def test_legacy_profile_is_visible(legacy_client: TestClient) -> None:
    listed = legacy_client.get("/api/profiles").json()
    entry = next(p for p in listed if p["name"] == "old-custom")
    assert "-dev Vulkan0" in entry["flags"]


def test_legacy_profile_metadata_edit_is_not_blocked_by_its_stored_flags(
    legacy_client: TestClient,
) -> None:
    """#1411 — THE HEADLINE CASE. The dashboard round-trips the whole profile on
    save, so editing `intent` resubmits the stored `-dev`/`--threads` flags
    verbatim. Screening an UNCHANGED value rejected a write that changes
    nothing, making every pre-existing custom profile permanently un-editable
    after upgrade, with no in-product way to fix it."""
    r = legacy_client.put(
        "/api/profiles/old-custom",
        json={"flags": "-fa on -dev Vulkan0 --threads 8", "intent": "Renamed"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["intent"] == "Renamed"
    assert r.json()["flags"] == "-fa on -dev Vulkan0 --threads 8"


def test_legacy_profile_edit_omitting_flags_also_works(legacy_client: TestClient) -> None:
    """A client that PATCHes only the field it changed was never broken; pinned
    so the delta rule does not regress it."""
    r = legacy_client.put("/api/profiles/old-custom", json={"intent": "Renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["intent"] == "Renamed"


def test_legacy_profile_cleaning_its_flags_is_allowed(legacy_client: TestClient) -> None:
    """The migration path: an operator CAN fix the offending flags."""
    r = legacy_client.put("/api/profiles/old-custom", json={"flags": "-fa on -b 512"})
    assert r.status_code == 200, r.text
    assert r.json()["flags"] == "-fa on -b 512"


def test_legacy_profile_cannot_gain_a_new_hardware_flag(legacy_client: TestClient) -> None:
    """The partition is still fully enforced on any ACTUAL change — the
    exemption is for byte-identical resubmission only, not a general amnesty."""
    r = legacy_client.put(
        "/api/profiles/old-custom",
        json={"flags": "-fa on -dev Vulkan0 --threads 8 -ngl 999"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.hardware_flag_denied"


def test_legacy_profile_cannot_gain_a_managed_flag(legacy_client: TestClient) -> None:
    """Cleaning the legacy hardware flags but smuggling in a managed arg is
    still rejected — and by the MANAGED screen, since no hardware flag remains
    to trip the hardware screen that runs first."""
    r = legacy_client.put("/api/profiles/old-custom", json={"flags": "-fa on --ctx-size 4096"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.managed_arg_denied"


def test_legacy_profile_edit_keeping_hardware_flags_reports_the_hardware_denial(
    legacy_client: TestClient,
) -> None:
    """Ordering guarantee: when a changed flag string still carries a hardware
    flag, the operator is told about the hardware flag (the thing that belongs
    on the slot), not whatever else is wrong."""
    r = legacy_client.put(
        "/api/profiles/old-custom",
        json={"flags": "-fa on -dev Vulkan0 --threads 8 --ctx-size 4096"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.hardware_flag_denied"


def test_create_is_still_fully_screened(legacy_client: TestClient) -> None:
    """A NEW profile gets no exemption — there is no legacy value to preserve."""
    r = legacy_client.post(
        "/api/profiles", json={"name": "brand-new", "flags": "-fa on --threads 8"}
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.hardware_flag_denied"
