"""Behavioral contracts for the release-manifest bootstrap trust boundary."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP = _REPO_ROOT / "installer" / "bootstrap.sh"
_CANONICAL_IDENTITY = (
    r"^https://github\.com/(Hal0ai|hal0ai)/hal0/"
    r"\.github/workflows/release\.yml@"
    r"(refs/tags/v\d+\.\d+\.\d+"
    r"(-(alpha|beta|rc)\.(0|[1-9]\d*)|-nightly\.\d{14})?"
    r"|refs/heads/main)$"
)
_CANONICAL_ISSUER = "https://token.actions.githubusercontent.com"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _bootstrap_env(
    tmp_path: Path,
    manifest: bytes,
    *,
    with_cosign: bool = True,
    cosign_rc: int = 1,
) -> tuple[dict[str, str], Path, Path]:
    """Build a hermetic PATH that records network and cosign behavior."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "uname", "mktemp", "rm", "tar", "sha256sum", "python3"):
        target = shutil.which(tool)
        assert target is not None
        os.symlink(target, bin_dir / tool)

    manifest_fixture = tmp_path / "manifest.fixture"
    manifest_fixture.write_bytes(manifest)
    curl_log = tmp_path / "curl.log"
    cosign_log = tmp_path / "cosign.log"

    cp = shutil.which("cp")
    assert cp is not None
    _write_executable(
        bin_dir / "curl",
        f"""#!/usr/bin/env bash
set -euo pipefail
out=""
url=""
while (($#)); do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        --retry|--retry-delay) shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
printf '%s\\n' "$url" >> "$CURL_LOG"
case "$url" in
    *.json|*.json\\?*) {cp} "$MANIFEST_FIXTURE" "$out" ;;
    *.json.bundle|*.json.bundle\\?*) printf 'fixture bundle\\n' > "$out" ;;
    *) exit 22 ;;
esac
""",
    )
    if with_cosign:
        _write_executable(
            bin_dir / "cosign",
            """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$COSIGN_LOG"
exit "$COSIGN_RC"
""",
        )

    env = {
        "PATH": str(bin_dir),
        "CURL_LOG": str(curl_log),
        "COSIGN_LOG": str(cosign_log),
        "COSIGN_RC": str(cosign_rc),
        "MANIFEST_FIXTURE": str(manifest_fixture),
    }
    return env, curl_log, cosign_log


def _run_bootstrap(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(_BOOTSTRAP)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_bootstrap_uses_canonical_channel_endpoint() -> None:
    script = _BOOTSTRAP.read_text(encoding="utf-8")
    assert "https://releases.hal0.dev/${HAL0_CHANNEL}.json" in script
    assert "/releases/latest/download" not in script


@pytest.mark.parametrize("channel", ["stable", "preview", "nightly"])
def test_bootstrap_accepts_supported_channels_and_fetches_exact_sibling_bundle(
    tmp_path: Path, channel: str
) -> None:
    env, curl_log, _ = _bootstrap_env(tmp_path, b"{not trusted yet")
    env["HAL0_CHANNEL"] = channel

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        f"https://releases.hal0.dev/{channel}.json",
        f"https://releases.hal0.dev/{channel}.json.bundle",
    ]


@pytest.mark.parametrize("channel", ["beta", "PREVIEW", "stable/../../owned"])
def test_bootstrap_rejects_invalid_channel_before_network(
    tmp_path: Path, channel: str
) -> None:
    env, curl_log, _ = _bootstrap_env(tmp_path, b"{}")
    env["HAL0_CHANNEL"] = channel

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "HAL0_CHANNEL must be one of: stable, preview, nightly" in proc.stderr
    assert not curl_log.exists()


def test_bootstrap_preserves_override_and_places_bundle_before_query(
    tmp_path: Path,
) -> None:
    env, curl_log, _ = _bootstrap_env(tmp_path, b"{not trusted yet")
    env["HAL0_CHANNEL"] = "preview"
    env["HAL0_RELEASES_URL"] = "https://mirror.example/pointers/custom.json?token=test"

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        "https://mirror.example/pointers/custom.json?token=test",
        "https://mirror.example/pointers/custom.json.bundle?token=test",
    ]


def test_bootstrap_verifies_manifest_before_parsing_or_fetching_artifacts(
    tmp_path: Path,
) -> None:
    manifest = b'{"url":"https://attacker.example/untrusted.tar.gz"}'
    env, curl_log, cosign_log = _bootstrap_env(tmp_path, manifest, cosign_rc=1)

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "release manifest signature verification FAILED" in proc.stderr
    assert "attacker.example" not in curl_log.read_text(encoding="utf-8")
    args = cosign_log.read_text(encoding="utf-8").splitlines()
    assert args[0] == "verify-blob"
    assert args[1] == "--bundle"
    assert args[2].endswith("/manifest.json.bundle")
    assert args[3:5] == ["--certificate-identity-regexp", _CANONICAL_IDENTITY]
    assert args[5:7] == ["--certificate-oidc-issuer", _CANONICAL_ISSUER]
    assert args[-1].endswith("/manifest.json")


def test_stable_bootstrap_rejects_authenticated_preview_artifact(
    tmp_path: Path,
) -> None:
    manifest = json.dumps(
        {
            "version": "1.0.0-rc.1",
            "channel": "stable",
            "release_kind": "preview",
            "prerelease_stage": "rc",
            "url": "https://attacker.example/preview.tar.gz",
            "bundle_url": "https://attacker.example/preview.tar.gz.bundle",
            "digest_sha256": "0" * 64,
            "signer_identity": "untrusted-until-manifest-verification",
        }
    ).encode()
    env, curl_log, _ = _bootstrap_env(tmp_path, manifest, cosign_rc=0)

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "release kind preview is not accepted for channel stable" in proc.stderr
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        "https://releases.hal0.dev/stable.json",
        "https://releases.hal0.dev/stable.json.bundle",
    ]


def test_bootstrap_missing_cosign_fails_closed_for_channel_manifest(
    tmp_path: Path,
) -> None:
    env, curl_log, _ = _bootstrap_env(tmp_path, b"{}", with_cosign=False)

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "cosign is required to verify the release manifest" in proc.stderr
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        "https://releases.hal0.dev/stable.json",
        "https://releases.hal0.dev/stable.json.bundle",
    ]
