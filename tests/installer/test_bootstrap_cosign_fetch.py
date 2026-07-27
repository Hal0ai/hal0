"""Bootstrap self-acquires a digest-pinned cosign when the host has none.

``installer/bootstrap.sh`` hard-requires cosign: it is the only thing that
turns "bytes off a CDN" into "bytes signed by the hal0 release workflow".
cosign is not packaged in apt, so on Debian/Ubuntu — the most common hal0
host — a hard requirement used to mean ``curl … | bash`` simply died.

``ensure_cosign()`` closes that without weakening anything: a system cosign
is used as-is, otherwise the official sigstore build for the detected
architecture is downloaded into the trap-guarded work directory and checked
against a sha256 pinned in the script. There is deliberately no opt-out
environment variable, and every failure mode is fail-closed.

These tests drive the real script under a hermetic PATH (the technique
``tests/installer/test_bootstrap_contract.py`` uses) so they need no network
and no root.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP = _REPO_ROOT / "installer" / "bootstrap.sh"
_SCRIPT = _BOOTSTRAP.read_text(encoding="utf-8")
# Comment prose legitimately *names* the flags we refuse to implement, so
# opt-out assertions run against executable lines only.
_CODE = "\n".join(line for line in _SCRIPT.splitlines() if not line.lstrip().startswith("#"))

_COSIGN_RELEASE_HOST = "https://github.com/sigstore/cosign/releases/download"

# uname -m spelling -> sigstore release asset the bootstrap must fetch.
_ARCH_MAP = {
    "x86_64": "cosign-linux-amd64",
    "amd64": "cosign-linux-amd64",
    "aarch64": "cosign-linux-arm64",
    "arm64": "cosign-linux-arm64",
}


def _pinned(name: str) -> str:
    match = re.search(rf"^{name}='([^']*)'$", _SCRIPT, re.MULTILINE)
    assert match is not None, f"{name} not found in bootstrap.sh"
    return match.group(1)


def _write_executable(path: Path, body: str) -> None:
    path.unlink(missing_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _cosign_env(
    tmp_path: Path,
    *,
    machine: str = "x86_64",
    system_cosign: bool = False,
    download_ok: bool = True,
    fake_sha256: str = "b" * 64,
) -> tuple[dict[str, str], Path, Path]:
    """Hermetic PATH that records every curl URL and every cosign invocation.

    ``fake_sha256`` is what the stubbed ``sha256sum`` reports for the
    downloaded blob. Stubbing the hash (rather than trying to forge a
    preimage of the real pinned digest) is what lets a single harness drive
    both the mismatch branch and the accept branch of ``ensure_cosign``.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("awk", "bash", "cat", "chmod", "jq", "mkdir", "mktemp", "python3", "rm", "tar"):
        target = shutil.which(tool)
        assert target is not None, f"test host is missing {tool}"
        os.symlink(target, bin_dir / tool)

    curl_log = tmp_path / "curl.log"
    cosign_log = tmp_path / "cosign.log"

    _write_executable(
        bin_dir / "uname",
        f"""#!/usr/bin/env bash
case "${{1:-}}" in
    -m) printf '%s\\n' {machine!r} ;;
    *)  printf 'Linux\\n' ;;
esac
""",
    )

    # A downloaded cosign must be executable from the work dir and must
    # answer `version` (the noexec smoke check) before it is trusted.
    payload = tmp_path / "cosign.payload"
    _write_executable(
        payload,
        """#!/usr/bin/env bash
printf 'BIN=%s\\n' "$0" >> "$COSIGN_LOG"
printf '%s\\n' "$@" >> "$COSIGN_LOG"
[[ "${1:-}" == "version" ]] && exit 0
exit "$COSIGN_RC"
""",
    )

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
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
printf '%s\\n' "$url" >> "$CURL_LOG"
case "$url" in
    *cosign-linux-*)
        [[ "$COSIGN_DOWNLOAD_OK" == "1" ]] || exit 22
        {cp} "$COSIGN_PAYLOAD" "$out"
        ;;
    *.json.bundle) printf 'fixture bundle\\n' > "$out" ;;
    *.json)        printf '{{"not":"trusted yet"}}\\n' > "$out" ;;
    *) exit 22 ;;
esac
""",
    )

    # `sha256sum` is stubbed so the harness controls the verdict; it still
    # has to emit the real `<digest>  <path>` shape the script parses.
    _write_executable(
        bin_dir / "sha256sum",
        f"""#!/usr/bin/env bash
printf '%s  %s\\n' {fake_sha256!r} "$1"
""",
    )

    if system_cosign:
        _write_executable(
            bin_dir / "cosign",
            """#!/usr/bin/env bash
