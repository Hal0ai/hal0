"""Security regression tests for ``/api/install/*`` (FINDINGS §29 + §30).

Two contracts:

  §29 — every mutating installer endpoint requires a writer-scoped
        credential when ``HAL0_AUTH_ENABLED=1``. When auth is off
        (fresh-install default), the gate is a pass-through so the
        FirstRun wizard still works.

  §30 — every installer endpoint that takes a ``slot`` parameter
        validates it against the slot-name policy BEFORE touching
        disk. Path-traversal payloads, absolute paths, and other
        malformed values are rejected with 400 ``slot.invalid_name``.

These are critical pre-launch findings from the v1.0 security audit
(see ``tests/harness/FINDINGS.md``). The matrix below covers the auth
gate at three credential levels (none / read-only / writer) plus the
auth-off pass-through, parametrised across every mutating install
endpoint. The slot-name matrix runs every malformed payload that came
up in the audit + one valid value to prove the gate doesn't reject
legitimate names.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.auth.tokens import TokenStore

# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def auth_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """TestClient with HAL0_AUTH_ENABLED=1 and an isolated token store."""
    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    monkeypatch.setenv("HAL0_OVERRIDE_DIR", "hal0_home")
    app: FastAPI = create_app()
    with TestClient(app) as c:
        c.app.state.token_store = TokenStore(tmp_path / "tokens.toml")
        yield c


@pytest.fixture
def open_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """TestClient with HAL0_AUTH_ENABLED unset — the fresh-install posture.

    The FirstRun wizard MUST work in this mode: no password has been set
    yet, so the require_writer gate has to short-circuit to anonymous.
    """
    monkeypatch.delenv("HAL0_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    monkeypatch.setenv("HAL0_OVERRIDE_DIR", "hal0_home")
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


def _bearer(client: TestClient, scope: str) -> dict[str, str]:
    store: TokenStore = client.app.state.token_store
    _, raw = store.create(label=f"test-{scope}", scope=scope)
    return {"Authorization": f"Bearer {raw}"}


# ── §29: mutating install endpoints require_writer ──────────────────────────
#
# Each tuple: (method, path, json_body). Bodies are minimal — the auth gate
# must fire BEFORE any business validation, so the test never asserts on a
# 2xx shape; it just asserts the gate's status code.

# Bodies use an unknown ``model_id`` for pick-default + slots-model so the
# auth-disabled pass-through test never fires a real HuggingFace download
# (the curated catalogue lookup / registry check 404s first). The auth gate
# fires BEFORE either of those handler-level validations.
WRITER_ROUTES: list[tuple[str, str, dict | None]] = [
    ("POST", "/api/install/probe", None),
    ("POST", "/api/install/complete", None),
    (
        "POST",
        "/api/install/pick-default",
        {"model_id": "this-id-is-not-curated", "slot": "primary"},
    ),
    ("PUT", "/api/install/slots/primary/model", {"model_id": "this-id-is-not-in-registry"}),
]


@pytest.mark.parametrize("method,path,body", WRITER_ROUTES)
def test_install_writer_routes_401_without_credentials(
    auth_app: TestClient, method: str, path: str, body: dict | None
) -> None:
    """Anonymous calls under HAL0_AUTH_ENABLED=1 must 401.

    Regression for FINDINGS §29: the installer router used to be mounted
    bare, so an unauthenticated LAN peer could hammer probe / write the
    sentinel / kick off a multi-GB pull. This test pins the gate.
    """
    response = auth_app.request(method, path, json=body)
    assert response.status_code == 401, (
        f"{method} {path} did not 401 without credentials: "
        f"status={response.status_code} body={response.text}"
    )
    assert response.json()["error"]["code"] == "auth.required"


@pytest.mark.parametrize("method,path,body", WRITER_ROUTES)
def test_install_writer_routes_403_for_readonly_token(
    auth_app: TestClient, method: str, path: str, body: dict | None
) -> None:
    """A read-only Bearer must 403 — write surface needs writer scope."""
    response = auth_app.request(method, path, json=body, headers=_bearer(auth_app, "read-only"))
    assert response.status_code == 403, (
        f"{method} {path} did not 403 for read-only: "
        f"status={response.status_code} body={response.text}"
    )
    assert response.json()["error"]["code"] == "auth.forbidden"


@pytest.mark.parametrize("method,path,body", WRITER_ROUTES)
def test_install_writer_routes_accept_admin_token(
    auth_app: TestClient, method: str, path: str, body: dict | None
) -> None:
    """An admin Bearer must pass the gate.

    Handler-level failures (e.g. 400 on a missing model_id, 4xx from
    business validation downstream) are fine — we only assert the auth
    layer does NOT reject the request.
    """
    response = auth_app.request(method, path, json=body, headers=_bearer(auth_app, "admin"))
    assert response.status_code not in (401, 403), (
        f"{method} {path} rejected admin scope: status={response.status_code} body={response.text}"
    )


@pytest.mark.parametrize("method,path,body", WRITER_ROUTES)
def test_install_writer_routes_open_when_auth_disabled(
    open_app: TestClient, method: str, path: str, body: dict | None
) -> None:
    """Fresh-install posture: HAL0_AUTH_ENABLED unset, wizard runs anonymous.

    This is the load-bearing UX contract for §29's fix — gating the
    installer router would lock first-run users out unless the gates
    short-circuit when no password / env-flag is set. Asserting "not
    401 / 403" rather than "== 200" keeps the test agnostic to handler
    errors (e.g. missing model registry seeds).
    """
    response = open_app.request(method, path, json=body)
    assert response.status_code not in (401, 403), (
        f"{method} {path} 401/403'd under HAL0_AUTH_ENABLED unset — the "
        f"first-run wizard relies on anonymous access during fresh "
        f"install. Body: {response.text}"
    )


# ── §30: slot-name validation rejects path traversal ────────────────────────


# Each malformed value the audit highlighted, plus a few representative
# edge cases. These are the values that MUST be rejected with code
# ``slot.invalid_name`` regardless of which install endpoint receives
# them — the path-traversal payload `../../tmp/pwn` is the primary vector
# called out in FINDINGS §30; the rest fence the policy.
#
# The empty string is omitted from this matrix because the pick-default
# handler treats an empty/missing ``slot`` as a request to use the
# ``primary`` default. ``set_slot_default_model`` accepts the slot in
# the URL path, where an empty segment 404s at the router before our
# validation runs. Either way the empty string is not a valid path
# traversal vector.
_BAD_SLOT_NAMES = [
    "../../tmp/pwn",
    "/etc/passwd",
    "..",
    ".",
    "a" * 64,  # over 32 chars
    "WITHCAPS",  # policy is lowercase
    "with spaces",
    "with/slash",
    "with\\backslash",
    "-leading-hyphen",  # policy requires alphanumeric first
    "_leading-underscore",
]


@pytest.mark.parametrize("slot", _BAD_SLOT_NAMES)
def test_pick_default_rejects_bad_slot_names(open_app: TestClient, slot: str) -> None:
    """POST /api/install/pick-default rejects malformed slots with 400.

    Regression for FINDINGS §30: ``slot="../../tmp/pwn"`` used to resolve
    to ``/tmp/pwn.toml`` via the f-string in ``_assign_to_slot``. The
    validation gate fires BEFORE any filesystem op, so the path is never
    constructed.

    Uses an unknown ``model_id`` so that — in the (regression) case where
    the slot-name gate fails to fire — we don't accidentally trigger a
    real HuggingFace download from the curated catalogue.
    """
    response = open_app.post(
        "/api/install/pick-default",
        json={"model_id": "this-id-is-not-curated", "slot": slot},
    )
    assert response.status_code == 400, (
        f"pick-default did not 400 for slot={slot!r}: "
        f"status={response.status_code} body={response.text}"
    )
    body = response.json()
    assert body["error"]["code"] == "slot.invalid_name", body
    assert body["error"]["details"]["slot"] == slot


def test_pick_default_accepts_valid_slot_name(open_app: TestClient) -> None:
    """A policy-compliant slot name passes the validation gate.

    We use an unknown ``model_id`` so the route 404s on the curated
    catalogue check BEFORE any background pull is queued — the test
    asserts only that the slot-name gate did not fire, not the
    handler's downstream behaviour.
    """
    response = open_app.post(
        "/api/install/pick-default",
        json={"model_id": "this-id-is-not-curated", "slot": "primary"},
    )
    # The handler 404s on the curated lookup; the assertion is that the
    # slot-name gate is not the reason.
    assert response.status_code != 200, (
        f"expected the curated lookup to 404, got {response.status_code}: {response.text}"
    )
    code = response.json()["error"]["code"]
    assert code != "slot.invalid_name", response.text


@pytest.mark.parametrize("slot", _BAD_SLOT_NAMES)
def test_set_slot_default_model_rejects_bad_slot_names(open_app: TestClient, slot: str) -> None:
    """PUT /api/install/slots/{slot}/model rejects malformed slots with 400.

    Same gate as ``pick-default`` but the slot arrives via the URL path,
    not the body — both ingress points must validate before
    ``_assign_to_slot`` is called.

    Empty / dot-only / slash-bearing slot values can't be expressed in a
    URL path segment (they'd hit FastAPI's 404 router first), so we use
    requests' raw URL form via ``client.request`` and skip values that
    can't be encoded.
    """
    # ``/`` and empty segments can't reach the route at all — the router
    # 404s before our gate runs. Those cases are exercised by the
    # pick-default body-param variant above.
    if not slot or "/" in slot or slot in {".", ".."}:
        pytest.skip("URL-unreachable slot value; covered by pick-default body matrix")
    response = open_app.put(
        f"/api/install/slots/{slot}/model",
        json={"model_id": "qwen3-4b"},
    )
    assert response.status_code == 400, (
        f"set_slot_default_model did not 400 for slot={slot!r}: "
        f"status={response.status_code} body={response.text}"
    )
    body = response.json()
    assert body["error"]["code"] == "slot.invalid_name", body


def test_set_slot_default_model_accepts_valid_slot_name(open_app: TestClient) -> None:
    """A policy-compliant slot name passes the validation gate."""
    response = open_app.put(
        "/api/install/slots/primary/model",
        json={"model_id": "does-not-exist"},
    )
    # The model_id is unknown, so the handler will 400 on the registry
    # check — but the slot-name gate must NOT be the reason.
    if response.status_code == 400:
        assert response.json()["error"]["code"] != "slot.invalid_name", response.text


def test_pick_default_default_slot_passes_validation(open_app: TestClient) -> None:
    """Omitting ``slot`` falls back to ``primary`` which passes validation.

    Uses an unknown curated id to short-circuit on the catalogue lookup
    (so no background pull is queued) — the assertion is that the
    slot-name gate doesn't fire on the default fallback value.
    """
    response = open_app.post(
        "/api/install/pick-default",
        json={"model_id": "this-id-is-not-curated"},
    )
    assert response.status_code != 200
    assert response.json()["error"]["code"] != "slot.invalid_name", response.text


def test_pick_default_rejects_non_string_slot(open_app: TestClient) -> None:
    """A non-string slot (e.g. a list) is rejected with 400 ``slot.invalid_name``.

    Defensive — the JSON body parser would otherwise let a typed payload
    bypass the regex check by being not-a-string.
    """
    response = open_app.post(
        "/api/install/pick-default",
        json={"model_id": "qwen3-4b", "slot": ["primary"]},
    )
    assert response.status_code == 400, response.text
    # Either the slot-name gate fires (preferred) or the body-shape
    # validator catches the type mismatch first. Both are acceptable
    # outcomes — neither leaves the path traversal vector open.
    assert response.json()["error"]["code"] in {"slot.invalid_name", "install.pick_default_failed"}
