"""Tests for hal0.updater.Updater — apply, rollback, check semantics.

These tests run entirely against ``HAL0_HOME`` tmp dirs and ``file://``
release manifests; no network and no cryptographic claims. Most happy-path
swap tests use the ``cosign_skip`` verification seam. Preview rehearsal tests
instead execute the real :func:`hal0.updater.updater._verify_cosign` subprocess
path against a hermetic fake ``cosign`` executable that validates and records
its bundle/blob arguments; cosign remains mandatory in production.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

import hal0.updater as updater_package
import hal0.updater.updater as updater_module
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
    _current_symlink,
    _is_newer,
    _parse_manifest,
    _previous_record,
    _read_release_notes,
    _version_tuple,
    _versioned_install_dir,
)

# ── helpers ────────────────────────────────────────────────────────────────────


VALID_MANIFEST: dict[str, Any] = {
    "_schema": "hal0.releases.v1",
    "version": "1.0.0",
    "channel": "stable",
    "release_kind": "stable",
    "url": "https://example.test/hal0.tar.gz",
    "bundle_url": "https://example.test/hal0.tar.gz.bundle",
    "digest_sha256": "0" * 64,
    "signer_identity": (
        r"^https://github\.com/(Hal0ai|hal0ai)/hal0/"
        r"\.github/workflows/release\.yml@refs/tags/v1\.0\.0$"
    ),
}


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
    version: str,
    bundle: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a full hal0.releases.v1 manifest pointing at file:// URLs.

    The synthetic manifest has the production ``bundle_url`` shape, but its
    placeholder bundle bytes are only for exercising test verification seams;
    they are not cryptographic signatures.
    """
    bundle = bundle if bundle is not None else Path(f"{tarball}.bundle")
    if not bundle.exists():
        bundle.write_bytes(b"sigstore-bundle-placeholder\n")
    payload: dict[str, Any] = {
        "_schema": "hal0.releases.v1",
        "version": version,
        "channel": "stable",
        "url": f"file://{tarball}",
        "bundle_url": f"file://{bundle}",
        "digest_sha256": _sha256_of(tarball),
        "signer_identity": (
            r"^https://github\.com/(Hal0ai|hal0ai)/hal0/"
            rf"\.github/workflows/release\.yml@refs/tags/v{re.escape(version)}$"
        ),
        "signer_issuer": "https://token.actions.githubusercontent.com",
        "min_data_version": 1,
        "released_at": "2026-05-15T12:00:00Z",
        "notes_url": "https://example.test/notes",
        "toolbox_images": {},
    }
    if overrides:
        payload.update(overrides)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    Path(f"{manifest_path}.bundle").write_bytes(b"manifest-bundle-placeholder\n")
    return payload


def _install_fake_cosign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, reject: bool = False
) -> Path:
    """Install a hermetic cosign verifier that validates pairs and logs argv.

    This exercises the updater's real subprocess wrapper, not cryptography.
    The executable requires the exact production ``verify-blob`` argument
    shape, existing files, and a sibling ``<blob>.bundle`` path.
    """
    fake_bin = tmp_path / "fake-cosign-bin"
    fake_bin.mkdir(exist_ok=True)
    log_path = tmp_path / "fake-cosign-invocations.jsonl"
    fake = fake_bin / "cosign"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """import hashlib
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if len(args) != 8 or args[0] != "verify-blob":
    print(f"unexpected cosign argv: {args!r}", file=sys.stderr)
    raise SystemExit(64)
if args[1] != "--bundle" or args[3] != "--certificate-identity-regexp":
    print(f"unexpected cosign flags: {args!r}", file=sys.stderr)
    raise SystemExit(64)
if args[5] != "--certificate-oidc-issuer":
    print(f"unexpected cosign flags: {args!r}", file=sys.stderr)
    raise SystemExit(64)

bundle = Path(args[2])
blob = Path(args[7])
if bundle != Path(f"{blob}.bundle"):
    print(f"bundle/blob mismatch: {bundle} != {blob}.bundle", file=sys.stderr)
    raise SystemExit(65)
if not blob.is_file() or not bundle.is_file():
    print(f"missing verification input: blob={blob} bundle={bundle}", file=sys.stderr)
    raise SystemExit(66)

entry = {
    "args": args,
    "blob": str(blob),
    "bundle": str(bundle),
    "blob_sha256": hashlib.sha256(blob.read_bytes()).hexdigest(),
    "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
}
with Path(os.environ["HAL0_FAKE_COSIGN_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(entry) + "\\n")
if os.environ.get("HAL0_FAKE_COSIGN_MODE") == "reject":
    print("synthetic signature rejection", file=sys.stderr)
    raise SystemExit(1)
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HAL0_FAKE_COSIGN_LOG", str(log_path))
    monkeypatch.setenv("HAL0_FAKE_COSIGN_MODE", "reject" if reject else "accept")
    return log_path


def _fake_cosign_calls(log_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def cosign_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out cosign verification for happy-path tests.

    Cosign verification has no runtime bypass in production; tests that
    don't care about the actual ``cosign verify-blob`` invocation stub the
    verify step directly so the swap orchestration can be exercised without
    a real signed artifact or a `cosign` binary on PATH.
    """
    monkeypatch.setattr(
        "hal0.updater.updater._verify_cosign",
        lambda *a, **k: None,
    )


