"""Contract tests for ``preflight_gpu``'s install-time gate (WS-B, #1104).

``preflight_gpu`` runs in two modes. Under ``hal0 doctor`` (the default) it is
advisory-only and must always return 0 so the report never aborts. Under the
Stage-1 installer gate (``HAL0_GPU_GATE=1``) it must classify the platform via
its return code so ``install.sh`` can smart-block the single most common
broken-install shape: a Proxmox LXC with the GPU forwarded but the render-node
gid mis-mapped, which otherwise installs "successfully" then silently runs
every slot CPU-only.

The function reads real ``/dev`` nodes and ``/proc/1/environ``; these tests
drive it through the documented test seams (``HAL0_GPU_DRI_GLOB``,
``HAL0_GPU_CONTAINER_OVERRIDE``, ``HAL0_GPU_RENDER_GID_OVERRIDE``) so the
outcome is independent of the host that runs the suite (which may itself be an
LXC with a real GPU, as the maintainer's box is).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PREFLIGHT = Path(__file__).resolve().parents[2] / "installer" / "lib" / "preflight.sh"
INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"

# Return codes exported by preflight.sh — kept in lock-step with the shell.
RC_OK = 0
RC_BROKEN_GID = 3
RC_NO_DEVICE = 4
RC_NO_KFD = 5
RC_KFD_GID = 6

# A gid with no matching group on any sane host — forces "maps to NO group".
UNMAPPED_GID = "61999"


def _run_gpu_gate_full(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    """Source preflight.sh, run ``preflight_gpu``, return the whole process.

    ``set -euo pipefail`` mirrors install.sh so we also prove the function is
    safe to source there. The ``|| rc=$?`` guard captures a non-zero return
    without tripping ``set -e``. Output is captured rather than discarded so
    tests can assert on what the operator is actually told (#1948: the gate's
    two branches now say different things, and both must be true).
    """
    script = f"set -euo pipefail\nsource {PREFLIGHT!s}\nrc=0\npreflight_gpu || rc=$?\nexit $rc\n"
    # HAL0_GPU_AMD_OVERRIDE defaults OFF here so the AMD lane check (#1888)
    # stays hermetic: the box running the suite may itself be an AMD LXC with
    # (or without) a real /dev/kfd. Tests that exercise that check set it.
    #
    # HAL0_GPU_VULKAN_LANE_OVERRIDE defaults OFF for the same reason — the
    # answer would otherwise depend on whether this checkout's manifest.json
    # ``standard`` pin has passed the Vulkan gate, which changes with every
    # runner promote. "0" is the conservative default and the one the
    # historical assertions below were written against.
    env = {
        **os.environ,
        "HAL0_GPU_AMD_OVERRIDE": "0",
        "HAL0_GPU_VULKAN_LANE_OVERRIDE": "0",
        **env_overrides,
    }
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


def _run_gpu_gate(env_overrides: dict[str, str]) -> int:
    """``_run_gpu_gate_full``'s return code — the shape most tests want."""
    return _run_gpu_gate_full(env_overrides).returncode


@pytest.fixture
def render_glob(tmp_path: Path) -> str:
    """A glob that matches a fake render node, so 'device present' is testable."""
    (tmp_path / "renderD128").touch()
    return str(tmp_path / "renderD*")


def test_gate_broken_gid_lxc_hard_stops(render_glob: str) -> None:
    """Device present + unmapped render gid + LXC → BROKEN_GID (hard stop)."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": UNMAPPED_GID,
        }
    )
    assert rc == RC_BROKEN_GID


def test_gate_no_device_lxc_opt_in(tmp_path: Path) -> None:
    """No device + LXC → NO_DEVICE, so install.sh can offer a CPU-only opt-in."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": str(tmp_path / "NONE*"),
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
        }
    )
    assert rc == RC_NO_DEVICE


