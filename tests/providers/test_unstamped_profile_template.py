"""Unstamped-model profile template (#1787) — the copy-on-stamp floor.

FLAGS-own made the model's materialized ``defaults`` the whole launch tune and
a profile a copy-on-stamp template read only in the drawer. But nothing on the
fresh-install path stamps: ``hal0 model scan``, pull and capability-apply all
register models with ``defaults = null``. Such a model therefore contributed no
tune at all, and the #1636 divergence overlay (gated on stamped provenance)
stayed silent too — so an ``embedding``/``rerank`` slot launched without
``--embedding``/``--reranking`` and served 501s while reporting ready.

The template segment closes that hole for exactly the unstamped case::

    base < slot_profile_template < model_extra_args < slot_profile
         < slot_hardware < chat_template < mmproj

A model that carries its own tune text (stamped OR hand-authored) is untouched:
no live profile read, golden #5 shape preserved.
"""

from __future__ import annotations

import pytest

from hal0.config.schema import ProfileConfig
from hal0.errors import BadRequest
from hal0.providers.container import _llama_argv_segments, _resolve_llama_scalars
from hal0.slots.argv import resolve_argv


class _NamedProfile(ProfileConfig):
    """ProfileConfig plus the ``name`` a ResolvedProfile carries."""

    name: str = ""


def _profile(name: str, flags: str) -> _NamedProfile:
    return _NamedProfile(name=name, flags=flags, mtp=False)


def _slot(profile: str, **kw) -> dict:
    return {"name": "embed", "profile": profile, "port": 8083, **kw}


def _model(defaults: dict | None) -> dict:
    return {
        "_model_key": "qwen3-embedding-0-6b-q8-0",
        "path": "/m/qwen3-embedding.gguf",
        "tags": ["embedding"],
        "defaults": defaults,
    }


EMBED_FLAGS = "--embedding -fa on -b 8192 -ub 8192"


# ── the GA blocker: auto-scanned model, embedding slot ───────────────────────


def test_unstamped_model_takes_the_slot_profile_as_its_template():
    """#1787 repro: ``defaults = null`` (auto-scan/pull) + ``profile=embedding``."""
    scalars = _resolve_llama_scalars(
        _slot("embedding"), _model(None), _profile("embedding", EMBED_FLAGS)
    )
    assert scalars["slot_profile_template_flags"] == EMBED_FLAGS
    assert scalars["slot_profile_flags"] == ""  # no provenance → no divergence


def test_embedding_flag_reaches_the_launched_argv():
    scalars = _resolve_llama_scalars(
        _slot("embedding"), _model(None), _profile("embedding", EMBED_FLAGS)
    )
    resolved = resolve_argv(
        _llama_argv_segments(
            port=8083,
            model_path="/m/qwen3-embedding.gguf",
            model_defaults=scalars["model_defaults"],
            slot_profile_flags=scalars["slot_profile_flags"],
            slot_profile_template_flags=scalars["slot_profile_template_flags"],
        )
    )
    assert "--embedding" in resolved.argv
    prov = {p.flag: p.source for p in resolved.provenance}
    assert prov["--embedding"] == "slot_profile_template"


def test_reranking_flag_reaches_the_launched_argv():
    rerank_flags = "--reranking -fa on -b 8192 -ub 8192"
    scalars = _resolve_llama_scalars(
        _slot("reranking"), _model(None), _profile("reranking", rerank_flags)
    )
    resolved = resolve_argv(
        _llama_argv_segments(
            port=8086,
            model_path="/m/qwen3-rerank.gguf",
            model_defaults=scalars["model_defaults"],
            slot_profile_template_flags=scalars["slot_profile_template_flags"],
        )
    )
    assert "--reranking" in resolved.argv


def test_empty_defaults_dict_is_also_unstamped():
    """``defaults = {}`` (a registry row with an empty bundle) counts as unstamped."""
    scalars = _resolve_llama_scalars(
        _slot("embedding"), _model({}), _profile("embedding", EMBED_FLAGS)
    )
    assert scalars["slot_profile_template_flags"] == EMBED_FLAGS


def test_typed_only_defaults_are_still_unstamped():
    """A row with typed knobs but no tune TEXT still needs the template."""
    scalars = _resolve_llama_scalars(
        _slot("embedding"), _model({"n_gpu_layers": 99}), _profile("embedding", EMBED_FLAGS)
    )
    assert scalars["slot_profile_template_flags"] == EMBED_FLAGS


# ── what the template must NOT touch ─────────────────────────────────────────


def test_hand_authored_model_tune_suppresses_the_template():
    """Golden #5 §8: a model that carries tune text launches with no profile read."""
    scalars = _resolve_llama_scalars(
        _slot("embedding"), _model({"extra_args": "-b 2048"}), _profile("embedding", EMBED_FLAGS)
    )
    assert scalars["slot_profile_template_flags"] == ""


