"""Tests for hal0.updater.Updater — apply, rollback, check semantics.

These tests run entirely against ``HAL0_HOME`` tmp dirs and ``file://``
release manifests; no network, no real cosign. Cosign verification is
gated behind ``HAL0_UPDATE_SKIP_COSIGN=1`` for the happy-path tests so
the swap orchestration can be exercised without a real signed artifact
(see PLAN §17 risk #2 — the documented gap closes before v1).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any

import pytest

from hal0.updater import (
    ReleaseInfo,
    ReleaseManifest,
    UpdateCosignFailed,
    UpdateCosignMissing,
    UpdateDownloadError,
    UpdateError,
    UpdateExtractError,
    UpdateManifestInvalid,
    Updater,
    UpdateRollbackUnavailable,
    UpdateVerifyError,
    releases_url,
)
from hal0.updater.updater import (
    _atomic_symlink_swap,
    _cosign_skip,
    _current_symlink,
    _is_pre_release,
    _parse_manifest,
    _previous_record,
    _versioned_install_dir,
)

# ── helpers ────────────────────────────────────────────────────────────────────


def _build_release_tarball(
    *, tmp: Path, version: str, contents: dict[str, str] | None = None
) -> Path:
    """Build a synthetic ``hal0-<version>.tar.gz`` with a top-level prefix."""
    contents = contents or {
        "bin/hal0": "#!/usr/bin/env bash\necho hal0 stub\n",
        "site-packages/hal0/__init__.py": f'__version__ = "{version}"\n',
        "ui/index.html": f"<!doctype html><html>hal0 {version}</html>\n",
        "VERSION": version,
    }
    src = tmp / f"hal0-{version}"
    src.mkdir(parents=True, exist_ok=True)
    for rel, body in contents.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    tar_path = tmp / f"hal0-{version}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(src, arcname=f"hal0-{version}")
    shutil.rmtree(src)
    return tar_path


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_release_manifest(
    *,
    manifest_path: Path,
    tarball: Path,
    sig: Path,
    version: str,
    cert: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a full hal0.releases.v1 manifest pointing at file:// URLs."""
    cert_url = f"file://{cert}" if cert is not None else f"file://{sig.with_suffix('.crt')}"
    payload: dict[str, Any] = {
        "_schema": "hal0.releases.v1",
        "version": version,
        "channel": "stable",
        "url": f"file://{tarball}",
        "sig_url": f"file://{sig}",
        "cert_url": cert_url,
        "digest_sha256": _sha256_of(tarball),
        "signer_identity": "^https://github\\.com/hal0ai/hal0/.*",
        "signer_issuer": "https://token.actions.githubusercontent.com",
        "min_data_version": 1,
        "released_at": "2026-05-15T12:00:00Z",
        "notes_url": "https://example.test/notes",
        "toolbox_images": {},
    }
    if overrides:
        payload.update(overrides)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


@pytest.fixture
def cosign_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass cosign verification for happy-path tests."""
    monkeypatch.setenv("HAL0_UPDATE_SKIP_COSIGN", "1")


@pytest.fixture
def synthetic_release(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """Build a synthetic v0.0.1 release on disk and point HAL0_RELEASES_URL at it."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    version = "0.0.1"
    tarball = _build_release_tarball(tmp=artifacts, version=version)
    # Stub signature + cert files — contents don't matter when cosign is skipped.
    sig = artifacts / f"hal0-{version}.tar.gz.sig"
    sig.write_bytes(b"signature-placeholder\n")
    cert = artifacts / f"hal0-{version}.tar.gz.crt"
    cert.write_bytes(b"-----BEGIN CERTIFICATE-----\nplaceholder\n-----END CERTIFICATE-----\n")
    manifest_path = artifacts / "latest.json"
    payload = _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball,
        sig=sig,
        cert=cert,
        version=version,
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest_path))
    return {
        "version": version,
        "tarball": tarball,
        "sig": sig,
        "cert": cert,
        "manifest_path": manifest_path,
        "payload": payload,
    }