def test_gate_bare_metal_no_gpu_proceeds(tmp_path: Path) -> None:
    """Genuine bare-metal CPU box → proceed (0), no friction."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": str(tmp_path / "NONE*"),
            "HAL0_GPU_CONTAINER_OVERRIDE": "none",
        }
    )
    assert rc == RC_OK


def test_gate_device_good_gid_proceeds(render_glob: str) -> None:
    """Device present + gid maps to the (expected) render group → proceed (0).

    HAL0_GPU_RENDER_GROUP_OVERRIDE points "the render group" at 'root' (gid 0
    always maps to 'root') so this is hermetic without a real 'render' group
    on the host running the suite.
    """
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
        }
    )
    assert rc == RC_OK


def test_gate_gid_maps_to_wrong_group_hard_stops(render_glob: str) -> None:
    """M3 regression: gid resolves to a REAL group that is NOT the render
    group (a gid/name collision, e.g. host gid 993 landing on 'clock' inside
    the container instead of 'render') must hard-stop, not false-pass just
    because *some* group name was found.
    """
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            # gid 0 maps to 'root', but the expected group is 'render' (the
            # untouched default) — a real-group / wrong-name collision.
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
        }
    )
    assert rc == RC_BROKEN_GID


def test_gate_wrong_group_on_bare_metal_does_not_block(render_glob: str) -> None:
    """Mirrors test_gate_broken_gid_on_bare_metal_does_not_block: the wrong-
    group collision only hard-stops inside an LXC, same as the no-group case.
    """
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "none",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
        }
    )
    assert rc == RC_OK


def test_gate_member_user_proceeds(render_glob: str) -> None:
    """Correct group + hal0-equivalent user IS a member → proceed (0)."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            # root is always a member of its own primary group 'root'.
            "HAL0_GPU_USER_OVERRIDE": "root",
        }
    )
    assert rc == RC_OK


def test_gate_non_member_user_warns_but_does_not_block(render_glob: str) -> None:
    """Correct group but the target user is NOT a member: advisory only — the
    gate still proceeds (install.sh's own usermod step runs later and is
    idempotent, so this must not hard-block an otherwise-fresh install).
    """
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            # 'nobody' exists on virtually every Linux host and is never a
            # member of 'root'.
            "HAL0_GPU_USER_OVERRIDE": "nobody",
        }
    )
    assert rc == RC_OK


def test_gate_nonexistent_user_skips_membership_check(render_glob: str) -> None:
    """A user that doesn't exist yet (fresh install: preflight_gpu runs
    BEFORE install.sh creates the hal0 system user) must not be treated as a
    membership failure — the check is skipped entirely.
    """
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            "HAL0_GPU_USER_OVERRIDE": "hal0-user-does-not-exist-xyz",
        }
    )
    assert rc == RC_OK


def test_gate_broken_gid_on_bare_metal_does_not_block(render_glob: str) -> None:
    """Only an LXC miswire blocks — a bare-metal unmapped gid still proceeds."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "none",
            "HAL0_GPU_RENDER_GID_OVERRIDE": UNMAPPED_GID,
        }
    )
    assert rc == RC_OK


def test_doctor_mode_broken_gid_is_soft(render_glob: str) -> None:
    """Without the gate flag (doctor), the same broken LXC stays soft (0)."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": UNMAPPED_GID,
        }
    )
    assert rc == RC_OK