@pytest.fixture
def synthetic_release(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """Build a synthetic v0.0.1 release on disk and point HAL0_RELEASES_URL at it."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    version = "0.0.1"
    tarball = _build_release_tarball(tmp=artifacts, version=version)
    manifest_path = artifacts / "latest.json"
    payload = _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball,
        version=version,
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest_path))
    # Simulate a prod (non-editable) install so apply() does not hit the
    # editable-install guard added in #625.  Individual tests that want to
    # exercise that guard override this back to True themselves.
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)
    # Stub out the venv reinstall step — the synthetic release tree is not a
    # real pip-installable package, and tests that want to verify reinstall
    # behaviour substitute their own stub explicitly.
    monkeypatch.setattr(
        "hal0.updater.updater._reinstall_into_venv",
        lambda install_dir, *, job_id=None: None,
    )
    return {
        "version": version,
        "tarball": tarball,
        "manifest_path": manifest_path,
        "payload": payload,
    }


@pytest.fixture
def synthetic_preview_release(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """Build local alpha bytes for the updater's synthetic verification seam.

    The bundle files are placeholders accepted by the fake cosign executable;
    this fixture does not represent or claim real cryptographic verification.
    """
    artifacts = tmp_path / "preview-artifacts"
    artifacts.mkdir()
    version = "99.0.0-alpha.1"
    tarball = _build_release_tarball(tmp=artifacts, version=version)
    manifest_path = artifacts / "preview-manifest.json"
    payload = _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball,
        version=version,
        overrides={
            "channel": "preview",
            "release_kind": "preview",
            "prerelease_stage": "alpha",
        },
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", manifest_path.as_uri())
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)
    return {
        "version": version,
        "tarball": tarball,
        "manifest_path": manifest_path,
        "payload": payload,
    }


# ── releases_url ───────────────────────────────────────────────────────────────


def test_releases_url_defaults_per_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the override env var the URL is per-channel under releases.hal0.dev."""
    monkeypatch.delenv("HAL0_RELEASES_URL", raising=False)
    assert releases_url("stable") == "https://releases.hal0.dev/stable.json"
    assert releases_url("preview") == "https://releases.hal0.dev/preview.json"
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
    payload = _write_release_manifest(
        manifest_path=tmp_path / "latest.json",
        tarball=tarball,
        version="0.0.1",
    )
    m = ReleaseManifest.model_validate(payload)
    assert m.version == "0.0.1"
    assert m.signer_identity.startswith("^https://github")
    assert len(m.digest_sha256) == 64


def test_manifest_schema_rejects_missing_required_fields() -> None:
    """The pydantic schema rejects manifests without bundle_url / digest_sha256."""
    with pytest.raises(UpdateManifestInvalid):
        _parse_manifest({"version": "9.9.9", "url": "https://x/y.tar.gz"})


@pytest.mark.parametrize(
    ("schema_value", "missing"),
    [
        pytest.param(None, True, id="missing"),
        pytest.param("hal0.releases.v2", False, id="unknown"),
        pytest.param(1, False, id="number"),
        pytest.param(None, False, id="null"),
    ],
)
def test_manifest_schema_requires_exact_v1_schema(schema_value: object, missing: bool) -> None:
    payload = dict(VALID_MANIFEST)
    if missing:
        payload.pop("_schema")
    else:
        payload["_schema"] = schema_value

    with pytest.raises(UpdateManifestInvalid):
        _parse_manifest(payload)


def test_manifest_schema_rejects_malformed_digest() -> None:
    """digest_sha256 must be hex; garbage strings fail validation."""
    payload = {
        "_schema": "hal0.releases.v1",
        "version": "0.0.1",
        "url": "file:///x",
        "bundle_url": "file:///x.bundle",
        "digest_sha256": "not-a-real-digest",
        "signer_identity": "^https://github.com/x/.*",
    }
    with pytest.raises(UpdateManifestInvalid):
        _parse_manifest(payload)


def test_manifest_schema_defaults_revoked_false(tmp_path: Path) -> None:
    """An older manifest without a ``revoked`` field parses with revoked=False."""
    tarball = _build_release_tarball(tmp=tmp_path, version="0.0.1")
    payload = _write_release_manifest(
        manifest_path=tmp_path / "latest.json",
        tarball=tarball,
        version="0.0.1",
    )
    m = _parse_manifest(payload)
    assert m.revoked is False
    assert m.revoked_reason == ""


def test_manifest_schema_rejects_no_signing_scheme() -> None:
    """A manifest without a bundle_url is invalid — it's the only signing scheme accepted."""
    payload = {
        "_schema": "hal0.releases.v1",
        "version": "0.0.1",
        "url": "file:///x",
        "digest_sha256": "0" * 64,
        "signer_identity": "^https://github.com/x/.*",
    }
    with pytest.raises(UpdateManifestInvalid):
        _parse_manifest(payload)


def test_manifest_schema_accepts_revoked(tmp_path: Path) -> None:
    """A manifest with ``revoked: true`` + reason parses and round-trips."""
    tarball = _build_release_tarball(tmp=tmp_path, version="0.0.1")
    payload = _write_release_manifest(
        manifest_path=tmp_path / "latest.json",
        tarball=tarball,
        version="0.0.1",
        overrides={"revoked": True, "revoked_reason": "bad cosign cert"},
    )
    m = _parse_manifest(payload)
    assert m.revoked is True
    assert m.revoked_reason == "bad cosign cert"


# ── preview / release-kind manifest validation ────────────────────────────────


def test_preview_manifest_accepts_preview_channel() -> None:
    manifest = _parse_manifest(
        {
            **VALID_MANIFEST,
            "channel": "preview",
            "release_kind": "preview",
            "prerelease_stage": "alpha",
        }
    )
    assert updater_module.validate_manifest_for_channel(manifest, "preview") is manifest


def test_promoted_stable_manifest_is_accepted_by_preview_channel() -> None:
    manifest = _parse_manifest(
        {
            **VALID_MANIFEST,
            "channel": "preview",
            "release_kind": "stable",
            "prerelease_stage": None,
        }
    )
    assert updater_module.validate_manifest_for_channel(manifest, "preview") is manifest


def test_stable_channel_rejects_preview_manifest() -> None:
    manifest = _parse_manifest(
        {
            **VALID_MANIFEST,
            "channel": "preview",
            "release_kind": "preview",
            "prerelease_stage": "alpha",
        }
    )
    with pytest.raises(ValueError, match=r"requested channel.*stable"):
        updater_module.validate_manifest_for_channel(manifest, "stable")


def test_requested_channel_must_match_manifest_channel() -> None:
    manifest = _parse_manifest({**VALID_MANIFEST, "channel": "nightly", "release_kind": "nightly"})
    with pytest.raises(ValueError, match=r"manifest channel.*nightly"):
        updater_module.validate_manifest_for_channel(manifest, "preview")


def test_unknown_requested_channel_is_rejected() -> None:
    manifest = _parse_manifest(VALID_MANIFEST)
    with pytest.raises(ValueError, match="unknown requested channel"):
        updater_module.validate_manifest_for_channel(manifest, "beta")


def test_manifest_schema_accepts_alpha_preview() -> None:
    """A full alpha preview manifest parses with new fields."""
    manifest = ReleaseManifest.model_validate(
        {
            "_schema": "hal0.releases.v1",
            "version": "1.0.0-alpha.1",
            "channel": "preview",
            "release_kind": "preview",
            "prerelease_stage": "alpha",
            "rollback_policy": "safe",
            "upgrade_from": ">=0.9.8",
            "operator_migrations": [],
            "url": "https://example.test/hal0.tar.gz",
            "bundle_url": "https://example.test/hal0.tar.gz.bundle",
            "digest_sha256": "0" * 64,
            "signer_identity": "release-workflow",
        }
    )
    assert manifest.prerelease_stage == "alpha"
    assert manifest.release_kind == "preview"
    assert manifest.rollback_policy == "safe"
    assert manifest.upgrade_from == ">=0.9.8"
    assert manifest.operator_migrations == []


def test_manifest_schema_rejects_preview_without_stage() -> None:
    """A preview manifest without a prerelease_stage raises ValueError."""
    with pytest.raises(UpdateManifestInvalid):
        _parse_manifest(
            {
                "_schema": "hal0.releases.v1",
                "version": "1.0.0",
                "channel": "preview",
                "release_kind": "preview",
                "url": "https://example.test/hal0.tar.gz",
                "bundle_url": "https://example.test/hal0.tar.gz.bundle",
                "digest_sha256": "0" * 64,
                "signer_identity": "release-workflow",
            }
        )


def test_manifest_schema_rejects_preview_with_wrong_channel() -> None:
    """A preview manifest with channel='stable' raises ValueError."""
    with pytest.raises(UpdateManifestInvalid):
        _parse_manifest(
            {
                "_schema": "hal0.releases.v1",
                "version": "1.0.0-alpha.1",
                "channel": "stable",
                "release_kind": "preview",
                "prerelease_stage": "alpha",
                "url": "https://example.test/hal0.tar.gz",
                "bundle_url": "https://example.test/hal0.tar.gz.bundle",
                "digest_sha256": "0" * 64,
                "signer_identity": "release-workflow",
            }
        )


def test_manifest_schema_rejects_stable_with_prerelease_stage() -> None:
    """A stable manifest with a prerelease_stage raises ValueError."""
    with pytest.raises(UpdateManifestInvalid):
        _parse_manifest(
            {
                "_schema": "hal0.releases.v1",
                "version": "1.0.0",
                "channel": "stable",
                "release_kind": "stable",
                "prerelease_stage": "rc",
                "url": "https://example.test/hal0.tar.gz",
                "bundle_url": "https://example.test/hal0.tar.gz.bundle",
                "digest_sha256": "0" * 64,
                "signer_identity": "release-workflow",
            }
        )


def test_manifest_schema_rejects_stable_with_wrong_channel() -> None:
    """A stable release_kind with channel='nightly' raises ValueError."""
    with pytest.raises(UpdateManifestInvalid):
        _parse_manifest(
            {
                "_schema": "hal0.releases.v1",
                "version": "1.0.0",
                "channel": "nightly",
                "release_kind": "stable",
                "url": "https://example.test/hal0.tar.gz",
                "bundle_url": "https://example.test/hal0.tar.gz.bundle",
                "digest_sha256": "0" * 64,
                "signer_identity": "release-workflow",
            }
        )


def test_manifest_schema_rejects_migrations_with_safe_rollback() -> None:
    """Non-empty operator_migrations requires backup-required or blocked rollback."""
    with pytest.raises(UpdateManifestInvalid):
        _parse_manifest(
            {
                "_schema": "hal0.releases.v1",
                "version": "1.0.0",
                "channel": "stable",
                "release_kind": "stable",
                "rollback_policy": "safe",
                "operator_migrations": ["migrate-db"],
                "url": "https://example.test/hal0.tar.gz",
                "bundle_url": "https://example.test/hal0.tar.gz.bundle",
                "digest_sha256": "0" * 64,
                "signer_identity": "release-workflow",
            }
        )


def test_manifest_schema_defaults_for_old_stable() -> None:
    """An old stable v1 manifest without new fields parses with safe defaults."""
    payload = {
        "_schema": "hal0.releases.v1",
        "version": "0.5.0",
        "channel": "stable",
        "url": "https://example.test/hal0.tar.gz",
        "bundle_url": "https://example.test/hal0.tar.gz.bundle",
        "digest_sha256": "0" * 64,
        "signer_identity": "^https://github\\.example/haloai/hal0/.*",
    }
    m = _parse_manifest(payload)
    assert m.release_kind == "stable"
    assert m.prerelease_stage is None
    assert m.rollback_policy == "safe"
    assert m.upgrade_from == ""
    assert m.operator_migrations == []


# ── check ──────────────────────────────────────────────────────────────────────


def test_check_returns_typed_release_info(
    synthetic_release: dict[str, Any], cosign_skip: None
) -> None:
    """Updater.check() returns a ReleaseInfo dataclass with the manifest fields."""
    info = asyncio.run(Updater().check())
    assert isinstance(info, ReleaseInfo)
    assert info.latest == "0.0.1"
    assert info.channel == "stable"
    assert info.digest_sha256 == synthetic_release["payload"]["digest_sha256"]
    assert info.signer_identity == synthetic_release["payload"]["signer_identity"]
    assert info.update_available is True or info.update_available is False  # type sanity


def test_preview_check_and_prepare_exercise_cosign_subprocess_without_activation(
    synthetic_preview_release: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Rehearse preview verification plumbing with synthetic file:// inputs."""
    cosign_log = _install_fake_cosign(tmp_path, monkeypatch)
    download_urls: list[str] = []
    original_download = updater_module._download

    async def record_local_download(url: str, destination: Path) -> None:
        download_urls.append(url)
        await original_download(url, destination)

    monkeypatch.setattr(updater_module, "_download", record_local_download)

    updater = Updater(channel="preview")
    info = asyncio.run(updater.check())

    version = synthetic_preview_release["version"]
    manifest_path = synthetic_preview_release["manifest_path"]
    payload = synthetic_preview_release["payload"]
    assert info.latest == version
    assert info.channel == "preview"
    assert info.raw_manifest["release_kind"] == "preview"
    assert not updater_module._cache_dir(version).exists()

    prepared = asyncio.run(updater.prepare())

    assert prepared["version"] == version
    assert Path(prepared["install_dir"]).is_dir()
    assert updater_module._manifest_cache_path(version).is_file()
    assert not _current_symlink().is_symlink()
    assert download_urls == [
        f"{manifest_path.as_uri()}.bundle",
        f"{manifest_path.as_uri()}.bundle",
        payload["url"],
        payload["bundle_url"],
    ]
    assert all(url.startswith("file://") for url in download_urls)

    verification_calls = _fake_cosign_calls(cosign_log)
    assert [Path(call["blob"]).name for call in verification_calls] == [
        "manifest.json",
        "manifest.json",
        synthetic_preview_release["tarball"].name,
    ]
    manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    manifest_bundle_digest = hashlib.sha256(
        Path(f"{manifest_path}.bundle").read_bytes()
    ).hexdigest()
    for call in verification_calls[:2]:
        assert call["blob_sha256"] == manifest_digest
        assert call["bundle_sha256"] == manifest_bundle_digest
        assert call["args"] == [
            "verify-blob",
            "--bundle",
            call["bundle"],
            "--certificate-identity-regexp",
            updater_module._MANIFEST_SIGNER_IDENTITY_REGEXP,
            "--certificate-oidc-issuer",
            updater_module._MANIFEST_SIGNER_ISSUER,
            call["blob"],
        ]

    artifact_call = verification_calls[2]
    cached_tarball = updater_module._cache_dir(version) / f"hal0-{version}.tar.gz"
    assert artifact_call["blob"] == str(cached_tarball)
    assert artifact_call["bundle"] == f"{cached_tarball}.bundle"
    assert artifact_call["blob_sha256"] == _sha256_of(synthetic_preview_release["tarball"])
    assert artifact_call["args"] == [
        "verify-blob",
        "--bundle",
        str(Path(f"{cached_tarball}.bundle")),
        "--certificate-identity-regexp",
        payload["signer_identity"],
        "--certificate-oidc-issuer",
        payload["signer_issuer"],
        str(cached_tarball),
    ]


@pytest.mark.parametrize("operation", ["check", "prepare"])
def test_preview_manifest_verification_failure_leaves_no_staged_state(
    operation: str,
    synthetic_preview_release: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A fake-cosign rejection cannot reach artifact staging or activation."""
    cosign_log = _install_fake_cosign(tmp_path, monkeypatch, reject=True)
    download_urls: list[str] = []
    original_download = updater_module._download

    async def record_local_download(url: str, destination: Path) -> None:
        download_urls.append(url)
        await original_download(url, destination)

    monkeypatch.setattr(updater_module, "_download", record_local_download)

    updater = Updater(channel="preview")
    with pytest.raises(UpdateCosignFailed):
        asyncio.run(getattr(updater, operation)())

    version = synthetic_preview_release["version"]
    manifest_path = synthetic_preview_release["manifest_path"]
    assert download_urls == [f"{manifest_path.as_uri()}.bundle"]
    assert len(_fake_cosign_calls(cosign_log)) == 1
    assert not updater_module._cache_dir(version).exists()
    assert not updater_module._manifest_cache_path(version).exists()
    assert not _versioned_install_dir(version).exists()
    assert not _current_symlink().is_symlink()


def test_check_uses_pinned_trust_root_for_unauthenticated_manifest(
    synthetic_release: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manifest-provided signer claims cannot select the manifest trust root."""
    forged_identity = r"^https://github\.com/attacker/forged/.*$"
    forged_issuer = "https://attacker.example/oidc"
    payload = synthetic_release["payload"]
    payload["signer_identity"] = forged_identity
    payload["signer_issuer"] = forged_issuer
    synthetic_release["manifest_path"].write_text(json.dumps(payload), encoding="utf-8")
    calls: list[tuple[bytes, bytes, str, str]] = []

    def record_verify(
        blob: Path,
        bundle: Path,
        *,
        identity_regexp: str,
        issuer: str,
        job_id: str | None = None,
    ) -> None:
        calls.append((blob.read_bytes(), bundle.read_bytes(), identity_regexp, issuer))

    monkeypatch.setattr(updater_module, "_verify_cosign", record_verify)

    info = asyncio.run(Updater().check())

    assert calls == [
        (
            synthetic_release["manifest_path"].read_bytes(),
            b"manifest-bundle-placeholder\n",
            updater_module._MANIFEST_SIGNER_IDENTITY_REGEXP,
            updater_module._MANIFEST_SIGNER_ISSUER,
        )
    ]
    assert info.signer_identity == forged_identity


@pytest.mark.parametrize(
    "identity",
    [
        "https://github.com/Hal0ai/hal0/.github/workflows/release.yml@refs/tags/v1.2.3",
        "https://github.com/hal0ai/hal0/.github/workflows/release.yml@refs/tags/v1.2.3-rc.1",
        "https://github.com/Hal0ai/hal0/.github/workflows/release.yml@refs/heads/main",
    ],
)
def test_manifest_pinned_identity_accepts_supported_release_refs(identity: str) -> None:
    """The trust root covers direct release tags and nightly's reusable caller ref."""
    assert re.fullmatch(updater_module._MANIFEST_SIGNER_IDENTITY_REGEXP, identity)


@pytest.mark.parametrize(
    "identity",
    [
        "https://github.com/attacker/hal0/.github/workflows/release.yml@refs/tags/v1.2.3",
        "https://github.com/Hal0ai/hal0/.github/workflows/other.yml@refs/tags/v1.2.3",
        "https://github.com/Hal0ai/hal0/.github/workflows/release.yml@refs/heads/feature",
        "https://github.com/Hal0ai/hal0/.github/workflows/release.yml@refs/tags/not-v1.2.3",
    ],
)
def test_manifest_pinned_identity_rejects_unofficial_subjects(identity: str) -> None:
    assert re.fullmatch(updater_module._MANIFEST_SIGNER_IDENTITY_REGEXP, identity) is None


def test_check_handles_missing_manifest(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A nonexistent manifest surfaces UpdateError, not a raw OSError."""
    monkeypatch.setenv("HAL0_RELEASES_URL", str(tmp_path / "nope.json"))
    with pytest.raises(UpdateError):
        asyncio.run(Updater().check())


def test_check_rejects_wrong_channel_manifest(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """check() rejects a manifest that prepare() would reject for the channel."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    tarball = _build_release_tarball(tmp=artifacts, version="99.0.0")
    manifest_path = artifacts / "latest.json"
    _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball,
        version="99.0.0",
        overrides={"channel": "nightly", "release_kind": "nightly"},
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest_path))

    with pytest.raises(UpdateManifestInvalid) as exc_info:
        asyncio.run(Updater(channel="stable").check())

    assert exc_info.value.code == "system.update_manifest_invalid"
    assert exc_info.value.details["channel"] == "stable"


def test_prepare_rejects_wrong_channel_before_download_or_cache_write(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """prepare() validates channel coherence before creating staged state."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    tarball = _build_release_tarball(tmp=artifacts, version="99.0.0")
    manifest_path = artifacts / "latest.json"
    _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball,
        version="99.0.0",
        overrides={"channel": "nightly", "release_kind": "nightly"},
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest_path))
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)
    downloads: list[str] = []

    async def record_download(url: str, destination: Path) -> None:
        downloads.append(url)

    monkeypatch.setattr(updater_module, "_download", record_download)

    with pytest.raises(UpdateManifestInvalid):
        asyncio.run(Updater(channel="stable").prepare())

    assert downloads == []
    assert not updater_module._cache_dir("99.0.0").exists()


def test_validate_manifest_for_channel_is_public_export() -> None:
    """The shared channel validator is available from the updater package."""
    assert "validate_manifest_for_channel" in updater_package.__all__
    assert (
        updater_package.validate_manifest_for_channel
        is updater_module.validate_manifest_for_channel
    )


def test_check_does_not_recommend_revoked_latest(
    tmp_hal0_home: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cosign_skip: None,
) -> None:
    """A revoked latest manifest is NOT reported as an available update."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    version = "99.0.0"  # far ahead of __version__ so it WOULD update if not revoked
    tarball = _build_release_tarball(tmp=artifacts, version=version)
    manifest_path = artifacts / "latest.json"
    _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball,
        version=version,
        overrides={"revoked": True, "revoked_reason": "yanked: broken slot load"},
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest_path))

    info = asyncio.run(Updater().check())
    assert info.latest == version
    assert info.update_available is False
    assert info.revoked is True
    assert info.revoked_reason == "yanked: broken slot load"


def test_check_recommends_non_revoked_newer_latest(
    tmp_hal0_home: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cosign_skip: None,
) -> None:
    """A non-revoked newer manifest IS reported as an available update."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    version = "99.0.0"
    tarball = _build_release_tarball(tmp=artifacts, version=version)
    manifest_path = artifacts / "latest.json"
    _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball,
        version=version,
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest_path))

    info = asyncio.run(Updater().check())
    assert info.latest == version
    assert info.update_available is True
    assert info.revoked is False


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
    manifest_path = Path(os.environ["HAL0_RELEASES_URL"])
    _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball2,
        version="0.0.2",
    )

    asyncio.run(Updater().apply())
    record = _previous_record()
    assert record.exists()
    assert "hal0-0.0.1" in record.read_text(encoding="utf-8")
    assert _versioned_install_dir("0.0.2").exists()
    assert Path(os.readlink(_current_symlink())).name == "hal0-0.0.2"


# ── apply re-pip into venv (#495) ────────────────────────────────────────────────


def test_apply_repips_swapped_tree_when_not_editable(
    synthetic_release: dict[str, Any], cosign_skip: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prod (non-editable) apply re-pips the swapped-in tree into the venv."""
    calls: list[Path] = []
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)
    monkeypatch.setattr(
        "hal0.updater.updater._reinstall_into_venv",
        lambda install_dir, *, job_id=None: calls.append(install_dir),
    )

    res = asyncio.run(Updater().apply())

    assert res["version"] == "0.0.1"
    assert calls == [_versioned_install_dir("0.0.1")]
    # Re-pip succeeded → the swap stands.
    assert Path(os.readlink(_current_symlink())).name == "hal0-0.0.1"


