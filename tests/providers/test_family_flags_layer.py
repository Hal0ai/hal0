"""ADR-0029 model layer: family quirks sit under the profile and over the base."""

from __future__ import annotations

import pytest

from hal0.config import schema
from hal0.providers.container import _llama_argv_segments
from hal0.slots.argv import resolve_argv


def test_family_quirks_land_in_the_model_layer_and_the_profile_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(schema.FAMILY_DEFAULTS, "qwen", "--temp 0.6 --top-k 20")
    quirks = schema.family_flags("qwen3-8b", architecture="qwen3")
    assert quirks == "--temp 0.6 --top-k 20"

    segments = _llama_argv_segments(
        port=8081,
        model_path="/m.gguf",
        context_size=4096,
        family_flags=quirks,
        jinja=True,
        profile_flags="--temp 1.0",
    )
    layers = dict(segments)
    assert layers["model"][:4] == ["--temp", "0.6", "--top-k", "20"]
    assert "--jinja" in layers["model"]

    argv = resolve_argv(segments).argv
    assert argv[argv.index("--temp") + 1] == "1.0"  # the profile wins
    assert argv[argv.index("--top-k") + 1] == "20"  # the quirk stays


def test_no_family_no_quirks() -> None:
    assert schema.family_flags("mystery-model", architecture="mamba") == ""