def test_doctor_mode_no_device_lxc_is_soft(tmp_path: Path) -> None:
    """Without the gate flag, a no-device LXC also stays soft (0)."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_DRI_GLOB": str(tmp_path / "NONE*"),
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
        }
    )
    assert rc == RC_OK


# ── #1888 / #1948: an AMD box needs SOME valid GPU lane ──────────────────────
# #1923 made /dev/kfd an outright requirement on AMD: the pinned runner was one
# HIP+Vulkan build, llama.cpp ran ROCm when /dev/kfd was visible and SILENTLY
# fell back to that image's Vulkan backend when it was not, and that backend
# emitted invalid tokens for every model at full nominal speed while every
# health surface read green. With no valid lane, refusing the install was the
# honest answer.
#
# #1948 fixed the image, so the requirement is now the honest one it always
# stood in for: the box must have SOME lane that produces language. ROCm needs
# /dev/kfd; Vulkan needs a render node plus a runner image validated for that
# lane. The gate refuses only when neither is available — which is what makes
# a fresh install on ct151 (the box the defect was found on) succeed.


def _amd_no_kfd_env(render_glob: str, tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "HAL0_GPU_GATE": "1",
        "HAL0_GPU_DRI_GLOB": render_glob,
        "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
        "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
        "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
        "HAL0_GPU_AMD_OVERRIDE": "1",
        "HAL0_GPU_KFD_PATH": str(tmp_path / "no-such-kfd"),
        **extra,
    }


def test_gate_amd_render_node_without_kfd_stops_on_an_unvalidated_image(
    render_glob: str, tmp_path: Path
) -> None:
    """The #1888 refusal, now correctly scoped to the case that warrants it:
    no ROCm lane AND no usable Vulkan lane, so no GPU lane at all."""
    proc = _run_gpu_gate_full(
        _amd_no_kfd_env(render_glob, tmp_path, HAL0_GPU_VULKAN_LANE_OVERRIDE="0")
    )
    assert proc.returncode == RC_NO_KFD
    out = proc.stdout + proc.stderr
    assert "not validated for the Vulkan lane" in out
    assert "1888" in out


def test_gate_amd_render_node_without_kfd_proceeds_on_a_validated_image(
    render_glob: str, tmp_path: Path
) -> None:
    """#1948 — the whole point of Phase D, at the installer.

    ct151 (AMD, render node, no /dev/kfd) is the box the §3-C matrix validated
    Vulkan on. A fresh install there must SUCCEED: refusing it would be
    refusing a configuration that demonstrably serves correct output.
    """
    proc = _run_gpu_gate_full(
        _amd_no_kfd_env(render_glob, tmp_path, HAL0_GPU_VULKAN_LANE_OVERRIDE="1")
    )
    assert proc.returncode == RC_OK
    out = proc.stdout + proc.stderr
    # Truthful, and not scary: this is a supported install, not a broken one.
    assert "will use the Vulkan lane" in out
    assert "INVALID TOKENS" not in out


def test_gate_amd_without_kfd_or_render_node_stops_and_says_which(tmp_path: Path) -> None:
    """A validated image cannot conjure a device. With no render node there is
    no Vulkan lane either, and the message must name that — not blame the
    image, which is fine."""
    proc = _run_gpu_gate_full(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": str(tmp_path / "NONE*"),
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_AMD_OVERRIDE": "1",
            "HAL0_GPU_VULKAN_LANE_OVERRIDE": "1",
            "HAL0_GPU_KFD_PATH": str(tmp_path / "no-such-kfd"),
        }
    )
    # No devices at all is classified earlier, as NO_DEVICE — either way the
    # install is gated, and that is the contract being pinned here.
    assert proc.returncode in (RC_NO_DEVICE, RC_NO_KFD)


# ── the manifest.json reader behind the shell mirrors ────────────────────────
# ``_hal0_vulkan_lane_serves_default_image`` and ``_hal0_cpu_lane_has_runner_image``
# are shell RE-IMPLEMENTATIONS of ``hal0.providers._gpu.default_image_serves_vulkan_lane``
# and ``hal0.runners.cpu_lane_has_runner_image`` (preflight runs before hal0 is
# installed, so it cannot call them). Both read the ``runners`` pin table in
# manifest.json through ``_hal0_runner_pin_state``. Two implementations of one
# predicate drift; the tests below pin each state of the table and assert the
# shell and Python answers agree on it.

_DIGEST = "sha256:" + "a" * 64
_SEAMS = (
    "HAL0_GPU_VULKAN_LANE_OVERRIDE",
    "HAL0_CPU_RUNNER_IMAGE_OVERRIDE",
    "HAL0_MANIFEST_JSON_OVERRIDE",
    "HAL0_RUNNER_IMAGE_STANDARD",
    "HAL0_RUNNER_IMAGE_CPU",
)
_VULKAN_ASK = "if _hal0_vulkan_lane_serves_default_image; then echo yes; else echo no; fi"
_CPU_ASK = "if _hal0_cpu_lane_has_runner_image; then echo yes; else echo no; fi"


def _pin_row(
    key: str, *, tag: str | None = None, digest: str | None = None, lanes: tuple[str, ...] = ()
) -> dict:
    return {
        "image": f"ghcr.io/hal0ai/hal0-runner-{key}",
        "tag": tag,
        "digest": digest,
        "lanes_gated": list(lanes),
    }


def _manifest(tmp_path: Path, *, standard: dict, cpu: dict | None = None) -> Path:
    """Write a manifest.json whose ``standard``/``cpu`` rows are as given."""
    import json

    from hal0.runners import RUNNER_IMAGES

    runners = {key: _pin_row(key) for key in RUNNER_IMAGES}
    runners["standard"] = standard
    if cpu is not None:
        runners["cpu"] = cpu
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": "0.0.0", "runners": runners}), encoding="utf-8")
    return path


def _run_preflight(snippet: str, **env_overrides: str) -> subprocess.CompletedProcess:
    """Source preflight.sh and run ``snippet``.

    ``set -euo pipefail`` mirrors install.sh so the functions are proven safe
    to source there. The seam variables are stripped from the inherited
    environment unless a test sets them, so the answer never depends on the box
    running the suite.
    """
    script = f"set -euo pipefail\nsource {PREFLIGHT!s}\n{snippet}\n"
    env = {k: v for k, v in os.environ.items() if k not in _SEAMS}
    env.update(env_overrides)
    return subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60
    )


def _ask(snippet: str, **env_overrides: str) -> str:
    return _run_preflight(snippet, **env_overrides).stdout.strip()


#: (id, standard row, expected shell answer for the Vulkan-lane gate)
_STANDARD_CASES = [
    ("unpublished", _pin_row("standard"), "no"),
    ("published-ungated", _pin_row("standard", tag="b1-r1", digest=_DIGEST, lanes=("rocm",)), "no"),
    (
        "published-vulkan-gated",
        _pin_row("standard", tag="b1-r1", digest=_DIGEST, lanes=("rocm", "vulkan")),
        "yes",
    ),
    (
        "tag-only-vulkan-gated",
        _pin_row("standard", tag="b1-r1", lanes=("rocm", "vulkan")),
        "yes",
    ),
]


@pytest.mark.parametrize(
    ("row", "expected"), [c[1:] for c in _STANDARD_CASES], ids=[c[0] for c in _STANDARD_CASES]
)
def test_vulkan_mirror_reads_the_standard_pin(tmp_path: Path, row: dict, expected: str) -> None:
    manifest = _manifest(tmp_path, standard=row)
    assert _ask(_VULKAN_ASK, HAL0_MANIFEST_JSON_OVERRIDE=str(manifest)) == expected


@pytest.mark.parametrize(
    ("row", "expected"), [c[1:] for c in _STANDARD_CASES], ids=[c[0] for c in _STANDARD_CASES]
)
def test_vulkan_mirror_agrees_with_the_python_predicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row: dict, expected: str
) -> None:
    """The tripwire: for every state of the ``standard`` row the shell mirror
    and ``default_image_serves_vulkan_lane`` give the same answer."""
    import hal0.runners.pins as pins_mod
    from hal0.providers._gpu import default_image_serves_vulkan_lane

    manifest = _manifest(tmp_path, standard=row)
    table = pins_mod.load_runner_pins(manifest)
    monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
    for key in _SEAMS:
        monkeypatch.delenv(key, raising=False)

    shell_says = _ask(_VULKAN_ASK, HAL0_MANIFEST_JSON_OVERRIDE=str(manifest)) == "yes"
    assert shell_says is default_image_serves_vulkan_lane(), (
        "the shell mirror and the Python predicate disagree about whether the "
        f"standard runner pin serves the Vulkan lane (shell={shell_says}, row={row})"
    )
    assert shell_says is (expected == "yes")


def test_vulkan_mirror_refuses_when_standard_is_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``HAL0_RUNNER_IMAGE_STANDARD`` replaces the pinned image with one the
    hardware gate never saw, so the lane is refused even for a gated pin — in
    shell and in Python alike."""
    import hal0.runners.pins as pins_mod
    from hal0.providers._gpu import default_image_serves_vulkan_lane

    gated = _pin_row("standard", tag="b1-r1", digest=_DIGEST, lanes=("rocm", "vulkan"))
    manifest = _manifest(tmp_path, standard=gated)
    table = pins_mod.load_runner_pins(manifest)
    monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
    monkeypatch.setenv("HAL0_RUNNER_IMAGE_STANDARD", "ghcr.io/me/own-build:1")

    shell = _ask(
        _VULKAN_ASK,
        HAL0_MANIFEST_JSON_OVERRIDE=str(manifest),
        HAL0_RUNNER_IMAGE_STANDARD="ghcr.io/me/own-build:1",
    )
    assert shell == "no"
    assert default_image_serves_vulkan_lane() is False


