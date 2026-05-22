"""Tests for the first-run wizard auth fix + "skip chat model" path.

Pins the behavioural contract introduced by commit 913d0ac
``fix(firstrun): unblock wizard writer calls + allow skipping chat model``:

  1. POST /api/auth/password on the first-run leg sets a hal0_session
     cookie so subsequent writer mutations from the wizard ride a normal
     session — without this the wizard 401s on every PUT/POST after the
     password step.
  2. The same endpoint on the rotation leg deliberately does NOT
     re-issue the cookie; the caller already had a session/Bearer to
     pass require_writer.
  3. The first-run claim window now reaches a few writer-gated routes
     the wizard fires before the password step (or while the operator
     chose "Skip — leave open"): PUT /api/config/models, the per-id
     pull endpoint, and the per-(slot, child) capability registration.
  4. The same claim window closes when POST /api/install/complete
     runs — the lockfile is consumed and follow-up anonymous writes
     bounce off the writer gate.
  5. The curated picker filters image models out so an operator can't
     pick Flux as their chat model.
  6. The path-matcher helper itself is unit-tested so the regex layer
     can't regress without a red bar.

The fixtures here mirror tests/api/test_password_auth.py — fresh app per
test, HAL0_AUTH_ENABLED=1, HAL0_HOME under tmp_path, loopback client-IP
pin so the OTP gate's loopback bypass exercises during set-password.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.auth import first_run as first_run_lock
from hal0.api.auth.password import verify_session_token
from hal0.api.middleware.auth import SESSION_COOKIE_NAME
from hal0.auth.tokens import TokenStore


def _install_loopback_client_middleware(app: object) -> None:
    """Pin ``request.client.host`` to ``127.0.0.1`` for the test app.

    Lifted verbatim from tests/api/test_password_auth.py — Starlette's
    TestClient hard-codes ``scope['client']`` to ``('testclient',
    50000)``, but the first-run OTP gate distinguishes loopback from
    LAN peers. We want set-password's loopback bypass to fire so the
    test doesn't have to wire the OTP through every call.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest

    class _PinClientMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: StarletteRequest, call_next):  # type: ignore[override]
            override = request.headers.get("x-test-client-ip")
            host = override or "127.0.0.1"
            request.scope["client"] = (host, 12345)
            return await call_next(request)

    app.add_middleware(_PinClientMiddleware)  # type: ignore[attr-defined]


