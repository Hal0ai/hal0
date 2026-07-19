"""Golden path #5 (pull→assign→infer) — launch-time profile-read invariant.

spec-flags-ownership §7 golden #5 states the TARGET invariant: a stamped model
launches with NO live profile read — the profile resolver is not consulted
post-stamp, because the model's materialized flags text is the whole tune.

The CURRENT launch path does NOT yet satisfy that: by the ML-5 design the
llama-server argv still layers a live ``profile`` segment (from a live profile
resolve) UNDER the model's ``model_defaults`` segment — see
``hal0.providers.container._llama_argv_segments``' documented precedence
``base < profile < model_defaults < … < extra_args``. This increment is the
ADDITIVE backend (increment 1); ripping out the profile layer is increment-2
migration-window work.

So this test documents the CURRENT (still-layered) behaviour and pins it, so
increment 2 has a red test to flip when it removes the profile segment. The
increment-2 delta is enumerated in the module docstring below.

Increment-2 work items (delta from the §7 target):
  1. Drop the ``profile`` segment from ``_llama_argv_segments`` (and the
     ``profile_flags`` param) so launch never resolves a profile.
  2. Remove the live ``_profile_image_and_flags`` flag-resolution call from the
     container load path (image resolution stays; flag layering goes).
  3. Delete the slot ``slot_overrides``/``extra_args`` flag segments per §2.
  4. Flip THIS test to assert the ``profile`` segment is absent / empty and the
     stamped ``model_defaults`` text is the sole tune source.
"""

from __future__ import annotations

from hal0.providers.container import _llama_argv_segments
from hal0.slots.argv import resolve_argv


def test_stamped_model_still_layers_a_live_profile_segment_at_launch() -> None:
    """CURRENT behaviour (increment 1): even when the model carries a stamped
    ``defaults.extra_args`` tune, the launch argv builder still emits a live
    ``profile`` segment and both sets of flags survive into the final argv.

    This is the delta from the §7 golden-#5 target (no profile read post-stamp)
    — documented, not yet fixed. Increment 2 flips this assertion.
    """
    segments = _llama_argv_segments(
        port=8081,
        model_path="/models/qwen3-4b.gguf",
        model_alias="qwen3-4b",
        context_size=8192,
        # A live-resolved profile flag string — as _profile_image_and_flags
        # would still hand in at launch today.
        profile_flags="-fa on",
        # The model is STAMPED: its own materialized tune text.
        model_defaults={"extra_args": "-b 2048"},
    )

    labels = {label for label, _tokens in segments}
    assert "profile" in labels, "launch builder no longer emits a profile segment"

    profile_seg = next(toks for label, toks in segments if label == "profile")
    # The profile segment is NON-EMPTY — a live profile read is layered in even
    # though the model is stamped (the invariant §7 wants gone).
    assert profile_seg == ["-fa", "on"]

    resolved = resolve_argv(segments)
    # Both the profile flag (-fa) and the stamped model flag (-b) reach launch.
    assert "-fa" in resolved.argv
    assert "-b" in resolved.argv
    # Provenance still attributes -fa to the live profile segment (proof the
    # profile participated at launch, not just the stamp).
    prov = {p.flag: p.source for p in resolved.provenance}
    assert prov.get("-fa") == "profile"
    assert prov.get("-b") == "model_defaults"


def test_stamped_model_defaults_win_over_profile_on_flag_collision() -> None:
    """When the stamp and the live profile set the SAME flag, the model tune
    wins (``model_defaults`` sits above ``profile`` in the precedence chain) —
    the closest current behaviour gets to 'the model owns its tune'."""
    segments = _llama_argv_segments(
        port=8081,
        model_path="/models/qwen3-4b.gguf",
        profile_flags="-b 512",
        model_defaults={"extra_args": "-b 2048"},
    )
    resolved = resolve_argv(segments)
    # Last-wins: the stamped -b 2048 survives, the profile's -b 512 is dropped.
    idx = resolved.argv.index("-b")
    assert resolved.argv[idx + 1] == "2048"
    prov = {p.flag: p.source for p in resolved.provenance}
    assert prov.get("-b") == "model_defaults"
