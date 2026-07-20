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

from hal0.providers.podman_introspect import SEAM_BIN, PodmanImagesResult, images


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
