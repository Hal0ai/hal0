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