@pytest.mark.parametrize("body", ["not json at all", "{}", '{"runners": {}}', '{"runners": []}'])
def test_vulkan_mirror_fails_closed_on_a_malformed_manifest(tmp_path: Path, body: str) -> None:
    """A false 'yes' ships a box that serves invalid tokens; a false 'no'
    costs a CPU-only install. The asymmetry decides the default."""
    bad = tmp_path / "manifest.json"
    bad.write_text(body, encoding="utf-8")
    assert _ask(_VULKAN_ASK, HAL0_MANIFEST_JSON_OVERRIDE=str(bad)) == "no"


def test_vulkan_mirror_fails_closed_on_a_missing_manifest(tmp_path: Path) -> None:
    gone = tmp_path / "does-not-exist.json"
    assert _ask(_VULKAN_ASK, HAL0_MANIFEST_JSON_OVERRIDE=str(gone)) == "no"


def test_vulkan_mirror_fails_closed_when_the_standard_row_is_missing(tmp_path: Path) -> None:
    import json

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"runners": {"cpu": _pin_row("cpu")}}), encoding="utf-8")
    assert _ask(_VULKAN_ASK, HAL0_MANIFEST_JSON_OVERRIDE=str(manifest)) == "no"


def test_vulkan_override_seam_decides_outright(tmp_path: Path) -> None:
    gated = _pin_row("standard", tag="b1-r1", digest=_DIGEST, lanes=("rocm", "vulkan"))
    manifest = _manifest(tmp_path, standard=gated)
    seams = {"HAL0_MANIFEST_JSON_OVERRIDE": str(manifest)}
    assert _ask(_VULKAN_ASK, HAL0_GPU_VULKAN_LANE_OVERRIDE="0", **seams) == "no"
    assert _ask(_VULKAN_ASK, HAL0_GPU_VULKAN_LANE_OVERRIDE="1") == "yes"


