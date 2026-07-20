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