def test_apply_hard_refuses_in_editable_mode_not_skip(
    synthetic_release: dict[str, Any], cosign_skip: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editable/dev installs raise UpdateError — they no longer silently skip re-pip.

    Pre-#625 behaviour was to skip the re-pip step and succeed; now apply()
    hard-refuses so the caller knows the update was not applied (#625).
    """
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: True)

    with pytest.raises(UpdateError) as exc_info:
        asyncio.run(Updater().apply())

    assert "editable" in str(exc_info.value).lower()


def test_apply_repip_failure_rolls_back_symlink(
    synthetic_release: dict[str, Any],
    cosign_skip: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed re-pip rolls `current` back to the prior tree (consistency)."""
    # First apply (non-editable, noop reinstall) lands current → 0.0.1.
    # (synthetic_release fixture already stubs _is_editable_install → False
    # and _reinstall_into_venv → noop, so this just exercises the swap.)
    asyncio.run(Updater().apply())
    assert Path(os.readlink(_current_symlink())).name == "hal0-0.0.1"

    # Build v0.0.2 and rewire the manifest.
    artifacts = tmp_path / "v2"
    artifacts.mkdir()
    tarball2 = _build_release_tarball(tmp=artifacts, version="0.0.2")
    _write_release_manifest(
        manifest_path=Path(os.environ["HAL0_RELEASES_URL"]),
        tarball=tarball2,
        version="0.0.2",
    )

    # Now force non-editable mode with a re-pip that blows up.
    def _boom(install_dir: Path, *, job_id: str | None = None) -> None:
        raise UpdateError("pip reinstall failed", details={})

    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)
    monkeypatch.setattr("hal0.updater.updater._reinstall_into_venv", _boom)

    with pytest.raises(UpdateError):
        asyncio.run(Updater().apply())

    # Rolled back: current still points at the prior (0.0.1) tree.
    assert Path(os.readlink(_current_symlink())).name == "hal0-0.0.1"


# ── prepare / commit split ─────────────────────────────────────────────────────


@pytest.mark.parametrize("requested_version", ["0.0.2", "../../outside"])
def test_prepare_rejects_mismatched_pin_before_staging_paths(
    requested_version: str,
    tmp_hal0_home: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An authenticated channel manifest cannot be staged under a caller label."""
    manifest = ReleaseManifest.model_validate({**VALID_MANIFEST, "version": "0.0.1"})

    async def fake_fetch(
        channel: str, *, job_id: str | None = None
    ) -> tuple[dict[str, Any], ReleaseManifest, str]:
        return manifest.model_dump(by_alias=True), manifest, "https://example.test/stable.json"

    async def unexpected_download(url: str, dest: Path) -> None:
        raise AssertionError(f"artifact download reached for mismatched pin: {url} -> {dest}")

    def unexpected_path(version: str) -> Path:
        raise AssertionError(f"staging path constructed for mismatched pin: {version}")

    monkeypatch.setattr(updater_module, "_is_editable_install", lambda: False)
    monkeypatch.setattr(updater_module, "_fetch_verified_release_manifest", fake_fetch)
    monkeypatch.setattr(updater_module, "_download", unexpected_download)
    monkeypatch.setattr(updater_module, "_cache_dir", unexpected_path)
    monkeypatch.setattr(updater_module, "_versioned_install_dir", unexpected_path)

    with pytest.raises(UpdateManifestInvalid) as exc_info:
        asyncio.run(Updater(channel="stable").prepare(requested_version))

    assert exc_info.value.details == {
        "channel": "stable",
        "requested_version": requested_version,
        "manifest_version": "0.0.1",
    }


@pytest.mark.parametrize("requested_version", ["0.0.1", " 0.0.1 "])
def test_prepare_matching_pin_stages_authenticated_manifest_version(
    requested_version: str,
    synthetic_release: dict[str, Any],
    cosign_skip: None,
) -> None:
    """An exact optimistic pin preserves the normal prepare flow after trimming."""
    res = asyncio.run(Updater().prepare(requested_version))

    assert res["version"] == "0.0.1"
    assert _versioned_install_dir("0.0.1").is_dir()
    assert updater_module._manifest_cache_path("0.0.1").is_file()


def test_apply_mismatched_pin_never_commits(
    tmp_hal0_home: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single-step apply propagates pin mismatch without activation."""
    manifest = ReleaseManifest.model_validate({**VALID_MANIFEST, "version": "0.0.1"})
    committed = False

    async def fake_fetch(
        channel: str, *, job_id: str | None = None
    ) -> tuple[dict[str, Any], ReleaseManifest, str]:
        return manifest.model_dump(by_alias=True), manifest, "https://example.test/stable.json"

    async def fake_commit(version: str) -> dict[str, Any]:
        nonlocal committed
        committed = True
        return {}

    updater = Updater(channel="stable")
    monkeypatch.setattr(updater_module, "_is_editable_install", lambda: False)
    monkeypatch.setattr(updater_module, "_fetch_verified_release_manifest", fake_fetch)
    monkeypatch.setattr(updater, "commit", fake_commit)

    with pytest.raises(UpdateManifestInvalid):
        asyncio.run(updater.apply("0.0.2"))

    assert committed is False
    assert not _current_symlink().exists()


def test_prepare_stages_without_swap(synthetic_release: dict[str, Any], cosign_skip: None) -> None:
    """prepare() downloads + verifies + extracts but activates nothing.

    The staged tree lands under /usr/lib/hal0-<version>/ yet the `current`
    symlink is untouched — an abandoned prepare is discarded by deleting the
    staged dir.
    """
    res = asyncio.run(Updater().prepare())
    assert res["version"] == "0.0.1"
    assert "notes" in res

    # The versioned tree is staged …
    install = _versioned_install_dir("0.0.1")
    assert install.exists()
    assert (install / "VERSION").read_text().strip() == "0.0.1"

    # … but nothing was activated: the `current` symlink does not resolve to it.
    link = _current_symlink()
    assert not link.is_symlink() or (Path(os.readlink(link)).resolve() != install.resolve())


def test_commit_without_prepare_raises(tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """commit() with nothing staged for the version raises UpdateError.

    A fresh HAL0_HOME means no prepare() has run, so `_versioned_install_dir`
    and the cached manifest are both absent.
    """
    # Pretend prod (non-editable) so we reach the staged-manifest guard rather
    # than the editable-install refusal.
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)
    with pytest.raises(UpdateError):
        asyncio.run(Updater().commit("0.0.1"))


def test_commit_rejects_cached_manifest_for_different_version(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """commit() never activates a staged tree whose cached manifest names another version."""
    target_version = "0.0.1"
    install_dir = _versioned_install_dir(target_version)
    install_dir.mkdir(parents=True)
    manifest_path = updater_module._manifest_cache_path(target_version)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({**VALID_MANIFEST, "version": "0.0.2"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(updater_module, "_is_editable_install", lambda: False)

    with pytest.raises(UpdateManifestInvalid) as exc_info:
        asyncio.run(Updater(channel="stable").commit(target_version))

    assert exc_info.value.details == {
        "channel": "stable",
        "target_version": target_version,
        "manifest_version": "0.0.2",
    }
    assert not _current_symlink().exists()


def test_prepare_then_commit_swaps(synthetic_release: dict[str, Any], cosign_skip: None) -> None:
    """prepare() then commit() reaches the same end state as apply()."""
    asyncio.run(Updater().prepare())
    # Not yet activated after prepare.
    assert not _current_symlink().is_symlink()

    res = asyncio.run(Updater().commit("0.0.1"))
    assert res["version"] == "0.0.1"

    link = _current_symlink()
    assert link.is_symlink()
    install = _versioned_install_dir("0.0.1")
    assert Path(os.readlink(link)).resolve() == install.resolve()


def test_prepare_reads_release_notes(
    tmp_hal0_home: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cosign_skip: None,
) -> None:
    """prepare() reads RELEASE_NOTES.md + release.json from the verified tree.

    Both files ship at the tarball ROOT (so they're covered by the sha256 +
    cosign verification) and surface on the returned ``notes`` dict.
    """
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    version = "0.0.1"
    contents = {
        "site-packages/hal0/__init__.py": f'__version__ = "{version}"\n',
        "VERSION": version,
        "RELEASE_NOTES.md": "# 0.0.1\n- did xyz\n",
        "release.json": json.dumps({"highlights": ["h"], "breaking": ["b"], "migrations": ["m"]}),
    }
    tarball = _build_release_tarball(tmp=artifacts, version=version, contents=contents)
    manifest_path = artifacts / "latest.json"
    _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball,
        version=version,
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest_path))
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)

    res = asyncio.run(Updater().prepare())
    notes = res["notes"]
    assert "did xyz" in notes["markdown"]
    assert notes["highlights"] == ["h"]
    assert notes["breaking"] == ["b"]
    assert notes["migrations"] == ["m"]


def test_read_release_notes_missing_is_empty(tmp_path: Path) -> None:
    """A tree with no notes files yields all-empty lists + empty markdown."""
    notes = _read_release_notes(tmp_path)
    assert notes["markdown"] == ""
    assert notes["highlights"] == []
    assert notes["breaking"] == []
    assert notes["migrations"] == []


# ── apply error paths ──────────────────────────────────────────────────────────


def test_apply_sha_mismatch_raises_typed_error(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cosign_skip: None
) -> None:
    """A tampered digest in the manifest produces UpdateVerifyError."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    tarball = _build_release_tarball(tmp=artifacts, version="0.0.1")
    manifest_path = artifacts / "latest.json"
    _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball,
        version="0.0.1",
        overrides={"digest_sha256": "0" * 64},
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest_path))
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)

    with pytest.raises(UpdateVerifyError) as exc_info:
        asyncio.run(Updater().apply())
    assert exc_info.value.code == "system.update_verify_failed"


def test_apply_refuses_when_install_dir_exists_with_foreign_content(
    synthetic_release: dict[str, Any], cosign_skip: None
) -> None:
    """If /usr/lib/hal0-<version>/ exists with foreign content, refuse.

    Foreign = no VERSION file and no hal0 pyproject.toml. We will not
    silently destroy whatever the operator parked there.
    """
    install = _versioned_install_dir("0.0.1")
    install.mkdir(parents=True, exist_ok=True)
    (install / "stale-marker").write_text("leftover")

    with pytest.raises(UpdateExtractError):
        asyncio.run(Updater().apply())


def test_apply_quarantines_stale_hal0_install_and_retries(
    synthetic_release: dict[str, Any], cosign_skip: None
) -> None:
    """A prior half-finished hal0 extract should be moved aside, not block."""
    install = _versioned_install_dir("0.0.1")
    install.mkdir(parents=True, exist_ok=True)
    # Marker that _looks_like_hal0_install() recognises.
    (install / "VERSION").write_text("0.0.1\n")
    (install / "leftover.txt").write_text("from prior failed apply")

    asyncio.run(Updater().apply())

    # Fresh extract landed.
    assert (install / "VERSION").read_text().strip() == "0.0.1"
    assert not (install / "leftover.txt").exists()
    # Old contents are recoverable next to it.
    siblings = [p for p in install.parent.iterdir() if p.name.startswith(f"{install.name}.stale-")]
    assert siblings, (
        f"expected one quarantine dir alongside {install}, got {list(install.parent.iterdir())}"
    )
    assert (siblings[0] / "leftover.txt").read_text() == "from prior failed apply"


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
        "bundle_url": f"file://{tmp_path / 'nope.tar.gz.bundle'}",
        "digest_sha256": "a" * 64,
        "signer_identity": "^https://github.com/.*",
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest_path))
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)

    with pytest.raises(UpdateDownloadError):
        asyncio.run(Updater().apply())


# ── cosign ─────────────────────────────────────────────────────────────────────


def test_cosign_missing_surfaces_typed_error(
    synthetic_release: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When cosign isn't installed, apply raises UpdateCosignMissing with
    install hints rather than silently falling back to unsigned acceptance."""
    # Force "cosign not found" by emptying PATH.
    monkeypatch.setenv("PATH", "")

    with pytest.raises(UpdateCosignMissing) as exc_info:
        asyncio.run(Updater().apply())
    assert exc_info.value.code == "system.update_cosign_missing"
    assert "install_hint_arch" in exc_info.value.details


def test_cosign_failure_surfaces_typed_error(
    synthetic_release: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When cosign exists but rejects the signature, apply raises UpdateCosignFailed."""
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
    manifest_path = Path(os.environ["HAL0_RELEASES_URL"])
    _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball2,
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


# ── rollback re-pip into venv (#980) ──────────────────────────────────────────


def test_rollback_repips_prior_tree_when_not_editable(
    synthetic_release: dict[str, Any],
    cosign_skip: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prod (non-editable) rollback re-pips the prior tree into the venv (#980).

    After apply() v1 → apply() v2 → rollback(), _reinstall_into_venv must be
    called with the v1 (prior) install directory so the next hal0-api restart
    actually runs v1, not the v2 code still in site-packages.
    """
    # Land v0.0.1.
    asyncio.run(Updater().apply())
    v1_dir = _versioned_install_dir("0.0.1")

    # Build and apply v0.0.2.
    artifacts = tmp_path / "v2"
    artifacts.mkdir()
    tarball2 = _build_release_tarball(tmp=artifacts, version="0.0.2")
    _write_release_manifest(
        manifest_path=Path(os.environ["HAL0_RELEASES_URL"]),
        tarball=tarball2,
        version="0.0.2",
    )
    asyncio.run(Updater().apply())
    assert Path(os.readlink(_current_symlink())).name == "hal0-0.0.2"

    # Capture _reinstall_into_venv calls during rollback.
    calls: list[Path] = []

    def _capture(install_dir: Path, *, job_id: str | None = None) -> None:
        calls.append(install_dir)

    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)
    monkeypatch.setattr("hal0.updater.updater._reinstall_into_venv", _capture)

    res = asyncio.run(Updater().rollback())

    # rollback returned the prior (v0.0.1) path.
    assert "hal0-0.0.1" in res["rolled_back_to"]
    # _reinstall_into_venv was called exactly once with the v0.0.1 dir.
    assert calls == [v1_dir]
    # Symlink points at v0.0.1.
    assert Path(os.readlink(_current_symlink())).name == "hal0-0.0.1"


def test_rollback_repip_failure_re_swaps_symlink_forward(
    synthetic_release: dict[str, Any],
    cosign_skip: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing re-pip during rollback swaps `current` forward again (#980).

    If _reinstall_into_venv raises during rollback, the symlink must be
    restored to the version we just rolled *away from* (current_target) so
    that the symlink and the venv's installed code remain consistent.
    """
    # Land v0.0.1.
    asyncio.run(Updater().apply())

    # Build and apply v0.0.2.
    artifacts = tmp_path / "v2"
    artifacts.mkdir()
    tarball2 = _build_release_tarball(tmp=artifacts, version="0.0.2")
    _write_release_manifest(
        manifest_path=Path(os.environ["HAL0_RELEASES_URL"]),
        tarball=tarball2,
        version="0.0.2",
    )
    asyncio.run(Updater().apply())
    assert Path(os.readlink(_current_symlink())).name == "hal0-0.0.2"

    # Force the re-pip to fail.
    def _boom(install_dir: Path, *, job_id: str | None = None) -> None:
        raise UpdateError("pip reinstall failed during rollback", details={})

    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)
    monkeypatch.setattr("hal0.updater.updater._reinstall_into_venv", _boom)

    with pytest.raises(UpdateError):
        asyncio.run(Updater().rollback())

    # Symlink should be restored to v0.0.2 (the version we tried to leave).
    assert Path(os.readlink(_current_symlink())).name == "hal0-0.0.2"


# ── channel switching ─────────────────────────────────────────────────────────


def test_check_uses_per_channel_url(
    tmp_hal0_home: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cosign_skip: None,
) -> None:
    """The check() method honours its channel argument when looking up the URL."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    tarball = _build_release_tarball(tmp=artifacts, version="0.0.1")
    manifest_path = artifacts / "latest.json"
    _write_release_manifest(
        manifest_path=manifest_path,
        tarball=tarball,
        version="0.0.1",
        overrides={"channel": "nightly", "release_kind": "nightly"},
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest_path))

    info = asyncio.run(Updater(channel="stable").check(channel="nightly"))
    assert info.channel == "nightly"


# ── #510: dead-code sweep ──────────────────────────────────────────────────────


def test_default_releases_url_export_removed() -> None:
    """The stale DEFAULT_RELEASES_URL export (pointed at /latest.json) is gone."""
    import hal0.updater as pkg
    import hal0.updater.updater as mod

    assert not hasattr(mod, "DEFAULT_RELEASES_URL")
    assert not hasattr(pkg, "DEFAULT_RELEASES_URL")
    assert "DEFAULT_RELEASES_URL" not in mod.__all__
    assert "DEFAULT_RELEASES_URL" not in pkg.__all__


def test_updater_pull_alias_removed() -> None:
    """Updater.pull (no callers) is removed; apply() is the only entrypoint."""
    assert not hasattr(Updater, "pull")


def test_release_manifest_channel_does_not_advertise_dev() -> None:
    """The manifest channel description does not advertise a dev channel."""
    desc = ReleaseManifest.model_fields["channel"].description or ""
    assert "dev" not in desc


# ── editable-install guard (#625) ─────────────────────────────────────────────


def test_apply_hard_refuses_on_editable_install(
    tmp_hal0_home: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Updater.apply() must raise UpdateError immediately on an editable install.

    Before #625, apply() silently extracted + swapped but never re-pipped,
    so `hal0 update` appeared to succeed while changing nothing.  The fix
    adds an early guard that hard-refuses with a clear message.
    """
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: True)

    with pytest.raises(UpdateError) as exc_info:
        asyncio.run(Updater().apply())

    err = exc_info.value
    # Must be an UpdateError (not a silent success or a generic exception).
    assert isinstance(err, UpdateError)
    # The message must guide the user toward the correct alternative.
    assert "editable" in str(err).lower() or "git pull" in str(err).lower()
    # No network call, no file I/O — the guard fires before Step 1.
    # (Verified implicitly: no HAL0_RELEASES_URL env → would fail if reached.)


# ── _version_tuple ordering — nightly timestamp monotonicity ──────────────────


def test_version_tuple_timestamp_nightly_beats_date_only_same_base() -> None:
    """A 14-digit UTC timestamp nightly sorts strictly above a date-only nightly.

    The nightly workflow now emits YYYYMMDDHHMMSS so same-day re-cuts produce
    a strictly larger version that the updater will recognise as newer.  Legacy
    YYYYMMDD tags still order below any timestamp tag with the same base (because
    20260615 < 20260615000000 as integers).
    """
    assert _version_tuple("0.5.1-nightly.20260615120000") > _version_tuple("0.5.1-nightly.20260615")
    assert _version_tuple("0.5.1-nightly.20260615") > _version_tuple("0.5.0-nightly.20260615")


# ── _is_newer — PEP 440 comparison (regression for the 0.8.0b3→0.8.1-beta.1 miss) ──


def test_is_newer_beta_across_patch_boundary() -> None:
    """Regression: a pip-normalised beta (``0.8.0b3``) must order *below* the next
    patch's beta in tag form (``0.8.1-beta.1``).

    The old ``_version_tuple`` digit-parser read ``0.8.0b3`` as ``(0, 8, 3)`` and
    ``0.8.1-beta.1`` as ``(0, 8, 1, 1)``, so every box on a ``0.8.0bN`` beta saw the
    new ``0.8.1`` as "not newer" and ``hal0 update`` reported nothing to apply.
    """
    assert _is_newer("0.8.1-beta.1", "0.8.0b3") is True
    assert _is_newer("0.8.0b3", "0.8.1-beta.1") is False
    # Same release (tag form vs pip-normalised) is not an upgrade.
    assert _is_newer("0.8.1-beta.1", "0.8.1b1") is False
    # Within a beta line still advances.
    assert _is_newer("0.8.1-beta.2", "0.8.1-beta.1") is True


def test_is_newer_falls_back_to_tuple_for_nightly() -> None:
    """Nightly tags are not valid PEP 440, so ``_is_newer`` falls back to the
    digit-tuple compare and keeps timestamp monotonicity."""
    assert _is_newer("0.5.1-nightly.20260615120000", "0.5.1-nightly.20260615") is True
    assert _is_newer("0.5.1-nightly.20260615", "0.5.0-nightly.20260615") is True


class TestVenvRefreshIsCommitAgnostic:
    """The venv refresh must NOT gate on the version string.

    Operator finding: ``hal0 update --source git`` on an unchanged version
    (0.9.8) left OLD code running because a plain ``pip install <tree>`` is a
    no-op when pip sees the same version already satisfied. The src-side
    refresh (:func:`_reinstall_into_venv`, used by every commit/apply/rollback)
    must therefore ALWAYS force-reinstall so a same-version-but-different-commit
    tree still lands its new code. This test locks that invariant.

    (The install.sh ``--source git`` path is out of this lane's fence — the
    corresponding ``pip install --force-reinstall`` delta is in the report.)
    """

    def test_reinstall_forces_and_does_not_version_gate(self, monkeypatch, tmp_path) -> None:
        from hal0.updater.updater import _reinstall_into_venv

        captured: dict[str, Any] = {}

        class _OK:
            returncode = 0
            stdout = ""
            stderr = ""

        def _fake_run(cmd, *a, **k):
            captured["cmd"] = cmd
            return _OK()

        monkeypatch.setattr("hal0.updater.updater.subprocess.run", _fake_run)
        _reinstall_into_venv(tmp_path / "hal0-0.9.8")

        cmd = captured["cmd"]
        assert "install" in cmd
        # Commit-agnostic: force-reinstall regardless of version equality.
        assert "--force-reinstall" in cmd
        # Never conditions the pip call on a version comparison.
        assert str(tmp_path / "hal0-0.9.8") in cmd