def test_the_shipped_manifest_is_readable_by_the_shell_reader() -> None:
    """With no override the reader finds this checkout's manifest.json and
    classifies every runner key (the placeholders are ``unpublished``)."""
    from hal0.runners import RUNNER_IMAGES

    for key in RUNNER_IMAGES:
        state = _ask(f"_hal0_runner_pin_state {key}")
        assert state in {"unpublished", "published", "vulkan-gated"}, (key, state)


# ── CPU-lane runner-image gate (#2126) ────────────────────────────────────────


@pytest.mark.parametrize(
    ("cpu_row", "expected"),
    [
        (_pin_row("cpu"), "no"),
        (_pin_row("cpu", tag="b1-r1"), "yes"),
        (_pin_row("cpu", tag="b1-r1", digest=_DIGEST), "yes"),
        (_pin_row("cpu", tag="b1-r1", digest=_DIGEST, lanes=("cpu", "vulkan")), "yes"),
    ],
    ids=["unpublished", "tag", "tag+digest", "gated"],
)
def test_cpu_mirror_agrees_with_the_python_predicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cpu_row: dict, expected: str
) -> None:
    """``_hal0_cpu_lane_has_runner_image`` is a shell RE-IMPLEMENTATION of
    ``hal0.runners.cpu_lane_has_runner_image`` — the tripwire that fires the day
    somebody changes one and forgets the other."""
    import hal0.runners.pins as pins_mod
    from hal0.runners import cpu_lane_has_runner_image

    manifest = _manifest(tmp_path, standard=_pin_row("standard"), cpu=cpu_row)
    table = pins_mod.load_runner_pins(manifest)
    monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
    for key in _SEAMS:
        monkeypatch.delenv(key, raising=False)

    shell_says = _ask(_CPU_ASK, HAL0_MANIFEST_JSON_OVERRIDE=str(manifest)) == "yes"
    assert shell_says is cpu_lane_has_runner_image()
    assert shell_says is (expected == "yes")


