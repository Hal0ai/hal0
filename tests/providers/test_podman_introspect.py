"""Unit tests for :mod:`hal0.providers.podman_introspect` — the O12 seam
routing read-only podman introspection through the ROOTFUL context slots
actually use.

Covers:
  * The seam is only ATTEMPTED when this process is the ``hal0`` service
    account (mirrors :class:`hal0.system.seam.SystemCtlSeam`'s gate) — never
    touches ``sudo`` on a dev/CI/test box.
  * The exact ``sudo -n hal0-podman-ro images`` argv when it IS attempted.
  * denied/failed seam → honest fallback to a direct rootless ``podman``
    call, with the result's ``context`` marking which store it came from.
  * ``None`` only when neither context produces a usable read.

``run`` / ``which`` / ``is_hal0_user`` are injected seams so this never
touches sudo, a real ``hal0`` user, or a privileged filesystem.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hal0.providers.podman_introspect import (
    SEAM_BIN,
    ImageProbe,
    PodmanImagesResult,
    container_argv,
    container_image,
    image_presence,
    images,
    is_valid_image_ref,
    is_valid_slot_token,
)


def _completed(returncode: int = 0, stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = ""
    return m


def _recorder(*, seam_returncode: int = 0, seam_stdout: str = "repo/a\nrepo/b\n<none>\n"):
    calls: list[list[str]] = []

    def _run(argv: object, **kwargs: object) -> MagicMock:
        calls.append(list(argv))  # type: ignore[arg-type]
        return _completed(seam_returncode, seam_stdout)

    return calls, _run


# ── gate: never touches sudo when not the hal0 service user ─────────────────


def test_images_skips_seam_when_not_hal0_user() -> None:
    calls, run = _recorder()
    result = images(run=run, which=lambda _n: "/usr/bin/podman", is_hal0_user=lambda: False)

    assert calls == [["/usr/bin/podman", "images", "--format", "{{.Repository}}"]]
    assert result == PodmanImagesResult(repos={"repo/a", "repo/b"}, context="rootless")


def test_images_returns_none_when_not_hal0_user_and_podman_missing() -> None:
    calls, run = _recorder()
    result = images(run=run, which=lambda _n: None, is_hal0_user=lambda: False)

    assert calls == []  # never even tries a subprocess
    assert result is None


# ── seam attempt argv + rootful success ──────────────────────────────────────


def test_images_routes_through_seam_when_hal0_user() -> None:
    calls, run = _recorder(seam_stdout="ghcr.io/hal0ai/tb\n")
    result = images(run=run, which=lambda _n: "/usr/bin/podman", is_hal0_user=lambda: True)

    assert calls == [["sudo", "-n", SEAM_BIN, "images"]]
    assert result == PodmanImagesResult(repos={"ghcr.io/hal0ai/tb"}, context="rootful")


# ── denied seam → honest rootless fallback ───────────────────────────────────


def test_images_falls_back_to_rootless_when_seam_denied() -> None:
    """sudo -n exits non-zero (grant missing / password required) — the seam
    attempt fails cleanly and this falls back to a direct rootless call,
    marking the result's context so callers/UI can tell."""
    calls: list[list[str]] = []

    def _run(argv: object, **kwargs: object) -> MagicMock:
        argv_list = list(argv)  # type: ignore[arg-type]
        calls.append(argv_list)
        if argv_list[:2] == ["sudo", "-n"]:
            return _completed(returncode=1, stdout="")  # sudo: a password is required
        return _completed(returncode=0, stdout="repo/rootless\n")

    result = images(run=_run, which=lambda _n: "/usr/bin/podman", is_hal0_user=lambda: True)

    assert calls == [
        ["sudo", "-n", SEAM_BIN, "images"],
        ["/usr/bin/podman", "images", "--format", "{{.Repository}}"],
    ]
    assert result == PodmanImagesResult(repos={"repo/rootless"}, context="rootless")


def test_images_falls_back_when_seam_binary_missing_raises_oserror() -> None:
    """The seam binary isn't installed at all (pre-O12-install box) — OSError
    from exec, not a sudo denial, must ALSO fall back honestly."""
    calls: list[list[str]] = []

    def _run(argv: object, **kwargs: object) -> MagicMock:
        argv_list = list(argv)  # type: ignore[arg-type]
        calls.append(argv_list)
        if argv_list[:2] == ["sudo", "-n"]:
            raise FileNotFoundError(SEAM_BIN)
        return _completed(returncode=0, stdout="repo/rootless\n")

    result = images(run=_run, which=lambda _n: "/usr/bin/podman", is_hal0_user=lambda: True)

    assert result == PodmanImagesResult(repos={"repo/rootless"}, context="rootless")


# ── neither context reachable → None (pre-O12 graceful-degrade contract) ────


def test_images_returns_none_when_seam_denied_and_podman_absent() -> None:
    def _run(argv: object, **kwargs: object) -> MagicMock:
        return _completed(returncode=1, stdout="")

    result = images(run=_run, which=lambda _n: None, is_hal0_user=lambda: True)

    assert result is None


