"""Schema + engine tests for per-upstream model filters.

Spec: docs/superpowers/specs/2026-07-06-upstream-model-filters.md.
The pure-filter tests exercise realistic OpenRouter-style model ids.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import UpstreamEntry, UpstreamModelFilters
from hal0.upstreams.filters import ModelFilters, apply_filters, is_advertised

OPENROUTER_IDS = [
    "anthropic/claude-sonnet-4",
    "anthropic/claude-3-haiku",
    "google/gemini-2.5-pro",
    "deepseek/deepseek-r1",
    "deepseek/deepseek-r1:free",
    "nvidia/llama-3.1-nemotron-70b",
    "meta-llama/llama-4-maverick:free",
]


# ── UpstreamModelFilters schema ───────────────────────────────────────────────


class TestFilterSchema:
    def test_defaults_are_empty(self) -> None:
        f = UpstreamModelFilters()
        assert f.models == [] and f.include == [] and f.exclude == []
        assert f.is_empty()

    def test_unknown_field_rejected(self) -> None:
        # extra="forbid" — a typo'd field name must not silently pass-all.
        with pytest.raises(ValidationError):
            UpstreamModelFilters(includes=["anthropic/*"])  # type: ignore[call-arg]

    def test_empty_strings_dropped(self) -> None:
        f = UpstreamModelFilters(models=["", "  ", "a/b"], include=["", "x/*"])
        assert f.models == ["a/b"]
        assert f.include == ["x/*"]

    def test_round_trips_on_upstream_entry(self) -> None:
        e = UpstreamEntry(
            name="openrouter",
            url="https://openrouter.ai/api/v1",
            model_filters=UpstreamModelFilters(include=["anthropic/*"], exclude=["*:free"]),
        )
        dumped = e.model_dump()
        e2 = UpstreamEntry.model_validate(dumped)
        assert e2.model_filters is not None
        assert e2.model_filters.include == ["anthropic/*"]
        assert e2.model_filters.exclude == ["*:free"]

    def test_absent_filters_default_none(self) -> None:
        assert UpstreamEntry(name="x", url="http://x").model_filters is None


# ── ModelFilters engine ───────────────────────────────────────────────────────


class TestFilterEngine:
    def test_none_or_empty_pass_all(self) -> None:
        assert apply_filters(OPENROUTER_IDS, None) == OPENROUTER_IDS
        assert apply_filters(OPENROUTER_IDS, ModelFilters()) == OPENROUTER_IDS

    def test_models_allowlist_exact(self) -> None:
        f = ModelFilters.from_lists(models=["anthropic/claude-sonnet-4"])
        assert apply_filters(OPENROUTER_IDS, f) == ["anthropic/claude-sonnet-4"]
        # exact match only — no implicit prefixing
        assert not is_advertised("anthropic/claude-sonnet-4:beta", f)

    def test_include_glob_by_provider_prefix(self) -> None:
        f = ModelFilters.from_lists(include=["anthropic/*", "google/*"])
        assert apply_filters(OPENROUTER_IDS, f) == [
            "anthropic/claude-sonnet-4",
            "anthropic/claude-3-haiku",
            "google/gemini-2.5-pro",
        ]

    def test_models_and_include_are_ored(self) -> None:
        # "all Anthropic and Google models, plus these specific DeepSeek ones"
        f = ModelFilters.from_lists(
            models=["deepseek/deepseek-r1"], include=["anthropic/*", "google/*"]
        )
        got = apply_filters(OPENROUTER_IDS, f)
        assert "deepseek/deepseek-r1" in got
        assert "anthropic/claude-sonnet-4" in got
        assert "nvidia/llama-3.1-nemotron-70b" not in got

    def test_exclude_only_hides_from_pass_all(self) -> None:
        f = ModelFilters.from_lists(exclude=["*:free", "nvidia/*"])
        got = apply_filters(OPENROUTER_IDS, f)
        assert got == [
            "anthropic/claude-sonnet-4",
            "anthropic/claude-3-haiku",
            "google/gemini-2.5-pro",
            "deepseek/deepseek-r1",
        ]

    def test_exclude_overrides_include(self) -> None:
        # "all Anthropic models EXCEPT claude-3-haiku"
        f = ModelFilters.from_lists(include=["anthropic/*"], exclude=["anthropic/claude-3-haiku"])
        assert apply_filters(OPENROUTER_IDS, f) == ["anthropic/claude-sonnet-4"]

    def test_exclude_overrides_exact_allowlist(self) -> None:
        f = ModelFilters.from_lists(models=["deepseek/deepseek-r1:free"], exclude=["*:free"])
        assert not is_advertised("deepseek/deepseek-r1:free", f)

    def test_question_mark_glob(self) -> None:
        f = ModelFilters.from_lists(include=["deepseek/deepseek-r?"])
        assert is_advertised("deepseek/deepseek-r1", f)
        assert not is_advertised("deepseek/deepseek-r1:free", f)

    def test_case_sensitive_matching(self) -> None:
        f = ModelFilters.from_lists(include=["Anthropic/*"])
        assert not is_advertised("anthropic/claude-sonnet-4", f)

    def test_from_lists_strips_and_drops_empty(self) -> None:
        f = ModelFilters.from_lists(models=[" a/b ", ""], include=None, exclude=["  "])
        assert f.models == ("a/b",)
        assert f.include == ()
        assert f.exclude == ()
        assert not f.is_empty()

    def test_order_preserved(self) -> None:
        f = ModelFilters.from_lists(exclude=["google/*"])
        got = apply_filters(OPENROUTER_IDS, f)
        assert got == [m for m in OPENROUTER_IDS if not m.startswith("google/")]
