"""``ReleaseManifest.upgrade_from`` — the supported-upgrade-path floor.

``manifest.json`` declares the floor and ``release.yml`` copies it into every
channel manifest; ``prepare()`` refuses to stage a release from a box below it.
The converters for state older than the floor are deleted, so the floor is
what keeps such a box from activating code that can no longer read its state.

It must still fail OPEN on anything unparseable. This is a supportability
statement, not a security control (cosign is the security control), so a
malformed specifier or a non-PEP-440 installed version degrades to "no
statement", never to a blocked update.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import hal0.updater.updater as updater_module
from hal0.updater.updater import (
    ReleaseManifest,
    Updater,
    UpdateUpgradePathUnsupported,
    _enforce_upgrade_floor,
    floor_comparable_version,
)

_ROOT = Path(__file__).resolve().parents[2]


def _manifest(upgrade_from: str = "") -> ReleaseManifest:
    return ReleaseManifest.model_validate(
        {
            "_schema": "hal0.releases.v1",
            "version": "1.4.0",
            "channel": "stable",
            "release_kind": "stable",
            "upgrade_from": upgrade_from,
            "url": "https://example.invalid/hal0-1.4.0.tar.gz",
            "bundle_url": "https://example.invalid/hal0-1.4.0.tar.gz.bundle",
            "digest_sha256": "0" * 64,
            "signer_identity": "^https://github.com/hal0ai/hal0/.*$",
        }
    )


def _shipped_floor() -> str:
    data = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))
    return str(data.get("upgrade_from") or "").strip()


# ── the shipped floor ─────────────────────────────────────────────────────────


def test_shipped_manifest_declares_the_1_3_floor() -> None:
    """The floor is a deliberate operator decision: upgrades start at 1.3.0."""
    assert _shipped_floor() == ">=1.3.0"


def test_release_workflow_copies_the_floor_into_every_channel_manifest() -> None:
    """release.yml must emit ``upgrade_from`` from manifest.json, not a literal."""
    text = (_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'repo_manifest.get("upgrade_from")' in text
    assert '"upgrade_from": upgrade_from,' in text


@pytest.mark.parametrize(
    "installed",
    ["1.3.0", "1.3.1", "1.4.0", "1.4.0-rc.1", "1.4.0rc1", "1.4.0-nightly.20261011035008"],
)
def test_releases_inside_the_window_are_admitted(installed: str) -> None:
    _enforce_upgrade_floor(_manifest(_shipped_floor()), current=installed)


@pytest.mark.parametrize(
    "installed",
    [
        "0.9.8",
        "1.0.0",
        "1.1.0",
        "1.2.0",
        "1.3.0-rc.1",
        "1.0.0-nightly.20260801120000",
        "1.0.0-nightly.20260801",
    ],
)
def test_below_the_1_3_floor_is_refused(installed: str) -> None:
    with pytest.raises(UpdateUpgradePathUnsupported) as ei:
        _enforce_upgrade_floor(_manifest(_shipped_floor()), current=installed)
    assert ei.value.details["installed_version"] == installed
    assert ei.value.details["upgrade_from"] == ">=1.3.0"
    assert ei.value.details["target_version"] == "1.4.0"
    assert installed in str(ei.value)
    assert ">=1.3.0" in str(ei.value)
    assert "reinstall" in ei.value.details["hint"]


def test_prepare_refuses_a_1_2_box_before_downloading(
    tmp_hal0_home: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``hal0 update`` on a 1.2.0 box stops at the floor, before any download."""
    manifest = _manifest(_shipped_floor())

    async def fake_fetch(
        channel: str, *, job_id: str | None = None
    ) -> tuple[dict[str, Any], ReleaseManifest, str]:
        return manifest.model_dump(by_alias=True), manifest, "https://example.test/stable.json"

    async def unexpected_download(url: str, dest: Path) -> None:
        raise AssertionError(f"download reached below the upgrade floor: {url}")

    monkeypatch.setattr(updater_module, "_is_editable_install", lambda: False)
    monkeypatch.setattr(updater_module, "_fetch_verified_release_manifest", fake_fetch)
    monkeypatch.setattr(updater_module, "_download", unexpected_download)
    monkeypatch.setattr(updater_module, "installed_version", lambda: "1.2.0")

    with pytest.raises(UpdateUpgradePathUnsupported) as ei:
        asyncio.run(Updater(channel="stable").prepare())
    assert ei.value.details["installed_version"] == "1.2.0"
    assert ei.value.details["upgrade_from"] == ">=1.3.0"


def test_nightly_maps_onto_a_dev_release_of_its_base() -> None:
    assert floor_comparable_version("1.4.0-nightly.20261011035008") == "1.4.0.dev20261011035008"
    assert floor_comparable_version("1.4.0") == "1.4.0"
    assert floor_comparable_version("1.4.0-rc.1") == "1.4.0-rc.1"


def test_compound_specifier_is_honoured() -> None:
    _enforce_upgrade_floor(_manifest(">=0.9.0,<2.0"), current="1.0.0")
    with pytest.raises(UpdateUpgradePathUnsupported):
        _enforce_upgrade_floor(_manifest(">=0.9.0,<2.0"), current="2.1.0")


# ── inert when undeclared, fail-open on anything unparseable ──────────────────


@pytest.mark.parametrize("empty", ["", "   "])
def test_no_floor_declared_is_a_noop(empty: str) -> None:
    _enforce_upgrade_floor(_manifest(empty), current="0.0.1")


def test_malformed_specifier_fails_open() -> None:
    _enforce_upgrade_floor(_manifest("this is not a specifier"), current="0.1.0")


def test_non_pep440_installed_version_fails_open() -> None:
    _enforce_upgrade_floor(_manifest(">=1.3.0"), current="not-a-version")
