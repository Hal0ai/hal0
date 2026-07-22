"""Behavioral contracts for the release-manifest bootstrap trust boundary."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP = _REPO_ROOT / "installer" / "bootstrap.sh"
_IDENTITY_PREFIX = (
    r"^https://github\.com/(Hal0ai|hal0ai)/hal0/"
    r"\.github/workflows/release\.yml@"
)
_STABLE_ADMISSION = _IDENTITY_PREFIX + r"refs/tags/v\d+\.\d+\.\d+$"
_PREVIEW_ADMISSION = (
    _IDENTITY_PREFIX + r"refs/tags/v\d+\.\d+\.\d+(-(alpha|beta|rc)\.(0|[1-9]\d*))?$"
)
_MAIN_IDENTITY = _IDENTITY_PREFIX + r"refs/heads/main$"
_CANONICAL_ISSUER = "https://token.actions.githubusercontent.com"


def _admission_identity(channel: str) -> str:
    return {
        "stable": _STABLE_ADMISSION,
        "preview": _PREVIEW_ADMISSION,
        "nightly": _MAIN_IDENTITY,
    }[channel]


def _exact_identity(release_kind: str, version: str) -> str:
    if release_kind == "nightly":
        return _MAIN_IDENTITY
    return _IDENTITY_PREFIX + "refs/tags/v" + version.replace(".", r"\.") + "$"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _bootstrap_env(
    tmp_path: Path,
    manifest: bytes,
    *,
    with_cosign: bool = True,
    cosign_rc: int = 1,
    artifact_fixture: Path | None = None,
    artifact_url: str = "",
    artifact_bundle_url: str = "",
    with_jq: bool = True,
) -> tuple[dict[str, str], Path, Path]:
    """Build a hermetic PATH that records network and cosign behavior."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in (
        "awk",
        "bash",
        "cat",
        "gzip",
        "uname",
        "mktemp",
        "mkdir",
        "rm",
        "tar",
        "sha256sum",
        "python3",
    ):
        target = shutil.which(tool)
        assert target is not None
        os.symlink(target, bin_dir / tool)
    if with_jq:
        jq = shutil.which("jq")
        assert jq is not None
        os.symlink(jq, bin_dir / "jq")

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
        --url) url="$2"; shift 2 ;;
        --config=*)
            cat "${{1#*=}}" > "$CONFIG_READ_LOG"
            shift
            ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
printf '%s\\n' "$url" >> "$CURL_LOG"
case "$url" in
    *.json|*.json\\?*) {cp} "$MANIFEST_FIXTURE" "$out" ;;
    *.json.bundle|*.json.bundle\\?*) printf 'fixture bundle\\n' > "$out" ;;
    "$ARTIFACT_URL") {cp} "$ARTIFACT_FIXTURE" "$out" ;;
    "$ARTIFACT_BUNDLE_URL") printf 'artifact fixture bundle\\n' > "$out" ;;
    *) exit 22 ;;
esac
""",
    )
    if with_cosign:
        _write_executable(
            bin_dir / "cosign",
            """#!/usr/bin/env bash
