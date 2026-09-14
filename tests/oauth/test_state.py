"""Tests for the in-process OAuth state-nonce store (CSRF/replay defense)."""

from __future__ import annotations

import pytest

from hal0.oauth.state import OAuthStateStore


def test_issue_then_pop_returns_the_bound_nonce() -> None:
    store = OAuthStateStore()
    state = store.issue("google", code_verifier="verifier123")

    nonce = store.pop(state)

    assert nonce is not None
    assert nonce.provider_id == "google"
    assert nonce.code_verifier == "verifier123"


def test_pop_is_single_use() -> None:
    store = OAuthStateStore()
    state = store.issue("google")

    first = store.pop(state)
    second = store.pop(state)

    assert first is not None
    assert second is None


def test_unknown_state_returns_none() -> None:
    store = OAuthStateStore()
    assert store.pop("never-issued") is None


def test_expired_nonce_is_pruned(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.oauth.state as state_mod

    clock = {"now": 1000.0}
    monkeypatch.setattr(state_mod.time, "time", lambda: clock["now"])
    store = OAuthStateStore(ttl_seconds=60)

    state = store.issue("google")
    clock["now"] += 61

    assert store.pop(state) is None


def test_nonces_for_different_providers_are_independent() -> None:
    store = OAuthStateStore()
    s1 = store.issue("google")
    s2 = store.issue("spotify")

    assert store.pop(s1).provider_id == "google"
    assert store.pop(s2).provider_id == "spotify"
