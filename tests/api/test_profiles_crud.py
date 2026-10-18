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
from hal0.config.schema import SEED_PROFILES

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

    The only way to stage state the write-path screens would refuse (a stale
    hardware flag, an out-of-vocabulary runner). Mirrors the
    ``[profile.<name>]`` table shape the loader reads.
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
            "ctx_size": 32768,
            "enable_thinking": False,
            "vision": "off",
            "speculation": "off",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "my-vulkan"
    assert body["flags"] == "-fa on"
    assert body["ctx_size"] == 32768
    assert body["enable_thinking"] is False
    assert body["vision"] == "off"
    assert body["speculation"] == "off"
    assert "resolved_flags" in body
    assert body["seed"] is False
    assert not {"device_class", "backend", "quant"} & set(body)
    listed = client.get("/api/profiles").json()
    assert any(p["name"] == "my-vulkan" for p in listed)


def test_create_defaults_are_auto_and_unset(client: TestClient) -> None:
    body = client.post("/api/profiles", json={"name": "bare"}).json()
    assert body["ctx_size"] is None
    assert body["enable_thinking"] is None
    assert body["vision"] == "auto"
    assert body["speculation"] == "auto"
    assert body["runner"] is None


# ── spec-hw-slot-ownership §5: profile flags reject slot-hardware flags ────────


