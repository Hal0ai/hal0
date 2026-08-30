"""runner-images v3 PHASE 4, Task 1 — the FIRST privileged WRITE surface for
podman: ``installer/wrappers/hal0-podman-rw``.

``hal0-podman-ro`` (packaging/sudoers/hal0-podman-ro) is read-only by design:
slots run ROOTFUL podman (Quadlet units under /etc/containers/systemd/,
root's image store), so hal0-api — running as the unprivileged ``hal0``
service user — needs a root-side seam to even SEE that store, and #1889
deliberately keeps rm/run/build/exec/pull off that seam.

D1(a)/D2 of the runner-images v3 spec need hal0-api to be able to PULL and
REMOVE images in that same rootful store (the one slots actually launch
from) — a bare rootless ``podman pull``/``podman rmi`` from hal0-api would
write to the wrong store entirely, the mirror-image of the #1889 bug this
sibling wrapper exists to avoid re-creating. Hence a new, narrower grant:
``hal0-podman-rw`` exposes exactly two verbs (``image-pull``, ``image-rm``),
each taking the same validated image ref as ``-ro``'s argument-taking verbs,
and reuses that wrapper's exact validation boundary (root-side regex before
podman ever sees the argv, LC_ALL=C pin, single positional operand only).

These tests exercise the REAL bash wrapper, no root/sudo/podman required for
the validation-boundary assertions (the ``-ro`` suite's posture) — only
``check-image-ref`` and the various rc-64/65 short-circuits are reachable
without podman installed. They also assert that ``-ro`` was not widened: its
help output must still list no write verb.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hal0.providers.podman_introspect import is_valid_image_ref

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "installer" / "wrappers" / "hal0-podman-rw"
RO_WRAPPER = REPO / "installer" / "wrappers" / "hal0-podman-ro"

# A minimal PATH: the wrapper must not depend on the caller's environment.
_ENV = {"PATH": "/usr/bin:/bin"}

_DIGEST = "a" * 64

#: Refs that MUST be accepted — same corpus as the -ro suite, since both
#: wrappers share IMAGE_REF_RE verbatim.
LEGITIMATE_REFS = [
    "alpine",
    "alpine:latest",
    "alpine:3.19",
    "docker.io/library/alpine:3.19",
    "ghcr.io/thinmintdev/hal0-rocmfpx:latest",
    "quay.io/hal0/runner:v1.0.0-rc.6",
    "localhost/hal0-toolbox:dev",
    "localhost:5000/team/img:v1.2.3",
    "registry.example.com:443/a/b/c/deep:tag",
    f"ghcr.io/x/y@sha256:{_DIGEST}",
    f"ghcr.io/x/y:tag@sha256:{_DIGEST}",
    "rocm/vllm-dev:nightly_main_20260101",
    "registry.example/team/model__gpu:v1",
    "team/model--gpu:v1",
    "my--registry.example.com/a__b/c.d-e_f:tag",
    "[2001:db8::1]:5000/team/model:v1",
    "[::1]/local/img",
]

#: Argv that must never reach podman. Same shapes as the -ro suite.
MALICIOUS_REFS = [
    "alpine; rm -rf /",
    "alpine && id",
    "alpine || id",
    "alpine | id",
    "alpine $(id)",
    "alpine `id`",
    "alpine\nid",
    "alpine\tid",
    "alpine id",
    "$(id)",
    ";id",
    "&id",
    ">/etc/passwd",
    "<(id)",
    "alpine:tag;whoami",
    # flag smuggling — a leading '-' must never be readable as an option
    "-v/:/host",
    "--format={{.Config}}",
    "--rm",
    "-f",
    "-",
    # path traversal shapes
    "../../../etc/shadow",
    "foo/../../etc/passwd",
    "foo..bar",
    "..",
    "./alpine",
    "/etc/passwd",
    # malformed digests
    "alpine@sha256:zzzz",
    # IPv6 brackets must not become a hole: only hex+colons inside
    "[2001:db8::1;id]:5000/foo",
    "[../etc]/foo",
    "[]/foo",
    "[2001:db8::1]extra/foo",
    "alpine@sha512:" + _DIGEST,
    "alpine@sha256:" + "a" * 63,
    "alpine@sha256:" + "A" * 64,
    # separators the OCI grammar does not allow
    "-alpine",
    "alpine-",
    "foo//bar",
    "foo/",
    "/foo",
    ":latest",
    "alpine:",
    "",
    # unicode / control bytes
    "alpine\rid",
    "alpine\x1b[31m",
    "alpine\x7f",
    "alpiné",
    "alpine‮id",
]


def _run(*argv: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WRAPPER), *argv],
        capture_output=True,
        text=True,
        check=False,
        env=env or _ENV,
    )


def _run_ro(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RO_WRAPPER), *argv],
        capture_output=True,
        text=True,
        check=False,
        env=_ENV,
    )


# ── the wrapper is syntactically sound and self-describing ─────────────────


def test_wrapper_parses() -> None:
    proc = subprocess.run(["bash", "-n", str(WRAPPER)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_help_lists_both_write_verbs() -> None:
    proc = _run("help")
    assert proc.returncode == 0, proc.stderr
    assert "image-pull" in proc.stdout
    assert "image-rm" in proc.stdout


def test_help_documents_the_67_exit_code() -> None:
    proc = _run("help")
    assert proc.returncode == 0, proc.stderr
    assert "67" in proc.stdout


def test_unknown_verb_is_rejected() -> None:
    proc = _run("rm")
    assert proc.returncode == 64
    assert "bad cmd" in proc.stderr


# ── arg-count / validation rejects (rc 64), reachable without podman ────────


@pytest.mark.parametrize("verb", ["image-pull", "image-rm"])
def test_argument_verbs_require_exactly_one_argument(verb: str) -> None:
    assert _run(verb).returncode == 64
    assert _run(verb, "alpine", "extra").returncode == 64


@pytest.mark.parametrize("verb", ["image-pull", "image-rm", "check-image-ref"])
@pytest.mark.parametrize("ref", MALICIOUS_REFS)
def test_wrapper_rejects_malicious_image_ref(verb: str, ref: str) -> None:
    proc = _run(verb, ref)
    assert proc.returncode == 64, f"{verb} ACCEPTED {ref!r} (rc={proc.returncode})"
    assert "hal0-podman-rw:" in proc.stderr


def test_wrapper_rejects_overlong_image_ref() -> None:
    proc = _run("check-image-ref", "a" * 513)
    assert proc.returncode == 64
    assert "too long" in proc.stderr
    assert _run("check-image-ref", "a" * 512).returncode == 0


# ── check-image-ref parity spot-check against -ro ───────────────────────────


@pytest.mark.parametrize("ref", LEGITIMATE_REFS)
def test_check_image_ref_accepts_same_good_refs_as_ro(ref: str) -> None:
    rw_proc = _run("check-image-ref", ref)
    ro_proc = _run_ro("check-image-ref", ref)
    assert rw_proc.returncode == 0, f"rejected {ref!r}: {rw_proc.stderr}"
    assert rw_proc.returncode == ro_proc.returncode
    assert rw_proc.stdout == ro_proc.stdout == f"{ref}\n"


@pytest.mark.parametrize("ref", [*LEGITIMATE_REFS, *MALICIOUS_REFS, "a" * 513])
def test_python_image_ref_mirror_agrees_with_the_wrapper(ref: str) -> None:
    assert is_valid_image_ref(ref) == (_run("check-image-ref", ref).returncode == 0), (
        f"mirror disagrees with the wrapper on {ref!r}"
    )


# ── locale independence, same pin as -ro ────────────────────────────────────


def test_wrapper_pins_the_locale_itself() -> None:
    src = WRAPPER.read_text()
    assert "export LC_ALL=C" in src
    lines = [ln.strip() for ln in src.splitlines()]
    assert lines.index("export LC_ALL=C") < lines.index(
        "validate_image_ref() {   # arg: image reference"
    )


# ── exit-code contract: image-rm's rc 0/1/2/other mapping ──────────────────


def test_image_rm_source_maps_rmi_exit_codes() -> None:
    """Structural pin (no podman/root available here): rc 0 -> "removed"/0,
    rc 1 (no such image) -> "missing"/0 (a real negative answer), rc 2 (image
    in use by a container) -> the NEW 67, anything else -> podman_failed 66.
    Never `rmi -f`."""
    src = WRAPPER.read_text()
    rmi_line = next(ln.strip() for ln in src.splitlines() if "rmi" in ln and "run_podman" in ln)
    assert rmi_line == 'run_podman rmi -- "$1"'
    assert "-f" not in rmi_line
    assert "removed" in src
    assert "missing" in src
    assert "exit 67" in src


def test_image_pull_execs_podman_directly() -> None:
    """`exec` is deliberate: progress lines stream raw to the caller and
    podman's own exit code passes straight through — no intermediate
    run_podman capture for this verb."""
    src = WRAPPER.read_text()
    assert 'exec "$PODMAN" pull -- "$1"' in src


def test_no_caller_supplied_format_string() -> None:
    src = WRAPPER.read_text()
    for line in src.splitlines():
        if "--format" in line and not line.lstrip().startswith("#"):
            assert '"$1"' not in line and '"$2"' not in line, line


# ── the ro wrapper stays read-only: no widening ─────────────────────────────


def test_ro_wrapper_help_has_no_write_verbs() -> None:
    proc = _run_ro("help")
    assert proc.returncode == 0, proc.stderr
    assert "image-pull" not in proc.stdout
    assert "image-rm" not in proc.stdout


def test_ro_wrapper_still_parses() -> None:
    proc = subprocess.run(
        ["bash", "-n", str(RO_WRAPPER)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