def test_images_returns_none_on_rootless_subprocess_error() -> None:
    def _run(argv: object, **kwargs: object) -> MagicMock:
        raise OSError("boom")

    result = images(run=_run, which=lambda _n: "/usr/bin/podman", is_hal0_user=lambda: False)

    assert result is None


def test_images_returns_none_on_rootless_nonzero_exit() -> None:
    def _run(argv: object, **kwargs: object) -> MagicMock:
        return _completed(returncode=125, stdout="")

    result = images(run=_run, which=lambda _n: "/usr/bin/podman", is_hal0_user=lambda: False)

    assert result is None


# ── #1889: argument-taking read verbs ───────────────────────────────────────
#
# The bug: image_present/running_image never used the seam at all, so on every
# standard install they read hal0-api's own ROOTLESS store — a store that by
# construction never contains a slot image. image_status was "missing" for
# every running, healthy slot and actual_image was always null, leaving the
# #663 drift detector permanently inert.


def _seam_recorder(*, returncode: int = 0, stdout: str = ""):
    calls: list[list[str]] = []

    def _run(argv: object, **kwargs: object) -> MagicMock:
        calls.append(list(argv))  # type: ignore[arg-type]
        return _completed(returncode, stdout)

    return calls, _run


# ── the gate: still never touches sudo off the hal0 service account ─────────


def test_image_presence_skips_seam_when_not_hal0_user() -> None:
    calls, run = _seam_recorder(stdout="present\n")

    probe = image_presence("alpine:3.19", run=run, is_hal0_user=lambda: False)

    assert probe == ImageProbe("unknown", "not-service-user")
    # Crucially NO rootless fallback here either: a "missing" from hal0's own
    # store about a NAMED image is not a stale answer, it is an answer about a
    # different object. container.py owns that fallback decision.
    assert calls == []


def test_container_image_skips_seam_when_not_hal0_user() -> None:
    calls, run = _seam_recorder(stdout="ghcr.io/x/y:1\n")

    assert container_image("brain", run=run, is_hal0_user=lambda: False) is None
    assert calls == []


# ── exact argv (the operand is positional, never a flag) ────────────────────


def test_image_presence_seam_argv() -> None:
    calls, run = _seam_recorder(stdout="present\n")

    probe = image_presence("ghcr.io/hal0/runner:rc6", run=run, is_hal0_user=lambda: True)

    assert probe == ImageProbe("present")
    assert calls == [["sudo", "-n", SEAM_BIN, "image-exists", "ghcr.io/hal0/runner:rc6"]]


def test_container_image_seam_argv_passes_the_bare_token() -> None:
    """The caller never supplies a container NAME — the wrapper builds
    hal0-slot-<token> on the root side, so this seam can only ever address a
    hal0 slot container."""
    calls, run = _seam_recorder(stdout="ghcr.io/hal0/runner:rc6\n")

    assert container_image("42", run=run, is_hal0_user=lambda: True) == "ghcr.io/hal0/runner:rc6"
    assert calls == [["sudo", "-n", SEAM_BIN, "container-image", "42"]]
    assert "hal0-slot-42" not in str(calls)


def test_container_argv_seam_argv() -> None:
    calls, run = _seam_recorder(stdout='["llama-server","--port","8080"]\n')

    out = container_argv("brain", run=run, is_hal0_user=lambda: True)

    assert out == '["llama-server","--port","8080"]'
    assert calls == [["sudo", "-n", SEAM_BIN, "container-argv", "brain"]]


# ── the actual #1889 fix: "present" is reachable ────────────────────────────


def test_image_presence_present() -> None:
    _calls, run = _seam_recorder(stdout="present\n")
    assert image_presence("alpine:3.19", run=run, is_hal0_user=lambda: True) == ImageProbe(
        "present"
    )


def test_image_presence_missing_is_authoritative_and_carries_no_reason() -> None:
    """rc 0 + "missing" is the ONLY thing that may read as ``missing``: podman
    was asked and said no. Every other outcome is ``unknown`` (#1939), which is
    what keeps ``missing`` + a running container a meaningful defect signal
    (regression ``image-status-wrong-podman-store``)."""
    _calls, run = _seam_recorder(stdout="missing\n")
    probe = image_presence("alpine:3.19", run=run, is_hal0_user=lambda: True)

    assert probe == ImageProbe("missing")
    assert probe.reason is None


# ── #1939: the wrapper's exit-code contract reaches the caller ──────────────
#
# hal0-podman-ro distinguishes "podman answered no" (rc 0 + "missing") from
# four flavours of "the seam is unusable" (rc 64/65/66, plus sudo's own rc 1
# when the grant is absent). Before #1939 every one of those collapsed into a
# bare ``None`` and then into an authoritative-looking ``image_status:
# "missing"``. Each now maps to a NAMED unknown so the journal line an
# operator reads names the actual failure.