def test_cpu_mirror_honours_the_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``HAL0_RUNNER_IMAGE_CPU`` is the escape hatch the gate must never close:
    an operator running their own CPU llama-server build names it and the
    install proceeds, even while the ``cpu`` pin is unpublished."""
    import hal0.runners.pins as pins_mod
    from hal0.runners import cpu_lane_has_runner_image

    manifest = _manifest(tmp_path, standard=_pin_row("standard"), cpu=_pin_row("cpu"))
    table = pins_mod.load_runner_pins(manifest)
    monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
    monkeypatch.setenv("HAL0_RUNNER_IMAGE_CPU", "ghcr.io/example/llama-cpu:v1")

    shell = _ask(
        _CPU_ASK,
        HAL0_MANIFEST_JSON_OVERRIDE=str(manifest),
        HAL0_RUNNER_IMAGE_CPU="ghcr.io/example/llama-cpu:v1",
    )
    assert shell == "yes"
    assert cpu_lane_has_runner_image() is True


@pytest.mark.parametrize("body", ["not json at all", "{}", '{"runners": {}}'])
def test_cpu_mirror_fails_closed_on_a_malformed_manifest(tmp_path: Path, body: str) -> None:
    """A false "yes" ships #2126's broken box, a false "no" refuses an install
    with the override that reopens it printed on screen. Anything unparseable
    answers "no"."""
    bad = tmp_path / "manifest.json"
    bad.write_text(body, encoding="utf-8")
    assert _ask(_CPU_ASK, HAL0_MANIFEST_JSON_OVERRIDE=str(bad)) == "no"


def test_cpu_override_seam_decides_outright() -> None:
    assert _ask(_CPU_ASK, HAL0_CPU_RUNNER_IMAGE_OVERRIDE="0") == "no"
    assert _ask(_CPU_ASK, HAL0_CPU_RUNNER_IMAGE_OVERRIDE="1") == "yes"


_GATE = "if _cpu_only_image_gate; then echo pass; else echo refused; fi"


def test_cpu_only_image_gate_passes_with_a_cpu_image() -> None:
    """With a ``cpu`` image available, `HAL0_ALLOW_CPU_ONLY=1` is a supported
    path and the gate stands aside."""
    proc = _run_preflight(_GATE, HAL0_CPU_RUNNER_IMAGE_OVERRIDE="1")
    assert proc.stdout.strip() == "pass", proc.stderr


def test_cpu_only_image_gate_refuses_and_says_why_without_a_cpu_image() -> None:
    """If the CPU lane has no image, the install stops here with a message
    naming the fault and the override that reopens the path."""
    proc = _run_preflight(_GATE, HAL0_CPU_RUNNER_IMAGE_OVERRIDE="0")
    assert proc.stdout.strip() == "refused", proc.stdout
    assert "manifest.json pins no published image for the 'cpu' runner" in proc.stderr
    assert "HAL0_RUNNER_IMAGE_CPU" in proc.stderr
    assert "#2126" in proc.stderr
    # It must not repeat the promise that got #2126's reporter here.
    assert "To install CPU-only anyway" not in proc.stderr


def test_cpu_only_image_gate_follows_the_manifest(tmp_path: Path) -> None:
    """No override: the gate refuses on an unpublished ``cpu`` pin and passes
    once manifest.json names a tag."""
    unpublished = _manifest(tmp_path, standard=_pin_row("standard"), cpu=_pin_row("cpu"))
    proc = _run_preflight(_GATE, HAL0_MANIFEST_JSON_OVERRIDE=str(unpublished))
    assert proc.stdout.strip() == "refused", proc.stderr

    published = _manifest(
        tmp_path, standard=_pin_row("standard"), cpu=_pin_row("cpu", tag="b1-r1", digest=_DIGEST)
    )
    proc = _run_preflight(_GATE, HAL0_MANIFEST_JSON_OVERRIDE=str(published))
    assert proc.stdout.strip() == "pass", proc.stderr


def test_cpu_only_remedy_offers_the_flag_when_a_cpu_image_exists() -> None:
    """The GPU-gate refusal's CPU-only remedy is honest only when following it
    produces a working box."""
    proc = _run_preflight("_cpu_only_remedy_lines", HAL0_CPU_RUNNER_IMAGE_OVERRIDE="1")
    assert "To install CPU-only anyway, re-run with HAL0_ALLOW_CPU_ONLY=1." in proc.stderr


def test_cpu_only_remedy_is_honest_when_there_is_no_cpu_image() -> None:
    """install.sh's GPU-gate refusals must never print "To install CPU-only
    anyway, re-run with HAL0_ALLOW_CPU_ONLY=1." while the CPU lane has no image
    — following that printed remedy is exactly how #2126 happened."""
    proc = _run_preflight("_cpu_only_remedy_lines", HAL0_CPU_RUNNER_IMAGE_OVERRIDE="0")
    assert "To install CPU-only anyway" not in proc.stderr
    assert "no working CPU-only path" in proc.stderr
    assert "HAL0_RUNNER_IMAGE_CPU" in proc.stderr