# ── releases_url ───────────────────────────────────────────────────────────────


def test_releases_url_defaults_per_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the override env var the URL is per-channel under releases.hal0.dev."""
    monkeypatch.delenv("HAL0_RELEASES_URL", raising=False)
    assert releases_url("stable") == "https://releases.hal0.dev/stable.json"
    assert releases_url("nightly") == "https://releases.hal0.dev/nightly.json"


def test_releases_url_honours_override_for_file_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file:// override is used verbatim regardless of channel."""
    monkeypatch.setenv("HAL0_RELEASES_URL", str(tmp_path / "rel.json"))
    assert releases_url("stable") == str(tmp_path / "rel.json")
    assert releases_url("nightly") == str(tmp_path / "rel.json")


def test_releases_url_appends_channel_for_http_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An http(s) override is rewritten with ?channel= for non-stable channels."""
    monkeypatch.setenv("HAL0_RELEASES_URL", "https://example.test/releases.json")
    assert releases_url("stable") == "https://example.test/releases.json"
    assert releases_url("nightly") == "https://example.test/releases.json?channel=nightly"


# ── manifest schema validation ─────────────────────────────────────────────────


def test_manifest_schema_accepts_full_payload(tmp_path: Path) -> None:
    """A full v1 manifest validates and round-trips through ReleaseManifest."""
    tarball = _build_release_tarball(tmp=tmp_path, version="0.0.1")
    sig = tmp_path / "sig"
    sig.write_bytes(b"x")
    payload = _write_release_manifest(
        manifest_path=tmp_path / "latest.json",
        tarball=tarball,
        sig=sig,
        version="0.0.1",
    )
    m = ReleaseManifest.model_validate(payload)
    assert m.version == "0.0.1"
    assert m.signer_identity.startswith("^https://github")
    assert len(m.digest_sha256) == 64


def test_manifest_schema_rejects_missing_required_fields() -> None:
    """The pydantic schema rejects manifests without sig_url / digest_sha256."""
    with pytest.raises(UpdateManifestInvalid):
        _parse_manifest({"version": "9.9.9", "url": "https://x/y.tar.gz"})


def test_manifest_schema_rejects_malformed_digest() -> None:
    """digest_sha256 must be hex; garbage strings fail validation."""
    payload = {
        "_schema": "hal0.releases.v1",
        "version": "0.0.1",
        "url": "file:///x",
        "sig_url": "file:///x.sig",
        "cert_url": "file:///x.crt",
        "digest_sha256": "not-a-real-digest",
        "signer_identity": "^https://github.com/x/.*",
    }
    with pytest.raises(UpdateManifestInvalid):
        _parse_manifest(payload)


# ── check ──────────────────────────────────────────────────────────────────────


def test_check_returns_typed_release_info(synthetic_release: dict[str, Any]) -> None:
    """Updater.check() returns a ReleaseInfo dataclass with the manifest fields."""
    info = asyncio.run(Updater().check())
    assert isinstance(info, ReleaseInfo)
    assert info.latest == "0.0.1"
    assert info.channel == "stable"
    assert info.digest_sha256 == synthetic_release["payload"]["digest_sha256"]
    assert info.signer_identity == synthetic_release["payload"]["signer_identity"]
    assert info.update_available is True or info.update_available is False  # type sanity


def test_check_handles_missing_manifest(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A nonexistent manifest surfaces UpdateError, not a raw OSError."""
    monkeypatch.setenv("HAL0_RELEASES_URL", str(tmp_path / "nope.json"))
    with pytest.raises(UpdateError):
        asyncio.run(Updater().check())


# ── atomic symlink swap ────────────────────────────────────────────────────────