printf 'BIN=%s\\n' "$0" >> "$COSIGN_LOG"
printf '%s\\n' "$@" >> "$COSIGN_LOG"
exit "$COSIGN_RC"
""",
        )

    env = {
        "PATH": str(bin_dir),
        "CURL_LOG": str(curl_log),
        "COSIGN_LOG": str(cosign_log),
        "COSIGN_RC": "1",
        "COSIGN_PAYLOAD": str(payload),
        "COSIGN_DOWNLOAD_OK": "1" if download_ok else "0",
        "TMPDIR": str(tmp_path),
    }
    return env, curl_log, cosign_log


def _run_bootstrap(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(_BOOTSTRAP), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


# ── pinned constants ───────────────────────────────────────────────────────


def test_pinned_cosign_version_is_a_release_tag() -> None:
    assert re.fullmatch(r"v\d+\.\d+\.\d+", _pinned("_COSIGN_VERSION"))


def test_pinned_cosign_base_url_is_the_official_sigstore_release_host() -> None:
    assert _pinned("_COSIGN_BASE_URL") == _COSIGN_RELEASE_HOST


@pytest.mark.parametrize("constant", ["_COSIGN_SHA256_LINUX_AMD64", "_COSIGN_SHA256_LINUX_ARM64"])
def test_pinned_digests_are_lowercase_sha256(constant: str) -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", _pinned(constant))


def test_pinned_digests_differ_per_arch() -> None:
    # Same digest for two architectures means a bump copy-pasted one line.
    assert _pinned("_COSIGN_SHA256_LINUX_AMD64") != _pinned("_COSIGN_SHA256_LINUX_ARM64")


def test_no_cosign_opt_out_environment_variable() -> None:
    # Keeping cosign mandatory is the whole point; a skip flag becomes the
    # copy-pasted default and silently un-does the signature hardening.
    for forbidden in (
        "HAL0_INSTALL_REQUIRE_COSIGN",
        "HAL0_INSTALL_SKIP_COSIGN",
        "HAL0_BOOTSTRAP_SKIP_COSIGN",
        "HAL0_UPDATE_SKIP_COSIGN",
    ):
        assert forbidden not in _CODE


def test_fetched_cosign_is_never_installed_persistently() -> None:
    for persistent in ("/usr/local/bin", "/usr/bin/cosign", "install -m"):
        assert persistent not in _CODE


# ── architecture detection ─────────────────────────────────────────────────


@pytest.mark.parametrize(("machine", "asset"), sorted(_ARCH_MAP.items()))
def test_arch_detection_requests_the_matching_release_asset(
    tmp_path: Path, machine: str, asset: str
) -> None:
    env, curl_log, _ = _cosign_env(tmp_path, machine=machine)

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    version = _pinned("_COSIGN_VERSION")
    assert curl_log.read_text(encoding="utf-8").splitlines()[0] == (
        f"{_COSIGN_RELEASE_HOST}/{version}/{asset}"
    )


@pytest.mark.parametrize("machine", ["i686", "riscv64", "ppc64le", "s390x", "armv7l", ""])
def test_unsupported_arch_fails_closed_before_any_network(tmp_path: Path, machine: str) -> None:
    env, curl_log, _ = _cosign_env(tmp_path, machine=machine)

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "pins no cosign" in proc.stderr
    assert f"uname -m: {machine}" in proc.stderr
    assert "install cosign manually" in proc.stderr
    assert not curl_log.exists()


# ── fail-closed paths ──────────────────────────────────────────────────────


def test_digest_mismatch_refuses_to_run_the_binary_or_fetch_the_release(
    tmp_path: Path,
) -> None:
    env, curl_log, cosign_log = _cosign_env(tmp_path, fake_sha256="b" * 64)

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "pinned cosign sha256 mismatch" in proc.stderr
    assert _pinned("_COSIGN_SHA256_LINUX_AMD64") in proc.stderr
    assert "refusing to run an unverified cosign binary" in proc.stderr
    assert "install cosign manually" in proc.stderr
    # Never executed, and the release manifest was never even requested.
    assert not cosign_log.exists()
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        f"{_COSIGN_RELEASE_HOST}/{_pinned('_COSIGN_VERSION')}/cosign-linux-amd64"
    ]
    assert not list(tmp_path.glob("hal0-install-*/cosign"))


def test_download_failure_fails_closed_with_manual_install_guidance(
    tmp_path: Path,
) -> None:
    env, curl_log, cosign_log = _cosign_env(tmp_path, download_ok=False)

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "could not download pinned cosign" in proc.stderr
    assert "install cosign manually" in proc.stderr
    assert not cosign_log.exists()
    assert "releases.hal0.dev" not in curl_log.read_text(encoding="utf-8")


# ── accept paths ───────────────────────────────────────────────────────────


def test_existing_cosign_short_circuits_the_download(tmp_path: Path) -> None:
    env, curl_log, cosign_log = _cosign_env(tmp_path, system_cosign=True)

    proc = _run_bootstrap(env)

    assert proc.returncode != 0
    assert "using system cosign" in proc.stdout
    urls = curl_log.read_text(encoding="utf-8").splitlines()
    assert not any("cosign-linux-" in url for url in urls)
    assert urls[0] == "https://releases.hal0.dev/stable.json"
    cosign_calls = cosign_log.read_text(encoding="utf-8").splitlines()
    assert cosign_calls[0] == f"BIN={tmp_path / 'bin' / 'cosign'}"
    assert cosign_calls[1] == "verify-blob"


def test_verified_download_is_used_from_the_ephemeral_work_dir(tmp_path: Path) -> None:
    env, curl_log, cosign_log = _cosign_env(
        tmp_path, fake_sha256=_pinned("_COSIGN_SHA256_LINUX_AMD64")
    )

    proc = _run_bootstrap(env)

    # Manifest verification still fails (COSIGN_RC=1) — what this proves is
    # that the fetched binary was accepted and became the verifier.
    assert proc.returncode != 0
    assert "pinned cosign" in proc.stdout
    assert "sha256 OK" in proc.stdout
    assert (
        f"{_COSIGN_RELEASE_HOST}/{_pinned('_COSIGN_VERSION')}/cosign-linux-amd64"
        in curl_log.read_text(encoding="utf-8")
    )
    calls = cosign_log.read_text(encoding="utf-8").splitlines()
    # First call is the noexec smoke check, then the real verification.
    assert calls[0].startswith(f"BIN={tmp_path}/hal0-install-")
    assert calls[0].endswith("/cosign")
    assert calls[1] == "version"
    assert "verify-blob" in calls
    # Ephemeral: the trap removed the whole work dir on exit.
    assert not list(tmp_path.glob("hal0-install-*"))


def test_bash_syntax_check() -> None:
    proc = subprocess.run(
        ["bash", "-n", str(_BOOTSTRAP)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
