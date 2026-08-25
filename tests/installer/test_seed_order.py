"""GH #1475: curated static slot seeds must be copied BEFORE ``hal0 setup
--auto`` runs in install.sh.

Both write the same slot names (agent/embed/rerank/stt/tts/vision) and both
are never-overwrite, so whichever runs first wins. ``hal0 setup --auto``
scaffolds them with generic derived profiles (agent -> chat, embed ->
embedding, ...); ``installer/etc-hal0/slots/*.toml`` carries hand-tuned
seeds (agent.toml's chadrock-moe profile, brain.toml's brain profile,
embed.toml's 4096 context, ...). If setup runs first, the curated seeds
never reach a fresh box — this is a static ordering check over the script
text (no systemd/podman harness exists for install.sh; this mirrors the
line-order-via-string-search pattern other installer tests use for
bootstrap.sh's cosign argv).
"""

from __future__ import annotations

from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"


def test_seed_copy_loop_runs_before_hal0_setup_auto() -> None:
    text = _INSTALL_SH.read_text(encoding="utf-8")

    seed_loop_marker = (
        "for seed_slot in flm tts rerank utility img agent brain qwen3tts coder embed; do"
    )
    setup_call_marker = '"${HAL0_BIN}" setup "${_setup_args[@]}"'

    seed_loop_pos = text.index(seed_loop_marker)
    setup_call_pos = text.index(setup_call_marker)

    assert seed_loop_pos < setup_call_pos, (
        "install.sh copies the curated static slot seeds AFTER `hal0 setup "
        "--auto` runs — setup's generic derived profiles win the "
        "never-overwrite race and the curated seeds (agent's chadrock-moe "
        "profile, brain's brain profile, embed's 4096 context, ...) never "
        "reach a fresh box. The seed-copy loop must run first."
    )


def test_seed_device_derivation_runs_after_copy_loop_and_before_setup() -> None:
    """GH #2023: the copy loop is verbatim by design (curated seeds), so a
    derivation pass over the freshly seeded slots must follow it — routed
    through hal0.install.static_seeds so bash and the api-lifespan closer share
    one implementation — and it must land BEFORE `hal0 setup --auto`, which
    treats an existing file as operator intent and never touches it again."""
    text = _INSTALL_SH.read_text(encoding="utf-8")

    seed_loop_marker = (
        "for seed_slot in flm tts rerank utility img agent brain qwen3tts coder embed; do"
    )
    derive_marker = "-m hal0.install.static_seeds"
    setup_call_marker = '"${HAL0_BIN}" setup "${_setup_args[@]}"'

    assert derive_marker in text, (
        "install.sh never routes the freshly copied slot seeds through "
        "hal0.install.static_seeds device derivation — every llama.cpp seed "
        "ships device = gpu-rocm verbatim and a kfd-less fresh install has "
        "zero loadable LLM slots (#2023)."
    )
    assert (
        text.index(seed_loop_marker) < text.index(derive_marker) < text.index(setup_call_marker)
    ), (
        "the seed device derivation pass must run after the verbatim copy "
        "loop and before hal0 setup --auto (#2023)"
    )