@pytest.mark.parametrize(
    ("returncode", "reason"),
    [
        # sudo -n itself refused: the sudoers grant is not installed, or we are
        # mid-upgrade between the wrapper and its policy file.
        (1, "grant-denied"),
        # the wrapper rejected the operand or the verb (root-side validation)
        (64, "invalid-argument"),
        # podman is not installed / not executable on the box
        (65, "podman-absent"),
        # podman RAN and broke — store corruption, lock contention, EPERM.
        # THE case from #1889's review: emphatically not "the image is absent".
        (66, "podman-failed"),
        # an rc the contract does not define at all
        (125, "seam-error"),
    ],
)
def test_image_presence_maps_every_wrapper_rc_to_a_named_unknown(
    returncode: int, reason: str
) -> None:
    _calls, run = _seam_recorder(returncode=returncode, stdout="")

    assert image_presence("alpine:3.19", run=run, is_hal0_user=lambda: True) == ImageProbe(
        "unknown", reason
    )


def test_image_presence_unknown_on_unexpected_stdout() -> None:
    """rc 0 with a word the contract never defines is wrapper drift, not an
    answer — the seam and its Python mirror have gone out of lock-step."""
    _calls, run = _seam_recorder(stdout="yes\n")
    assert image_presence("alpine:3.19", run=run, is_hal0_user=lambda: True) == ImageProbe(
        "unknown", "seam-error"
    )


def test_image_presence_unknown_on_subprocess_error() -> None:
    def _run(argv: object, **kwargs: object) -> MagicMock:
        raise OSError("boom")

    assert image_presence("alpine:3.19", run=_run, is_hal0_user=lambda: True) == ImageProbe(
        "unknown", "seam-error"
    )


def test_container_image_none_when_no_such_container() -> None:
    """rc 0 with empty stdout is a real negative answer, not a failure."""
    _calls, run = _seam_recorder(stdout="\n")
    assert container_image("brain", run=run, is_hal0_user=lambda: True) is None


def test_container_argv_none_when_no_such_container() -> None:
    _calls, run = _seam_recorder(stdout="")
    assert container_argv("brain", run=run, is_hal0_user=lambda: True) is None


# ── unprivileged-side validation: a bad operand never costs a sudo hop ──────


@pytest.mark.parametrize(
    "ref",
    ["alpine; rm -rf /", "--rm", "-v/:/host", "../../etc/passwd", "", "a" * 513, "foo bar"],
)
def test_image_presence_rejects_bad_ref_without_calling_sudo(ref: str) -> None:
    calls, run = _seam_recorder(stdout="present\n")

    # A ref this side rejects was never ASKED about, so it is unknown — never
    # "missing". Same reason as rc 64, reported with the same name.
    assert image_presence(ref, run=run, is_hal0_user=lambda: True) == ImageProbe(
        "unknown", "invalid-argument"
    )
    assert calls == []


@pytest.mark.parametrize(
    "token", ["../root", "foo bar", "foo;id", "foo/bar", "", "x" * 65, "foo.bar"]
)
def test_container_verbs_reject_bad_token_without_calling_sudo(token: str) -> None:
    calls, run = _seam_recorder(stdout="ghcr.io/x/y:1\n")

    assert container_image(token, run=run, is_hal0_user=lambda: True) is None
    assert container_argv(token, run=run, is_hal0_user=lambda: True) is None
    assert calls == []


# ── the mirrors themselves ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ref",
    [
        "alpine",
        "alpine:latest",
        "docker.io/library/alpine:3.19",
        "ghcr.io/thinmintdev/hal0-rocmfpx:latest",
        "localhost:5000/team/img:v1.2.3",
        "ghcr.io/x/y@sha256:" + "a" * 64,
    ],
)
def test_is_valid_image_ref_accepts_real_refs(ref: str) -> None:
    assert is_valid_image_ref(ref) is True


@pytest.mark.parametrize("token", ["brain", "1", "my_slot", "my-slot", "x" * 64])
def test_is_valid_slot_token_accepts_real_tokens(token: str) -> None:
    assert is_valid_slot_token(token) is True


def test_container_image_none_on_operational_podman_failure() -> None:
    _calls, run = _seam_recorder(returncode=66, stdout="")
    assert container_image("brain", run=run, is_hal0_user=lambda: True) is None


@pytest.mark.parametrize(
    "ref",
    [
        # distribution-reference separators: "." | "_" | "__" | "-"+
        "registry.example/team/model__gpu:v1",
        "team/model--gpu:v1",
        "my--registry.example.com/a__b/c.d-e_f:tag",
    ],
)
def test_is_valid_image_ref_accepts_double_separators(ref: str) -> None:
    """A ref this wrongly rejects never reaches the rootful store, so the
    caller degrades to the rootless one and #1889 returns for that image."""
    assert is_valid_image_ref(ref) is True
