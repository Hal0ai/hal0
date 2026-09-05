"""scripts/update-toolbox-digests.sh must never null a valid digest pin (#1676).

The script only resolved digests via the ghcr.io registry v2 curl path. The
comfyui entry used to pin a docker.io, digest-referenced tag
(``docker.io/kyuz0/amd-strix-halo-comfyui@sha256:...``): the ghcr-only path
mis-parsed it, reported it "unpublished", and overwrote a valid digest with
null — which then failed ``release.yml``'s null-digest gate on the very
manifest this script prepares. Comfyui has since flipped to hal0's own
``ghcr.io/hal0ai/hal0-comfyui`` image, so no live manifest entry exercises
the non-ghcr paths any more — the #1676 coverage lives entirely in the
synthetic docker.io fixtures below.

These tests drive the REAL script against throwaway fixture manifests,
under a hermetic PATH where ``curl`` and ``docker`` are stubbed (offline /
token-less by default, or scripted registry answers) so the tests are
deterministic regardless of the sandbox's real network access. A
digest-pinned ref must resolve without ever touching either stub; ANY
tag-only ref that cannot be resolved — ghcr or not — must keep its
previously-recorded digest instead of going null (#2218 extended the #1676
rule to the ghcr path: ghcr.io/hal0ai/hal0-strix-vulkan serves no anonymous
pull token, and the old ghcr path read that DENIED as an unpublish). Only a
DEFINITIVE unpublish — a pull token granted and then a 404 for the
manifest — nulls a digest, as does a failure with no recorded digest to
keep.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "update-toolbox-digests.sh"

# Synthetic non-ghcr, digest-pinned ref shaped like the comfyui entry that
# originally triggered #1676 (docker.io/<owner>/<repo>@sha256:...).
_PINNED_DIGEST = "sha256:" + "0066678a" * 8
_PINNED_TAG = f"docker.io/example/imggen@{_PINNED_DIGEST}"


def _write_always_failing(path: Path) -> None:
    path.write_text("#!/usr/bin/env bash\nexit 22\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def offline_path(tmp_path: Path) -> str:
    """PATH with curl/docker stubbed to always fail — hermetic, no real network."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_always_failing(bin_dir / "curl")
    _write_always_failing(bin_dir / "docker")
    return f"{bin_dir}:{os.environ['PATH']}"


