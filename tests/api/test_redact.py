"""Tests for hal0.api._redact — shared config-echo redaction (#553).

Every config-echoing endpoint (settings, upstreams,
secrets) routes its response through :func:`redact_config` so a key
whose NAME matches a sensitive regex is returned masked, with a ``set``
flag carrying the "is it configured" bit. This file pins down that
behaviour at the helper level — endpoint-level wiring is asserted
alongside the existing route tests in test_settings_routes.py and
test_upstream_dedup.py.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api._redact import (
    is_sensitive_key,
    redact_config,
    redact_log_line,
    redact_value,
)
from hal0.observability.sentry import SENSITIVE_HEADERS
from hal0.redaction import MASK, redact_secret_shapes, redact_shareable_text

# ── is_sensitive_key ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "OPENROUTER_API_KEY",
        "api_key",
        "Api_Key",
        "HF_TOKEN",
        "hf_token",
        "TOKENIZER_ID",  # TOKEN as substring
        "password",
        "PASSWORD",
        "PRIVATE_KEY",
        "private_key",
        "ENCRYPTION_KEY",
        "encryption_key",
        "SALT",
        "salt",
        "secret",
        "SECRET_KEY",
    ],
)
def test_is_sensitive_key_matches_documented_patterns(key: str) -> None:
    """Every pattern from the issue spec matches, case-insensitive."""
    assert is_sensitive_key(key), key


@pytest.mark.parametrize(
    "key",
    [
        "ctx_size",
        "port",
        "url",
        "host",
        "name",
        "kind",
        "auth_value_env",  # env-var NAME, not the secret itself
        "auth_style",
        "models",
        "enabled",
    ],
)
def test_is_sensitive_key_leaves_plain_keys_alone(key: str) -> None:
    """Non-sensitive config keys are not flagged (no over-redaction)."""
    assert not is_sensitive_key(key), key


# ── redact_value ───────────────────────────────────────────────────────────


def test_redact_value_masks_nonempty_token() -> None:
    """A non-empty sensitive value → MASK + set=True."""
    out = redact_value("sk-abc123")
    assert out == {"value": "***REDACTED***", "set": True}


def test_redact_value_empty_string_yields_set_false() -> None:
    """An empty string is treated as 'unset' so the UI can render a blank slot."""
    assert redact_value("") == {"value": "***REDACTED***", "set": False}


def test_redact_value_none_yields_set_false() -> None:
    """A None value is treated as 'unset'."""
    assert redact_value(None) == {"value": "***REDACTED***", "set": False}


def test_redact_value_zero_is_treated_as_set() -> None:
    """A non-None falsy value (0, False) still counts as 'set' — only the
    empty-string / None cases are 'unset'."""
    assert redact_value(0) == {"value": "***REDACTED***", "set": True}
    assert redact_value(False) == {"value": "***REDACTED***", "set": True}


# ── redact_config (flat) ───────────────────────────────────────────────────


def test_redact_config_token_key_masked_with_set_true() -> None:
    """Acceptance criterion #1: a known token-bearing key comes back masked,
    with ``set=true``."""
    out = redact_config({"OPENROUTER_API_KEY": "sk-abc"})
    assert out == {"OPENROUTER_API_KEY": {"value": "***REDACTED***", "set": True}}


def test_redact_config_plain_key_passes_through_unmasked() -> None:
    """A non-sensitive key (e.g. ``ctx_size``) is echoed verbatim."""
    out = redact_config({"ctx_size": 4096, "port": 8080})
    assert out == {"ctx_size": 4096, "port": 8080}


def test_redact_config_empty_sensitive_key_yields_set_false() -> None:
    """An empty sensitive value is masked with ``set=false`` so the UI can
    render the slot as 'not configured' without ever receiving the secret."""
    out = redact_config({"API_KEY": ""})
    assert out == {"API_KEY": {"value": "***REDACTED***", "set": False}}


def test_redact_config_does_not_mutate_input() -> None:
    """The input dict is not mutated — redaction is a pure projection."""
    src = {"OPENROUTER_API_KEY": "sk-abc", "ctx_size": 4096}
    redact_config(src)
    assert src == {"OPENROUTER_API_KEY": "sk-abc", "ctx_size": 4096}


# ── redact_config (nested) ─────────────────────────────────────────────────


def test_redact_config_walks_nested_dicts() -> None:
    """Nested dicts are scrubbed: any sensitive key at any depth is masked."""
    out = redact_config(
        {
            "providers": {
                "openrouter": {
                    "api_key": "sk-abc",
                    "base_url": "https://openrouter.ai",
                },
            },
        },
    )
    assert out["providers"]["openrouter"]["api_key"] == {
        "value": "***REDACTED***",
        "set": True,
    }
    assert out["providers"]["openrouter"]["base_url"] == "https://openrouter.ai"


def test_redact_config_walks_lists_of_dicts() -> None:
    """Lists of dicts are walked element-by-element.

    Note the container key (``upstreams``) is deliberately NON-sensitive:
    a sensitive container key (e.g. ``secrets``) is over-redacted wholesale
    by design (see test_redact_sensitive_container_masks_wholesale), so it
    would never reach the list. We want to exercise list recursion here.
    """
    out = redact_config(
        {
            "upstreams": [
                {"name": "OPENAI", "token": "sk-1"},
                {"name": "ANTHROPIC", "token": ""},
            ],
        },
    )
    assert out["upstreams"][0]["token"] == {"value": "***REDACTED***", "set": True}
    assert out["upstreams"][1]["token"] == {"value": "***REDACTED***", "set": False}
    assert out["upstreams"][0]["name"] == "OPENAI"  # plain key passes through


def test_redact_sensitive_container_masks_wholesale() -> None:
    """A sensitive *container* key over-redacts its whole value (by design).

    ``re.search`` on the key name means a container named ``secrets`` is
    masked wholesale rather than recursed — the conservative behaviour the
    spec asks for (never leak a secret), accepting that structure under
    such a key is lost.
    """
    out = redact_config({"secrets": [{"token": "sk-1"}]})
    assert out["secrets"] == {"value": "***REDACTED***", "set": True}


def test_redact_config_list_of_scalars_passes_through() -> None:
    """A list of scalars is not masked — only keyed containers are scrubbed."""
    assert redact_config({"models": ["a", "b", "c"]}) == {"models": ["a", "b", "c"]}


def test_redact_config_scalars_returned_verbatim() -> None:
    """Scalars at the root are passed through (helper expects a dict/list)."""
    assert redact_config(42) == 42
    assert redact_config("hello") == "hello"
    assert redact_config(None) is None


# ── integration: settings endpoint echoes masked secrets ───────────────────


@pytest.fixture
def isolated_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    """TestClient with writes isolated under tmp_hal0_home.

    Mirrors the pattern in tests/api/test_settings_routes.py — the
    shared ``client`` fixture instantiates the app before tmp_hal0_home
    is set, so a PUT-driven test would write to /etc/hal0.
    """
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


def test_settings_get_redacts_sensitive_keys(
    isolated_client: TestClient,
) -> None:
    """End-to-end: PUT a sensitive-named extra-allow field, then GET — the
    echoed config must mask the value, never return it in plaintext.

    Hal0Config uses ``extra="allow"`` at the top level (forward-compat for
    future tables) so a top-level ``api_key`` is accepted by the
    validator and round-trips through the file. The redaction pass then
    catches the key on the way out.
    """
    secret = "sk-not-a-real-key-12345"
    put = isolated_client.put("/api/settings", json={"api_key": secret})
    assert put.status_code == 200, put.text

    r = isolated_client.get("/api/settings")
    assert r.status_code == 200, r.text
    body = r.json()

    # The sensitive field is present, masked, with set=True.
    assert "api_key" in body, body
    assert body["api_key"] == {"value": "***REDACTED***", "set": True}
    # Plaintext is never echoed.
    assert secret not in str(body)


def test_settings_get_empty_sensitive_key_yields_set_false(
    isolated_client: TestClient,
) -> None:
    """Empty sensitive value comes back masked with set=false (so the UI
    can render the slot as 'not configured')."""
    put = isolated_client.put("/api/settings", json={"openrouter_api_key": ""})
    assert put.status_code == 200, put.text

    r = isolated_client.get("/api/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["openrouter_api_key"] == {"value": "***REDACTED***", "set": False}


def test_upstreams_serialize_redacts_api_key_if_present() -> None:
    """If an Upstream entry carries an api_key in its extra-allow dict, the
    serialize helper masks it before it leaves the API. This is a pure
    unit test against the redact_config helper as applied to the
    upstream-shape dict — the route's serializer is the integration
    surface; the route itself only stores the env-var NAME, not a value.
    """
    upstream_dict = {
        "name": "openrouter",
        "kind": "remote",
        "url": "https://openrouter.ai/api/v1",
        "auth_style": "bearer",
        "auth_value_env": "OPENROUTER_API_KEY",
        "api_key": "sk-abc",  # someone put this in their toml via extra="allow"
        "models": [],
    }
    out = redact_config(upstream_dict)
    assert out["name"] == "openrouter"
    assert out["auth_value_env"] == "OPENROUTER_API_KEY"  # env-var NAME, not value
    assert out["api_key"] == {"value": "***REDACTED***", "set": True}
    assert out["models"] == []


# ── redact_log_line (api-logs-redact) ──────────────────────────────────────
#
# Free-text counterpart to redact_config: scans a raw log LINE for known
# secret shapes rather than walking a dict by key name. Shared by
# hal0.mcp.admin's logs_tail/slot_logs redaction and
# hal0.api.routes.logs's /api/logs + /api/logs/stream (moved here so the
# REST route doesn't have to import hal0.mcp.admin, which hard-fails
# without the optional mcp SDK installed).


@pytest.mark.parametrize(
    "raw,expected_masked_fragment,leaked_secret",
    [
        (
            "GET /v1/models Authorization: Bearer sk-or-supersecret-xyz",
            "Authorization: Bearer ***REDACTED***",
            "sk-or-supersecret-xyz",
        ),
        (
            "env loaded: HAL0_BEARER_TOKEN=hal0_tok_xyz",
            "HAL0_BEARER_TOKEN=***REDACTED***",
            "hal0_tok_xyz",
        ),
        (
            "raw fallback: Bearer abcDEF123_-.tok still gets masked",
            "Bearer ***REDACTED***",
            "abcDEF123_-.tok",
        ),
        (
            "mcp.tool.invoked client_id=abcdefghijklmnopqrstuvwxyz0123456789 tool=slot_list",
            "client_id=***REDACTED***",
            "abcdefghijklmnopqrstuvwxyz0123456789",
        ),
        (
            # Bare `_KEY=`-suffixed secret (hal0's own admin/client keys) —
            # the leak shape halo150 O9 found in structured config, now
            # also guarded against verbatim in free-text log lines.
            "env dump: HAL0_ADMIN_KEY=abcdef1234567890",
            "HAL0_ADMIN_KEY=***REDACTED***",
            "abcdef1234567890",
        ),
        (
            "env dump: SOME_API_KEY=sk-live-abcdef1234567890",
            "SOME_API_KEY=***REDACTED***",
            "sk-live-abcdef1234567890",
        ),
        (
            "config dump: KEY=abcdef1234567890",
            "KEY=***REDACTED***",
            "abcdef1234567890",
        ),
    ],
)
def test_redact_log_line_masks_known_secret_shapes(
    raw: str, expected_masked_fragment: str, leaked_secret: str
) -> None:
    out = redact_log_line(raw)
    assert expected_masked_fragment in out
    assert leaked_secret not in out


def test_redact_log_line_passes_through_safe_content() -> None:
    """No false positives on lines that don't carry secrets — including
    KEY-shaped substrings that are not a trailing `_KEY=`/`KEY=` field."""
    line = "[12:00:00] hal0.api.startup version=0.2.0a2 KEY_ROTATION_DAYS=30"
    assert redact_log_line(line) == line


def test_redact_log_line_does_not_mask_short_client_id_labels() -> None:
    """The hashed client_id label (12 hex chars) and short agent ids stay
    visible — only long, key-shaped `client_id=` values are secrets."""
    line = "mcp.tool.invoked client_id=1a2b3c4d5e6f tool=slot_list outcome=ok"
    assert redact_log_line(line) == line


class TestBareKeySuffix:
    """halo150 O9: hal0's own auth keys must mask — bare _KEY suffix."""

    @pytest.mark.parametrize(
        "name",
        ["HAL0_ADMIN_KEY", "HAL0_CLIENT_KEY", "admin_key", "hmac_key", "KEY"],
    )
    def test_key_suffix_is_sensitive(self, name):
        assert is_sensitive_key(name) is True

    @pytest.mark.parametrize(
        "name",
        ["KEY_ROTATION_DAYS", "KEYBOARD_LAYOUT", "MONKEY_PATCH", "HAL0_PORT"],
    )
    def test_non_secret_key_words_stay_clear(self, name):
        assert is_sensitive_key(name) is False