def test_atomic_symlink_swap_creates_link(tmp_path: Path) -> None:
    """First swap creates the symlink; prior is None."""
    target = tmp_path / "v1"
    target.mkdir()
    link = tmp_path / "current"
    prior = _atomic_symlink_swap(target, link)
    assert prior is None
    assert link.is_symlink()
    assert os.readlink(link) == str(target)


def test_atomic_symlink_swap_replaces_existing(tmp_path: Path) -> None:
    """A second swap returns the prior target and points at the new one."""
    a = tmp_path / "vA"
    a.mkdir()
    b = tmp_path / "vB"
    b.mkdir()
    link = tmp_path / "current"
    _atomic_symlink_swap(a, link)
    prior = _atomic_symlink_swap(b, link)
    assert prior == Path(str(a))
    assert os.readlink(link) == str(b)


def test_atomic_symlink_swap_chaos_no_temp_left(tmp_path: Path) -> None:
    """After 50 rapid swaps no .swap-* turds remain in the install root.

    Stress-tests the os.symlink-then-os.replace pattern under load to
    confirm the rename really is atomic and we never leak a half-formed
    tmp symlink.
    """
    targets = []
    for i in range(4):
        t = tmp_path / f"v{i}"
        t.mkdir()
        targets.append(t)
    link = tmp_path / "current"
    for i in range(50):
        _atomic_symlink_swap(targets[i % len(targets)], link)
    # No .current.swap-* leftovers
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".current.swap")]
    assert leftovers == [], leftovers
    # Final state is a valid symlink
    assert link.is_symlink()
    assert Path(os.readlink(link)).exists()


# ── apply happy path ───────────────────────────────────────────────────────────


def test_apply_happy_path_swaps_symlink(
    synthetic_release: dict[str, Any], cosign_skip: None
) -> None:
    """End-to-end apply: download → sha verify → extract → symlink swap."""
    res = asyncio.run(Updater().apply())
    assert res["version"] == "0.0.1"
    assert res["cosign_skipped"] is True

    link = _current_symlink()
    assert link.is_symlink()
    install = _versioned_install_dir("0.0.1")
    assert Path(os.readlink(link)).resolve() == install.resolve()
    # The extracted tree has the files we packed.
    assert (install / "VERSION").read_text().strip() == "0.0.1"
    assert (install / "site-packages" / "hal0" / "__init__.py").exists()


def test_apply_records_previous_for_rollback(
    synthetic_release: dict[str, Any],
    cosign_skip: None,
    tmp_hal0_home: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a second apply, /var/lib/hal0/hal0.previous points at the old tree."""
    # First install — bootstrap previous from an existing symlink.
    asyncio.run(Updater().apply())
    first_install = _versioned_install_dir("0.0.1")
    assert first_install.exists()

    # Build a second release v0.0.2 and rewire the manifest to it.
    artifacts = tmp_path / "v2"
    artifacts.mkdir()
    tarball2 = _build_release_tarball(tmp=artifacts, version="0.0.2")
    sig2 = artifacts / "hal0-0.0.2.tar.gz.sig"
    sig2.write_bytes(b"sig")
    cert2 = artifacts / "hal0-0.0.2.tar.gz.crt"
    cert2.write_bytes(b"cert")
    manifest_path = Path(os.environ["HAL0_RELEASES_URL"])
    _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball2,
        sig=sig2,
        cert=cert2,
        version="0.0.2",
    )

    asyncio.run(Updater().apply())
    record = _previous_record()
    assert record.exists()
    assert "hal0-0.0.1" in record.read_text(encoding="utf-8")
    assert _versioned_install_dir("0.0.2").exists()
    assert Path(os.readlink(_current_symlink())).name == "hal0-0.0.2"


# ── apply error paths ──────────────────────────────────────────────────────────


def test_apply_sha_mismatch_raises_typed_error(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cosign_skip: None
) -> None:
    """A tampered digest in the manifest produces UpdateVerifyError."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    tarball = _build_release_tarball(tmp=artifacts, version="0.0.1")
    sig = artifacts / "hal0-0.0.1.tar.gz.sig"
    sig.write_bytes(b"sig")
    cert = artifacts / "hal0-0.0.1.tar.gz.crt"
    cert.write_bytes(b"cert")
    manifest_path = artifacts / "latest.json"
    _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball,
        sig=sig,
        cert=cert,
        version="0.0.1",
        overrides={"digest_sha256": "0" * 64},
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest_path))

    with pytest.raises(UpdateVerifyError) as exc_info:
        asyncio.run(Updater().apply())
    assert exc_info.value.code == "system.update_verify_failed"