printf '%s\\n' "$@" >> "$COSIGN_LOG"
exit "$COSIGN_RC"
""",
        )

    env = {
        "PATH": str(bin_dir),
        "CURL_LOG": str(curl_log),
        "COSIGN_LOG": str(cosign_log),
        "COSIGN_RC": str(cosign_rc),
        "MANIFEST_FIXTURE": str(manifest_fixture),
        "ARTIFACT_FIXTURE": str(artifact_fixture or ""),
        "ARTIFACT_URL": artifact_url,
        "ARTIFACT_BUNDLE_URL": artifact_bundle_url,
        "CONFIG_READ_LOG": str(tmp_path / "config-read.log"),
        "TMPDIR": str(tmp_path),
    }
    return env, curl_log, cosign_log


def _run_bootstrap(
    env: dict[str, str],
    *args: str,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(_BOOTSTRAP), *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _valid_manifest(
    *,
    channel: str = "stable",
    release_kind: str = "stable",
    prerelease_stage: str | None = None,
    version: str = "1.2.3",
) -> dict[str, object]:
    return {
        "_schema": "hal0.releases.v1",
        "version": version,
        "channel": channel,
        "release_kind": release_kind,
        "prerelease_stage": prerelease_stage,
        "url": "https://fixtures.example/hal0.tar.gz",
        "bundle_url": "https://fixtures.example/hal0.tar.gz.bundle",
        "digest_sha256": "A" * 64,
        "signer_identity": _exact_identity(release_kind, version),
        "signer_issuer": _CANONICAL_ISSUER,
    }


def test_bootstrap_uses_canonical_channel_endpoint() -> None:
    script = _BOOTSTRAP.read_text(encoding="utf-8")
    assert "https://releases.hal0.dev/${HAL0_CHANNEL}.json" in script
    assert "/releases/latest/download" not in script


def test_bootstrap_requires_jq_and_artifact_bundle_without_detached_fallback() -> None:
    script = _BOOTSTRAP.read_text(encoding="utf-8")
    assert "need jq" in script
    assert 'verify_args=(--bundle "${bundle}")' in script
    assert "--signature" not in script
    assert "sig_url" not in script
    assert "cert_url" not in script


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
def test_bootstrap_rejects_invalid_channel_before_network(tmp_path: Path, channel: str) -> None:
    env, curl_log, _ = _bootstrap_env(tmp_path, b"{}")
    env["HAL0_CHANNEL"] = channel

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "HAL0_CHANNEL must be one of: stable, preview, nightly" in proc.stderr
    assert not curl_log.exists()


def test_bootstrap_missing_jq_fails_preflight_before_network(tmp_path: Path) -> None:
    env, curl_log, _ = _bootstrap_env(tmp_path, b"{}", with_jq=False)

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "missing dependency: jq" in proc.stderr
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


@pytest.mark.parametrize("channel", ["stable", "preview", "nightly"])
def test_bootstrap_first_verification_identity_depends_only_on_requested_channel(
    tmp_path: Path, channel: str
) -> None:
    forged = _valid_manifest(
        channel="nightly",
        release_kind="nightly",
        version="1.2.3-nightly.20260722123000",
    )
    env, _, cosign_log = _bootstrap_env(tmp_path, json.dumps(forged).encode(), cosign_rc=1)
    env["HAL0_CHANNEL"] = channel

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    args = cosign_log.read_text(encoding="utf-8").splitlines()
    assert args[3:5] == ["--certificate-identity-regexp", _admission_identity(channel)]


def test_bootstrap_verifies_manifest_before_parsing_or_fetching_artifacts(
    tmp_path: Path,
) -> None:
    manifest = b'{"url":"https://attacker.example/untrusted.tar.gz", "x": "$(touch owned)"}'
    env, curl_log, cosign_log = _bootstrap_env(tmp_path, manifest, cosign_rc=1)
    jq_log = tmp_path / "jq.log"
    (tmp_path / "bin" / "jq").unlink()
    _write_executable(
        tmp_path / "bin" / "jq",
        f"#!/usr/bin/env bash\nprintf invoked > {jq_log}\nexit 99\n",
    )

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "release manifest signature verification FAILED" in proc.stderr
    assert "attacker.example" not in curl_log.read_text(encoding="utf-8")
    assert not jq_log.exists()
    assert not (tmp_path / "owned").exists()
    args = cosign_log.read_text(encoding="utf-8").splitlines()
    assert args[0] == "verify-blob"
    assert args[1] == "--bundle"
    assert args[2].endswith("/manifest.json.bundle")
    assert args[3:5] == ["--certificate-identity-regexp", _STABLE_ADMISSION]
    assert args[5:7] == ["--certificate-oidc-issuer", _CANONICAL_ISSUER]
    assert args[-1].endswith("/manifest.json")


@pytest.mark.parametrize(
    ("updates", "removals", "requested_channel"),
    [
        pytest.param({}, {"_schema"}, "stable", id="schema-missing"),
        pytest.param({"_schema": "hal0.releases.v2"}, set(), "stable", id="schema-unknown"),
        pytest.param({"_schema": 1}, set(), "stable", id="schema-non-string"),
        pytest.param({}, {"version"}, "stable", id="version-missing"),
        pytest.param({"version": ""}, set(), "stable", id="version-empty"),
        pytest.param({"version": 1}, set(), "stable", id="version-non-string"),
        pytest.param({"version": "1.2.3-rc.1"}, set(), "stable", id="stable-version-preview"),
        pytest.param(
            {
                "version": "1.2.3",
                "channel": "preview",
                "release_kind": "preview",
                "prerelease_stage": "rc",
            },
            set(),
            "preview",
            id="preview-version-final",
        ),
        pytest.param(
            {
                "version": "1.2.3-nightly.20260722123",
                "channel": "nightly",
                "release_kind": "nightly",
            },
            set(),
            "nightly",
            id="nightly-version-wrong-width",
        ),
        pytest.param({}, {"url"}, "stable", id="url-missing"),
        pytest.param({"url": ""}, set(), "stable", id="url-empty"),
        pytest.param({"url": []}, set(), "stable", id="url-non-string"),
        pytest.param({}, {"bundle_url"}, "stable", id="bundle-url-missing"),
        pytest.param({"bundle_url": ""}, set(), "stable", id="bundle-url-empty"),
        pytest.param({"bundle_url": 1}, set(), "stable", id="bundle-url-non-string"),
        pytest.param({}, {"signer_identity"}, "stable", id="identity-missing"),
        pytest.param({"signer_identity": ""}, set(), "stable", id="identity-empty"),
        pytest.param({"signer_identity": False}, set(), "stable", id="identity-non-string"),
        pytest.param({}, {"signer_issuer"}, "stable", id="issuer-missing"),
        pytest.param({"signer_issuer": ""}, set(), "stable", id="issuer-empty"),
        pytest.param({"signer_issuer": {}}, set(), "stable", id="issuer-non-string"),
        pytest.param({}, {"digest_sha256"}, "stable", id="digest-missing"),
        pytest.param({"digest_sha256": 1}, set(), "stable", id="digest-non-string"),
        pytest.param({"digest_sha256": "0" * 63}, set(), "stable", id="digest-short"),
        pytest.param({"digest_sha256": "md5:" + "0" * 64}, set(), "stable", id="digest-prefix"),
        pytest.param({}, {"channel"}, "stable", id="channel-missing"),
        pytest.param({"channel": 1}, set(), "stable", id="channel-non-string"),
        pytest.param({"channel": "beta"}, set(), "stable", id="channel-noncanonical"),
        pytest.param({}, {"release_kind"}, "stable", id="kind-missing"),
        pytest.param({"release_kind": []}, set(), "stable", id="kind-non-string"),
        pytest.param({"release_kind": "beta"}, set(), "stable", id="kind-noncanonical"),
        pytest.param({}, set(), "preview", id="requested-channel-mismatch"),
        pytest.param(
            {"release_kind": "preview", "prerelease_stage": "rc"},
            set(),
            "stable",
            id="stable-rejects-preview-kind",
        ),
        pytest.param(
            {"channel": "preview", "release_kind": "preview", "prerelease_stage": None},
            set(),
            "preview",
            id="preview-stage-missing",
        ),
        pytest.param(
            {"channel": "preview", "release_kind": "preview", "prerelease_stage": "dev"},
            set(),
            "preview",
            id="preview-stage-unknown",
        ),
        pytest.param({"prerelease_stage": "rc"}, set(), "stable", id="stable-has-stage"),
        pytest.param(
            {"channel": "nightly", "release_kind": "nightly", "prerelease_stage": "alpha"},
            set(),
            "nightly",
            id="nightly-has-stage",
        ),
        pytest.param(
            {"channel": "preview", "prerelease_stage": "rc"},
            set(),
            "preview",
            id="promoted-stable-has-stage",
        ),
    ],
)
def test_authenticated_malformed_manifest_rejected_before_artifact_io(
    tmp_path: Path,
    updates: dict[str, object],
    removals: set[str],
    requested_channel: str,
) -> None:
    payload = copy.deepcopy(_valid_manifest())
    payload.update(updates)
    for field in removals:
        payload.pop(field)
    env, curl_log, cosign_log = _bootstrap_env(tmp_path, json.dumps(payload).encode(), cosign_rc=0)
    env["HAL0_CHANNEL"] = requested_channel

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "authenticated release manifest failed strict policy validation" in proc.stderr
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        f"https://releases.hal0.dev/{requested_channel}.json",
        f"https://releases.hal0.dev/{requested_channel}.json.bundle",
    ]
    assert cosign_log.read_text(encoding="utf-8").splitlines().count("verify-blob") == 1


def test_authenticated_manifest_signer_must_match_exact_release_identity(
    tmp_path: Path,
) -> None:
    payload = _valid_manifest()
    payload["signer_identity"] = _PREVIEW_ADMISSION
    env, curl_log, cosign_log = _bootstrap_env(tmp_path, json.dumps(payload).encode(), cosign_rc=0)

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "signer_identity does not match exact release identity" in proc.stderr
    assert len(curl_log.read_text(encoding="utf-8").splitlines()) == 2
    assert cosign_log.read_text(encoding="utf-8").splitlines().count("verify-blob") == 1


@pytest.mark.parametrize(
    "manifest",
    [
        pytest.param(
            json.dumps({"not": "a release manifest"}).encode()
            + b"\n"
            + json.dumps(_valid_manifest()).encode(),
            id="invalid-then-valid",
        ),
        pytest.param(
            json.dumps(_valid_manifest()).encode() + b"\n" + json.dumps(_valid_manifest()).encode(),
            id="valid-then-valid",
        ),
        pytest.param(
            json.dumps(_valid_manifest()).encode() + b"\ntrue",
            id="valid-then-trailing-value",
        ),
    ],
)
def test_authenticated_manifest_must_contain_exactly_one_json_value(
    tmp_path: Path,
    manifest: bytes,
) -> None:
    env, curl_log, cosign_log = _bootstrap_env(tmp_path, manifest, cosign_rc=0)
    env["HAL0_BOOTSTRAP_KEEP_TMP"] = "1"
    install_log = tmp_path / "install.log"
    env["INSTALL_LOG"] = str(install_log)

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "authenticated release manifest failed strict policy validation" in proc.stderr
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        "https://releases.hal0.dev/stable.json",
        "https://releases.hal0.dev/stable.json.bundle",
    ]
    assert cosign_log.read_text(encoding="utf-8").splitlines().count("verify-blob") == 1
    assert not install_log.exists()
    assert not list(tmp_path.glob("hal0-install-*/artifact.tar.gz"))


@pytest.mark.parametrize("hostile_field", ["url", "bundle_url"])
def test_authenticated_option_like_artifact_urls_are_explicit_curl_urls(
    tmp_path: Path,
    hostile_field: str,
) -> None:
    artifact = tmp_path / "artifact.fixture"
    artifact.write_bytes(b"authenticated artifact fixture")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    curl_config = tmp_path / "hostile.curlrc"
    curl_config.write_text("url = file:///etc/passwd\n", encoding="utf-8")
    hostile_url = f"--config={curl_config}"
    artifact_url = "https://fixtures.example/hal0.tar.gz"
    artifact_bundle_url = "https://fixtures.example/hal0.tar.gz.bundle"
    manifest = _valid_manifest()
    manifest.update(
        {
            "url": hostile_url if hostile_field == "url" else artifact_url,
            "bundle_url": hostile_url if hostile_field == "bundle_url" else artifact_bundle_url,
            "digest_sha256": digest,
        }
    )
    env, curl_log, cosign_log = _bootstrap_env(
        tmp_path,
        json.dumps(manifest).encode(),
        cosign_rc=0,
        artifact_fixture=artifact,
        artifact_url=artifact_url,
        artifact_bundle_url=artifact_bundle_url,
    )
    env["HAL0_BOOTSTRAP_KEEP_TMP"] = "1"
    install_log = tmp_path / "install.log"
    env["INSTALL_LOG"] = str(install_log)

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert curl_log.read_text(encoding="utf-8").splitlines()[-1] == hostile_url
    assert cosign_log.read_text(encoding="utf-8").splitlines().count("verify-blob") == 2
    assert not Path(env["CONFIG_READ_LOG"]).exists()
    assert not install_log.exists()
    assert not list(tmp_path.glob("hal0-install-*/artifact.tar.gz.bundle"))
    if hostile_field == "url":
        assert not list(tmp_path.glob("hal0-install-*/artifact.tar.gz"))


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


def test_bootstrap_cleanup_does_not_evaluate_hostile_tmpdir(
    tmp_path: Path,
) -> None:
    env, _, _ = _bootstrap_env(tmp_path, b"{}", cosign_rc=1)
    marker = tmp_path / "trap-injected"
    marker.touch()
    hostile_tmpdir = tmp_path / "hostile'; rm -f trap-injected; : '"
    hostile_tmpdir.mkdir()
    env["TMPDIR"] = str(hostile_tmpdir)

    proc = _run_bootstrap(env, cwd=tmp_path)

    assert proc.returncode != 0
    assert marker.exists()
    assert list(hostile_tmpdir.iterdir()) == []


@pytest.mark.parametrize(
    ("channel", "release_kind", "prerelease_stage", "version"),
    [
        pytest.param("stable", "stable", None, "1.2.3", id="stable"),
        pytest.param("preview", "preview", "rc", "1.2.3-rc.1", id="preview"),
        pytest.param(
            "nightly",
            "nightly",
            None,
            "1.2.3-nightly.20260722123000",
            id="nightly",
        ),
        pytest.param(
            "nightly",
            "nightly",
            None,
            "1.2.3-nightly.20260722",
            id="legacy-nightly",
        ),
        pytest.param("preview", "stable", None, "1.2.3", id="promoted-stable"),
    ],
)
def test_bootstrap_successfully_verifies_extracts_and_hands_off_fixture(
    tmp_path: Path,
    channel: str,
    release_kind: str,
    prerelease_stage: str | None,
    version: str,
) -> None:
    artifact_url = f"https://fixtures.example/hal0-{version}.tar.gz"
    artifact_bundle_url = f"{artifact_url}.bundle"
    artifact = tmp_path / f"hal0-{version}.tar.gz"
    install_log = tmp_path / "install.log"

    install_script = tmp_path / "tree" / f"hal0-{version}" / "installer" / "install.sh"
    install_script.parent.mkdir(parents=True)
    _write_executable(
        install_script,
        """#!/usr/bin/env bash