class TestCamelAndRunTogetherKeyNames:
    """#2384: ``apikey``, ``apiKey``, ``accessKey`` and friends are secrets
    too; the KEY words match with or without a ``_``/``-`` separator."""

    @pytest.mark.parametrize(
        "name",
        [
            "apikey",
            "apiKey",
            "APIKEY",
            "api-key",
            "x-api-key",
            "accessKey",
            "access_key",
            "AWS_SECRET_ACCESS_KEY",
            "accessToken",
            "privateKey",
            "encryptionKey",
            "clientSecret",
            "passwd",
        ],
    )
    def test_run_together_secret_names_are_sensitive(self, name):
        assert is_sensitive_key(name) is True

    @pytest.mark.parametrize(
        "name",
        ["keyboard", "monkey", "KEYBOARD_LAYOUT", "MONKEY_PATCH", "hotkey", "keys", "api_base"],
    )
    def test_key_lookalikes_stay_clear(self, name):
        assert is_sensitive_key(name) is False


# ── #2409: the shared shareable-text redactor (failure-report shape set) ────


class TestShareableTextShapes:
    """``redact_shareable_text`` ports installer/lib/failure-report.sh's
    pattern pass and literal harvest; one case per shape."""

    @pytest.mark.parametrize(
        ("line", "secret"),
        [
            ("git clone https://user:urlpw_Rr44Ee55@example.com/r.git", "urlpw_Rr44Ee55"),
            ("Authorization: Basic dXNlcjpodW50ZXIy", "dXNlcjpodW50ZXIy"),
            ("Authorization: token ghtok_Aa11Bb22Cc33", "ghtok_Aa11Bb22Cc33"),
            ("curl -H 'Authorization: Bearer brr_Zz99Yy88Xx77'", "brr_Zz99Yy88Xx77"),
            ("hf download --token flagtok_Ww12Qq34 m", "flagtok_Ww12Qq34"),
            ("hf download --token=flagtok_Ee56Rr78 m", "flagtok_Ee56Rr78"),
            ("loaded hf_" + "a" * 30, "hf_" + "a" * 30),
            ("using sk-" + "b" * 30, "sk-" + "b" * 30),
            ("pat ghp_" + "c" * 36, "ghp_" + "c" * 36),
            ("key hal0bk_cc." + "d" * 43, "d" * 43),
            (
                "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJoYWwwIn0.c2lnbmF0dXJlMTIzNDU2",
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJoYWwwIn0.c2lnbmF0dXJlMTIzNDU2",
            ),
            ("aws AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
            ('{"token": "JsonTok_88bbccdd"}', "JsonTok_88bbccdd"),
            ("registry login password: Colon_Secret_77aa", "Colon_Secret_77aa"),
            ("X-Api-Key: HdrK3y_55eeff00", "HdrK3y_55eeff00"),
            ("Environment=HAL0_SECRET=sysd_Qq12Ww34Ee56", "sysd_Qq12Ww34Ee56"),
            ("HF_TOKEN=hf_short1", "hf_short1"),
            ("GET https://x.invalid/v1?apikey=abcd1234efgh&q=1", "abcd1234efgh"),
            ('{"apiKey": "abcd1234efgh"}', "abcd1234efgh"),
            ("export HAL0_CLIENT_KEY='h0c_quoted_Kk11'", "h0c_quoted_Kk11"),
            ("mcp client_id=abcdefghijklmnopqrstuvwxyz0123", "abcdefghijklmnopqrstuvwxyz0123"),
        ],
    )
    def test_each_shape_is_masked(self, line: str, secret: str) -> None:
        out = redact_shareable_text(line)
        assert secret not in out, out
        assert MASK in out

    def test_a_secret_learned_on_one_line_is_masked_where_it_reappears(self) -> None:
        text = (
            "export UPSTREAM_TOKEN=Zq8vR2mW9xK4tL7pQ3\n"
            '{"password": "Colon_Secret_77aa"}\n'
            "retry https://example.invalid/hook?t=Zq8vR2mW9xK4tL7pQ3\n"
            "later reused bare: Colon_Secret_77aa end\n"
        )
        out = redact_shareable_text(text)
        assert "Zq8vR2mW9xK4tL7pQ3" not in out
        assert "Colon_Secret_77aa" not in out
        assert "later reused bare: ***REDACTED*** end" in out

    @pytest.mark.parametrize(
        "line",
        [
            "llama: max_tokens=4096 ctx=8192",
            '{"max_tokens": 4096, "tokenizer": "Qwen/Qwen2.5-7B-Instruct"}',
            "load tokenizer=Qwen/Qwen2.5-7B-Instruct",
            "token_count=123456789",
            "layout keyboard: us",
            "zoo monkey=bananaphone99",
            "api_key_env=HF_TOKEN_FILE",
            "12 tests passed: 0 failed",
            "KEY_ROTATION_DAYS=30",
            "mcp client_id=1a2b3c4d5e6f",
        ],
    )
    def test_lookalikes_survive(self, line: str) -> None:
        assert redact_shareable_text(line) == line

    def test_lookalike_values_are_not_masked_elsewhere(self) -> None:
        text = (
            "provider.credential_written key=OPENAI_API_KEY\n"
            "load tokenizer=Qwen/Qwen2.5-7B-Instruct max_tokens=40960000\n"
            "set OPENAI_API_KEY; model Qwen/Qwen2.5-7B-Instruct; budget 40960000\n"
        )
        out = redact_shareable_text(text)
        assert "set OPENAI_API_KEY; model Qwen/Qwen2.5-7B-Instruct; budget 40960000" in out

    def test_it_is_idempotent(self) -> None:
        text = "HF_TOKEN=hf_" + "a" * 30 + "\ngit clone https://u:pw12345678@h/r\n"
        once = redact_shareable_text(text)
        assert redact_shareable_text(once) == once


# ── #2403: redact_log_line uses the shared shape pass ──────────────────────


class TestLogLineSharedShapes:
    @pytest.mark.parametrize(
        ("line", "secret"),
        [
            ("hal0.startup HF_TOKEN=hf_abcdefghijklmnop", "hf_abcdefghijklmnop"),
            ("GET /v1?apikey=abcd1234efgh HTTP/1.1", "abcd1234efgh"),
            ('{"event": "upstream", "apiKey": "abcd1234efgh"}', "abcd1234efgh"),
            ('{"accessToken": "abcd1234efgh"}', "abcd1234efgh"),
            ("db password: Colon_Secret_77aa", "Colon_Secret_77aa"),
            ("clone https://user:urlpw_Rr44Ee55@example.com/r.git", "urlpw_Rr44Ee55"),
        ],
    )
    def test_shared_shapes_are_masked(self, line: str, secret: str) -> None:
        out = redact_log_line(line)
        assert secret not in out, out
        assert MASK in out

    @pytest.mark.parametrize(
        "line",
        [
            "llama.request max_tokens=4096 temperature=0.7",
            "slot.load tokenizer=Qwen/Qwen2.5-7B-Instruct",
            '{"usage": {"total_tokens": 1234, "token_count": 99}}',
            "ui.prefs keyboard: us",
            "pytest: 12 passed: 0 failed",
            "HF_TOKEN_FILE=/run/secrets/hf",
        ],
    )
    def test_live_log_lookalikes_survive(self, line: str) -> None:
        assert redact_log_line(line) == line


# ── #2410: a scheme-less Authorization header value ────────────────────────


class TestSchemelessAuthorization:
    @pytest.mark.parametrize(
        "line",
        [
            "Authorization: rawauth_Pl34Ok56Ij78",
            "curl -H 'Authorization: rawauth_Pl34Ok56Ij78' https://x.invalid/",
            '{"Authorization": "rawauth_Pl34Ok56Ij78"}',
            "proxy-authorization: rawauth_Pl34Ok56Ij78",
        ],
    )
    def test_a_raw_header_value_is_masked(self, line: str) -> None:
        for redact in (redact_shareable_text, redact_log_line):
            out = redact(line)
            assert "rawauth_Pl34Ok56Ij78" not in out, out
            assert MASK in out

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("Authorization: Bearer abcdef123456", "Authorization: Bearer ***REDACTED***"),
            ("Authorization: Basic dXNlcjpwYXNz", "Authorization: Basic ***REDACTED***"),
            ("authorization: denied", "authorization: denied"),
            ("Authorization: ApiKey ak_live_Zx81Qw45", "Authorization: ApiKey ***REDACTED***"),
            ("Authorization: Bot MTk4NjIyNDgzNDcx", "Authorization: Bot ***REDACTED***"),
            ("Authorization: SSWS 00aBcD1234efGh", "Authorization: SSWS ***REDACTED***"),
            ("Authorization: Negotiate YIIC4wYGKwYB", "Authorization: Negotiate ***REDACTED***"),
            ("authorization: denied for bob", "authorization: denied for bob"),
            ("Authorization: abcdefghijklmnop qrstuvwxyz", "Authorization: ***REDACTED***"),
            ("authorization: required for user", "authorization: ***REDACTED***"),
        ],
    )
    def test_a_scheme_is_kept_and_short_words_survive(self, line: str, expected: str) -> None:
        assert redact_shareable_text(line) == expected
        assert redact_log_line(line) == expected
        assert redact_shareable_text(expected) == expected  # idempotent


