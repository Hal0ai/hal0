"""The installer is the single user-facing entry point (v1.0, Stream A).

Static-text / ordering assertions against ``installer/install.sh`` plus a few
live CLI checks. Same technique as ``test_platform_gate_hardening.py``: actually
running the installer needs root + systemd + a disposable box, which the
black-box harness (``tests/harness/installer-test.sh``) covers instead.

Four invariants are pinned here, each of which regressed at least once:

1. ``hal0 setup`` is INTERNAL — absent from ``hal0 --help``, absent from the
   install banner's "Next steps", and never offered as a post-install wizard.
2. The curated static slot seeds are copied BEFORE the first-run scaffold pass,
   so ``agent.toml``'s ``profile = "chadrock-moe"`` and ``brain.toml``'s
   ``profile = "brain"`` actually reach a fresh box.
3. The two operator prompts (model store, HF token) run only on a real
   terminal, so ``curl | bash`` and tty-less ssh installs stay unattended.
4. ``UI_STEP_TOTAL`` equals the number of ``ui_step`` banners.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"


@pytest.fixture(scope="module")
def install_sh_text() -> str:
    assert _INSTALL_SH.exists(), f"missing {_INSTALL_SH}"
    return _INSTALL_SH.read_text(encoding="utf-8")


def _line_of(text: str, needle: str) -> int:
    """1-based line number of the first line containing *needle*."""
    for i, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return i
    raise AssertionError(f"{needle!r} not found in install.sh")


# ── 1. `hal0 setup` is internal ─────────────────────────────────────────────


def test_setup_is_hidden_from_the_user_facing_command_list() -> None:
    """``hal0 --help`` must not advertise ``setup``.

    v1.0 makes the installer the only way an operator provisions a box;
    ``setup`` survives purely as the internal verb install.sh drives, so it is
    registered with ``hidden=True`` (the same convention ``hal0 model
    register`` / ``hal0 slot add`` use).
    """
    from typer.testing import CliRunner

    from hal0.cli.main import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "status" in result.output, f"help output looks wrong:\n{result.output}"
    assert not re.search(r"^\s*│?\s*setup\b", result.output, flags=re.MULTILINE), (
        "`setup` is advertised in `hal0 --help` — it must stay hidden:\n" + result.output
    )


def test_setup_still_works_as_an_internal_entry_point() -> None:
    """Hidden must not mean gone — install.sh invokes ``hal0 setup --auto``.

    ``--help`` on the subcommand is the cheapest side-effect-free way to prove
    it is still routable.
    """
    from typer.testing import CliRunner

    from hal0.cli.main import app

    result = CliRunner().invoke(app, ["setup", "--help"])
    assert result.exit_code == 0, result.output
    assert "--auto" in result.output


def test_install_banner_does_not_offer_hal0_setup(install_sh_text: str) -> None:
    """The "Next steps" box used to lead with ``hal0 setup``. It must not."""
    box = install_sh_text[install_sh_text.index("Next steps:") :]
    box = box[: box.index("ui_box")]
    assert "hal0 setup" not in box, "the install summary box still advertises `hal0 setup`"


def test_install_does_not_launch_a_post_install_wizard(install_sh_text: str) -> None:
    """No Stage-2 handoff: the installer must not prompt to launch a wizard.

    The old block ran ``HAL0_FORCE_INTERACTIVE=1 hal0 setup </dev/tty`` after
    the summary box. Every question it asked is now either asked by install.sh
    itself or already performed by it.
    """
    assert "HAL0_FORCE_INTERACTIVE" not in install_sh_text
    assert "_confirm_launch_setup" not in install_sh_text
    # The ONLY `hal0 setup` invocation left is the internal --auto seeding call.
    invocations = re.findall(r'^\s*[^#\n]*"\$\{HAL0_BIN\}" setup.*$', install_sh_text, re.MULTILINE)
    assert len(invocations) == 1, f"expected one internal setup invocation, got {invocations}"
    assert "_setup_args" in invocations[0]


# ── 2. curated seeds beat the scaffold ──────────────────────────────────────


def test_curated_slot_seeds_are_copied_before_the_scaffold_pass(install_sh_text: str) -> None:
    """Ordering fix for the seed-vs-scaffold race (v1.0 HIGH defect).

    ``hal0 setup --auto`` writes a generic model-less scaffold for any slot it
    does not already see on disk. When the curated-seed loop ran AFTER it, the
    loop's ``[[ -f ]]`` guard reported "exists — left alone" and the curated
    ``agent.toml`` / ``brain.toml`` were silently discarded on every fresh box.
    Seeding first inverts it using behaviour that already exists
    (``build_auto_selections``' ``existing_slots`` skip) instead of a special
    case, so this ordering IS the fix — assert it directly.
    """
    seed_loop = _line_of(install_sh_text, "for seed_slot in flm tts rerank")
    scaffold = _line_of(install_sh_text, '"${HAL0_BIN}" setup "${_setup_args[@]}"')
    assert seed_loop < scaffold, (
        f"curated seed loop (line {seed_loop}) must run BEFORE the "
        f"`hal0 setup --auto` scaffold pass (line {scaffold}) or the curated "
        "agent/brain seeds are discarded"
    )


def test_scaffold_pass_skips_slots_that_already_have_a_config() -> None:
    """The other half of the ordering fix, on the Python side.

    ``build_auto_selections`` must skip any slot whose config file already
    exists — that is what lets the curated seeds survive the scaffold pass.
    """
    from hal0.cli.setup_command import build_auto_selections
    from hal0.config.schema import HardwareInfo

    hw = HardwareInfo()
    seeded = frozenset({"agent", "brain", "embed", "rerank", "tts", "coder"})
    sel = build_auto_selections(hw, storage_dir="/tmp/models", existing_slots=seeded)
    names = {s.slot_name for s in sel.slots}
    assert not (names & seeded), f"scaffold would overwrite curated seeds: {sorted(names & seeded)}"


def test_agent_and_brain_seeds_carry_their_curated_profiles() -> None:
    """What the ordering fix exists to deliver, asserted on the seed files.

    A generically-derived scaffold writes ``profile = "vulkan"`` (or similar);
    the curated seeds carry the hand-tuned recipes. If these drift, the
    ordering fix above is protecting nothing.
    """
    import tomllib

    slots_dir = _REPO_ROOT / "installer" / "etc-hal0" / "slots"
    agent = tomllib.loads((slots_dir / "agent.toml").read_text(encoding="utf-8"))
    brain = tomllib.loads((slots_dir / "brain.toml").read_text(encoding="utf-8"))
    assert agent["profile"] == "chadrock-moe"
    assert brain["profile"] == "brain"
    assert brain["port"] == 8089


# ── 3. headless installs stay unattended ────────────────────────────────────


def test_operator_prompts_are_gated_on_an_interactive_stdin(install_sh_text: str) -> None:
    """``curl | bash`` and tty-less ssh must never block on a prompt.

    The gate has to test **stdin**, not ``/dev/tty``: under ``curl … | sudo
    bash`` from a real terminal ``/dev/tty`` IS readable, so a ``-r /dev/tty``
    gate alone would prompt — exactly the regression this guards. ``[[ -t 0 ]]``
    is false for the piped script and for ``ssh host 'cmd'``.
    """
    m = re.search(r"_interactive\(\)\s*\{(.*?)\n\}", install_sh_text, flags=re.DOTALL)
    assert m, "the _interactive() gate is gone"
    body = m.group(1)
    assert "-t 0" in body, "_interactive() must test stdin (`[[ -t 0 ]]`), not just /dev/tty"
    assert "HAL0_NONINTERACTIVE" in body, "no env kill-switch for the prompts"

    # Every operator prompt must sit behind that gate. _tty_read is the only
    # prompting helper; its call sites must all be inside `if _interactive`.
    gate_line = _line_of(install_sh_text, "if _interactive; then")
    lines = install_sh_text.splitlines()
    call_sites = [i for i, line in enumerate(lines, start=1) if re.match(r"\s+_tty_read ", line)]
    assert call_sites, "no _tty_read call sites found — did the prompts move?"
    assert all(i > gate_line for i in call_sites), (
        f"_tty_read called outside the _interactive gate (gate at {gate_line}, "
        f"calls at {call_sites})"
    )


def test_bootstrap_pipes_stdin_so_the_gate_can_do_its_job() -> None:
    """bootstrap.sh's stdin contract is what makes ``[[ -t 0 ]]`` correct.

    It forwards stdin to install.sh, so a downloaded ``sudo bash install.sh``
    can prompt while ``curl | bash`` (stdin = the script pipe) cannot.
    """
    text = (_REPO_ROOT / "installer" / "bootstrap.sh").read_text(encoding="utf-8")
    assert "exec bash " in text and "installer/install.sh" in text


def test_fresh_test_ct_drives_the_install_without_a_tty() -> None:
    """The fresh-CT test script must keep using a tty-less ssh invocation."""
    text = (_REPO_ROOT / "scripts" / "fresh-test-ct.sh").read_text(encoding="utf-8")
    assert re.search(r"SSH\s+\"[^\"]*bash installer/install\.sh", text), (
        "fresh-test-ct.sh no longer runs install.sh over a plain (tty-less) ssh — "
        "an allocated tty would make the install interactive"
    )
    assert "-t -t" not in text and "ssh -t" not in text


def test_models_dir_and_hf_token_prompts_prefill_from_env(install_sh_text: str) -> None:
    """Both prompts must be pre-filled, so Enter reproduces the headless value."""
    block = install_sh_text[install_sh_text.index("Operator input: model store") :]
    block = block[: block.index("ui_step")]
    # Pre-fill sources.
    assert 'HF_TOKEN_VAL="${HF_TOKEN:-${HUGGING_FACE_HUB_TOKEN:-}}"' in block
    assert '_tty_read _md_answer "Where should downloaded models live?" "${MODELS_DIR}"' in block
    # An explicit --models-dir / HAL0_MODELS_DIR is never re-asked.
    assert "MODELS_DIR_EXPLICIT" in block
    # The token is read silently (never echoed into the transcript).
    assert re.search(r'_tty_read _hf_answer .*" 1', block), "HF token prompt is not silent"


# ── 4. step counter ─────────────────────────────────────────────────────────


def test_ui_step_total_matches_the_number_of_banners(install_sh_text: str) -> None:
    """``UI_STEP_TOTAL`` drifted to 13-vs-14 before v1.0. Keep them in lockstep."""
    banners = re.findall(r"^ui_step ", install_sh_text, flags=re.MULTILINE)
    m = re.search(r"^UI_STEP_TOTAL=(\d+)", install_sh_text, flags=re.MULTILINE)
    assert m, "UI_STEP_TOTAL is gone"
    assert int(m.group(1)) == len(banners), (
        f"UI_STEP_TOTAL={m.group(1)} but there are {len(banners)} `ui_step` banners"
    )


def test_every_ui_step_banner_is_unconditional(install_sh_text: str) -> None:
    """A ``ui_step`` inside an if/else branch makes the counter non-deterministic.

    The regex above only counts column-0 ``ui_step`` calls, so an indented one
    would silently break the "N of TOTAL" display on some hosts.
    """
    indented = [
        line
        for line in install_sh_text.splitlines()
        if line.lstrip().startswith("ui_step ") and not line.startswith("ui_step ")
    ]
    assert not indented, f"conditional/indented ui_step calls: {indented}"


# ── 5. brain model pull is fail-soft ────────────────────────────────────────


def test_brain_model_pull_cannot_fail_the_install(install_sh_text: str) -> None:
    """Never hard-fail an install over an optional model pull.

    install.sh runs under ``set -euo pipefail``, so the brain-model step needs
    an explicit ``|| warn`` — the same containment the absent-HF_TOKEN path and
    the ``hal0 setup --auto`` call already use.
    """
    idx = install_sh_text.index("-m hal0.install.brain_model")
    tail = install_sh_text[idx : idx + 400]
    assert "|| warn" in tail, "the brain model pull has no `|| warn` containment"
    assert "HAL0_SKIP_BRAIN_MODEL" in install_sh_text, "no opt-out for the brain model pull"