def test_apply_refuses_when_install_dir_exists_nonempty(
    synthetic_release: dict[str, Any], cosign_skip: None
) -> None:
    """If /usr/lib/hal0-<version>/ already exists and is non-empty, refuse."""
    install = _versioned_install_dir("0.0.1")
    install.mkdir(parents=True, exist_ok=True)
    (install / "stale-marker").write_text("leftover")

    with pytest.raises(UpdateExtractError):
        asyncio.run(Updater().apply())


def test_apply_download_failure_surfaces_typed_error(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cosign_skip: None
) -> None:
    """A missing tarball URL produces UpdateDownloadError, not a stack trace."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    # Manifest points at a tarball that doesn't exist.
    manifest_path = artifacts / "latest.json"
    payload = {
        "_schema": "hal0.releases.v1",
        "version": "9.9.9",
        "url": f"file://{tmp_path / 'nope.tar.gz'}",
        "sig_url": f"file://{tmp_path / 'nope.sig'}",
        "cert_url": f"file://{tmp_path / 'nope.crt'}",
        "digest_sha256": "a" * 64,
        "signer_identity": "^https://github.com/.*",
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest_path))

    with pytest.raises(UpdateDownloadError):
        asyncio.run(Updater().apply())


# ── cosign ─────────────────────────────────────────────────────────────────────


def test_cosign_missing_surfaces_typed_error(
    synthetic_release: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When cosign isn't installed and HAL0_UPDATE_SKIP_COSIGN isn't set,
    apply raises UpdateCosignMissing with install hints rather than
    silently falling back to unsigned acceptance."""
    monkeypatch.delenv("HAL0_UPDATE_SKIP_COSIGN", raising=False)
    # Force "cosign not found" by emptying PATH.
    monkeypatch.setenv("PATH", "")

    with pytest.raises(UpdateCosignMissing) as exc_info:
        asyncio.run(Updater().apply())
    assert exc_info.value.code == "system.update_cosign_missing"
    assert "skip_env" in exc_info.value.details