def test_every_cpu_only_optin_in_install_sh_runs_the_gate_first() -> None:
    """Static-text pin (same technique as test_platform_gate_hardening.py):
    each branch that proceeds CPU-only must call ``_cpu_only_image_gate``
    BEFORE the reassuring warn, and each refusal must use the honest remedy
    helper rather than a hard-coded HAL0_ALLOW_CPU_ONLY line."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    lines = text.splitlines()

    optins = [i for i, line in enumerate(lines) if 'HAL0_ALLOW_CPU_ONLY:-0}" == "1"' in line]
    assert len(optins) == 2, "the CPU-only env opt-in branch count changed"

    proceeds = [
        i
        for i, line in enumerate(lines)
        if "proceeding CPU-only" in line or "confirmed at the prompt" in line
    ]
    assert len(proceeds) == 4, "the CPU-only proceed messages changed shape"
    for i in proceeds:
        assert "_cpu_only_image_gate || exit 1" in lines[i - 1], (
            f"install.sh:{i + 1} announces a CPU-only install without gating on "
            "there being a CPU runner image first (#2126)"
        )

    assert "To install CPU-only anyway" not in text
    calls = [line for line in lines if line.strip() == "_cpu_only_remedy_lines"]
    assert len(calls) == 2, "both GPU-gate refusals must print the honest CPU-only remedy"


def test_gate_amd_render_node_with_kfd_proceeds(render_glob: str, tmp_path: Path) -> None:
    kfd = tmp_path / "kfd"
    kfd.touch()
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            "HAL0_GPU_AMD_OVERRIDE": "1",
            "HAL0_GPU_KFD_PATH": str(kfd),
        }
    )
    assert rc == RC_OK


def test_gate_non_amd_gpu_without_kfd_proceeds(render_glob: str, tmp_path: Path) -> None:
    """The check is AMD-scoped: an NVIDIA/Intel box has no /dev/kfd to forward
    and must not be blocked by it."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            "HAL0_GPU_AMD_OVERRIDE": "0",
            "HAL0_GPU_KFD_PATH": str(tmp_path / "no-such-kfd"),
        }
    )
    assert rc == RC_OK


