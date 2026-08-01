"""Contract tests for hal0.observability.sentry.

Two things are being pinned here, and they are the two things that would
actually hurt if they regressed:

1. **Off by default.** No DSN ⇒ no init, no SDK import, no behaviour change.
   hal0 ships as a self-hosted appliance; silently acquiring telemetry would
   be a defect, not a feature.
2. **The scrubber holds.** Every event passes through ``scrub_event``; if it
   ever lets a bearer token, an api key or a request body through, secrets
   leave the operator's LAN.

The tests never call the real ``sentry_sdk`` (the extra is optional and is
not in the ``dev`` group) — ``scrub_event`` is a pure function over the
event dict, which is exactly the part worth testing.
"""

from __future__ import annotations

import pytest

from hal0.api._redact import MASK
from hal0.observability import sentry


@pytest.fixture(autouse=True)
def _clean_sentry_env(monkeypatch: pytest.MonkeyPatch):
    """Neutralise ambient config and the module-level init latch.

    A developer box with HAL0_SENTRY_DSN exported (or a previous test that
    initialised the module) must not change what these tests assert.
    """
    for name in sentry.DSN_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    sentry._reset_for_tests()
    yield
    sentry._reset_for_tests()


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------


def test_no_dsn_means_off(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sentry.dsn_from_env() == ""
    assert sentry.init_sentry("api") is False
    assert sentry.active_component() is None


def test_blank_dsn_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # `HAL0_SENTRY_DSN=` in an env file is the shape a commented-out line
    # degrades into. It must mean "off", not "misconfigured".
    monkeypatch.setenv("HAL0_SENTRY_DSN", "   ")
    assert sentry.dsn_from_env() == ""
    assert sentry.init_sentry("api") is False


def test_hal0_prefix_wins_over_bare_sentry_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://ambient@example.invalid/1")
    monkeypatch.setenv("HAL0_SENTRY_DSN", "https://hal0@example.invalid/2")
    assert sentry.dsn_from_env() == "https://hal0@example.invalid/2"


def test_capture_exception_is_a_noop_when_off() -> None:
    # No exception, no import of sentry_sdk, no output.
    sentry.capture_exception(RuntimeError("boom"))


def test_bad_sample_rate_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # A typo in a sample rate must never stop a service from starting, so
    # the parser clamps to the default instead of raising.
    monkeypatch.setenv("RATE", "not-a-float")
    assert sentry._float_env("RATE", 0.25) == 0.25
    monkeypatch.setenv("RATE", "7")
    assert sentry._float_env("RATE", 0.25) == 0.25
    monkeypatch.setenv("RATE", "0.5")
    assert sentry._float_env("RATE", 0.25) == 0.5


# ---------------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------------


def test_scrub_drops_user_block() -> None:
    event = {"user": {"id": "alexander", "ip_address": "10.0.1.42"}, "message": "hi"}
    scrubbed = sentry.scrub_event(dict(event))
    assert scrubbed is not None
    assert "user" not in scrubbed


def test_scrub_drops_request_body_cookies_and_query() -> None:
    event = {
        "request": {
            "url": "http://halo:8080/v1/chat/completions?api_key=sk-live-abcdef0123456789",
            "query_string": "api_key=sk-live-abcdef0123456789",
            "cookies": {"hal0_session": "deadbeef"},
            "data": {"messages": [{"role": "user", "content": "my private prompt"}]},
            "env": {"REMOTE_ADDR": "10.0.1.42"},
            "headers": {
                "Authorization": "Bearer sk-live-abcdef0123456789",
                "User-Agent": "curl/8.5.0",
            },
        }
    }
    scrubbed = sentry.scrub_event(event)
    assert scrubbed is not None
    request = scrubbed["request"]

    for dropped in sentry.DROPPED_REQUEST_KEYS:
        assert dropped not in request
    # The URL keeps its shape (useful for grouping) but loses the query.
    assert request["url"] == "http://halo:8080/v1/chat/completions"
    assert request["headers"]["Authorization"] == MASK
    # Non-sensitive headers survive — the scrubber must stay useful.
    assert request["headers"]["User-Agent"] == "curl/8.5.0"

    # And the prompt text is gone with the body.
    assert "my private prompt" not in repr(scrubbed)


def test_scrub_masks_sensitive_keys_anywhere_in_the_event() -> None:
    event = {
        "extra": {
            "HAL0_ADMIN_KEY": "hal0-admin-abc123",
            "hf_token": "hf_abcdefghijklmno",
            "slot_name": "flm",
            "nested": [{"password": "hunter2"}],
        }
    }
    scrubbed = sentry.scrub_event(event)
    assert scrubbed is not None
    extra = scrubbed["extra"]
    assert extra["HAL0_ADMIN_KEY"] == MASK
    assert extra["hf_token"] == MASK
    assert extra["nested"][0]["password"] == MASK
    # Non-sensitive keys are untouched.
    assert extra["slot_name"] == "flm"


def test_scrub_redacts_secrets_inside_free_text() -> None:
    # The case the key-name rule cannot catch: a credential embedded in an
    # exception message. redact_log_line keeps the prefix, kills the token.
    event = {
        "exception": {
            "values": [
                {
                    "type": "HTTPStatusError",
                    "value": "401 from upstream (Authorization: Bearer sk-live-abcdef0123456789)",
                }
            ]
        }
    }
    scrubbed = sentry.scrub_event(event)
    assert scrubbed is not None
    value = scrubbed["exception"]["values"][0]["value"]
    assert "sk-live-abcdef0123456789" not in value
    assert MASK in value
    # Prefix preserved so an operator can still tell what kind of secret it was.
    assert "Authorization: Bearer" in value


def test_scrub_fails_closed_on_a_broken_event() -> None:
    class Exploding(dict):
        def get(self, *_args, **_kwargs):  # type: ignore[override]
            raise RuntimeError("unexpected event shape")

    # An event we cannot scrub is an event we do not send.
    assert sentry.scrub_event(Exploding()) is None


def test_scrub_bounds_recursion() -> None:
    # A self-referential-by-depth structure must terminate rather than blow
    # the stack inside the SDK's before_send hook.
    event: dict = {}
    node = event
    for _ in range(40):
        child: dict = {}
        node["child"] = child
        node = child
    node["done"] = True

    scrubbed = sentry.scrub_event(event)
    assert scrubbed is not None

    depth = 0
    cursor = scrubbed
    while isinstance(cursor, dict) and "child" in cursor:
        cursor = cursor["child"]
        depth += 1
    assert depth <= sentry._MAX_SCRUB_DEPTH + 1