def test_cosign_failure_surfaces_typed_error(
    synthetic_release: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When cosign exists but rejects the signature, apply raises UpdateCosignFailed."""
    monkeypatch.delenv("HAL0_UPDATE_SKIP_COSIGN", raising=False)
    # Plant a fake `cosign` on PATH that always exits non-zero.
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake = fake_bin / "cosign"
    fake.write_text("#!/usr/bin/env bash\necho 'bad signature' >&2\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    with pytest.raises(UpdateCosignFailed) as exc_info:
        asyncio.run(Updater().apply())
    assert exc_info.value.code == "system.update_cosign_failed"
    assert "stderr" in exc_info.value.details


# ── rollback ───────────────────────────────────────────────────────────────────


def test_rollback_without_record_raises(tmp_hal0_home: str) -> None:
    """With no /var/lib/hal0/hal0.previous, rollback raises UpdateRollbackUnavailable."""
    with pytest.raises(UpdateRollbackUnavailable) as exc_info:
        asyncio.run(Updater().rollback())
    assert exc_info.value.code == "system.update_rollback_unavailable"


def test_rollback_swaps_symlink_back(
    synthetic_release: dict[str, Any],
    cosign_skip: None,
    tmp_hal0_home: str,
    tmp_path: Path,
) -> None:
    """Apply v1 → apply v2 → rollback restores v1 and updates the record."""
    # v0.0.1
    asyncio.run(Updater().apply())
    v1_dir = _versioned_install_dir("0.0.1")

    # v0.0.2
    artifacts = tmp_path / "v2"
    artifacts.mkdir()
    tarball2 = _build_release_tarball(tmp=artifacts, version="0.0.2")
    sig2 = artifacts / "hal0-0.0.2.tar.gz.sig"
    sig2.write_bytes(b"sig")
    cert2 = artifacts / "hal0-0.0.2.tar.gz.crt"
    cert2.write_bytes(b"cert")
    manifest_path = Path(os.environ["HAL0_RELEASES_URL"])
    _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball2,
        sig=sig2,
        cert=cert2,
        version="0.0.2",
    )
    asyncio.run(Updater().apply())
    assert Path(os.readlink(_current_symlink())).name == "hal0-0.0.2"

    # rollback → back to v0.0.1
    res = asyncio.run(Updater().rollback())
    assert "hal0-0.0.1" in res["rolled_back_to"]
    assert Path(os.readlink(_current_symlink())).resolve() == v1_dir.resolve()
    # The previous record now points at v0.0.2 (so a second rollback bounces).
    assert "hal0-0.0.2" in _previous_record().read_text(encoding="utf-8")


# ── channel switching ─────────────────────────────────────────────────────────


def test_check_uses_per_channel_url(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check() method honours the channel argument when looking up the URL.

    With HAL0_RELEASES_URL set to a file:// path, the channel parameter
    doesn't rewrite the URL but the returned ReleaseInfo.channel reflects
    the requested channel — exactly the contract the route layer needs.
    """
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    tarball = _build_release_tarball(tmp=artifacts, version="0.0.1")
    sig = artifacts / "hal0-0.0.1.tar.gz.sig"
    sig.write_bytes(b"sig")
    manifest_path = artifacts / "latest.json"
    _write_release_manifest(manifest_path=manifest_path, tarball=tarball, sig=sig, version="0.0.1")
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest_path))

    info_stable = asyncio.run(Updater(channel="stable").check())
    info_nightly = asyncio.run(Updater(channel="nightly").check())
    assert info_stable.channel == "stable"
    assert info_nightly.channel == "nightly"


# ── HAL0_UPDATE_SKIP_COSIGN gate (pre-release only) ──────────────────────────


@pytest.mark.parametrize(
    "version,expected",
    [
        ("0.0.0", True),
        ("0.1.0", True),
        ("0.99.0", True),
        ("1.0.0-rc1", True),
        ("1.0.0-dev", True),
        ("2.3.4-rc.5", True),
        ("1.0.0", False),
        ("1.2.3", False),
        ("2.0.0", False),
    ],
)
def test_is_pre_release(version: str, expected: bool) -> None:
    """0.x and any version with a hyphen are pre-release; bare 1+ is stable."""
    assert _is_pre_release(version) is expected


def test_cosign_skip_honored_on_pre_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a pre-release build, HAL0_UPDATE_SKIP_COSIGN=1 disables verification."""
    monkeypatch.setattr("hal0.__version__", "1.0.0-rc1")
    monkeypatch.setenv("HAL0_UPDATE_SKIP_COSIGN", "1")
    assert _cosign_skip() is True


def test_cosign_skip_ignored_on_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    """On stable v1+, HAL0_UPDATE_SKIP_COSIGN=1 is silently dropped."""
    monkeypatch.setattr("hal0.__version__", "1.0.0")
    monkeypatch.setenv("HAL0_UPDATE_SKIP_COSIGN", "1")
    assert _cosign_skip() is False


def test_cosign_skip_false_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env, no skip — regardless of build channel."""
    monkeypatch.setattr("hal0.__version__", "1.0.0-rc1")
    monkeypatch.delenv("HAL0_UPDATE_SKIP_COSIGN", raising=False)
    assert _cosign_skip() is False