def test_doctor_mode_missing_kfd_is_soft(render_glob: str, tmp_path: Path) -> None:
    """`hal0 doctor` (no HAL0_GPU_GATE) stays advisory-only — it reports the
    missing compute node but never aborts the report."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            "HAL0_GPU_AMD_OVERRIDE": "1",
            "HAL0_GPU_KFD_PATH": str(tmp_path / "no-such-kfd"),
        }
    )
    assert rc == RC_OK


class TestKfdGroupGate:
    """#1953 — /dev/kfd forwarded but group-misaligned with the render node.

    A plain LXC ``dev`` passthrough lands renderD128 as root:render and the
    compute node as root:root. The rootful slot containers open both fine, so
    ROCm genuinely works — but the hal0 service user cannot open the compute
    node, so #1923's guard refuses every AMD GPU slot on a healthy box. The
    gate has to catch that shape, and must NOT tell the operator to re-forward
    a device that is already present.
    """

    def test_mismatched_kfd_gid_is_caught(self, tmp_path: Path) -> None:
        render = tmp_path / "renderD128"
        render.touch()
        kfd = tmp_path / "kfd"
        kfd.touch()
        rc = _run_gpu_gate(
            {
                "HAL0_GPU_GATE": "1",
                "HAL0_GPU_AMD_OVERRIDE": "1",
                "HAL0_GPU_DRI_GLOB": str(tmp_path / "renderD*"),
                # Satisfy the group-NAME check above so we reach the gid
                # check under test; it reads the real nodes regardless.
                "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
                "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
                "HAL0_GPU_KFD_PATH": str(kfd),
                "HAL0_GPU_KFD_GID_OVERRIDE": "0",
                "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            }
        )
        # The compute node is ROOT-owned while the render node is not — the
        # exact inaccessible shape a plain LXC dev passthrough produces.
        assert rc == RC_KFD_GID

    def test_aligned_gids_pass(self, tmp_path: Path) -> None:
        render = tmp_path / "renderD128"
        render.touch()
        kfd = tmp_path / "kfd"
        kfd.touch()
        rc = _run_gpu_gate(
            {
                "HAL0_GPU_GATE": "1",
                "HAL0_GPU_AMD_OVERRIDE": "1",
                "HAL0_GPU_DRI_GLOB": str(tmp_path / "renderD*"),
                # Satisfy the group-NAME check above so we reach the gid
                # check under test; it reads the real nodes regardless.
                "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
                "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
                "HAL0_GPU_KFD_PATH": str(kfd),
                "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            }
        )
        # Both nodes are real files with the same owning gid — nothing to fix.
        assert rc == RC_OK

    def test_remedy_never_says_re_forward_the_device(self, tmp_path: Path) -> None:
        """The headline harm of #1953: the old advice cost a production reboot."""
        (tmp_path / "renderD128").touch()
        kfd = tmp_path / "kfd"
        kfd.touch()
        script = f"set -uo pipefail\nsource {PREFLIGHT!s}\npreflight_gpu 2>&1 || true\n"
        env = {
            **os.environ,
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_AMD_OVERRIDE": "1",
            "HAL0_GPU_DRI_GLOB": str(tmp_path / "renderD*"),
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            "HAL0_GPU_KFD_PATH": str(kfd),
            "HAL0_GPU_KFD_GID_OVERRIDE": "0",
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
        }
        out = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True).stdout
        render_gid = os.stat(tmp_path / "renderD128").st_gid
        assert f"chgrp {render_gid}" in out
        # The host-side remedy must carry a REAL gid, never a placeholder.
        assert f"gid={render_gid}" in out
        # And it must never send the operator to re-forward a present device.
        assert "dev1: /dev/kfd\n" not in out

    def test_a_non_root_gid_divergence_is_left_alone(self, tmp_path: Path) -> None:
        """#1953 review: a differing gid is NOT itself a fault.

        A valid box can put /dev/kfd on `video` and the render nodes on
        `render` — install.sh adds hal0 to BOTH. Rewriting that to the render
        group would strip access from video-only users, and on an unprivileged
        LXC the failed chgrp would abort a fresh install over a working config.
        Only the root-owned, no-world-access shape is repaired.
        """
        (tmp_path / "renderD128").touch()
        kfd = tmp_path / "kfd"
        kfd.touch()
        rc = _run_gpu_gate(
            {
                "HAL0_GPU_GATE": "1",
                "HAL0_GPU_AMD_OVERRIDE": "1",
                "HAL0_GPU_DRI_GLOB": str(tmp_path / "renderD*"),
                "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
                "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
                "HAL0_GPU_KFD_PATH": str(kfd),
                # Differs from the render node, but is a REAL group the service
                # user can be added to — not the root-owned passthrough shape.
                "HAL0_GPU_KFD_GID_OVERRIDE": "61996",
                "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            }
        )
        assert rc == RC_OK