set -euo pipefail
printf 'verified=%s\\n' "${HAL0_BOOTSTRAP_VERIFIED:-}" > "$INSTALL_LOG"
printf 'arg=%s\\n' "$@" >> "$INSTALL_LOG"
""",
    )
    with tarfile.open(artifact, "w:gz") as archive:
        archive.add(install_script.parents[1], arcname=f"hal0-{version}")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = json.dumps(
        {
            "_schema": "hal0.releases.v1",
            "version": version,
            "channel": channel,
            "release_kind": release_kind,
            "prerelease_stage": prerelease_stage,
            "url": artifact_url,
            "bundle_url": artifact_bundle_url,
            "digest_sha256": f"sha256:{digest.upper()}",
            "signer_identity": _exact_identity(release_kind, version),
            "signer_issuer": _CANONICAL_ISSUER,
        }
    ).encode()
    env, curl_log, cosign_log = _bootstrap_env(
        tmp_path,
        manifest,
        cosign_rc=0,
        artifact_fixture=artifact,
        artifact_url=artifact_url,
        artifact_bundle_url=artifact_bundle_url,
    )
    env.update(
        {
            "HAL0_CHANNEL": channel,
            "INSTALL_LOG": str(install_log),
        }
    )

    proc = _run_bootstrap(env, "--no-tls", "--models-dir=/fixture")

    assert proc.returncode == 0, proc.stderr
    assert f"sha256 OK ({digest[:12]}…)" in proc.stdout
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        f"https://releases.hal0.dev/{channel}.json",
        f"https://releases.hal0.dev/{channel}.json.bundle",
        artifact_url,
        artifact_bundle_url,
    ]
    cosign_args = cosign_log.read_text(encoding="utf-8").splitlines()
    expected_identity = _exact_identity(release_kind, version)
    expected_verify_count = 2 if channel == "nightly" else 3
    assert cosign_args.count("verify-blob") == expected_verify_count
    assert cosign_args[1] == "--bundle"
    assert cosign_args[2].endswith("/manifest.json.bundle")
    assert cosign_args[3:5] == [
        "--certificate-identity-regexp",
        _admission_identity(channel),
    ]
    assert cosign_args[5:7] == ["--certificate-oidc-issuer", _CANONICAL_ISSUER]
    assert cosign_args[7].endswith("/manifest.json")
    if channel == "nightly":
        artifact_verify = cosign_args.index("verify-blob", 1)
    else:
        exact_verify = cosign_args.index("verify-blob", 1)
        assert cosign_args[exact_verify + 3 : exact_verify + 5] == [
            "--certificate-identity-regexp",
            expected_identity,
        ]
        artifact_verify = cosign_args.index("verify-blob", exact_verify + 1)
    assert cosign_args[artifact_verify + 1] == "--bundle"
    assert cosign_args[artifact_verify + 2].endswith("/artifact.tar.gz.bundle")
    assert cosign_args[artifact_verify + 3 : artifact_verify + 5] == [
        "--certificate-identity-regexp",
        expected_identity,
    ]
    assert cosign_args[artifact_verify + 5 : artifact_verify + 7] == [
        "--certificate-oidc-issuer",
        _CANONICAL_ISSUER,
    ]
    assert cosign_args[artifact_verify + 7].endswith("/artifact.tar.gz")
    assert install_log.read_text(encoding="utf-8").splitlines() == [
        "verified=1",
        "arg=--no-tls",
        "arg=--models-dir=/fixture",
    ]