def _run(manifest: Path, path: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = path
    return subprocess.run(
        ["bash", str(_SCRIPT), str(manifest)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _load(manifest: Path) -> dict:
    return json.loads(manifest.read_text(encoding="utf-8"))


# ── isolated behaviour, minimal fixture manifests ──────────────────────────


def _fixture_manifest(tmp_path: Path, toolbox_images: dict) -> Path:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"version": "0.0.0", "toolbox_images": toolbox_images}, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def test_digest_pinned_non_ghcr_ref_resolves_without_touching_curl_or_docker(
    tmp_path: Path, offline_path: str
) -> None:
    manifest = _fixture_manifest(
        tmp_path, {"imggen": {"tag": _PINNED_TAG, "digest": "sha256:" + "0" * 64}}
    )

    proc = _run(manifest, offline_path)

    assert proc.returncode == 0, proc.stderr
    assert _load(manifest)["toolbox_images"]["imggen"]["digest"] == _PINNED_DIGEST
    assert "unpublished" not in proc.stderr
    assert "1 digest(s) updated, 0 left null" in proc.stdout


def test_unresolvable_non_ghcr_tag_keeps_its_existing_digest(
    tmp_path: Path, offline_path: str
) -> None:
    # No digest pin in the ref itself, registry isn't ghcr.io, and both
    # resolution paths are stubbed offline — must NOT regress to null.
    existing = "sha256:" + "a" * 64
    manifest = _fixture_manifest(
        tmp_path, {"widget": {"tag": "docker.io/example/widget:v1", "digest": existing}}
    )

    proc = _run(manifest, offline_path)

    assert proc.returncode == 0, proc.stderr
    assert _load(manifest)["toolbox_images"]["widget"]["digest"] == existing
    assert "keeping existing digest" in proc.stderr


def test_unverified_ghcr_failure_keeps_its_existing_digest(
    tmp_path: Path, offline_path: str
) -> None:
    # The #2218 fix: an auth-shaped/transport failure on the ghcr path (here
    # the token endpoint itself is unreachable — same bucket as "no token
    # issued" / DENIED / 401 / 403) proves nothing about the published
    # state, so the recorded pin must survive, exactly like the non-ghcr
    # branch. Nulling here regressed the valid strix pin during the v1.2.0
    # cut, which release.yml's null-digest gate then rejects.
    existing = "sha256:" + "b" * 64
    manifest = _fixture_manifest(
        tmp_path, {"vulkan": {"tag": "ghcr.io/hal0ai/hal0-toolbox-vulkan:v1", "digest": existing}}
    )

    proc = _run(manifest, offline_path)

    assert proc.returncode == 0, proc.stderr
    assert _load(manifest)["toolbox_images"]["vulkan"]["digest"] == existing
    assert "could not be re-verified" in proc.stderr
    assert "keeping existing digest" in proc.stderr


def test_unverified_ghcr_failure_with_no_recorded_digest_stays_null(
    tmp_path: Path, offline_path: str
) -> None:
    # Nothing to keep: an unverifiable ghcr ref whose entry has no recorded
    # digest stays null (the runtime's pull-by-tag contract).
    manifest = _fixture_manifest(
        tmp_path, {"vulkan": {"tag": "ghcr.io/hal0ai/hal0-toolbox-vulkan:v1", "digest": None}}
    )

    proc = _run(manifest, offline_path)

    assert proc.returncode == 0, proc.stderr
    assert _load(manifest)["toolbox_images"]["vulkan"]["digest"] is None
    assert "no digest is recorded" in proc.stderr


def _write_ghcr_stub(path: Path, manifest_status: str) -> None:
    """A curl stub that grants a pull token, then answers ``manifest_status``
    (e.g. "404 Not Found") for the manifest GET — the two-request shape of
    the script's anonymous ghcr flow."""
    path.write_text(
        "#!/usr/bin/env bash\n"
        'for arg in "$@"; do\n'
        '  case "${arg}" in\n'
        '    *"/token?"*) printf \'{"token":"stub-token"}\\n\'; exit 0 ;;\n'
        '    *"/v2/"*"/manifests/"*)\n'
        f"      printf 'HTTP/2 {manifest_status}\\r\\n'\n"
        "      printf 'content-type: application/json\\r\\n'\n"
        "      printf '\\r\\n'\n"
        "      exit 0 ;;\n"
        "  esac\n"
        "done\n"
        "exit 22\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _ghcr_stub_path(tmp_path: Path, manifest_status: str) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_ghcr_stub(bin_dir / "curl", manifest_status)
    _write_always_failing(bin_dir / "docker")
    return f"{bin_dir}:{os.environ['PATH']}"


def test_definitive_ghcr_404_nulls_even_a_recorded_digest(tmp_path: Path) -> None:
    # The one case that legitimately nulls a pin: the registry granted a
    # pull token and then answered 404 for the manifest — the image really
    # is unpublished, and a stale pin must not survive that.
    existing = "sha256:" + "d" * 64
    manifest = _fixture_manifest(
        tmp_path, {"vulkan": {"tag": "ghcr.io/hal0ai/hal0-toolbox-vulkan:v1", "digest": existing}}
    )

    proc = _run(manifest, _ghcr_stub_path(tmp_path, "404 Not Found"))

    assert proc.returncode == 0, proc.stderr
    assert _load(manifest)["toolbox_images"]["vulkan"]["digest"] is None
    assert "unpublished (registry answered 404" in proc.stderr


def test_ghcr_403_after_token_grant_keeps_its_existing_digest(tmp_path: Path) -> None:
    # A non-404 registry refusal AFTER a token was granted is still only an
    # auth-shaped failure — keep the pin.
    existing = "sha256:" + "e" * 64
    manifest = _fixture_manifest(
        tmp_path, {"vulkan": {"tag": "ghcr.io/hal0ai/hal0-toolbox-vulkan:v1", "digest": existing}}
    )

    proc = _run(manifest, _ghcr_stub_path(tmp_path, "403 Forbidden"))

    assert proc.returncode == 0, proc.stderr
    assert _load(manifest)["toolbox_images"]["vulkan"]["digest"] == existing
    assert "keeping existing digest" in proc.stderr


def test_missing_tag_still_nulls(tmp_path: Path, offline_path: str) -> None:
    manifest = _fixture_manifest(tmp_path, {"empty": {"tag": "", "digest": "sha256:" + "c" * 64}})

    proc = _run(manifest, offline_path)

    assert proc.returncode == 0, proc.stderr
    assert _load(manifest)["toolbox_images"]["empty"]["digest"] is None


def test_bash_syntax_check() -> None:
    proc = subprocess.run(["bash", "-n", str(_SCRIPT)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
