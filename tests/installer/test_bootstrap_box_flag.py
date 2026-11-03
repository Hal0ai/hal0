"""``--box`` hands the cosign-verified tree to the box launcher."""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

from test_bootstrap_contract import (
    _CANONICAL_ISSUER,
    _bootstrap_env,
    _exact_identity,
    _run_bootstrap,
    _write_executable,
)

_VERSION = "1.2.3"
_STUB = """#!/usr/bin/env bash
printf '%s\\n' "${0##*/}" "$@" > "$STUB_LOG"
printf 'verified=%s\\n' "${HAL0_BOOTSTRAP_VERIFIED:-}" >> "$STUB_LOG"
"""


def _run(tmp_path: Path, *args: str, with_box: bool = True):
    url = f"https://fixtures.example/hal0-{_VERSION}.tar.gz"
    root = tmp_path / "tree" / f"hal0-{_VERSION}"
    (root / "installer").mkdir(parents=True)
    _write_executable(root / "installer" / "install.sh", _STUB)
    if with_box:
        (root / "installer" / "box").mkdir()
        _write_executable(root / "installer" / "box" / "box.sh", _STUB)
    artifact = tmp_path / "hal0.tar.gz"
    with tarfile.open(artifact, "w:gz") as archive:
        archive.add(root, arcname=f"hal0-{_VERSION}")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = json.dumps(
        {
            "_schema": "hal0.releases.v1",
            "version": _VERSION,
            "channel": "stable",
            "release_kind": "stable",
            "prerelease_stage": None,
            "url": url,
            "bundle_url": f"{url}.bundle",
            "digest_sha256": f"sha256:{digest}",
            "signer_identity": _exact_identity("stable", _VERSION),
            "signer_issuer": _CANONICAL_ISSUER,
        }
    ).encode()
    env, _, cosign_log = _bootstrap_env(
        tmp_path,
        manifest,
        cosign_rc=0,
        artifact_fixture=artifact,
        artifact_url=url,
        artifact_bundle_url=f"{url}.bundle",
    )
    log = tmp_path / "stub.log"
    env["STUB_LOG"] = str(log)
    proc = _run_bootstrap(env, *args)
    return proc, log, cosign_log


def test_box_flag_execs_launcher_after_verification(tmp_path: Path) -> None:
    proc, log, cosign_log = _run(tmp_path, "--box", "--no-pi")

    assert proc.returncode == 0, proc.stderr
    # verification ran (artifact cosign verify) before the launcher was reached
    assert cosign_log.read_text(encoding="utf-8").count("verify-blob") >= 2
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "box.sh"
    assert lines[1] == "install"
    assert lines[2].endswith("/unpacked")
    assert lines[3:-1] == ["--no-pi"]
    assert lines[-1] == "verified=1"


def test_box_flag_is_stripped_wherever_it_appears(tmp_path: Path) -> None:
    proc, log, _ = _run(tmp_path, "--no-pi", "--box", "--models-dir=/x")

    assert proc.returncode == 0, proc.stderr
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "box.sh"
    assert "--box" not in lines
    assert lines[3:-1] == ["--no-pi", "--models-dir=/x"]


def test_without_box_flag_install_sh_runs(tmp_path: Path) -> None:
    proc, log, _ = _run(tmp_path, "--no-pi")

    assert proc.returncode == 0, proc.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "install.sh",
        "--no-pi",
        "verified=1",
    ]


def test_box_flag_fails_clearly_when_release_lacks_launcher(tmp_path: Path) -> None:
    proc, log, cosign_log = _run(tmp_path, "--box", with_box=False)

    assert proc.returncode != 0
    assert "does not include the box launcher" in proc.stderr
    assert not log.exists()
    assert cosign_log.read_text(encoding="utf-8").count("verify-blob") >= 2