def test_stamped_model_suppresses_the_template():
    scalars = _resolve_llama_scalars(
        _slot("embedding"),
        _model({"profile": "embedding", "extra_args": "-b 2048"}),
        _profile("embedding", EMBED_FLAGS),
    )
    assert scalars["slot_profile_template_flags"] == ""


def test_stamped_divergent_model_keeps_the_1636_overlay():
    """#1636 must not regress: stamped provenance still routes to ``slot_profile``."""
    scalars = _resolve_llama_scalars(
        _slot("coding", name="coder"),
        {
            "_model_key": "qwen3-4b",
            "path": "/m/qwen3-4b.gguf",
            "defaults": {"profile": "chat", "extra_args": "-b 2048"},
        },
        _profile("coding", "--temp 0.7"),
    )
    assert scalars["slot_profile_flags"] == "--temp 0.7"
    assert scalars["slot_profile_template_flags"] == ""


def test_slot_with_no_profile_gets_no_template():
    scalars = _resolve_llama_scalars(_slot(""), _model(None), _profile("", ""))
    assert scalars["slot_profile_template_flags"] == ""


def test_base_fallback_profile_produces_no_template():
    """The named profile no longer resolves; the backend base must not leak in."""
    scalars = _resolve_llama_scalars(
        _slot("retired-profile"), _model(None), _profile("rocm", "-fa on")
    )
    assert scalars["slot_profile_template_flags"] == ""


def test_wrong_type_profile_produces_no_template():
    """An out-of-band TTS profile on an LLM slot must not inject its
    mode-changing ``--model_path`` — same fit predicate the overlay uses."""
    scalars = _resolve_llama_scalars(
        _slot("kokoro", type="llm"), _model(None), _profile("kokoro", "--model_path /m/kokoro")
    )
    assert scalars["slot_profile_template_flags"] == ""


# ── segment placement + screening ────────────────────────────────────────────


def _segments(template: str = "", **kw):
    return _llama_argv_segments(
        port=8083,
        model_path="/m/x.gguf",
        model_defaults={"extra_args": "-b 2048"},
        slot_profile_template_flags=template,
        **kw,
    )


def test_template_segment_sits_below_the_model_tune():
    labels = [label for label, _ in _segments("-b 8192", slot_n_gpu_layers=30)]
    assert labels.index("slot_profile_template") < labels.index("model_extra_args")
    assert labels.index("model_extra_args") < labels.index("slot_hardware")


def test_no_segment_when_empty():
    assert "slot_profile_template" not in {label for label, _ in _segments("")}


def test_model_tune_wins_collisions_over_the_template():
    resolved = resolve_argv(_segments("-b 8192 --embedding"))
    prov = {p.flag: p.source for p in resolved.provenance}
    assert resolved.argv[resolved.argv.index("-b") + 1] == "2048"
    assert prov["-b"] == "model_extra_args"
    # Template flags the model tune does not mention still reach the argv.
    assert "--embedding" in resolved.argv


def test_slot_hardware_still_wins_over_the_template():
    resolved = resolve_argv(_segments("--threads 4", slot_threads=16))
    assert resolved.argv[resolved.argv.index("--threads") + 1] == "16"


def test_grandfathered_ngl_in_profile_is_stripped_not_rejected():
    """``-ngl`` is on BOTH denylists; for a profile-sourced segment the §5
    partition rule is "silently ignored", so a grandfathered ``-ngl 999``
    profile must not hard-fail the launch (it used to be inert entirely)."""
    resolved = resolve_argv(_segments("--flash-attn on -ngl 999", slot_n_gpu_layers=30))
    assert "--flash-attn" in resolved.argv
    assert resolved.argv[resolved.argv.index("-ngl") + 1] == "30"


def test_grandfathered_ngl_in_divergent_profile_is_stripped_too():
    """Same rule for the #1636 overlay segment — one code path, one behaviour."""
    resolved = resolve_argv(
        _llama_argv_segments(
            port=8083,
            model_path="/m/x.gguf",
            model_defaults={"extra_args": "-b 2048"},
            slot_profile_flags="--temp 0.7 -ngl 999",
            slot_n_gpu_layers=30,
        )
    )
    assert resolved.argv[resolved.argv.index("-ngl") + 1] == "30"


def test_managed_flag_in_template_fails_loudly():
    with pytest.raises(BadRequest):
        resolve_argv(_segments("--port 9999"))


def test_jinja_is_never_carried_by_the_template():
    """jinja is a runner+model capability resolved as ``effective_jinja``."""
    assert "--jinja" not in resolve_argv(_segments("--jinja -b 8192")).argv


def test_malformed_template_flags_fail_controlled():
    from hal0.errors import UnprocessableEntity

    with pytest.raises(UnprocessableEntity):
        _segments('--foo "unclosed')