@pytest.fixture
def auth_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Fresh app with auth enabled, isolated store + keyring, lockfile kept.

    The lifespan auto-mints ``.first-run.lock`` when no password is set,
    so by default the first-run claim window is OPEN here. Tests that
    want the post-claim posture (lockfile consumed) explicitly delete
    the file themselves.
    """
    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    monkeypatch.setenv("HAL0_OVERRIDE_DIR", "hal0_home")
    app = create_app()
    _install_loopback_client_middleware(app)
    with TestClient(app) as c:
        # Pin the store onto a tmp-rooted path so the password hash
        # lands under tmp_path/etc/hal0/tokens.toml. Mirrors the
        # test_password_auth fixture pattern.
        store = TokenStore(tmp_path / "etc" / "hal0" / "tokens.toml")
        c.app.state.token_store = store
        # Reset the rate-limit bucket so a noisy fixture doesn't tip a
        # later set-password into 429.
        limiter = getattr(c.app.state, "auth_rate_limiter", None)
        if limiter is not None:
            limiter.reset()
        yield c


def _admin_bearer(client: TestClient, label: str = "test-admin") -> dict[str, str]:
    store: TokenStore = client.app.state.token_store
    _, raw = store.create(label=label, scope="admin")
    return {"Authorization": f"Bearer {raw}"}


# ── (1) Set-password issues Set-Cookie on the first-run leg ─────────────────


def test_first_run_set_password_issues_session_cookie(auth_app: TestClient) -> None:
    """A fresh-install set-password call returns a HttpOnly hal0_session cookie.

    Without this, the wizard's subsequent PUT /api/config/models / pull
    / capability calls would 401 because no credential exists yet. The
    fix mints a session JWT and pins it onto the response so the rest of
    the wizard rides a normal cookie session.
    """
    # Sanity: lockfile was minted by the lifespan and password isn't set.
    assert first_run_lock.lockfile_path().exists()

    response = auth_app.post("/api/auth/password", json={"password": "correcthorse"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["password_set"] is True
    assert body["rotated"] is False

    # Set-Cookie header must carry the session cookie with HttpOnly +
    # Path=/. SameSite=lax is part of the cookie contract too — we
    # don't assert Secure because the TestClient is not HTTPS.
    set_cookie = response.headers.get("set-cookie", "")
    assert f"{SESSION_COOKIE_NAME}=" in set_cookie, set_cookie
    assert "HttpOnly" in set_cookie, set_cookie
    assert "Path=/" in set_cookie, set_cookie

    # The cookie value must verify as a real session token with the
    # owner identity + admin scope. The middleware reads sub + scope to
    # populate AuthIdentity on subsequent requests.
    cookie_value = auth_app.cookies.get(SESSION_COOKIE_NAME)
    assert cookie_value, "cookie not stashed in client jar"
    claims = verify_session_token(cookie_value)
    assert claims is not None, "session cookie failed to verify"
    assert claims["sub"] == "owner"
    assert claims["scope"] == "admin"


# ── (2) Rotation does NOT re-issue the cookie ───────────────────────────────


def test_set_password_rotation_does_not_reissue_cookie(auth_app: TestClient) -> None:
    """Rotation under an existing session keeps the existing cookie.

    The rotation leg already has a credential (Bearer or session); the
    fix only mints on the first-run path. Re-issuing on rotation would
    be a quiet TTL refresh side-effect — not desired here.
    """
    # First-run claim — primes the password + sets the initial cookie.
    first = auth_app.post("/api/auth/password", json={"password": "correcthorse"})
    assert first.status_code == 200, first.text
    initial_cookie = auth_app.cookies.get(SESSION_COOKIE_NAME)
    assert initial_cookie, "first-run leg should have set the cookie"

    # Rotate via the existing session. The cookie path goes through the
    # CSRF tripwire, so add X-Requested-With.
    rotate = auth_app.post(
        "/api/auth/password",
        json={"password": "newbatterystaple"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert rotate.status_code == 200, rotate.text
    body = rotate.json()
    assert body["rotated"] is True

    # The response must NOT carry a fresh Set-Cookie for hal0_session
    # — rotation is the "you already had auth, prove it; we don't touch
    # the session" path. The TestClient cookie jar still holds the
    # original cookie (httpx doesn't pop on absence).
    set_cookie = rotate.headers.get("set-cookie", "") or ""
    assert f"{SESSION_COOKIE_NAME}=" not in set_cookie, (
        f"rotation must not re-issue the session cookie; got: {set_cookie!r}"
    )


# ── (3) First-run claim covers PUT /api/config/models ───────────────────────


def test_first_run_claim_admits_put_config_models(auth_app: TestClient) -> None:
    """Anonymous PUT /api/config/models lands during the claim window.

    The wizard fires this after the operator picks storage directories.
    Before the fix added the path to the claim set it bounced with 401
    auth.required, blocking step 2 entirely.
    """
    # Sanity: claim window is open (lockfile present + no password).
    assert first_run_lock.lockfile_path().exists()
    response = auth_app.put(
        "/api/config/models",
        json={"roots": [], "auto_scan_on_start": False},
    )
    # Either the route's business logic accepts the body (200) or it
    # rejects on a downstream concern — what we must NEVER see is the
    # 401 auth.required that would mean the claim path-set missed.
    assert response.status_code != 401, (
        f"PUT /api/config/models must be reachable during the first-run "
        f"claim window; got {response.status_code}: {response.text}"
    )
    # In practice the route returns 200 on the minimal body — pin that
    # so a future regression that flips the auth into a different 4xx
    # gets caught.
    assert response.status_code == 200, response.text


# ── (4) First-run claim covers regex routes ─────────────────────────────────


def test_first_run_claim_admits_models_pull(auth_app: TestClient) -> None:
    """Anonymous POST /api/models/{id}/pull lands during the claim window.

    The handler may reject downstream (unknown model id, missing
    HF_TOKEN, etc.) — we only assert the request makes it past the
    auth gate.
    """
    assert first_run_lock.lockfile_path().exists()
    response = auth_app.post("/api/models/this-id-is-not-curated/pull")
    assert response.status_code != 401, (
        f"POST /api/models/{{id}}/pull must be reachable during the "
        f"first-run claim window; got {response.status_code}: {response.text}"
    )


def test_first_run_claim_admits_capability_registration(auth_app: TestClient) -> None:
    """Anonymous POST /api/capabilities/{slot}/{child} lands during the claim."""
    assert first_run_lock.lockfile_path().exists()
    # Use a known-legal slot/child so the orchestrator doesn't 400 on
    # the slot lookup; the auth layer is what we're pinning anyway.
    response = auth_app.post(
        "/api/capabilities/embed/embed",
        json={"enabled": False},
    )
    assert response.status_code != 401, (
        f"POST /api/capabilities/{{slot}}/{{child}} must be reachable "
        f"during the first-run claim window; got {response.status_code}: "
        f"{response.text}"
    )


def test_first_run_claim_rejects_extra_path_segment(auth_app: TestClient) -> None:
    """An extra trailing segment must NOT slip past the regex.

    ``re.fullmatch`` anchors at both ends, so /api/models/foo/pull/extra
    is not a claim-eligible path even though the leading segment
    matches. We assert at the helper level rather than the HTTP layer
    because FastAPI's router resolves paths before auth deps fire, so a
    non-existent path 404/405s before reaching ``_first_run_claim_active``
    — exactly what we want defensively, but it makes the HTTP-level
    assertion brittle. The unit-level path matrix below covers the
    fullmatch contract directly.
    """
    from hal0.api.middleware.auth import _path_is_claim_eligible

    assert _path_is_claim_eligible("/api/models/foo/pull/extra") is False
    assert _path_is_claim_eligible("/api/capabilities/embed/embed/extra") is False
    # And the matching base paths still ARE eligible — proof the regex
    # is doing the work, not a blanket "no extras" rule that would also
    # break legitimate routes.
    assert _path_is_claim_eligible("/api/models/foo/pull") is True
    assert _path_is_claim_eligible("/api/capabilities/embed/embed") is True


# ── (5) /api/install/complete consumes the lockfile ─────────────────────────


def test_install_complete_consumes_lockfile_and_closes_claim(
    auth_app: TestClient,
) -> None:
    """POST /api/install/complete unlinks the lockfile and closes the window.

    Before the fix the wizard's "Skip — leave open" branch never
    reached POST /api/auth/password, so the lockfile (and its
    anonymous claim window) survived the wizard's completion. The new
    consume call in /api/install/complete closes the window
    unconditionally — the moment the wizard signals "done", the claim
    paths revert to writer-gated.
    """
    # Sanity: lockfile present + claim window open. Demonstrate the
    # anonymous-write path works BEFORE we call /complete.
    lock = first_run_lock.lockfile_path()
    assert lock.exists()
    pre_response = auth_app.put(
        "/api/config/models",
        json={"roots": [], "auto_scan_on_start": False},
    )
    assert pre_response.status_code == 200, pre_response.text

    # POST /api/install/complete is itself a writer route that the
    # claim window admits anonymously — same gate, same path-set.
    complete = auth_app.post("/api/install/complete")
    assert complete.status_code == 200, complete.text

    # Lockfile must be gone — the install-complete handler calls
    # ``first_run_lock.consume_lockfile()`` after writing the sentinel.
    assert not lock.exists(), (
        f"install/complete must consume the first-run lockfile; still present at {lock}"
    )

    # Follow-up anonymous PUT now bounces with 401 auth.required —
    # the claim window is closed, the writer dep is enforced.
    post_response = auth_app.put(
        "/api/config/models",
        json={"roots": [], "auto_scan_on_start": False},
    )
    assert post_response.status_code == 401, (
        f"after /api/install/complete the writer gate must re-enforce; "
        f"got {post_response.status_code}: {post_response.text}"
    )
    assert post_response.json()["error"]["code"] == "auth.required"


# ── (6) Curated picker filters image models ─────────────────────────────────


def test_curated_models_filters_out_image_slot(auth_app: TestClient) -> None:
    """GET /api/install/curated-models surfaces only chat picks.

    Pre-fix the curated catalogue included image-gen rows
    (``recommended_slot="img"``), which let an operator install Flux
    as their "chat model" — meaningless. The fix filters the response
    to ``recommended_slot == "primary"`` so the wizard's chat step
    only shows valid chat candidates.

    We also assert directly against the source catalogue to prove the
    filter is real (i.e. there ARE img entries upstream — without
    that, the filter could silently be a no-op).
    """
    from hal0.registry.curated import CURATED_MODELS

    # Source catalogue MUST contain at least one img entry — otherwise
    # the filter doesn't actually filter anything and the test is
    # vacuous.
    assert any(m.recommended_slot == "img" for m in CURATED_MODELS), (
        "catalogue invariant: at least one image-gen entry must exist to make the filter meaningful"
    )

    response = auth_app.get("/api/install/curated-models")
    assert response.status_code == 200, response.text
    body = response.json()
    img_picks = [m for m in body["models"] if m.get("recommended_slot") == "img"]
    assert img_picks == [], (
        f"chat-picker must filter out img-slot models; got: {[m['id'] for m in img_picks]}"
    )
    # And every surfaced row must positively be a chat pick.
    for m in body["models"]:
        assert m.get("recommended_slot") == "primary", (
            f"curated-models must only surface primary-slot picks; "
            f"got {m.get('id')!r} with slot {m.get('recommended_slot')!r}"
        )


# ── (7) _path_is_claim_eligible — pure-function path matrix ─────────────────


@pytest.mark.parametrize(
    "path,expected",
    [
        # Fixed paths (exact set membership)
        ("/api/install/state", True),
        ("/api/install/probe", True),
        ("/api/install/complete", True),
        ("/api/install/curated-models", True),
        ("/api/install/pick-default", True),
        ("/api/auth/password", True),
        ("/api/config/models", True),
        # Regex matches — /api/models/{id}/pull
        ("/api/models/qwen3-4b/pull", True),
        ("/api/models/some-weird_id.v2/pull", True),
        # Regex matches — /api/capabilities/{slot}/{child}
        ("/api/capabilities/embed/embed", True),
        ("/api/capabilities/voice/stt", True),
        ("/api/capabilities/img/img", True),
        # Near-misses: extra trailing segments must NOT match (fullmatch
        # contract). These are the regression cases for §28 / §29.
        ("/api/models/foo/pull/extra", False),
        ("/api/capabilities/embed/embed/extra", False),
        # Near-misses: missing required segments.
        ("/api/models/pull", False),
        ("/api/models//pull", False),
        ("/api/capabilities/embed", False),
        ("/api/capabilities//child", False),
        ("/api/capabilities/embed/", False),
        # Totally unrelated paths — the helper must reject everything
        # outside the claim surface so the wizard's path-set can't
        # accidentally leak into the rest of the admin API.
        ("/api/slots", False),
        ("/api/slots/primary", False),
        ("/api/auth/login", False),
        ("/api/auth/logout", False),
        ("/api/hardware", False),
        ("/api/models", False),
        ("/api/models/foo", False),
        ("/", False),
        ("", False),
        # Case sensitivity — paths are case-sensitive in the helper.
        ("/API/install/state", False),
    ],
)
def test_path_is_claim_eligible(path: str, expected: bool) -> None:
    """Walk the full path matrix for the claim-eligible helper.

    Anchors:
      - Fixed-path hits via :data:`_FIRST_RUN_CLAIM_PATHS` (set
        membership).
      - Regex hits via :data:`_FIRST_RUN_CLAIM_PATTERNS` using
        ``re.fullmatch`` — extra trailing segments must NOT match.
      - Unrelated paths fall through to False so the admin surface
        stays gated.
    """
    from hal0.api.middleware.auth import _path_is_claim_eligible

    assert _path_is_claim_eligible(path) is expected, (
        f"claim-eligibility mismatch for path={path!r}: got {not expected}, expected {expected}"
    )


# ── (8) /api/auth/disable — first-run skip → trusted-LAN posture ────────────
#
# The wizard's "Skip — leave open" button writes HAL0_AUTH_DISABLED=1
# into /etc/hal0/api.env and schedules a service restart so the
# dashboard's writer routes stop 401'ing on a no-credential install.
# These tests pin the gates (password-not-set + lockfile-present) and
# the env-writer's idempotency.


@pytest.fixture
def stub_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the ``_schedule_service_restart`` helper with a no-op.

    Without this the test would try to ``systemctl restart hal0-api``,
    which is either missing (CI runners) or actually restarts the test
    process (real systemd). The endpoint's contract is "best-effort
    deferred restart, never blocks on failure"; the no-op stub keeps
    the rest of the assertions clean.
    """
    from hal0.api.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "_schedule_service_restart", lambda: None)


