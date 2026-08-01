"""``ReleaseManifest.upgrade_from`` — the supported-upgrade-path floor.

The field has existed since the manifest schema was written and was never read
anywhere, so there was no floor at all on how old a `current` version could be
before ``prepare()`` would try to converge it.

Two hard requirements on any floor we add:

  * the update-test baselines — **0.9.8** (primary; where most real installs
    are) and **1.0.0-alpha.2** — must both converge cleanly;
  * it must fail OPEN. This is a supportability statement, not a security
    control (cosign is the security control), so a malformed specifier or a
    non-PEP-440 installed version degrades to "no statement", never to a
    blocked update.
"""

from __future__ import annotations

import pytest

from hal0.updater.updater import (
    ReleaseManifest,
    UpdateUpgradePathUnsupported,
    _enforce_upgrade_floor,
)


def _manifest(upgrade_from: str = "") -> ReleaseManifest:
    return ReleaseManifest.model_validate(
        {
            "_schema": "hal0.releases.v1",
            "version": "1.0.0-rc.2",
            "channel": "preview",
            "release_kind": "preview",
            "prerelease_stage": "rc",
            "upgrade_from": upgrade_from,
            "url": "https://example.invalid/hal0-1.0.0-rc.2.tar.gz",
            "bundle_url": "https://example.invalid/hal0-1.0.0-rc.2.tar.gz.bundle",
            "digest_sha256": "0" * 64,
            "signer_identity": "^https://github.com/hal0ai/hal0/.*$",
        }
    )


# ── the release-test baselines ────────────────────────────────────────────────


@pytest.mark.parametrize("baseline", ["0.9.8", "1.0.0-alpha.2", "1.0.0a2", "1.0.0"])
def test_release_test_baselines_are_admitted(baseline: str) -> None:
    """0.9.8 and 1.0.0-alpha.2 are the sanctioned update-test baselines.

    ``prereleases=True`` is what makes 1.0.0-alpha.2 pass a bare ">=0.9.8";
    packaging excludes prereleases from a specifier by default.
    """
    _enforce_upgrade_floor(_manifest(">=0.9.8"), current=baseline)


def test_below_the_floor_is_refused_with_both_versions_named() -> None:
    with pytest.raises(UpdateUpgradePathUnsupported) as ei:
        _enforce_upgrade_floor(_manifest(">=0.9.8"), current="0.9.7")
    assert ei.value.details["installed_version"] == "0.9.7"
    assert ei.value.details["upgrade_from"] == ">=0.9.8"
    assert "0.9.8" in str(ei.value)


def test_compound_specifier_is_honoured() -> None:
    _enforce_upgrade_floor(_manifest(">=0.9.0,<2.0"), current="1.0.0")
    with pytest.raises(UpdateUpgradePathUnsupported):
        _enforce_upgrade_floor(_manifest(">=0.9.0,<2.0"), current="2.1.0")


# ── inert by default, fail-open on anything unparseable ───────────────────────


@pytest.mark.parametrize("empty", ["", "   "])
def test_no_floor_declared_is_a_noop(empty: str) -> None:
    """Every published manifest today carries the "" default."""
    _enforce_upgrade_floor(_manifest(empty), current="0.0.1")


def test_shipped_manifest_declares_no_floor() -> None:
    """Guard: adding a floor to manifest.json must be a deliberate act."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert not (data.get("upgrade_from") or "").strip()


def test_malformed_specifier_fails_open() -> None:
    _enforce_upgrade_floor(_manifest("this is not a specifier"), current="0.1.0")


def test_non_pep440_installed_version_fails_open() -> None:
    _enforce_upgrade_floor(_manifest(">=0.9.8"), current="not-a-version")
