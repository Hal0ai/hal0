"""Tests for the ``hal0 doctor profiles`` slot↔profile audit classifiers.

Pure functions over plain fixtures (no catalog, no filesystem, no podman) —
each maps to a real failure mode:

  * ``check_slot_profile_refs``       — slot points at a deleted profile
                                        (KeyError at slot start).
  * ``check_profile_images_present``  — in-use profile's image not pulled.
  * ``_image_repo``                   — tag/digest stripping for image match.
"""

from __future__ import annotations

import json as jsonlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer

from hal0.cli.doctor_commands import (
    _image_repo,
    check_profile_images_present,
    check_slot_profile_refs,
    doctor_profiles,
)


def _profile(name, *, seed=False, cloned_from=None, flags="", image="", used_by=()):
    """Build a ResolvedProfile-shaped stand-in (only the read attrs matter)."""
    return SimpleNamespace(
        name=name,
        seed=seed,
        cloned_from=cloned_from,
        flags=flags,
        image=image,
        used_by=tuple(used_by),
    )


# ── check_slot_profile_refs ───────────────────────────────────────────────────


def test_refs_ok_when_slot_profile_exists() -> None:
    rows = check_slot_profile_refs([("primary", "rocm")], {"rocm", "tts"})
    assert rows == [{"label": "primary", "status": "ok", "detail": "→ rocm"}]


def test_refs_drift_when_profile_missing() -> None:
    rows = check_slot_profile_refs([("primary", "ghost")], {"rocm"})
    assert len(rows) == 1
    assert rows[0]["status"] == "drift"
    assert "ghost" in rows[0]["detail"]
    assert "fail to start" in rows[0]["detail"]


def test_refs_skip_base_image_slots() -> None:
    # profile=None is a base-image slot — legal, not a reference to resolve.
    assert check_slot_profile_refs([("primary", None), ("x", "")], {"rocm"}) == []


def test_refs_flag_a_profileless_capability_slot() -> None:
    """#1830: a profile-less embedding/reranking slot is a silent 501.

    Nothing warned about this shape. The slot loads to ``state=ready`` and
    returns HTTP 501 from the one endpoint it exists to serve, because the
    profile is what carries llama-server's ``--embedding``/``--reranking``.
    New slots infer it at create time; an upgraded box carries whatever
    profile-less slots its operator created historically, and no seed loop
    back-fills an existing TOML — so doctor has to name it.
    """
    rows = check_slot_profile_refs(
        [("embed", None), ("rerank", "")],
        {"embedding", "reranking"},
        slot_types={"embed": "embedding", "rerank": "reranking"},
    )
    assert [r["status"] for r in rows] == ["drift", "drift"]
    assert "501" in rows[0]["detail"]
    assert "hal0 slot edit embed --profile embedding" in rows[0]["detail"]
    assert "hal0 slot edit rerank --profile reranking" in rows[1]["detail"]


def test_refs_repair_command_is_device_aware(tmp_hal0_home: str) -> None:
    """The repair must name the profile create-time inference would choose.

    Keying the repair on the slot TYPE alone told an NPU embedding slot to
    run ``hal0 slot edit <name> --profile embedding`` — llama-server flags
    (``--embedding -fa auto -b 8192 -ub 8192``) onto the FLM runtime. The
    create-time rule in the same release answers ``flm`` for that pair, so
    doctor now reuses it rather than keeping a second, disagreeing rule.
    """
    rows = check_slot_profile_refs(
        [("npu-embed", None), ("cpu-embed", None)],
        {"embedding", "flm"},
        slot_types={"npu-embed": "embedding", "cpu-embed": "embedding"},
        slot_devices={"npu-embed": "npu", "cpu-embed": "cpu"},
    )
    assert "hal0 slot edit npu-embed --profile flm" in rows[0]["detail"]
    assert "hal0 slot edit cpu-embed --profile embedding" in rows[1]["detail"]


def test_refs_never_recommend_a_profile_missing_from_the_catalog(tmp_hal0_home: str) -> None:
    """Repairing must not turn a 501 into a slot that cannot start at all.

    If the seed the repair names has been removed/renamed in the installed
    catalog, ``hal0 slot edit <slot> --profile <name>`` would write a dangling
    reference — ``resolve_slot_profile`` then raises ``KeyError`` at start. The
    row stays drift (the slot really is broken), but names no profile.
    """
    rows = check_slot_profile_refs(
        [("rerank", None)],
        {"chat"},  # catalog without the reranking seed
        slot_types={"rerank": "reranking"},
        slot_devices={"rerank": "cpu"},
    )
    assert rows[0]["status"] == "drift"
    assert "--profile" not in rows[0]["detail"]
    assert "501" in rows[0]["detail"]


def test_refs_still_skip_a_profileless_llm_slot() -> None:
    """An llm slot has no mode flag at stake — profile-less is legal there."""
    assert check_slot_profile_refs([("agent", None)], {"chat"}, slot_types={"agent": "llm"}) == []


# ── check_profile_images_present ──────────────────────────────────────────────


def test_images_skipped_entirely_when_podman_unavailable() -> None:
    p = _profile("rocm", image="ghcr.io/hal0ai/tb:v1", used_by=("primary",))
    assert check_profile_images_present([p], None) == []


def test_images_ignore_unused_profiles() -> None:
    # An unused profile whose image is absent is not a live problem.
    p = _profile("rocm", image="ghcr.io/hal0ai/tb:v1", used_by=())
    assert check_profile_images_present([p], set()) == []


# ── _image_repo ───────────────────────────────────────────────────────────────


def test_image_repo_strips_tag() -> None:
    assert _image_repo("ghcr.io/hal0ai/toolbox:v1.2") == "ghcr.io/hal0ai/toolbox"


def test_image_repo_strips_digest() -> None:
    assert _image_repo("ghcr.io/hal0ai/toolbox@sha256:abc") == "ghcr.io/hal0ai/toolbox"


def test_image_repo_keeps_host_port() -> None:
    # A registry port (host:5000) must survive; only the trailing tag is dropped.
    assert _image_repo("localhost:5000/tb:latest") == "localhost:5000/tb"


# ── doctor_profiles end-to-end scan (list_slots id-awareness, inc4) ───────────


def test_doctor_profiles_reports_id_keyed_slot_by_real_name(
    tmp_hal0_home: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # P3-runtime-db inc4: the `hal0 doctor profiles` scan loop enumerates
    # list_slots() stems directly; on an id-keyed box that's a digit, not the
    # slot's real name. The reported label must be the real name.
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "143.toml").write_text(
        '[slot]\nid = 143\nname = "brain"\nport = 8081\nprofile = "ghost-profile"\n',
        encoding="utf-8",
    )
    with pytest.raises(typer.Exit) as exc_info:
        doctor_profiles(json_output=True)
    assert exc_info.value.exit_code == 1  # ghost-profile doesn't exist -> drift

    out = jsonlib.loads(capsys.readouterr().out)
    summary = " ".join(d.get("summary", "") for d in out)
    assert "brain" in summary
    assert "143" not in summary