def test_disable_auth_writes_env_and_consumes_lockfile(
    auth_app: TestClient, tmp_path: Path, stub_restart: None
) -> None:
    """First-run anonymous POST sticks HAL0_AUTH_DISABLED=1 + closes claim."""
    lock = first_run_lock.lockfile_path()
    assert lock.exists()

    api_env = tmp_path / "etc" / "hal0" / "api.env"
    # Pre-seed an existing api.env with the commented form, the way
    # installer/install.sh writes it. The handler should uncomment.
    api_env.parent.mkdir(parents=True, exist_ok=True)
    api_env.write_text(
        "HAL0_PORT=8080\nHAL0_LOG_LEVEL=info\n# HAL0_AUTH_DISABLED=1\n",
        encoding="utf-8",
    )

    response = auth_app.post("/api/auth/disable")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["auth_disabled"] is True
    assert body["restart_scheduled"] is True

    written = api_env.read_text(encoding="utf-8")
    assert "HAL0_AUTH_DISABLED=1" in written
    # Commented form must be gone — the handler uncomments in place
    # rather than appending a duplicate.
    assert "# HAL0_AUTH_DISABLED=1" not in written

    # Lockfile consumed → the claim window closes.
    assert not lock.exists()


def test_disable_auth_rejects_after_password_set(auth_app: TestClient, stub_restart: None) -> None:
    """403 once an owner password exists — can't toggle auth anonymously."""
    # Set a password via the normal first-run flow.
    set_response = auth_app.post(
        "/api/auth/password",
        json={"password": "supersecret123"},
    )
    assert set_response.status_code == 200, set_response.text

    # Clear the session cookie the set-password response minted —
    # we want to exercise the anonymous-call gate, not the
    # authenticated-rotation path.
    auth_app.cookies.clear()

    response = auth_app.post("/api/auth/disable")
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "auth.forbidden"


