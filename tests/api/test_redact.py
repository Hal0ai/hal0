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

from collections.abc import Iterator

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
from hal0.redaction import MASK, redact_shareable_text

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
            "mixed_content_tool_tokens",
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