# ── #2466: plural ``tokens`` is a benign count only with a count qualifier ──


class TestPluralTokenNames:
    _VALUE = "Plural_Tok3n_99xyzw"

    @pytest.mark.parametrize(
        "name",
        [
            "api_tokens",
            "auth_tokens",
            "tokens_by_host",
            "tokens",
            "authTokens",
            # ALL-CAPS: a capital is not a word end unless it starts a hump.
            "API_TOKENS_PER_SERVICE",
            "API_TOKENS_PER_KEY",
            "GITHUB_TOKENS_PER_SITE",
            "HF_TOKENS_PER_SPACE",
            # A weak qualifier only counts at the start of the name or after
            # a count word, not after a credential prefix.
            "github_new_tokens",
            "oauth_cached_tokens",
            "mcp_tool_tokens",
            "api_tokens_per_token",
            "apiTokenSCount",
            # #2488: draft/content/thinking/generation are weak qualifiers.
            "cms_draft_tokens",
            "api_content_tokens",
            "api_thinking_tokens",
            "hf_generation_tokens",
            "mixed_content_tool_tokens",
        ],
    )
    def test_an_unqualified_tokens_name_is_masked(self, name: str) -> None:
        from hal0.redaction import redact_secret_named_values

        line = f"{name}={self._VALUE}"
        for redact in (redact_shareable_text, redact_log_line):
            out = redact(line)
            assert self._VALUE not in out, (redact.__name__, out)
            assert MASK in out
        assert redact_secret_named_values({name: self._VALUE}) == {name: MASK}

    def test_an_unqualified_tokens_value_is_masked_where_it_reappears(self) -> None:
        out = redact_shareable_text(f"api_tokens={self._VALUE}\nlater bare: {self._VALUE} end\n")
        assert self._VALUE not in out
        assert "later bare: ***REDACTED*** end" in out

    def test_a_hyphenated_all_caps_name_is_judged_whole(self) -> None:
        line = f"API-TOKENS-PER-SECRET={self._VALUE}"
        for redact in (redact_shareable_text, redact_log_line):
            assert self._VALUE not in redact(line), redact.__name__

    @pytest.mark.parametrize(
        "name",
        [
            "max_tokens",
            "extraction_max_tokens",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "n_prompt_tokens",
            "max_new_tokens",
            "max_completion_tokens",
            "tokens_per_sec",
            "output_tokens_per_second",
            "tokens_count",
            "tokens_in",
            "tokens_completed",
            "text_tokens",
            "image_tokens",
            "tool_call_tokens",
            "video_tokens",
            "tool_response_tokens",
            "tokens_per_iteration",
            "cached_tokens",
            "cache_tokens",
            "prompt_cached_tokens",
            "new_tokens",
            "tokens_predicted",
            "tokens_evaluated",
            "accepted_prediction_tokens",
            "HAL0_MAX_TOKENS",
            "HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS",
            "OUTPUT_TOKENS_PER_SECOND",
            "maxTokens",
            "totalTokens",
            "max-tokens",
            "tokenizer",
            "token_count",
        ],
    )
    def test_a_count_qualified_tokens_name_is_left_alone(self, name: str) -> None:
        from hal0.redaction import redact_secret_named_values

        line = f"{name}={self._VALUE}"
        assert redact_shareable_text(line) == line
        assert redact_log_line(line) == line
        assert redact_secret_named_values({name: 4096}) == {name: 4096}

    @pytest.mark.parametrize(
        "name",
        [
            "login_tokens",
            "max_tokens_secret",
            "tokens_in_vault",
            "api_tokens_count_key",
            "api_tokens_in",
            "auth_tokens_out",
            "api_tokens_per_host",
            "auth_tokens_per_user",
        ],
    )
    def test_a_qualifier_does_not_hide_another_secret_word(self, name: str) -> None:
        line = f"{name}={self._VALUE}"
        assert self._VALUE not in redact_shareable_text(line)

    @pytest.mark.parametrize(
        "name",
        ["secretEnv", "clientSecretEnv", "tokenEnv", "passwordFile", "privateKeyPath", "TokenFile"],
    )
    def test_a_camel_case_location_name_stays_masked(self, name: str) -> None:
        """Only ``_env``/``_file``/``_path``/``_dir`` mark a location: a
        Helm-style ``secretEnv`` is a map of secret values, not a reference."""
        from hal0.redaction import redact_secret_named_values

        assert redact_secret_named_values({name: {"OPENAI": self._VALUE}}) == {name: MASK}


# ── #2488: a secret name that continues with a hyphen ──────────────────────