def test_disable_auth_rejects_after_lockfile_consumed(
    auth_app: TestClient, stub_restart: None
) -> None:
    """403 once the claim window has closed (lockfile gone)."""
    first_run_lock.consume_lockfile()
    assert not first_run_lock.lockfile_path().exists()

    response = auth_app.post("/api/auth/disable")
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "auth.forbidden"


def test_set_auth_disabled_in_env_idempotency(tmp_path: Path) -> None:
    """``_set_auth_disabled_in_env`` handles the three input shapes.

    Cases:
      - File missing → appended.
      - Commented (``# HAL0_AUTH_DISABLED=1``) → uncommented in place.
      - Already set with a different value → replaced.
      - Already set with the canonical value → no-op (still exactly one line).
    """
    from hal0.api.routes.auth import _set_auth_disabled_in_env

    target = tmp_path / "api.env"

    # 1. Missing file → handler creates parent + writes the line.
    _set_auth_disabled_in_env(target)
    assert target.read_text(encoding="utf-8") == "HAL0_AUTH_DISABLED=1\n"

    # 2. Commented form → uncomment.
    target.write_text(
        "HAL0_PORT=8080\n# HAL0_AUTH_DISABLED=1\n",
        encoding="utf-8",
    )
    _set_auth_disabled_in_env(target)
    contents = target.read_text(encoding="utf-8")
    assert "HAL0_AUTH_DISABLED=1\n" in contents
    assert "# HAL0_AUTH_DISABLED=1" not in contents
    # Exactly one occurrence.
    assert contents.count("HAL0_AUTH_DISABLED=1") == 1

    # 3. Different value → overwritten.
    target.write_text(
        "HAL0_PORT=8080\nHAL0_AUTH_DISABLED=0\n",
        encoding="utf-8",
    )
    _set_auth_disabled_in_env(target)
    assert target.read_text(encoding="utf-8").count("HAL0_AUTH_DISABLED=1") == 1
    assert "HAL0_AUTH_DISABLED=0" not in target.read_text(encoding="utf-8")

    # 4. Already canonical → no-op (still exactly one line).
    _set_auth_disabled_in_env(target)
    assert target.read_text(encoding="utf-8").count("HAL0_AUTH_DISABLED=1") == 1
