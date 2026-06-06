"""Tests for /api/lemonade/config — admin panel surface (PR-13).

Covers (plan §11 PR-13 + plan §2.2 + ADR-0008 §1/§7):

  - GET returns the lemond /internal/config snapshot verbatim + the
    immediate-vs-deferred effect partition + the locked-invariant
    pointers the UI uses for inline validation hints.
  - POST happy path: known keys forwarded to /internal/set; response
    echoes the immediate/deferred split for the keys the request
    touched.
  - POST validation: unknown key rejected; llamacpp_args without
    --threads or with --threads below 2 rejected; flm_args missing
    either trio flag rejected; extra_models_dir diverging from the
    symlink farm root rejected.
  - Auth: the routes are mounted under the parent _admin_auth gate so
    no per-route auth check is needed; this is covered structurally
    by the include_router wiring in hal0.api.__init__ and exercised
    end-to-end by tests/api/test_auth_middleware.py.

Lemonade is stubbed via the same MockTransport pattern as
test_slots_lemonade_state.py — install a fake LemonadeProvider on the
process-wide singleton, yield, restore on teardown.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hal0.providers as providers_mod
from hal0.api import create_app
from hal0.api.routes.lemonade_admin import (
    ADMIN_KEYS,
    DEFERRED_KEYS,
    IMMEDIATE_KEYS,
    LOCKED_EXTRA_MODELS_DIR,
)
from hal0.lemonade.client import LemonadeClient
from hal0.providers.lemonade import LemonadeProvider

# ── stub fixture ──────────────────────────────────────────────────────


@pytest.fixture
def lemonade_state() -> dict[str, Any]:
    """Mutable handle: ``config`` is what /internal/config returns;
    ``last_set`` captures the last /internal/set body for assertions."""
    return {
        "config": {
            "host": "127.0.0.1",
            "port": 13305,
            "ctx_size": 4096,
            "max_loaded_models": 4,
            "extra_models_dir": LOCKED_EXTRA_MODELS_DIR,
            "global_timeout": 900,
            "no_broadcast": True,
            "log_level": "info",
            "llamacpp": {
                "args": "--parallel 1 --threads 8",
                "backend": "rocm",
            },
            "flm": {"args": "--asr 1 --embed 1"},
            "sdcpp": {
                "backend": "rocm",
                "steps": 20,
                "cfg_scale": 7.0,
                "width": 512,
                "height": 512,
            },
            "whispercpp": {"backend": "vulkan"},
        },
        "last_set": None,
    }


@pytest.fixture
def installed_lemonade_stub(
    lemonade_state: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    """Install a Lemonade stub whose /internal/* surface obeys
    lemonade_state mutations.

    /internal/config returns the current snapshot; /internal/set merges
    the body into the snapshot and echoes ``{"applied": [keys]}``. All
    other paths return 404 so we never accidentally rely on an
    un-mocked endpoint.
    """
    state = lemonade_state

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/internal/config" and req.method == "GET":
            return httpx.Response(200, json=state["config"])
        if req.url.path == "/internal/set" and req.method == "POST":
            import json as _json

            body = _json.loads(req.content.decode() or "{}")
            state["last_set"] = body
            # Mirror the merge into the snapshot so a follow-up GET
            # reflects what was just set — matches lemond's real
            # atomic-set semantics.
            state["config"].update(body)
            return httpx.Response(200, json={"applied": list(body)})
        return httpx.Response(404, json={"detail": f"unmocked {req.url.path}"})

    transport = httpx.AsyncClient(
        transport=httpx.MockTransport(h),
        base_url="http://test",
    )
    provider = LemonadeProvider(client=LemonadeClient(http_client=transport))
    original = providers_mod._PROVIDERS["lemonade"]
    providers_mod._PROVIDERS["lemonade"] = provider
    try:
        yield state
    finally:
        providers_mod._PROVIDERS["lemonade"] = original


@pytest.fixture
def isolated_app(tmp_hal0_home: str) -> FastAPI:
    return create_app()


@pytest.fixture
def isolated_client(isolated_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(isolated_app) as c:
        yield c


# ── GET /api/lemonade/config ─────────────────────────────────────────


def test_get_config_returns_lemond_snapshot_verbatim(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """Every key in lemond's /internal/config response appears in our
    GET response unchanged. The route adds metadata under ``_hal0`` but
    must not mutate the upstream payload."""
    r = isolated_client.get("/api/lemonade/config")
    assert r.status_code == 200, r.text
    body = r.json()
    for key, value in installed_lemonade_stub["config"].items():
        assert body[key] == value, f"key {key!r} mutated by hal0 route"


def test_get_config_attaches_immediate_deferred_partition(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """``_hal0.effects`` carries the sorted immediate + deferred lists
    the UI uses to render the "takes effect now" / "next load" labels
    without re-encoding them in the frontend."""
    r = isolated_client.get("/api/lemonade/config")
    body = r.json()
    effects = body["_hal0"]["effects"]
    assert effects["immediate"] == sorted(IMMEDIATE_KEYS)
    assert effects["deferred"] == sorted(DEFERRED_KEYS)


def test_get_config_attaches_locked_invariants(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """``_hal0.locked`` carries the canonical extra_models_dir pointer
    so the UI's inline hint stays in sync with the backend validator.

    v0.3: the locked value is derived from
    ``[models].effective_store()`` so an operator who sets the store via
    POST /api/settings/models/store sees the admin panel hint follow.
    Under tmp_hal0_home the effective value is the HAL0_HOME-rooted
    models_dir; production installs without ``[models].store`` see the
    legacy ``/var/lib/hal0/models`` literal.
    """
    from hal0.api.routes.lemonade_admin import _locked_extra_models_dir

    r = isolated_client.get("/api/lemonade/config")
    body = r.json()
    assert body["_hal0"]["locked"]["extra_models_dir"] == _locked_extra_models_dir()


def test_get_config_synthesizes_flm_args_from_nested(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """lemond stores the trio args NESTED at ``flm.args`` — there is no
    top-level ``flm_args`` in its schema. The GET route synthesizes a
    convenience top-level ``flm_args`` from ``flm.args`` so the dashboard
    (which reads ``data.flm_args``) sees the live value."""
    r = isolated_client.get("/api/lemonade/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["flm_args"] == "--asr 1 --embed 1"
    # The nested source is still present, verbatim.
    assert body["flm"] == {"args": "--asr 1 --embed 1"}


def test_immediate_and_deferred_partitions_are_disjoint() -> None:
    """No key may be in both halves — a key that's "immediate" can't
    also be "deferred"; if lemond ever changes this, the constants here
    need to flip too."""
    assert not (IMMEDIATE_KEYS & DEFERRED_KEYS)
    assert ADMIN_KEYS == IMMEDIATE_KEYS | DEFERRED_KEYS


# ── POST /api/lemonade/config — happy path ───────────────────────────


def test_post_config_forwards_known_keys_to_internal_set(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """A patch with valid keys lands on /internal/set verbatim — no
    drop, no rename, no enrichment."""
    patch = {"log_level": "debug", "max_loaded_models": 6}
    r = isolated_client.post("/api/lemonade/config", json=patch)
    assert r.status_code == 200, r.text
    assert installed_lemonade_stub["last_set"] == patch


def test_post_config_echoes_immediate_deferred_split(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """The response's ``effects`` block partitions JUST the touched
    keys (not the global lists) so the UI can render a precise toast."""
    patch = {
        "log_level": "debug",  # immediate
        "max_loaded_models": 6,  # deferred
        "ctx_size": 8192,  # deferred
    }
    r = isolated_client.post("/api/lemonade/config", json=patch)
    body = r.json()
    assert body["effects"]["immediate"] == ["log_level"]
    assert body["effects"]["deferred"] == ["ctx_size", "max_loaded_models"]


def test_post_config_immediate_only_patch(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """A patch touching only immediate keys reports no deferred work —
    the UI uses this to suppress the "next load" hint."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"log_level": "warn", "no_broadcast": False},
    )
    body = r.json()
    assert body["effects"]["deferred"] == []
    assert set(body["effects"]["immediate"]) == {"log_level", "no_broadcast"}


def test_post_config_deferred_only_patch(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """Symmetric to the immediate-only case — touching only deferred
    keys reports nothing immediate so the UI shows the "next load"
    notice."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"ctx_size": 2048},
    )
    body = r.json()
    assert body["effects"]["immediate"] == []
    assert body["effects"]["deferred"] == ["ctx_size"]


def test_post_config_applied_field_carries_lemond_echo(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """``applied`` echoes lemond's response so a future protocol bump
    (e.g. lemond starting to return rejected keys here) surfaces to
    the UI without a hal0 release."""
    r = isolated_client.post("/api/lemonade/config", json={"log_level": "info"})
    body = r.json()
    assert body["applied"] == {"applied": ["log_level"]}


# ── POST /api/lemonade/config — validation ───────────────────────────


def test_post_config_rejects_unknown_key(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """A key outside IMMEDIATE | DEFERRED is refused 400 — we don't
    forward random keys to lemond because the surface might accept
    them silently with non-obvious effects (ADR-0008 §7)."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"rocm_channel": "nightly"},  # not admin-editable
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "lemonade.config_invalid"
    assert "rocm_channel" in body["error"]["details"]
    # lemond stub should not have been called.
    assert installed_lemonade_stub["last_set"] is None


def test_post_config_rejects_llamacpp_args_without_threads(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """Omitting --threads trips the LXC oversubscribe deadlock — refuse
    early per hal0_lemonade_threads_deadlock."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"llamacpp_args": "--parallel 1"},
    )
    assert r.status_code == 400, r.text
    details = r.json()["error"]["details"]
    assert "llamacpp_args" in details
    assert "--threads" in details["llamacpp_args"]
    assert installed_lemonade_stub["last_set"] is None


def test_post_config_rejects_llamacpp_args_threads_zero(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """--threads 0 is functionally the same as omitting the flag (libc
    interprets 0 as ``nproc``) — refuse with a clear "below minimum"
    message so the operator sees the floor, not a generic shape error."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"llamacpp_args": "--parallel 1 --threads 0"},
    )
    assert r.status_code == 400, r.text
    details = r.json()["error"]["details"]
    assert "below the required minimum" in details["llamacpp_args"]


def test_post_config_rejects_llamacpp_args_threads_one(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """--threads 1 is below the documented floor — refuse with the
    same message as threads=0 because the deadlock risk is identical."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"llamacpp_args": "--parallel 1 --threads 1"},
    )
    assert r.status_code == 400, r.text


def test_post_config_accepts_llamacpp_args_threads_equals_form(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """``--threads=8`` is the same as ``--threads 8`` — the validator
    tolerates the equals form so operators copying llama.cpp invocations
    from upstream docs don't trip the gate."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"llamacpp_args": "--parallel 1 --threads=8"},
    )
    assert r.status_code == 200, r.text


def test_post_config_accepts_flm_args_chat_plus_embed_only(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """Spec 2 relaxation: a chat+embed stack (no ASR) is a valid config.
    The dashboard NPU section sets flags explicitly and keeps the NPU
    transcription slot disabled to match, so this no longer 404s."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"flm_args": "--embed 1"},
    )
    assert r.status_code == 200, r.text


def test_post_config_accepts_flm_args_chat_plus_asr_only(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """Symmetric: a chat+ASR stack (no embed) is valid under Spec 2."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"flm_args": "--asr 1"},
    )
    assert r.status_code == 200, r.text


def test_post_config_accepts_flm_args_explicit_disable(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """``--asr 0`` (explicit disable) is now a valid, accepted value — the
    NPU section sends explicit 0/1 and disables the matching slot."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"flm_args": "--asr 0 --embed 1"},
    )
    assert r.status_code == 200, r.text


def test_post_config_translates_flm_args_to_nested_wire_shape(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """Callers keep the convenient top-level ``flm_args`` contract, but the
    payload reaching lemond's ``/internal/set`` is the NESTED ``flm.args``
    shape — never a top-level ``flm_args`` (which lemond rejects 400 as an
    unknown key). The response contract is unchanged: ``flm_args`` still
    appears in ``effects.deferred``."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"flm_args": "--asr 0 --embed 1"},
    )
    assert r.status_code == 200, r.text
    # Wire payload: nested, no top-level flm_args.
    sent = installed_lemonade_stub["last_set"]
    assert sent == {"flm": {"args": "--asr 0 --embed 1"}}, sent
    assert "flm_args" not in sent
    # Response contract unchanged — flm_args still classified deferred.
    assert "flm_args" in r.json()["effects"]["deferred"]


def test_post_config_preserves_other_keys_when_translating_flm_args(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """A mixed patch carrying ``flm_args`` plus other keys forwards the
    other keys untouched and translates only ``flm_args`` → ``flm.args``."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"flm_args": "--asr 1 --embed 1", "log_level": "debug"},
    )
    assert r.status_code == 200, r.text
    sent = installed_lemonade_stub["last_set"]
    assert sent == {"flm": {"args": "--asr 1 --embed 1"}, "log_level": "debug"}, sent
    assert "flm_args" not in sent


def test_post_config_rejects_flm_args_malformed_value(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """Only 0/1 are valid for --asr/--embed; a non-binary value is
    malformed and must be rejected."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"flm_args": "--asr 2 --embed 1"},
    )
    assert r.status_code == 400, r.text


def test_post_config_accepts_canonical_flm_args(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """The recommended ``--asr 1 --embed 1`` value passes — sanity
    check the positive path so the validator can't drift into rejecting
    its own canonical form."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={"flm_args": "--asr 1 --embed 1"},
    )
    assert r.status_code == 200, r.text


def test_post_config_rejects_extra_models_dir_divergence(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """Flipping extra_models_dir off the hal0 store root would silently
    desync the dashboard's catalog from what lemond can load. Refuse with
    the canonical (currently-effective) path in the error message. To
    change the path, use POST /api/settings/models/store."""
    from hal0.api.routes.lemonade_admin import _locked_extra_models_dir

    r = isolated_client.post(
        "/api/lemonade/config",
        json={"extra_models_dir": "/some/other/absolute/path"},
    )
    assert r.status_code == 400, r.text
    details = r.json()["error"]["details"]
    assert _locked_extra_models_dir() in details["extra_models_dir"]


def test_post_config_accepts_canonical_extra_models_dir(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """Setting extra_models_dir to its locked value is a no-op pass —
    operators rotating the config should not hit a 400 just because
    they re-sent the canonical value."""
    from hal0.api.routes.lemonade_admin import _locked_extra_models_dir

    r = isolated_client.post(
        "/api/lemonade/config",
        json={"extra_models_dir": _locked_extra_models_dir()},
    )
    assert r.status_code == 200, r.text


def test_post_config_aggregates_multiple_validation_errors(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """If three keys fail at once the response carries all three reasons
    in ``details`` — the UI's per-field inline error rendering depends
    on this (single 4xx per submit, not three round-trips)."""
    r = isolated_client.post(
        "/api/lemonade/config",
        json={
            "llamacpp_args": "--parallel 1",
            # `--asr 1` is now VALID (Spec 2 relaxation); use a malformed
            # non-binary value so flm_args still fails and the aggregation
            # of multiple errors is exercised.
            "flm_args": "--asr 2",
            "rocm_channel": "nightly",
        },
    )
    assert r.status_code == 400, r.text
    details = r.json()["error"]["details"]
    assert set(details.keys()) == {"llamacpp_args", "flm_args", "rocm_channel"}


# ── POST /api/lemonade/config — empty + malformed bodies ─────────────


def test_post_config_rejects_empty_body(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """An empty patch is a no-op; we surface a typed 400 rather than
    proxying ``{}`` to lemond (whose behaviour for empty bodies is
    unspecified)."""
    r = isolated_client.post("/api/lemonade/config", json={})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "lemonade.config_empty"


def test_post_config_rejects_non_object_body(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """A JSON array (or scalar) at the top level is rejected — lemond's
    /internal/set expects an object."""
    r = isolated_client.post("/api/lemonade/config", json=["log_level"])
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "request.not_an_object"


def test_post_config_rejects_non_json_body(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """Non-JSON bodies surface as ``request.invalid_json`` rather than
    crashing with a parser stack trace."""
    r = isolated_client.post(
        "/api/lemonade/config",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400, r.text


# ── Round-trip ───────────────────────────────────────────────────────


def test_post_then_get_reflects_change(
    isolated_client: TestClient,
    installed_lemonade_stub: dict[str, Any],
) -> None:
    """After POST applies the patch (against the merging stub), a
    follow-up GET shows the new value — confirms the stub mirrors
    lemond's atomic-set semantics + the route doesn't cache GET."""
    r = isolated_client.post("/api/lemonade/config", json={"log_level": "warn"})
    assert r.status_code == 200, r.text
    r2 = isolated_client.get("/api/lemonade/config")
    assert r2.status_code == 200, r2.text
    assert r2.json()["log_level"] == "warn"