class TestHyphenatedSecretNames:
    _VALUE = "Zq8vR2mW9xK4tL7pQ3"

    @pytest.mark.parametrize(
        "name",
        [
            "api-token-prod",
            "github-token-ci",
            "auth-tokens-per-user",
            "x-api-key-v2",
            "HF-TOKEN-READ",
            "db-password-primary",
            "client-secret-staging",
            "my-key",
        ],
    )
    def test_a_hyphenated_secret_name_is_masked(self, name: str) -> None:
        from hal0.redaction import redact_secret_named_values

        v = self._VALUE
        for line in (f"{name}={v}", f"{name}: {v}", f'{{"{name}": "{v}"}}', f"--{name}={v}"):
            for redact in (redact_shareable_text, redact_log_line):
                out = redact(line)
                assert v not in out, (redact.__name__, line, out)
                # The name is kept so a reader sees a secret was present.
                assert name in out, out
        assert redact_secret_named_values({name: self._VALUE}) == {name: MASK}

    def test_a_hyphenated_secret_value_is_masked_where_it_reappears(self) -> None:
        out = redact_shareable_text(f"api-token-prod={self._VALUE}\nlater: {self._VALUE} end\n")
        assert self._VALUE not in out
        assert "later: ***REDACTED*** end" in out

    @pytest.mark.parametrize(
        "name",
        [
            "max-tokens",
            "api-token-file",
            "api-token-path",
            "hf-token-env",
            "client-secret-dir",
            "max-completion-tokens",
            "tokens-per-sec",
            "max-thinking-tokens",
        ],
    )
    def test_a_hyphenated_count_or_location_name_is_left_alone(self, name: str) -> None:
        from hal0.redaction import redact_secret_named_values

        line = f"{name}={self._VALUE}"
        assert redact_shareable_text(line) == line
        assert redact_log_line(line) == line
        assert redact_secret_named_values({name: 4096}) == {name: 4096}

    @pytest.mark.parametrize(
        "name",
        [
            "draft_tokens",
            "content_tokens",
            "thinking_tokens",
            "generation_tokens",
            "max_thinking_tokens",
            "max_draft_tokens",
            "n_generation_tokens",
            "maxThinkingTokens",
        ],
    )
    def test_a_weak_qualifier_at_the_start_or_after_a_count_word_is_a_count(
        self, name: str
    ) -> None:
        line = f"{name}={self._VALUE}"
        assert redact_shareable_text(line) == line
        assert redact_log_line(line) == line

    def test_strip_is_judged_at_the_match_position(self) -> None:
        """``max_tokensx`` is not a count word; the later ``max_tokens`` is.
        Only the later one is removed, leaving ``tokensx``, a secret word."""
        from hal0.redaction import redact_secret_named_values

        name = "a_max_tokensx_max_tokens"
        assert redact_secret_named_values({name: self._VALUE}) == {name: MASK}

    def test_a_long_name_is_judged_as_written(self) -> None:
        """Past 128 characters no count part is removed, as in the installer:
        such a name is masked whenever it carries a secret word."""
        from hal0.redaction import redact_secret_named_values

        name = "max_tokens_" * 20
        assert redact_secret_named_values({name: self._VALUE}) == {name: MASK}
        assert redact_secret_named_values({"max_tokens": 1}) == {"max_tokens": 1}


