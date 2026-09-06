"""Tests for RFC 7636 PKCE pair generation."""

from __future__ import annotations

import base64
import hashlib
import re

from hal0.oauth.pkce import generate_pkce_pair

_UNRESERVED = re.compile(r"^[A-Za-z0-9\-._~]+$")


def test_verifier_matches_rfc7636_charset_and_length() -> None:
    pair = generate_pkce_pair()
    assert 43 <= len(pair.verifier) <= 128
    assert _UNRESERVED.match(pair.verifier)


def test_challenge_is_s256_of_verifier() -> None:
    pair = generate_pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(pair.verifier.encode("ascii")).digest())
    expected = expected.rstrip(b"=").decode("ascii")
    assert pair.challenge == expected
    assert pair.method == "S256"


def test_challenge_has_no_padding() -> None:
    pair = generate_pkce_pair()
    assert "=" not in pair.challenge


def test_pairs_are_unique() -> None:
    a = generate_pkce_pair()
    b = generate_pkce_pair()
    assert a.verifier != b.verifier
    assert a.challenge != b.challenge
