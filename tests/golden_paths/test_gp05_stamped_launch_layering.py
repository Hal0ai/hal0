"""Golden path #5 (pull→assign→infer) — launch-time profile-read invariant.

spec-flags-ownership §7/§8 golden #5: a stamped model launches with NO live
profile read — the profile resolver is not consulted post-stamp, because the
model's materialized ``defaults`` text is the whole tune.

FLAGS-own (increment 2, this lane) removed the ``profile`` segment and the slot
``slot_overrides`` / ``extra_args`` segments from
``hal0.providers.container._llama_argv_segments``. These tests pin the TARGET:
the launch builder emits NO profile / slot flag segment, and the stamped model's
``model_extra_args`` (+ trusted ``model_defaults`` -ngl) is the sole tune source.
"""

from __future__ import annotations

from unittest.mock import patch

from hal0.providers.container import _llama_argv_segments, _resolve_llama_scalars
from hal0.slots.argv import resolve_argv


def test_launch_builder_emits_no_profile_or_slot_flag_segment() -> None:
    """FLAGS-own: even handed the (now-inert) profile/slot flag params, the argv
    builder emits ONLY base + the model's tune + chat/mmproj — no ``profile``,
    ``slot_overrides`` or ``extra_args`` segment."""
    segments = _llama_argv_segments(
        port=8081,
        model_path="/models/qwen3-4b.gguf",
        model_alias="qwen3-4b",
        context_size=8192,
        # Inert now — a stale caller may still thread these; they must NOT reach
        # the argv.
        profile_flags="-fa on",
        slot_n_gpu_layers=30,
        slot_parallel=8,
        extra_args="--foo bar",
        # The model is STAMPED: its own materialized tune text.
        model_defaults={"extra_args": "-b 2048", "n_gpu_layers": 99},
    )

    labels = {label for label, _tokens in segments}
    assert "profile" not in labels
    assert "slot_overrides" not in labels
    assert "extra_args" not in labels
    assert labels == {"base", "model_extra_args", "chat_template", "mmproj", "slot_hardware"}

    resolved = resolve_argv(segments)
    prov = {p.flag: p.source for p in resolved.provenance}
    # The stamped model tune reaches launch...
    assert "-b" in resolved.argv
    assert prov.get("-b") == "model_extra_args"
    # spec-hw-slot-ownership §2: the slot now owns -ngl — the slot's 30 wins
    # over the model's defaulted 99.
    assert resolved.argv[resolved.argv.index("-ngl") + 1] == "30"
    # ...and none of the inert profile/slot flags do.
    assert "-fa" not in resolved.argv  # profile flag gone
    assert "--foo" not in resolved.argv  # slot extra_args gone
    assert "--parallel" not in resolved.argv  # slot parallel gone


def test_stamped_launch_does_not_consult_the_profile_flag_resolver() -> None:
    """spec §8: assert the profile flag resolver is NOT called on the launch
    path — a stamped model's tune is materialized, so ``resolve_profile_flags``
    (the profile→flags copy) must not run at ``container_spec`` time."""
    slot_cfg = {
        "name": "primary",
        "profile": "rocm",
        "port": 8081,
        "model": {"default": "qwen3-4b"},
        "server": {},
    }
    model_info = {
        "_model_key": "qwen3-4b",
        "path": "/models/qwen3-4b.gguf",
        "defaults": {"extra_args": "-b 2048"},
    }

    with patch("hal0.providers.container.resolve_profile_flags") as rpf:
        _resolve_llama_scalars(slot_cfg, model_info, _FakeProfile(), for_launch=True)

    rpf.assert_not_called()


class _FakeProfile:
    """Minimal stand-in for a resolved profile (image/device axis only)."""

    image = "ghcr.io/hal0ai/x:rocm-server"
    flags = "-fa on -ngl 999"
    resolved_flags = "-fa on -ngl 999"
    mtp = False
    device_class = "gpu"
    backend = "rocm"