def test_create_profile_rejects_slot_hardware_flag(client: TestClient) -> None:
    """A profile is a device-agnostic tune — its flags must not carry a
    grid-owned hardware flag (--threads/-ngl/--device). The create hard-rejects
    with the "belongs on the slot" envelope and persists nothing."""
    r = client.post(
        "/api/profiles",
        json={
            "name": "hw-profile",
            "flags": "-fa on --threads 8",
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


# ── #1730: malformed shell quoting must be rejected at save time ───────────


def test_create_profile_rejects_unmatched_quoting(client: TestClient) -> None:
    """#1639 added a launch-time guard for a divergent profile with unmatched
    quotes (slot.profile_flags_malformed) so it no longer 500s the
    launch/preview path — but a profile could still be SAVED with bad
    quoting in the first place, since screen_profile_flags silently returned
    on a shlex.split failure instead of rejecting it. The create hard-rejects
    with a 422 and persists nothing."""
    r = client.post(
        "/api/profiles",
        json={"name": "bad-quotes", "flags": '-fa on "unterminated'},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "profiles.flags_malformed"
    listed = client.get("/api/profiles").json()
    assert not any(p["name"] == "bad-quotes" for p in listed)


def test_update_profile_rejects_unmatched_quoting(client: TestClient) -> None:
    """PUT screens the same shlex.split validation — a clean profile edited to
    carry unmatched quotes is rejected and the prior flags survive."""
    created = client.post(
        "/api/profiles",
        json={"name": "edit-quotes", "flags": "-fa on"},
    )
    assert created.status_code == 201, created.text
    r = client.put("/api/profiles/edit-quotes", json={"flags": "-b 2048 'unterminated"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "profiles.flags_malformed"
    listed = client.get("/api/profiles").json()
    edited = next(p for p in listed if p["name"] == "edit-quotes")
    assert edited["flags"] == "-fa on"


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


# ── ADR-0029: there is no grandfathering of stored hardware flags ──────────────
#
# The #1411 exemption (a profile authored before the screen shipped stayed
# editable) is gone: the profile-centric converter rewrites stored profiles
# once, so a PUT that SENDS flags is always screened.


def test_update_without_flags_key_never_screens(tmp_hal0_home: str) -> None:
    """A patch that doesn't name `flags` at all must not be screened against the
    stored ones: a profile holding a stale hardware flag can still be edited
    in its other fields."""
    with TestClient(create_app()) as client:
        _seed_profiles_toml(tmp_hal0_home, "stale2", "-fa on -dev ROCm0")
        r = client.put("/api/profiles/stale2", json={"intent": "still editable"})
        assert r.status_code == 200, r.text
        assert r.json()["intent"] == "still editable"
        assert r.json()["flags"] == "-fa on -dev ROCm0"


def test_update_resending_a_stored_hardware_flag_is_rejected(tmp_hal0_home: str) -> None:
    """Saving a stored hardware flag back verbatim is a hard 400 now."""
    with TestClient(create_app()) as client:
        _seed_profiles_toml(tmp_hal0_home, "stale", "-fa on -dev ROCm0 --threads 8")
        stored = client.get("/api/profiles/stale").json()["flags"]
        r = client.put("/api/profiles/stale", json={"flags": stored, "intent": "retuned"})
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "slot.hardware_flag_denied"
        # Nothing was written.
        assert client.get("/api/profiles/stale").json()["intent"] == ""


def test_update_can_clean_up_the_stored_hardware_flag(tmp_hal0_home: str) -> None:
    """The way off a stale flag is to replace the flag text."""
    with TestClient(create_app()) as client:
        _seed_profiles_toml(tmp_hal0_home, "stale4", "-fa on -dev ROCm0")
        cleaned = client.put("/api/profiles/stale4", json={"flags": "-fa on"})
        assert cleaned.status_code == 200, cleaned.text
        assert cleaned.json()["flags"] == "-fa on"
        r = client.put("/api/profiles/stale4", json={"flags": "-fa on -dev ROCm0"})
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "slot.hardware_flag_denied"


def test_create_screens_hardware_flags(client: TestClient) -> None:
    """POST rejects hardware flags outright."""
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


def test_create_resolved_flags_equals_flags(client: TestClient) -> None:
    """resolved_flags is the profile's own flags: the MTP draft bundle is
    composed at launch from the model + runner, never shown here."""
    for mode in ("auto", "off", "ngram"):
        r = client.post(
            "/api/profiles",
            json={"name": f"spec-{mode}", "flags": "-fa on", "speculation": mode},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["resolved_flags"] == body["flags"] == "-fa on"
        assert "--spec-type" not in body["resolved_flags"]


@pytest.mark.parametrize("field", ["vision", "speculation"])
@pytest.mark.parametrize("value", [True, False, "on", "yes", ""])
def test_create_vision_and_speculation_accept_only_auto_or_off(
    client: TestClient, field: str, value: object
) -> None:
    """a bool or free text is a 422, not coerced."""
    r = client.post("/api/profiles", json={"name": "bad-switch", field: value})
    assert r.status_code == 422, r.text


def test_create_rejects_the_retired_fields_silently_ignored_never(client: TestClient) -> None:
    """device_class / backend / quant are gone from the body. FastAPI ignores
    unknown JSON keys, so they must simply not appear on the stored profile."""
    r = client.post(
        "/api/profiles",
        json={"name": "no-hints", "flags": "-fa on", "device_class": "gpu", "quant": "Q4"},
    )
    assert r.status_code == 201, r.text
    assert not {"device_class", "backend", "quant"} & set(r.json())


@pytest.mark.parametrize("ctx", [0, 64, 127, 16_777_217, -1])
def test_create_ctx_size_out_of_range_400(client: TestClient, ctx: int) -> None:
    r = client.post("/api/profiles", json={"name": "bad-ctx", "ctx_size": ctx})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "profiles.ctx_size_out_of_range"
    assert not any(p["name"] == "bad-ctx" for p in client.get("/api/profiles").json())


@pytest.mark.parametrize("ctx", [128, 4096, 16_777_216])
def test_create_ctx_size_bounds_are_inclusive(client: TestClient, ctx: int) -> None:
    r = client.post("/api/profiles", json={"name": f"ctx-{ctx}", "ctx_size": ctx})
    assert r.status_code == 201, r.text
    assert r.json()["ctx_size"] == ctx


@pytest.mark.parametrize(
    ("flags", "code"),
    [
        ("--jinja -fa on", "profiles.model_fact_flag_denied"),
        ("-fa on --no-mmproj", "profiles.model_fact_flag_denied"),
        ("--mmproj /x/m.gguf", "profiles.model_fact_flag_denied"),
        ("--mmproj-auto", "profiles.model_fact_flag_denied"),
        ("--chat-template chatml", "profiles.model_fact_flag_denied"),
        ("--chat-template-file t.jinja", "profiles.model_fact_flag_denied"),
        ("-fa on --spec-draft-device ROCm0", "profiles.model_fact_flag_denied"),
        ("--reasoning-format none", "slot.reasoning_format_none_denied"),
        ("--ctx-size 4096", "slot.managed_arg_denied"),
        ("-c 4096", "slot.managed_arg_denied"),
        ("-t 8", "slot.hardware_flag_denied"),
        ("--n-gpu-layers 10", "slot.hardware_flag_denied"),
    ],
)
def test_create_and_update_screen_every_flag_a_profile_may_not_carry(
    client: TestClient, flags: str, code: str
) -> None:
    r = client.post("/api/profiles", json={"name": "screened", "flags": flags})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == code
    assert not any(p["name"] == "screened" for p in client.get("/api/profiles").json())

    assert (
        client.post("/api/profiles", json={"name": "to-edit", "flags": "-fa on"}).status_code == 201
    )
    r = client.put("/api/profiles/to-edit", json={"flags": flags})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == code
    assert client.get("/api/profiles/to-edit").json()["flags"] == "-fa on"


def test_reasoning_format_other_than_none_is_allowed(client: TestClient) -> None:
    r = client.post(
        "/api/profiles", json={"name": "rf", "flags": "--reasoning-format deepseek -fa on"}
    )
    assert r.status_code == 201, r.text


def test_create_invalid_bad_vision_422(client: TestClient) -> None:
    r = client.post(
        "/api/profiles",
        json={"name": "valid-name", "vision": "badvalue"},
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


def test_update_sparse_preserves_other_fields(client: TestClient) -> None:
    """PUT with ONLY one field set updates it and preserves the rest."""
    client.post(
        "/api/profiles",
        json={
            "name": "my-vulkan",
            "flags": "-fa on",
            "ctx_size": 16384,
            "enable_thinking": True,
            "vision": "off",
            "speculation": "off",
        },
    )
    r = client.put("/api/profiles/my-vulkan", json={"vision": "auto"})
    assert r.status_code == 200
    body = r.json()
    assert body["vision"] == "auto"
    assert body["flags"] == "-fa on"
    assert body["ctx_size"] == 16384
    assert body["enable_thinking"] is True
    assert body["speculation"] == "off"
    # Persisted view agrees.
    item = next(p for p in client.get("/api/profiles").json() if p["name"] == "my-vulkan")
    assert (item["vision"], item["ctx_size"], item["speculation"]) == ("auto", 16384, "off")


def test_update_ctx_size_null_clears_but_omitting_leaves_alone(client: TestClient) -> None:
    client.post(
        "/api/profiles",
        json={"name": "ctx-edit", "ctx_size": 16384, "enable_thinking": True},
    )
    left = client.put("/api/profiles/ctx-edit", json={"intent": "x"})
    assert left.status_code == 200, left.text
    assert left.json()["ctx_size"] == 16384
    assert left.json()["enable_thinking"] is True

    changed = client.put("/api/profiles/ctx-edit", json={"ctx_size": 8192})
    assert changed.json()["ctx_size"] == 8192

    cleared = client.put("/api/profiles/ctx-edit", json={"ctx_size": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["ctx_size"] is None
    assert cleared.json()["enable_thinking"] is True

    both = client.put("/api/profiles/ctx-edit", json={"enable_thinking": None})
    assert both.json()["enable_thinking"] is None
    assert client.get("/api/profiles/ctx-edit").json()["enable_thinking"] is None


@pytest.mark.parametrize("ctx", [0, 127, 16_777_217])
def test_update_ctx_size_out_of_range_400(client: TestClient, ctx: int) -> None:
    client.post("/api/profiles", json={"name": "ctx-bad", "ctx_size": 4096})
    r = client.put("/api/profiles/ctx-bad", json={"ctx_size": ctx})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "profiles.ctx_size_out_of_range"
    assert client.get("/api/profiles/ctx-bad").json()["ctx_size"] == 4096


@pytest.mark.parametrize("field", ["vision", "speculation"])
def test_update_vision_and_speculation_reject_non_switch_values(
    client: TestClient, field: str
) -> None:
    client.post("/api/profiles", json={"name": "sw-edit"})
    for value in (True, "on", ""):
        r = client.put("/api/profiles/sw-edit", json={field: value})
        assert r.status_code == 422, (value, r.text)


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


def test_quarantined_profile_is_listed_and_deletable(
    client: TestClient, tmp_hal0_home: str
) -> None:
    """#2653: an entry that failed validation shows as a read-only row and DELETE removes it."""
    from hal0.config import paths

    path = paths.profiles_toml()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[profile.broken]\nnot_a_field = "surprise"\n', encoding="utf-8")

    row = next(p for p in client.get("/api/profiles").json() if p["name"] == "broken")
    assert row["quarantined"] is True
    assert row["error"]

    assert client.delete("/api/profiles/broken").status_code == 204
    assert not any(p["name"] == "broken" for p in client.get("/api/profiles").json())


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
    app = create_app()
    with TestClient(app) as c:
        # Create the custom profile (seeds are the starting catalog), then
        # point a slot at it. The slot is written AFTER boot: the boot-time
        # conversion re-points a slot naming a profile that does not exist yet.
        c.post(
            "/api/profiles",
            json={"name": "my-vulkan"},
        )
        _seed_slot_toml(tmp_hal0_home, "gpu-slot", "my-vulkan")
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
    app = create_app()
    with TestClient(app) as c:
        c.post(
            "/api/profiles",
            json={"name": "my-vulkan"},
        )
        _seed_flat_slot_toml(tmp_hal0_home, "gpu-slot", "my-vulkan")
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
    app = create_app()
    with TestClient(app) as c:
        c.post(
            "/api/profiles",
            json={"name": "my-vulkan"},
        )
        _seed_corrupt_slot_toml(tmp_hal0_home, "broken-slot")
        _seed_slot_toml(tmp_hal0_home, "gpu-slot", "my-vulkan", port=8091)
        r = c.delete("/api/profiles/my-vulkan")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "profiles.in_use"
    assert "gpu-slot" in err["details"]["slots"]


def test_delete_fails_closed_with_only_corrupt_toml_and_warns(
    tmp_hal0_home: str,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only a corrupt slot TOML on disk: DELETE refuses (#2574); listing works.

    The unreadable file may well name the profile, so the delete fails closed
    with 409 ``profiles.slot_unreadable`` naming the slot, the scan warning
    still fires and ``GET /api/profiles`` keeps working.

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
            listed = c.get("/api/profiles")
        captured = capsys.readouterr()
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "profiles.slot_unreadable"
    assert err["details"]["slots"] == ["broken-slot"]
    assert "broken-slot" in err["message"]
    assert listed.status_code == 200
    assert "my-vulkan" in {p["name"] for p in listed.json()}
    log_text = captured.out + captured.err + caplog.text
    assert "profiles.in_use_scan_error" in log_text
    assert "broken-slot" in log_text


def test_delete_in_use_409_for_unconverted_slot(tmp_hal0_home: str) -> None:
    """A slot refused with ``slot.unconverted`` still counts as using its profile (#2574)."""
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/profiles", json={"name": "my-vulkan"})
        root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
        root.mkdir(parents=True, exist_ok=True)
        (root / "stale-slot.toml").write_text(
            '[slot]\nname = "stale-slot"\nport = 8093\nprofile = "my-vulkan"\nctx_size = 4096\n',
            encoding="utf-8",
        )
        listed = c.get("/api/profiles").json()
        r = c.delete("/api/profiles/my-vulkan")
    item = next(p for p in listed if p["name"] == "my-vulkan")
    assert item["used_by"] == ["stale-slot"]
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "profiles.in_use"
    assert err["details"]["slots"] == ["stale-slot"]


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


# ── a stored profile still carrying stale hardware flags ──────────────────────


def _seed_stale_profiles_toml(home: str, name: str, flags: str) -> Path:
    """Write a custom profile carrying flags today's screen rejects.

    The API (correctly) refuses to create one; the profile-centric converter
    rewrites these once at boot, but a hand-edited file can still hold one.
    """
    root = Path(home) / "etc" / "hal0"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "profiles.toml"
    path.write_text(
        f'[profile.{name}]\nflags = "{flags}"\nspeculation = "auto"\nintent = "Stale"\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def stale_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    """Client over a box that already has a stale custom profile on disk."""
    with TestClient(create_app()) as c:
        # Seeded AFTER boot: the boot-time conversion rewrites stale flags once.
        _seed_stale_profiles_toml(tmp_hal0_home, "old-custom", "-fa on -dev Vulkan0 --threads 8")
        yield c


def test_stale_profile_is_visible(stale_client: TestClient) -> None:
    listed = stale_client.get("/api/profiles").json()
    entry = next(p for p in listed if p["name"] == "old-custom")
    assert "-dev Vulkan0" in entry["flags"]


def test_stale_profile_metadata_edit_without_flags_works(stale_client: TestClient) -> None:
    """A client that sends only the field it changed is never blocked by the
    profile's stored flags."""
    r = stale_client.put("/api/profiles/old-custom", json={"intent": "Renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["intent"] == "Renamed"


def test_stale_profile_resending_its_flags_is_rejected(stale_client: TestClient) -> None:
    """No grandfathering: resending the stored text is screened like new text."""
    r = stale_client.put(
        "/api/profiles/old-custom",
        json={"flags": "-fa on -dev Vulkan0 --threads 8", "intent": "Renamed"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.hardware_flag_denied"


def test_stale_profile_cleaning_its_flags_is_allowed(stale_client: TestClient) -> None:
    """The migration path: an operator CAN fix the offending flags."""
    r = stale_client.put("/api/profiles/old-custom", json={"flags": "-fa on -b 512"})
    assert r.status_code == 200, r.text
    assert r.json()["flags"] == "-fa on -b 512"


def test_stale_profile_cannot_gain_a_managed_flag(stale_client: TestClient) -> None:
    r = stale_client.put("/api/profiles/old-custom", json={"flags": "-fa on --ctx-size 4096"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.managed_arg_denied"


def test_hardware_denial_is_reported_before_the_managed_denial(stale_client: TestClient) -> None:
    """When flags introduce BOTH a hardware flag and a managed flag, the operator
    is told about the hardware flag (the thing that belongs on the slot) first."""
    r = stale_client.put(
        "/api/profiles/old-custom",
        json={"flags": "-fa on -ngl 999 --ctx-size 4096"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.hardware_flag_denied"


def test_create_is_fully_screened(stale_client: TestClient) -> None:
    r = stale_client.post(
        "/api/profiles", json={"name": "brand-new", "flags": "-fa on --threads 8"}
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.hardware_flag_denied"


# ── D4: profile.runner (optional RUNNER_IMAGES key) ────────────────────────────


def test_create_profile_with_runner_roundtrip(client: TestClient) -> None:
    r = client.post("/api/profiles", json={"name": "pf-api", "flags": "", "runner": "cuda"})
    assert r.status_code == 201
    assert r.json()["runner"] == "cuda"


def test_create_profile_unknown_runner_422(client: TestClient) -> None:
    r = client.post("/api/profiles", json={"name": "bad-api", "flags": "", "runner": "nope"})
    assert r.status_code == 422


# ── #2186 clear sentinel + #2183 out-of-vocab grandfathering ──────────────────


@pytest.fixture
def ghost_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    """Client over a box whose stored profile pins a runtime this build's
    RUNNER_IMAGES no longer carries (removed/renamed key, or a downgrade).

    Seeded AFTER the app has booted: the runner-system conversion that runs at
    boot drops an unknown profile ``runner`` once, so a ghost key only survives
    when it arrives later (a hand edit, a downgrade-then-upgrade).
    """
    with TestClient(create_app()) as c:
        _seed_profiles_toml(
            tmp_hal0_home, "ghosted", "-fa on", runner="ghost-runner", intent="before"
        )
        yield c


def _pinned(client: TestClient, name: str = "pf-runtime") -> None:
    r = client.post("/api/profiles", json={"name": name, "flags": "", "runner": "cuda"})
    assert r.status_code == 201, r.text


def test_put_blank_runner_clears_the_stored_runtime(client: TestClient) -> None:
    """#2186 — the drawer's '— Auto —' option has to actually reach Auto."""
    _pinned(client)
    r = client.put("/api/profiles/pf-runtime", json={"flags": "", "runner": ""})
    assert r.status_code == 200, r.text
    assert r.json()["runner"] is None
    listed = next(p for p in client.get("/api/profiles").json() if p["name"] == "pf-runtime")
    assert listed["runner"] is None


def test_put_omitting_runner_keeps_the_stored_runtime(client: TestClient) -> None:
    _pinned(client)
    r = client.put("/api/profiles/pf-runtime", json={"intent": "Renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["runner"] == "cuda"


def test_put_null_runner_keeps_the_stored_runtime(client: TestClient) -> None:
    """Explicit null stays leave-unchanged (the field's documented meaning) —
    clearing is '' and nothing else."""
    _pinned(client)
    r = client.put("/api/profiles/pf-runtime", json={"intent": "Renamed", "runner": None})
    assert r.status_code == 200, r.text
    assert r.json()["runner"] == "cuda"


def test_ghost_profile_metadata_edit_is_not_blocked_by_its_stored_runner(
    ghost_client: TestClient,
) -> None:
    """#2183 — the drawer round-trips `runner` on every save, so screening an
    UNCHANGED value 422'd every field of a profile whose runtime left the
    registry. Mirrors the #1411 flags grandfathering."""
    r = ghost_client.put(
        "/api/profiles/ghosted", json={"flags": "-fa on", "runner": "ghost-runner", "intent": "Now"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["intent"] == "Now"
    assert r.json()["runner"] == "ghost-runner"


def test_ghost_profile_cannot_switch_to_another_unknown_runner(ghost_client: TestClient) -> None:
    """The exemption is byte-identical resubmission only — any ACTUAL change is
    still fully screened."""
    r = ghost_client.put("/api/profiles/ghosted", json={"runner": "other-ghost"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "profiles.unknown_runner"


def test_ghost_profile_edit_omitting_runner_also_works(ghost_client: TestClient) -> None:
    r = ghost_client.put("/api/profiles/ghosted", json={"intent": "Now"})
    assert r.status_code == 200, r.text
    assert r.json()["runner"] == "ghost-runner"


def test_ghost_profile_can_be_cleared_to_auto(ghost_client: TestClient) -> None:
    """The migration path off a stale key: clearing bypasses the screen."""
    r = ghost_client.put("/api/profiles/ghosted", json={"runner": ""})
    assert r.status_code == 200, r.text
    assert r.json()["runner"] is None


# ── speculation replaces mtp (ADR-0031 §7) ────────────────────────────────────


def test_profile_create_accepts_speculation_ngram(client: TestClient) -> None:
    r = client.post(
        "/api/profiles", json={"name": "spec-ngram", "flags": "-fa on", "speculation": "ngram"}
    )
    assert r.status_code == 201
    assert r.json()["speculation"] == "ngram"


@pytest.mark.parametrize("method", ["post", "put"])
def test_profile_body_mtp_is_refused_422_naming_speculation(
    client: TestClient, method: str
) -> None:
    client.post("/api/profiles", json={"name": "has-spec", "flags": "-fa on"})
    if method == "post":
        r = client.post("/api/profiles", json={"name": "old-shape", "mtp": "off"})
    else:
        r = client.put("/api/profiles/has-spec", json={"mtp": "off"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "profiles.field_renamed"
    assert err["message"] == "profile 'mtp' is now 'speculation' (auto | off | ngram)"


def test_profile_detail_reports_speculation(client: TestClient) -> None:
    client.post("/api/profiles", json={"name": "spec-off", "speculation": "off"})
    body = client.get("/api/profiles/spec-off").json()
    assert body["speculation"] == "off"
    assert "mtp" not in body
    assert client.get("/api/profiles/agent").json()["speculation"] == "auto"


def test_profile_speculation_rejects_unknown_value(client: TestClient) -> None:
    r = client.post("/api/profiles", json={"name": "bad-spec", "speculation": "turbo"})
    assert r.status_code == 422