class TestShareableLiteralsSinglePass:
    """#2533: harvested secrets are masked in one scan, with the same result
    as one ``str.replace`` per secret, longest first."""

    @staticmethod
    def _loop(text: str, literals: list[str]) -> str:
        for literal in literals:
            text = text.replace(literal, MASK)
        return text

    @pytest.mark.parametrize(
        ("text", "literals"),
        [
            # A longer literal wins over a shorter one that starts earlier.
            ("aaaaaaaaBBBBBBBB", ["aaaaaaaBBBBBBBB", "aaaaaaaaB"]),
            # A substring is masked only where its container is not.
            ("x=abcdefghij y=cdefghij z", ["abcdefghij", "cdefghij"]),
            # Overlapping occurrences of one literal: left to right.
            ("aaaaaaaaaaaaaaaaaaaaa", ["aaaaaaaa"]),
            # Equal length, overlapping: list order decides.
            ("xabcdefgh", ["xabcdefg", "abcdefgh"]),
            ("xabcdefgh", ["abcdefgh", "xabcdefg"]),
            # A repeated literal masks nothing more.
            ("s3cr3tvalue s3cr3tvalue", ["s3cr3tvalue", "s3cr3tvalue"]),
            # Literals that could match a MASK take the loop itself.
            ("abc***x abc", ["abc*", "abc***x", "***x"]),
            ("vD***REDACTED***x", ["vD", "D***R"]),
            ("a\nb.c(d)[e]^$", ["\nb.c(", "(d)[e]^$"]),
        ],
    )
    def test_matches_one_replace_per_literal(self, text: str, literals: list[str]) -> None:
        from hal0.redaction import _mask_literals

        assert _mask_literals(text, literals) == self._loop(text, literals)

    def test_a_longer_literal_wins_over_an_earlier_shorter_one(self) -> None:
        from hal0.redaction import _mask_literals

        out = _mask_literals("aaaaaaaaBBBBBBBB", ["aaaaaaaBBBBBBBB", "aaaaaaaaB"])
        assert out == f"a{MASK}"

    @pytest.mark.parametrize("value", ["val{:07d}", "v*{:07d}"])
    @pytest.mark.timing
    def test_800kb_of_distinct_secrets_is_fast(self, value: str) -> None:
        """18 s on main for the plain form; a ``*`` inside a value that
        cannot touch a MASK keeps the single pass. Each value also appears
        bare on its own line, which only the harvested-literal pass masks,
        so the output proves that pass ran over every value.

        The fix runs in about 1.2 s on a dev box and about 5 s on a loaded
        PR runner (xdist workers share its cores), so the bound sits at 10 s:
        the old per-literal loop's 18 s still fails it."""
        import time

        text = "\n".join(f"token={value.format(i)}\n{value.format(i)}" for i in range(45_000))
        assert len(text) > 700_000
        start = time.perf_counter()
        out = redact_shareable_text(text)
        elapsed = time.perf_counter() - start
        assert elapsed < 10.0, f"{len(text)} chars took {elapsed:.2f}s"
        assert out == "\n".join([f"token={MASK}\n{MASK}"] * 45_000)

    def test_differential_against_the_loop(self) -> None:
        """Seeded random literal sets over tiny alphabets (overlaps,
        substrings, repeats, ties, MASK fragments, regex metacharacters):
        the single pass equals the loop and leaves no literal behind that
        the loop would have masked."""
        import random

        from hal0.redaction import _mask_literals

        rng = random.Random(2533)
        alphabets = ["ab", "abc", "aB*", "a*RED", "xy\n.", "é€a", "*R", "a(b)[", "\\a"]
        for _ in range(20_000):
            alphabet = rng.choice(alphabets)
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 50)))
            literals = []
            for _ in range(rng.randint(1, 8)):
                if text and rng.random() < 0.7:
                    i = rng.randint(0, len(text) - 1)
                    literals.append(text[i : rng.randint(i + 1, min(len(text), i + 12))])
                else:
                    literals.append("".join(rng.choice(alphabet) for _ in range(rng.randint(1, 9))))
            if rng.random() < 0.5:
                literals.sort(key=len, reverse=True)
            assert _mask_literals(text, literals) == self._loop(text, literals), (text, literals)

    @pytest.mark.parametrize(
        "text",
        [
            # Glued units: one harvested value as long as the line.
            "X_KEY=a." * 60_000,
            # A long quoted value that never closes, plain and with an escape
            # at every other character.
            'password="' + "a" * 120_000,
            'password="' + "\\a" * 120_000,
            # A periodic value with a longer period.
            "X_KEY=" + "abcdefghijklmnop" * 30_000,
        ],
        ids=["glued", "unclosed", "unclosed-escapes", "periodic"],
    )
    @pytest.mark.timing
    def test_a_long_secret_in_periodic_text_is_masked_in_linear_time(self, text: str) -> None:
        """#2600: the scan for a harvested literal retried it at every position,
        and in text that repeats the literal's own prefix each try ran far into
        it: 4.5 s for 320 KB of ``X_KEY=a.`` and 4.7 s for 120 KB of ``a`` in an
        open quote on main. Linear, they take about 0.1 s on a dev box (the
        bound leaves 10x for a loaded runner); the old scan fails it."""
        import time

        start = time.perf_counter()
        out = redact_shareable_text(text)
        elapsed = time.perf_counter() - start
        assert elapsed < 3.0, f"{len(text)} chars took {elapsed:.2f}s"
        assert MASK in out

    def test_past_the_budget_the_longest_literals_still_take_the_replace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only as many long literals as the budget allows are replaced, and they
        are the longest ones: the periodic one that makes the scan quadratic is
        not left to it because thousands of others do not fit (#2600)."""
        import hal0.redaction as redaction

        seen: list[list[str]] = []
        real = redaction._replace_each

        def spy(text: str, literals: list[str]) -> str:
            seen.append(list(literals))
            return real(text, literals)

        monkeypatch.setattr(redaction, "_replace_each", spy)
        longest = "ab" * 200
        others = [f"{i:04d}" + "x" * 150 for i in range(50)]
        text = "\n".join([longest, *others, longest])
        monkeypatch.setattr(redaction, "_LITERAL_REPLACE_BUDGET", 2 * len(text))
        out = redaction._mask_literals(text, [longest, *others])
        assert seen[0] == [longest, others[0]]
        assert out == self._loop(text, [longest, *others])

    @pytest.mark.parametrize(
        ("longest", "budget"),
        [(1, 10**9), (2, 10**9), (3, 10**9), (5, 10**9), (2, 0), (3, 40), (2, 100), (1, 150)],
    )
    def test_the_longest_literals_first_run_the_loop_with_the_same_result(
        self, longest: int, budget: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Literals past ``_LITERAL_SCAN_MAX`` characters are masked one
        ``str.replace`` at a time, ahead of the scan, while their number times
        the text's length is within ``_LITERAL_REPLACE_BUDGET`` (#2600). Lowering
        the limits runs the differential on both sides of them."""
        import random

        import hal0.redaction as redaction
        from hal0.redaction import _mask_literals

        monkeypatch.setattr(redaction, "_LITERAL_SCAN_MAX", longest)
        monkeypatch.setattr(redaction, "_LITERAL_REPLACE_BUDGET", budget)
        rng = random.Random(2600 + longest)
        alphabets = ["ab", "abc", "aB*", "a*RED", "xy\n.", "é€a", "a(b)[", "\\a"]
        for _ in range(8_000):
            alphabet = rng.choice(alphabets)
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 50)))
            literals = []
            for _ in range(rng.randint(1, 8)):
                if text and rng.random() < 0.7:
                    i = rng.randint(0, len(text) - 1)
                    literals.append(text[i : rng.randint(i + 1, min(len(text), i + 12))])
                else:
                    literals.append("".join(rng.choice(alphabet) for _ in range(rng.randint(1, 9))))
            if rng.random() < 0.7:
                literals.sort(key=len, reverse=True)
            assert _mask_literals(text, literals) == self._loop(text, literals), (text, literals)

    @pytest.mark.parametrize(
        ("setting", "value"),
        [("_LITERAL_TRIE_MAX_NESTING", 0), ("_LITERAL_OCCURRENCES_PER_CHAR_MAX", 0)],
    )
    def test_fallbacks_still_mask_every_secret(
        self, setting: str, value: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Past the nesting or occurrence cap the exact loop runs, so every
        harvested secret is still masked, including bare reoccurrences."""
        import hal0.redaction as redaction

        monkeypatch.setattr(redaction, setting, value)
        secrets = ["hunter2hunter2", "hunter2hunter2xyz", "s3cr3t-value-abc"]
        text = "\n".join(f"password={s}\nechoed {s} and {s}." for s in secrets)
        out = redact_shareable_text(text)
        assert not any(s in out for s in secrets), out
        assert out == self._loop(text, sorted(secrets, key=len, reverse=True))

    def test_uncompilable_scan_falls_back_to_the_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If ``re`` cannot compile the trie pattern, the loop masks
        instead: a compile failure never costs a secret its mask."""
        import re

        import hal0.redaction as redaction

        real_compile = re.compile

        def failing_compile(pattern: object, *args: object, **kwargs: object) -> object:
            if isinstance(pattern, str) and pattern.startswith("(?=("):
                raise RecursionError("maximum recursion depth exceeded")
            return real_compile(pattern, *args, **kwargs)  # type: ignore[call-overload]

        monkeypatch.setattr(redaction.re, "compile", failing_compile)
        text = "api_key=abcdefgh12345 then abcdefgh12345 again"
        out = redact_shareable_text(text)
        assert "abcdefgh12345" not in out
        assert out == f"api_key={MASK} then {MASK} again"


class TestRedactionTimeIsLinear:
    """#2501: a long run of name characters is redacted in linear time.

    On v1.4.0-rc ``main`` 80 KB of ``api_token_`` took about 21 s, the
    hyphenated form about 50 s, and 8 KB of ``--token-x-`` about 20 s (the
    ``--flag`` rule was cubic), while journald passes lines of up to about
    48 KB that a client can partly control. Each line here takes well under
    0.1 s with the fix; the 1 s bound only leaves room for a slow CI box.
    """

    _BOUND_S = 1.0

    @pytest.mark.parametrize(
        ("unit", "length"),
        [
            ("api_token_", 80_000),  # name continuation, underscore form
            ("api-token-", 80_000),  # hyphen form, also the URL scheme rule
            ("aaaaaaaaaa", 80_000),  # URL scheme rule alone
            ("eyJ", 80_000),  # JWT rule
            ("-", 80_000),  # ``--flag`` rule, no secret word
            ("--token-x-", 8_000),  # ``--flag`` rule, a word at every start
            ("\u0131a", 80_000),  # a name run of letters a name may start at (#2599)
            ("\u0131api_key=,x ", 40_000),  # a folded-letter name at every start
        ],
    )
    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    @pytest.mark.timing
    def test_a_worst_case_line_is_fast(self, redact, unit: str, length: int) -> None:
        import time

        line = (unit * (length // len(unit) + 1))[:length]
        start = time.perf_counter()
        redact(line)
        elapsed = time.perf_counter() - start
        assert elapsed < self._BOUND_S, f"{len(line)} chars of {unit!r} took {elapsed:.2f}s"

    def test_a_secret_after_a_long_run_is_still_masked(self) -> None:
        """The fast rules start at a run's start; a secret at its end still
        counts, with what comes before it kept."""
        run = "x" * 50_000
        line = (
            f"{run}--api-key=hunter2hunter2 {run}+https://u:pw@h "
            f"{run}eyJaaaaaaaaaaaa.bbbbbbbbbbbb.cccccccccccc api-token-{'v' * 50_000}=s3cr3tvalue"
        )
        out = redact_log_line(line)
        assert f"{run}--api-key={MASK} " in out
        assert f"{run}+https://u:{MASK}@h " in out
        assert f" {run}{MASK} " in out
        assert out.endswith(f"v={MASK}")
        assert "hunter2" not in out and "s3cr3t" not in out and ":pw@" not in out


# ── #2529: a second secret packed after ``;`` or ``,`` ──────────────────────


class TestPackedSecrets:
    """#2529: a bare value used to run to the next whitespace, so a second
    secret packed after it with ``;`` or ``,`` lost its name to the first
    mask, and its own value, after a space, was left visible."""

    @pytest.mark.parametrize(
        ("line", "secrets"),
        [
            # The two lines from the issue.
            ("X_KEY=pk_first_1;Bearer pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("HAL0_BEARER_TOKEN=pk_first_1,HF_TOKEN= pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("token=pk_first_1;password=pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("api_key=pk_first_1,secret=pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("token=pk_first_1;HF_TOKEN= pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("X_KEY=pk_first_1;HF_TOKEN = pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("X_KEY=pk_first_1;password: pk_second_2", ["pk_first_1", "pk_second_2"]),
            ('X_KEY=pk_first_1;"HF_TOKEN": "pk second 2"', ["pk_first_1", "second"]),
            ("HF_TOKEN=pk_first_1;Bearer pk_second_2", ["pk_first_1", "pk_second_2"]),
            # A benign first name used to swallow the second secret's name too.
            ("max_tokens=5;HF_TOKEN= pk_second_2", ["pk_second_2"]),
            ("hf_token_file=/run/x;HF_TOKEN= pk_second_2", ["pk_second_2"]),
            # Authorization header values.
            (
                "Authorization: Bearer pk_first_1;HF_TOKEN= pk_second_2",
                ["pk_first_1", "pk_second_2"],
            ),
            (
                "Authorization: Basic pk_first_1;HF_TOKEN= pk_second_2",
                ["pk_first_1", "pk_second_2"],
            ),
            (
                "Authorization: Basic pk_first_1,HF_TOKEN= pk_second_2",
                ["pk_first_1", "pk_second_2"],
            ),
            (
                "Authorization: Token pk_first_1;password: pk_second_2",
                ["pk_first_1", "pk_second_2"],
            ),
            ("X_KEY=pk_first_1,Authorization: Basic pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("X_KEY=pk_first_1;Authorization: pk_second_2_raw", ["pk_first_1", "pk_second_2"]),
            # ``--flag`` values.
            ("--token=pk_first_1;--password pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("--token=pk_first_1;HF_TOKEN= pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("X_KEY=pk_first_1;--password pk_second_2", ["pk_first_1", "pk_second_2"]),
            # Three in a row.
            (
                "a_key=pk_first_1;b_token= pk_second_2,Bearer pk_third_3",
                ["pk_first_1", "pk_second_2", "pk_third_3"],
            ),
        ],
    )
    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    def test_every_packed_secret_is_masked(self, redact, line: str, secrets: list[str]) -> None:
        out = redact(line)
        for secret in secrets:
            assert secret not in out, (line, out)
        assert redact(out) == out  # idempotent

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("X_KEY=abc;Bearer tok", f"X_KEY={MASK};Bearer {MASK}"),
            ("HAL0_BEARER_TOKEN=x,HF_TOKEN= hf_v", f"HAL0_BEARER_TOKEN={MASK},HF_TOKEN= {MASK}"),
            ("token=a;password=b", f"token={MASK};password={MASK}"),
            ("max_tokens=5;HF_TOKEN= hf_v", f"max_tokens=5;HF_TOKEN= {MASK}"),
        ],
    )
    def test_each_packed_secret_keeps_its_own_name(self, line: str, expected: str) -> None:
        assert redact_log_line(line) == expected

    @pytest.mark.parametrize(
        "line",
        [
            # What follows the ``;`` is no secret, so it may be part of the
            # first value: it stays masked with it.
            "X_KEY=pk;first;1",
            "X_KEY=pk_first_1;max_tokens=pk_more_2",
            "X_KEY=pk_first_1;tokenizer=pk_more_2",
            "X_KEY=pk_first_1,Bearer",
            "X_KEY=pk_first_1;HF_TOKEN=",
        ],
    )
    def test_a_value_runs_on_over_a_separator_that_starts_no_secret(self, line: str) -> None:
        assert redact_log_line(line) == f"X_KEY={MASK}"

    def test_an_empty_value_before_a_packed_secret(self) -> None:
        assert redact_log_line("X_KEY=;HF_TOKEN= hf_v") == f"X_KEY=;HF_TOKEN= {MASK}"

    def test_the_shareable_harvest_stops_at_a_packed_secret(self) -> None:
        """The literal harvest reads the same values: ``abc;Bearer`` is not
        one literal that, masked, hides the bearer token's prefix."""
        out = redact_shareable_text("HF_TOKEN=abcdefgh12;Bearer tok_Zz12\nlater abcdefgh12\n")
        assert out == f"HF_TOKEN={MASK};Bearer {MASK}\nlater {MASK}\n"

    @pytest.mark.parametrize(
        "unit", ["x_key=a;", "token=a;", "a;Bearer ", "--token=a;", "Authorization: Basic a;"]
    )
    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    @pytest.mark.timing
    def test_a_long_packed_run_is_fast(self, redact, unit: str) -> None:
        import time

        line = unit * (80_000 // len(unit))
        start = time.perf_counter()
        redact(line)
        elapsed = time.perf_counter() - start
        assert elapsed < TestRedactionTimeIsLinear._BOUND_S, f"{unit!r} took {elapsed:.2f}s"


# ── #2594: an empty value before a second secret ────────────────────────────


class TestEmptyValueBeforeASecret:
    #: Wall-clock bound for the long-run test below. The empty-value run costs
    #: about 1 s on a loaded PR runner (1.01 s twice on 2026-10-14/15, #2677),
    #: so borrowing TestRedactionTimeIsLinear's 1 s bound made unrelated PRs
    #: red. 3x headroom keeps the linear-time property asserted (a quadratic
    #: scan of 80 KB took tens of seconds, #2600) without flaking.
    _BOUND_S = 3.0

    """#2594: ``NAME=`` with an empty value, then whitespace, took the next
    word as its value. When that word was another secret's name, the name was
    masked as the first value and the second value was left visible."""

    @pytest.mark.parametrize(
        ("line", "secrets"),
        [
            # The line from the issue.
            ("KEY=  X_KEY= pk_second_2", ["pk_second_2"]),
            ("HF_TOKEN=  password: pk_second_2", ["pk_second_2"]),
            ("X_KEY= Y_KEY= pk_second_2", ["pk_second_2"]),
            ("api_key=\tsecret= pk_second_2", ["pk_second_2"]),
            ("password: HF_TOKEN= pk_second_2", ["pk_second_2"]),
            ('password: HF_TOKEN= "pk second 2"', ["pk", "second"]),
            ("token= Bearer pk_second_2", ["pk_second_2"]),
            ("token= --password pk_second_2", ["pk_second_2"]),
            ("token= Authorization: Basic pk_second_2", ["pk_second_2"]),
            ("Authorization: Basic X_KEY= pk_second_2", ["pk_second_2"]),
            ("--token X_KEY= pk_second_2", ["pk_second_2"]),
            # Bytes glued to the second name may be the first value: masked.
            ("KEY= pk_first_1X_KEY= pk_second_2", ["pk_first_1", "pk_second_2"]),
            # A benign first name took the second secret's name too.
            ("max_tokens= HF_TOKEN= pk_second_2", ["pk_second_2"]),
            # A chain of empty values.
            ("A_KEY= B_KEY= C_KEY= pk_third_3", ["pk_third_3"]),
            # The env-dump shape: one assignment per line.
            ("HF_TOKEN=\nX_KEY= pk_second_2", ["pk_second_2"]),
        ],
    )
    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    def test_the_second_secret_is_masked(self, redact, line: str, secrets: list[str]) -> None:
        out = redact(line)
        for secret in secrets:
            assert secret not in out, (line, out)
        assert redact(out) == out  # idempotent

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            # The redactor cannot tell where the second name begins, so the
            # first mask runs on over the second value (fail closed).
            ("KEY=  X_KEY= v", f"KEY=  {MASK}"),
            ("HF_TOKEN=  password: pw", f"HF_TOKEN=  {MASK}"),
            ("A_KEY= B_KEY= C_KEY= v", f"A_KEY= {MASK}"),
            ("X_KEY= Y_KEY= v w", f"X_KEY= {MASK} w"),
            ('token= HF_TOKEN= "v w" x', f"token= {MASK} x"),
            # A benign value is not masked: it ends before the secret instead.
            ("max_tokens= HF_TOKEN= v", f"max_tokens= HF_TOKEN= {MASK}"),
            ("max_tokens= 5", "max_tokens= 5"),
        ],
    )
    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    def test_the_rendering(self, redact, line: str, expected: str) -> None:
        assert redact(line) == expected

    @pytest.mark.parametrize(
        "line",
        [
            # The next word starts no secret, so it may be the value: masked.
            "token= abc",
            "token= max_tokens=5",
            "token= tokenizer=x",
            "token= HF_TOKEN=",
            "token= Bearer",
        ],
    )
    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    def test_a_word_that_starts_no_secret_is_still_the_value(self, redact, line: str) -> None:
        assert redact(line) == f"token= {MASK}"

    def test_the_shareable_harvest_takes_the_swallowed_value(self) -> None:
        """The literal harvest reads the same values, and also takes the
        swallowed second value on its own, so it is masked where it appears
        bare."""
        out = redact_shareable_text("KEY=  X_KEY= abcdefgh12\nlater abcdefgh12 X_KEY=\n")
        assert out == f"KEY=  {MASK}\nlater {MASK} X_KEY=\n"

    def test_the_shareable_harvest_takes_no_name_as_a_value(self) -> None:
        """``api_key=`` is not harvested as ``Y_KEY``'s value: masked where it
        appears, it would hide that name and leave a short value in clear."""
        out = redact_shareable_text("Y_KEY=   api_key= \nlater api_key= sh0rt\n")
        assert out == f"Y_KEY=   {MASK} \nlater api_key= {MASK}\n"

    @pytest.mark.parametrize("unit", ["x_key= ", "token= ", "a_key=  "])
    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    @pytest.mark.timing
    def test_a_long_run_of_empty_values_is_fast(self, redact, unit: str) -> None:
        import time

        line = unit * (80_000 // len(unit)) + "v"
        start = time.perf_counter()
        out = redact(line)
        elapsed = time.perf_counter() - start
        assert out.endswith(MASK)
        assert elapsed < self._BOUND_S, f"{unit!r} took {elapsed:.2f}s"


# ── #2595: secrets glued together with no separator ─────────────────────────


class TestGluedSecrets:
    """#2595: with no whitespace, ``;`` or ``,`` between two secrets, the
    first value ran on over the second secret's name, and the second value
    was left visible."""

    @pytest.mark.parametrize(
        ("line", "secrets"),
        [
            # The line from the issue.
            ("Bearer pk_first_1.Authorization: Token pk_second_2", ["pk_first_1", "pk_second_2"]),
            # Concatenated headers: the name is glued to the first value.
            (
                "Authorization: Bearer pk_first_1X-Api-Key: pk_second_2",
                ["pk_first_1", "pk_second_2"],
            ),
            ("X_KEY=pk_first_1Bearer pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("X_KEY=pk_first_1.HF_TOKEN= pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("token=pk_first_1.HF_TOKEN= pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("token=pk_first_1--password pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("X_KEY=pk_first_1/password: pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("Authorization: Basic pk_first_1Bearer pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("client_id=pk_first_1_abcdefghHF_TOKEN= pk_second_2", ["pk_first_1", "pk_second_2"]),
            ("Authorization: Bearer X_KEY= pk_second_2", ["pk_second_2"]),
            # The second value starts inside the first but runs on past it.
            ('X_KEY=pk_first_1.HF_TOKEN="pk_second_2 pk_third_3"', ["pk_first_1", "pk_third_3"]),
            ("Bearer pk_first_1.b_token=pk_second_2:pk_third_3", ["pk_first_1", "pk_third_3"]),
            # A raw Authorization value holds spaces: a secret after one is
            # masked with it.
            ('Authorization: pk_first_1raw HF_TOKEN"=pk_second_2', ["pk_first_1", "pk_second_2"]),
            # Three in a row.
            (
                "Bearer pk_first_1.X_KEY= pk_second_2.Authorization: Basic pk_third_3",
                ["pk_first_1", "pk_second_2", "pk_third_3"],
            ),
            # A benign first name: its value is shown, and ends at the secret.
            ("max_tokens=5.HF_TOKEN= pk_second_2", ["pk_second_2"]),
        ],
    )
    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    def test_the_glued_secret_is_masked(self, redact, line: str, secrets: list[str]) -> None:
        out = redact(line)
        for secret in secrets:
            assert secret not in out, (line, out)
        assert redact(out) == out  # idempotent

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            # Where the second name begins is unknown, so the first mask runs
            # on over the second value (fail closed).
            ("Bearer a.Authorization: Token b", f"Bearer {MASK}"),
            ("Bearer a.Authorization: Token b c", f"Bearer {MASK} c"),
            ("Authorization: Bearer abcX-Api-Key: def", f"Authorization: Bearer {MASK}"),
            ("X_KEY=a.HF_TOKEN= b c", f"X_KEY={MASK} c"),
            ('Authorization: rawcred12 HF_TOKEN"=v', f"Authorization: {MASK}"),
            # A second secret all inside the first value is masked with it.
            ("X_KEY=a.HF_TOKEN=b c", f"X_KEY={MASK} c"),
            # A benign value ends before the secret, which keeps its name.
            ("max_tokens=5.HF_TOKEN= v", f"max_tokens=5.HF_TOKEN= {MASK}"),
        ],
    )
    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    def test_the_rendering(self, redact, line: str, expected: str) -> None:
        assert redact(line) == expected

    def test_the_furthest_reading_of_nested_names_is_masked(self) -> None:
        """``Fin-X_KEY=`` and ``X_KEY=`` both start the second secret; read as
        ``X_KEY=`` by LOG_SECRET_RE its value runs on over the quote, so the
        mask does too."""
        assert redact_log_line("Bearer a.Fin-X_KEY=b'pk_third_3 d") == f"Bearer {MASK} d"

    def test_the_shareable_harvest_takes_the_glued_value(self) -> None:
        out = redact_shareable_text("HF_TOKEN=abcdefgh12.X_KEY= secretval9\nlater secretval9\n")
        assert out == f"HF_TOKEN={MASK}\nlater {MASK}\n"

    @pytest.mark.parametrize(
        "unit",
        [
            "a.b_token=",
            "aBearer ",
            "X_KEY=a.",
            "a.Authorization: Basic ",
            "--token=a.",
            "aHF_TOKEN=",
        ],
    )
    @pytest.mark.parametrize("redact", [redact_log_line, redact_secret_shapes])
    @pytest.mark.timing
    def test_a_long_glued_run_is_fast(self, redact, unit: str) -> None:
        import time

        for line in (unit * (80_000 // len(unit)), unit * (80_000 // len(unit)) + " v"):
            start = time.perf_counter()
            redact(line)
            elapsed = time.perf_counter() - start
            assert elapsed < TestRedactionTimeIsLinear._BOUND_S, f"{unit!r} took {elapsed:.2f}s"


# ── #2593: a quote that does not close where it should ──────────────────────


class TestUnclosedQuotes:
    """#2593: a quoted value ran to the next quote. When its own quote did
    not close, that was the quote of a later secret's name or value, and the
    later value was left visible. A quote that never closed left the whole
    value visible."""

    @pytest.mark.parametrize(
        ("line", "secrets"),
        [
            # The line from the issue.
            ('--api-key="pk_first_1, db-password": pk_second_2', ["pk_first_1", "pk_second_2"]),
            ('password="pk_first_1 HF_TOKEN="pk_second_2"', ["pk_first_1", "pk_second_2"]),
            ('password="pk_first_1.HF_TOKEN="pk_second_2"', ["pk_first_1", "pk_second_2"]),
            ('password="pk_first_1 b_token": pk_second_2', ["pk_first_1", "pk_second_2"]),
            ("secret='pk_first_1, api_key': pk_second_2", ["pk_first_1", "pk_second_2"]),
            ('password: "HF_TOKEN": "pk_second_2 pk_third_3"', ["pk_second_2", "pk_third_3"]),
            # A quote that never closes masks the rest of the line.
            ('password="pk_first_1 pk_second_2', ["pk_first_1", "pk_second_2"]),
            ('{"token": "pk_first_1', ["pk_first_1"]),
            ("password: 'pk_first_1 pk_second_2", ["pk_first_1", "pk_second_2"]),
            # A benign quoted value ends before a secret inside it.
            ('max_tokens="5 HF_TOKEN="pk_second_2"', ["pk_second_2"]),
            ("tokenizer='Qwen password: pk_second_2", ["pk_second_2"]),
        ],
    )
    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    def test_the_later_secret_is_masked(self, redact, line: str, secrets: list[str]) -> None:
        out = redact(line)
        for secret in secrets:
            assert secret not in out, (line, out)
        assert redact(out) == out  # idempotent

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ('password="abc HF_TOKEN="zzz" more', f'password="{MASK}" more'),
            ('password="a.b_token": zzz more', f'password="{MASK}" more'),
            ('password: "HF_TOKEN": "v w"', f'password: "{MASK}"'),
            ('password="abc def', f'password="{MASK}'),
            ('{"token": "abc', f'{{"token": "{MASK}'),
            ('password="abc\nX=1', f'password="{MASK}\nX=1'),
            ('max_tokens="5 HF_TOKEN="zzz"', f'max_tokens="5 HF_TOKEN="{MASK}"'),
            # Quotes that close where they should are read as before.
            ('"X_KEY": "v", "HF_TOKEN": "w"', f'"X_KEY": "{MASK}", "HF_TOKEN": "{MASK}"'),
            ('max_tokens="5"', 'max_tokens="5"'),
        ],
    )
    def test_the_rendering(self, line: str, expected: str) -> None:
        assert redact_log_line(line) == expected
        assert redact_shareable_text(line) == expected

    def test_the_issue_line_renders_without_the_second_value(self) -> None:
        line = '--api-key="v, db-password": w'
        assert redact_shareable_text(line) == f"--api-key={MASK}"
        # The log pass masks the whole quoted value too (#2598), not up to its
        # first space.
        assert redact_log_line(line) == f"--api-key={MASK}"

    @pytest.mark.parametrize(
        "unit",
        [
            'password="a ',
            'x="a.b_token": ',
            "secret: '",
            'tokenizer="a ',
            'a"b_token="',
            # A quoted ``*_KEY=`` value, closed or not (#2598).
            "api_key='a ",
            "api_key='a b' ",
            'X_KEY="a HF_TOKEN=" ',
            # An unclosed quote ends at its line, so one on every line is no run
            # that pairs with the next (#2598).
            'api_key="a\nx="b\n',
            # A backslash and the character after it are one unit (#2604).
            'api_key="a\\" ',
            'Authorization: Bearer "a ',
            "Authorization: Basic 'a b' ",
            'Authorization: Digest "a\\" ',
            # An escaped opening quote (#2616).
            'password=\\"a ',
            'password=\\"a\\\\" ',
            '{\\"token\\": \\"a b\\", ',
            '--token \\"a ',
            'password=\\"' + "\\" * 3,
            "api_key='a \\' ",
            'api_key="a\\\\"b ',
        ],
    )
    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    @pytest.mark.timing
    def test_a_long_run_of_quotes_is_fast(self, redact, unit: str) -> None:
        import time

        line = unit * (80_000 // len(unit))
        start = time.perf_counter()
        redact(line)
        elapsed = time.perf_counter() - start
        assert elapsed < TestRedactionTimeIsLinear._BOUND_S, f"{unit!r} took {elapsed:.2f}s"


class TestBackslashEscapedQuotes:
    """#2604: a quote that a backslash escapes does not end a quoted value, so
    a secret that holds one is masked whole in every pass."""

    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    @pytest.mark.parametrize(
        "line",
        [
            r'password="\"pk_a pk_b\"" z',
            r"password='it\'s pk_a pk_b' z",
            r'api_key="pk_a \"pk_b\" pk_c" z',
            r'{"token": "pk_a \"pk_b\"", "x": 1}',
        ],
    )
    def test_the_value_is_masked_whole(self, redact, line: str) -> None:
        out = redact(line)
        assert "pk_" not in out, out
        assert redact(out) == out  # idempotent

    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    def test_an_escaped_backslash_does_not_escape_the_closing_quote(self, redact) -> None:
        out = redact(r'password="pk_a\\" next=1')
        assert out.endswith(" next=1"), out
        assert "pk_a" not in out, out


class TestQuotedAuthorizationValue:
    """#2605: an ``Authorization`` value that opens with a quote, after a known
    scheme, is masked whole by the shareable-text pass as it is by the log pass,
    and keeps its quotes."""

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ('Authorization: Bearer "abc def"', f'Authorization: Bearer "{MASK}"'),
            ("Authorization: Bearer 'abc def' z", f"Authorization: Bearer '{MASK}' z"),
            ('Authorization: Basic "abc def"', f'Authorization: Basic "{MASK}"'),
            ('Proxy-Authorization: Token "abc def" z', f'Proxy-Authorization: Token "{MASK}" z'),
            (r'Authorization: Bearer "a\"b c" z', f'Authorization: Bearer "{MASK}" z'),
            ('Authorization: Bearer "abc def', f'Authorization: Bearer "{MASK}'),
            ('Authorization: Bearer ""', f'Authorization: Bearer "{MASK}"'),
            (
                'Authorization: Bearer "a b"\nmessage="hello"',
                f'Authorization: Bearer "{MASK}"\nmessage="hello"',
            ),
        ],
    )
    def test_the_quoted_value_is_masked_whole(self, line: str, expected: str) -> None:
        out = redact_shareable_text(line)
        assert out == expected
        assert redact_shareable_text(out) == expected  # idempotent

    def test_a_header_that_ends_a_line_takes_nothing_from_the_next(self) -> None:
        line = 'Authorization: Basic\n"hello world" next'
        assert redact_shareable_text(line) == line
        assert redact_log_line(line) == line

    @pytest.mark.parametrize(
        "line", ['Authorization: Bearer "abc def"', "Authorization: Bearer 'abc def'"]
    )
    def test_the_log_pass_masks_it_too(self, line: str) -> None:
        assert "abc" not in redact_log_line(line)

    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    @pytest.mark.parametrize("scheme", ["Basic", "Bearer"])
    def test_one_packed_after_another_secret_is_masked(self, redact, scheme: str) -> None:
        # The ``;`` ends the first value only where a secret starts after it, and
        # a quoted value counts (#2605).
        out = redact(f'X_KEY=abc;Authorization: {scheme} "TOP SECRET"')
        assert "TOP" not in out and "SECRET" not in out, out
        assert out.startswith(f"X_KEY={MASK};Authorization: {scheme}"), out
        assert redact(out) == out


class TestEscapedOpeningQuote:
    """#2616: a value that opens with a backslash-escaped quote (text escaped to
    sit inside a string: a JSON value in a log message, a command line in a JSON
    field) is read as a quoted value closed by the same delimiter, so it is
    masked whole, in every place a value can start."""

    _HEADS = (
        "password=",
        "api_key=",
        "X_KEY=",
        "HAL0_BEARER_TOKEN=",
        "--token ",
        "--token=",
        "--api-key ",
        "Authorization: Basic ",
        "Authorization: Bearer ",
        "Proxy-Authorization: Token ",
        r"{\"password\": ",
        r"{\"token\":",
        r"{\\\"password\\\": ",
    )

    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    @pytest.mark.parametrize("head", _HEADS)
    @pytest.mark.parametrize("k", [1, 2, 3])
    @pytest.mark.parametrize("quote", ['"', "'"])
    @pytest.mark.parametrize("tail", [" z", ', "x": 1}', "\nnext line"])
    def test_the_value_is_masked_whole(self, redact, head: str, k: int, quote: str, tail: str):
        delim = "\\" * k + quote
        for value in ("SECRETONE SECRETTWO", "SECRETONE,SECRETTWO;x"):
            out = redact(f"{head}{delim}{value}{delim}{tail}")
            assert "SECRET" not in out, out
            # What follows the closing delimiter stays (a bare value's own comma goes).
            assert out.endswith(tail.lstrip(",")), out

    @pytest.mark.parametrize("redact", [redact_log_line, redact_shareable_text])
    @pytest.mark.parametrize("head", _HEADS)
    def test_an_unclosed_value_masks_to_the_end_of_its_line(self, redact, head: str) -> None:
        out = redact(f'{head}\\"SECRETONE SECRETTWO\nnext \\"q\\" x')
        assert "SECRET" not in out, out
        assert out.endswith('\nnext \\"q\\" x'), out

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            (r"password=\"abc def\" z", rf"password=\"{MASK}\" z"),
            (r"password=\'abc def\' z", rf"password=\'{MASK}\' z"),
            (r"{\"password\": \"a b\", \"x\": 1}", rf"{{\"password\": \"{MASK}\", \"x\": 1}}"),
            (r"x {\"token\":\"a b\"} y", rf"x {{\"token\":\"{MASK}\"}} y"),
            # An escaped backslash and a quote is its own delimiter.
            (r'password=\\"abc def\\" z', rf'password=\\"{MASK}\\" z'),
            # A longer run inside the value does not close it.
            (r"password=\"abc \\\"def\\\" ghi\" z", rf"password=\"{MASK}\" z"),
            (r"{\\\"password\\\": \\\"a b\\\"}", rf"{{\\\"password\\\": \\\"{MASK}\\\"}}"),
            (r"--token \"abc def\" z", rf"--token \"{MASK}\" z"),
            (r"Authorization: Basic \"abc def\" z", rf"Authorization: Basic \"{MASK}\" z"),
            # Not an opener: a backslash that is no part of an escaped quote.
            (r"password=\abc def", f"password={MASK} def"),
            # A plain quoted value after a flag and a space runs to its quote.
            ('--token "abc def" z', f"--token {MASK} z"),
            ("--password 'abc def' z", f"--password {MASK} z"),
        ],
    )
    def test_the_share_pass_rendering_keeps_the_delimiters(self, line: str, expected: str) -> None:
        # The log pass drops the quotes of a ``*_KEY=`` and a bearer value, the
        # share pass keeps them; both are idempotent.
        out = redact_shareable_text(line)
        assert out == expected
        assert redact_shareable_text(out) == expected
        assert redact_log_line(redact_log_line(line)) == redact_log_line(line)

    def test_a_secret_after_the_closing_delimiter_is_masked_too(self) -> None:
        out = redact_shareable_text(r"password=\"a b\" HF_TOKEN=hf_secretvalue1")
        assert out == rf"password=\"{MASK}\" HF_TOKEN={MASK}"


