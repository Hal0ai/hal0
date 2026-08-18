"""#1889 — root-side argument allow-list for the hal0-podman-ro read verbs.

``installer/wrappers/hal0-podman-ro`` is reachable as root by the unprivileged
``hal0`` service account (packaging/sudoers/hal0-podman-ro), so its argv is
attacker-controlled at a privilege boundary. Before #1889 the wrapper dodged
that by accepting zero caller arguments — at the cost of leaving
``image_present`` / ``running_image`` on a bare rootless ``podman`` call
against the wrong store, which is why ``image_status`` was ``"missing"`` for
every running slot and ``actual_image`` was always ``null``.

Three verbs now take exactly one positional operand. These tests pin the
validators that stand between that operand and podman's argv, and they do it
through the *real* bash wrapper's side-effect-free ``check-image-ref`` /
``check-slot-token`` verbs — no root, no sudo, no podman, no provisioned box
(the ``hal0-systemctl`` drop-in suite's posture).

They also assert PARITY with the Python mirrors in
:mod:`hal0.providers.podman_introspect`: those exist so the unprivileged side
fails fast, and a mirror that drifts looser than the wrapper turns a fast
rejection into an opaque ``rc 64``, while one that drifts stricter silently
re-creates #1889 for the refs it wrongly rejects.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hal0.providers.podman_introspect import is_valid_image_ref, is_valid_slot_token

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "installer" / "wrappers" / "hal0-podman-ro"

# A minimal PATH: the wrapper must not depend on the caller's environment.
_ENV = {"PATH": "/usr/bin:/bin"}

_DIGEST = "a" * 64

#: Refs that MUST be accepted — a false rejection degrades straight back to
#: image_status="missing", the bug this issue is about.
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
    # distribution-reference separators: "." | "_" | "__" | "-"+
    "registry.example/team/model__gpu:v1",
    "team/model--gpu:v1",
    "my--registry.example.com/a__b/c.d-e_f:tag",
    # bracketed IPv6 registry literals are legal reference hosts
    "[2001:db8::1]:5000/team/model:v1",
    "[::1]/local/img",
]

#: Argv that must never reach podman. Each is a shape that would either run a
#: second command, smuggle a podman flag, escape the intended object, or
#: exhaust the box.
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

#: Slot tokens that must be accepted (see src/hal0/slots/naming.py — the token
#: is either the slot's opaque numeric id or its name).
LEGITIMATE_TOKENS = ["brain", "1", "42", "my_slot", "my-slot", "A_b-9", "x" * 64]

MALICIOUS_TOKENS = [
    "",
    "../root",
    "foo bar",
    "foo;id",
    "foo$(id)",
    "foo/bar",
    "foo.bar",
    "foo@bar",
    "foo:bar",
    "foo\nbar",
    "x" * 65,
    "brain'",
    'brain"',
    "brain\\",
]


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WRAPPER), *argv],
        capture_output=True,
        text=True,
        check=False,
        env=_ENV,
    )


# ── the wrapper is syntactically sound and self-describing ─────────────────


def test_wrapper_parses() -> None:
    proc = subprocess.run(["bash", "-n", str(WRAPPER)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_help_lists_every_verb() -> None:
    proc = _run("help")
    assert proc.returncode == 0, proc.stderr
    for verb in (
        "images",
        "image-exists",
        "container-image",
        "container-argv",
        "check-image-ref",
        "check-slot-token",
    ):
        assert verb in proc.stdout, f"{verb} missing from usage"


def test_unknown_verb_is_rejected() -> None:
    proc = _run("rm")
    assert proc.returncode == 64
    assert "bad cmd" in proc.stderr


@pytest.mark.parametrize("verb", ["image-exists", "container-image", "container-argv"])
def test_write_verbs_are_not_reachable(verb: str) -> None:
    """The seam stays READ-ONLY: no verb spells a mutation, and the three
    argument-taking verbs refuse a second argv word outright (so a validated
    operand can never be followed by a smuggled one)."""
    proc = _run(verb, "alpine", "extra")
    assert proc.returncode == 64
    assert "exactly one argument" in proc.stderr


@pytest.mark.parametrize("verb", ["image-exists", "container-image", "container-argv"])
def test_argument_verbs_require_an_argument(verb: str) -> None:
    proc = _run(verb)
    assert proc.returncode == 64


# ── image refs ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ref", LEGITIMATE_REFS)
def test_wrapper_accepts_legitimate_image_ref(ref: str) -> None:
    proc = _run("check-image-ref", ref)
    assert proc.returncode == 0, f"rejected {ref!r}: {proc.stderr}"
    # Echoed verbatim: what podman receives is exactly what was validated.
    assert proc.stdout == f"{ref}\n"


@pytest.mark.parametrize("ref", MALICIOUS_REFS)
def test_wrapper_rejects_malicious_image_ref(ref: str) -> None:
    proc = _run("check-image-ref", ref)
    assert proc.returncode == 64, f"ACCEPTED {ref!r} (rc={proc.returncode})"
    assert "hal0-podman-ro:" in proc.stderr


def test_wrapper_rejects_overlong_image_ref() -> None:
    proc = _run("check-image-ref", "a" * 513)
    assert proc.returncode == 64
    assert "too long" in proc.stderr
    # ...and the cap is not off-by-one against a legitimate long ref.
    assert _run("check-image-ref", "a" * 512).returncode == 0


@pytest.mark.parametrize("ref", MALICIOUS_REFS)
def test_malicious_ref_is_rejected_by_the_real_verb_too(ref: str) -> None:
    """The validator runs BEFORE podman is even located, so this holds on a
    box with no podman installed (CI) as well as on a provisioned one."""
    proc = _run("image-exists", ref)
    assert proc.returncode == 64, f"ACCEPTED {ref!r} (rc={proc.returncode})"


# ── slot tokens ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("token", LEGITIMATE_TOKENS)
def test_wrapper_accepts_legitimate_slot_token(token: str) -> None:
    proc = _run("check-slot-token", token)
    assert proc.returncode == 0, f"rejected {token!r}: {proc.stderr}"
    # The container NAME is assembled on the root side from the bare token —
    # the caller never supplies a container name, so it can only ever address
    # a hal0 slot container.
    assert proc.stdout == f"hal0-slot-{token}\n"


@pytest.mark.parametrize("token", MALICIOUS_TOKENS)
def test_wrapper_rejects_malicious_slot_token(token: str) -> None:
    proc = _run("check-slot-token", token)
    assert proc.returncode == 64, f"ACCEPTED {token!r} (rc={proc.returncode})"


@pytest.mark.parametrize("token", MALICIOUS_TOKENS)
def test_malicious_token_is_rejected_by_the_real_verbs_too(token: str) -> None:
    for verb in ("container-image", "container-argv"):
        proc = _run(verb, token)
        assert proc.returncode == 64, f"{verb} ACCEPTED {token!r} (rc={proc.returncode})"


# ── wrapper ↔ Python-mirror parity ─────────────────────────────────────────


@pytest.mark.parametrize("ref", [*LEGITIMATE_REFS, *MALICIOUS_REFS, "a" * 513])
def test_python_image_ref_mirror_agrees_with_the_wrapper(ref: str) -> None:
    assert is_valid_image_ref(ref) == (_run("check-image-ref", ref).returncode == 0), (
        f"mirror disagrees with the wrapper on {ref!r}"
    )


@pytest.mark.parametrize("token", [*LEGITIMATE_TOKENS, *MALICIOUS_TOKENS])
def test_python_slot_token_mirror_agrees_with_the_wrapper(token: str) -> None:
    assert is_valid_slot_token(token) == (_run("check-slot-token", token).returncode == 0), (
        f"mirror disagrees with the wrapper on {token!r}"
    )


# ── exit-code contract: operational failure ≠ negative answer ───────────────
#
# Podman's own rc cannot be exercised here without injecting a fake podman
# path, and making PODMAN overridable would be a hole in the very boundary
# this file defends. These assert the contract structurally instead; the
# consuming half (rc 66 → "seam did not answer", never "missing") is pinned in
# tests/providers/test_podman_introspect.py.


def test_presence_probes_use_exists_not_inspect() -> None:
    """`podman inspect` collapses "not found" and every other failure into a
    single rc 125, so it cannot tell a missing image from a broken store.
    `image exists` / `container exists` are rc 0 / rc 1 / error."""
    src = WRAPPER.read_text()
    assert "run_podman image exists --" in src
    assert "run_podman container exists --" in src
    code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if "image inspect" in ln]


def test_operational_podman_failure_exits_66() -> None:
    src = WRAPPER.read_text()
    assert "exit 66" in src
    # every podman call site routes its non-{0,1} rc into podman_failed
    assert src.count("podman_failed") >= 4


def test_no_caller_supplied_format_string() -> None:
    """Every --format is a literal in this file; none is read from argv."""
    src = WRAPPER.read_text()
    for line in src.splitlines():
        if "--format" in line and not line.lstrip().startswith("#"):
            assert '"$1"' not in line and '"$2"' not in line, line