# ── #2413: the cases shared with the dashboard's browser-side port ─────────


class TestSharedFixture:
    """``tests/fixtures/redaction_shapes.json`` is also read by
    ``ui/src/sentry.test.ts``, which pins ``redactSecrets`` in
    ``ui/src/sentry.ts``, the hand-port of :func:`redact_log_line`. A case added
    there is checked against both implementations."""

    _CASES = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "redaction_shapes.json").read_text()
    )

    @pytest.mark.parametrize(("line", "secret"), _CASES["mask"])
    def test_a_secret_is_masked(self, line: str, secret: str) -> None:
        out = redact_log_line(line)
        assert secret not in out, out
        assert MASK in out

    @pytest.mark.parametrize("line", _CASES["keep"])
    def test_a_lookalike_is_left_alone(self, line: str) -> None:
        assert redact_log_line(line) == line

    @pytest.mark.parametrize(("line", "expected"), _CASES["exact"])
    def test_the_exact_rendering(self, line: str, expected: str) -> None:
        assert redact_log_line(line) == expected
        assert redact_log_line(expected) == expected  # idempotent

    @pytest.mark.parametrize("name", _CASES["mask_names"])
    def test_a_secret_name_is_masked(self, name: str) -> None:
        out = redact_log_line(f"{name}={self._CASES['value']}")
        assert self._CASES["value"] not in out, out
        assert MASK in out

    @pytest.mark.parametrize("name", _CASES["keep_names"])
    def test_a_count_or_location_name_is_left_alone(self, name: str) -> None:
        line = f"{name}={self._CASES['value']}"
        assert redact_log_line(line) == line

    @pytest.mark.parametrize("name", _CASES["mask_hyphen_names"])
    def test_a_hyphenated_secret_name_is_masked_in_every_form(self, name: str) -> None:
        v = self._CASES["hyphen_value"]
        for line in (f"{name}={v}", f"{name}: {v}", f'{{"{name}": "{v}"}}', f"--{name}={v}"):
            out = redact_log_line(line)
            assert v not in out, out
            assert name in out, out

    @pytest.mark.parametrize("key", _CASES["sensitive_keys"])
    def test_a_secret_key_name_is_sensitive(self, key: str) -> None:
        assert is_sensitive_key(key)

    @pytest.mark.parametrize("key", _CASES["plain_keys"])
    def test_a_plain_key_name_is_not_sensitive(self, key: str) -> None:
        assert not is_sensitive_key(key)

    def test_the_sensitive_headers_are_the_backend_set(self) -> None:
        # ui/src/sentry.ts masks the same headers whole; its test reads this list.
        assert set(self._CASES["sensitive_headers"]) == SENSITIVE_HEADERS
