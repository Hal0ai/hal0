"""#1889 — ContainerProvider's image reads go through the ROOTFUL seam.

The bug this pins: ``image_present`` / ``running_image`` / ``running_argv``
shelled out to a bare ``podman`` as the unprivileged ``hal0`` service user,
which has no subuid ranges at all, so they read hal0-api's own ROOTLESS image
store. Slots run ROOTFUL podman (Quadlet, root's store), so that store by
construction never holds a slot image. Result on every standard install:
``GET /api/slots`` reported ``image_status: "missing"`` for every running,
healthy slot, ``actual_image`` was always ``null``, and the #663 image-drift
detector could never fire.

The seam (``hal0-podman-ro``) and the ``is_hal0_service_user()`` gate already
existed — commit ``cbc8e94d`` wired them into ``/api/system-info``. These
tests assert the three sibling call sites are now on the same seam, and that
the fallback to the rootless read survives for the dev/CI case where the
process is NOT the hal0 service user (there is no grant there, and the
operator's own store IS the right one).

``podman_introspect`` is patched rather than ``subprocess`` so these never
touch sudo, a real ``hal0`` account, or podman.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.providers import container as container_mod
from hal0.providers.container import ContainerProvider

SLOT: dict[str, Any] = {"id": 7, "name": "gpu-chat"}
IMAGE = "ghcr.io/thinmintdev/hal0-runner:v1.0.0-rc.6"


@pytest.fixture
def no_rootless(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Fail loudly if anything reaches the rootless podman path.

    A test that "passes" by silently falling through to ``subprocess.run``
    would prove nothing about the seam, so the fallback is booby-trapped
    rather than merely unused.
    """
    calls: list[list[str]] = []

    def _boom(argv: list[str], **_kw: object) -> None:
        calls.append(list(argv))
        raise AssertionError(f"rootless podman was invoked: {argv}")

    monkeypatch.setattr(container_mod.subprocess, "run", _boom)
    return calls


# ── image_present: the "always missing" bug ────────────────────────────────


def test_image_present_true_from_rootful_seam(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    """The whole point of #1889: a slot image that IS in root's store now
    reports present, so slot_view renders image_status="present"."""
    seen: list[str] = []

    def _exists(image: str) -> bool:
        seen.append(image)
        return True

    monkeypatch.setattr(container_mod.podman_introspect, "image_exists", _exists)

    assert ContainerProvider().image_present(IMAGE) is True
    assert seen == [IMAGE]


def test_image_present_false_is_an_honest_rootful_negative(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    """A seam that answered "missing" is authoritative — we must NOT then
    re-ask the rootless store and let its (also wrong) answer stand in."""
    monkeypatch.setattr(container_mod.podman_introspect, "image_exists", lambda _i: False)

    assert ContainerProvider().image_present(IMAGE) is False


def test_image_present_falls_back_to_rootless_when_seam_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev checkout / CI / no grant: the seam returns None and the operator's
    own store is the right one to read."""
    monkeypatch.setattr(container_mod.podman_introspect, "image_exists", lambda _i: None)
    monkeypatch.setattr(container_mod, "_container_runtime", lambda: "/usr/bin/podman")

    calls: list[list[str]] = []

    class _Proc:
        returncode = 0

    def _run(argv: list[str], **_kw: object) -> _Proc:
        calls.append(list(argv))
        return _Proc()

    monkeypatch.setattr(container_mod.subprocess, "run", _run)

    assert ContainerProvider().image_present(IMAGE) is True
    assert calls == [["/usr/bin/podman", "image", "inspect", IMAGE]]


def test_image_present_false_when_seam_silent_and_no_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(container_mod.podman_introspect, "image_exists", lambda _i: None)

    def _no_runtime() -> str:
        raise RuntimeError("no podman runtime found")

    monkeypatch.setattr(container_mod, "_container_runtime", _no_runtime)

    assert ContainerProvider().image_present(IMAGE) is False


# ── running_image: the "actual_image is always null" bug ───────────────────


def test_running_image_from_rootful_seam(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    seen: list[str] = []

    def _container_image(token: str) -> str:
        seen.append(token)
        return IMAGE

    monkeypatch.setattr(container_mod.podman_introspect, "container_image", _container_image)

    assert ContainerProvider().running_image(SLOT) == IMAGE
    # The bare INSTANCE TOKEN crosses the boundary (#1417: id-keyed slots), not
    # a container name — the wrapper builds hal0-slot-<token> root-side.
    assert seen == ["7"]


def test_running_image_falls_back_to_rootless_when_seam_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(container_mod.podman_introspect, "container_image", lambda _t: None)
    monkeypatch.setattr(container_mod, "_container_runtime", lambda: "/usr/bin/podman")

    calls: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = f"{IMAGE}\n"

    def _run(argv: list[str], **_kw: object) -> _Proc:
        calls.append(list(argv))
        return _Proc()

    monkeypatch.setattr(container_mod.subprocess, "run", _run)

    assert ContainerProvider().running_image(SLOT) == IMAGE
    assert calls == [["/usr/bin/podman", "inspect", "hal0-slot-7", "--format", "{{.ImageName}}"]]


# ── running_argv ───────────────────────────────────────────────────────────


def test_running_argv_from_rootful_seam(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    monkeypatch.setattr(
        container_mod.podman_introspect,
        "container_argv",
        lambda _t: '["llama-server", "--port", "8080"]',
    )

    assert ContainerProvider().running_argv(SLOT) == ["llama-server", "--port", "8080"]


def test_running_argv_falls_back_when_seam_returns_unparseable_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A seam answer that is not a JSON array is treated as no answer, not as
    "the slot has no command" — status callers must never read garbage as
    drift."""
    monkeypatch.setattr(container_mod.podman_introspect, "container_argv", lambda _t: "null")
    monkeypatch.setattr(container_mod, "_container_runtime", lambda: "/usr/bin/podman")

    class _Proc:
        returncode = 0
        stdout = '["llama-server"]'

    monkeypatch.setattr(container_mod.subprocess, "run", lambda *_a, **_k: _Proc())

    assert ContainerProvider().running_argv(SLOT) == ["llama-server"]


def test_running_argv_none_when_neither_context_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(container_mod.podman_introspect, "container_argv", lambda _t: None)

    def _no_runtime() -> str:
        raise RuntimeError("no podman runtime found")

    monkeypatch.setattr(container_mod, "_container_runtime", _no_runtime)

    assert ContainerProvider().running_argv(SLOT) is None


# ── the promoted regression test (issue #1889, "Test to promote") ──────────


def test_slot_view_reports_present_for_a_running_slot(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    """End of the chain: the value slot_view puts in ``image_status``.

    slot_view reaches ``ContainerProvider.image_present`` through its TTL
    cache helper; this drives the real provider through that same helper so a
    future regression anywhere on the path re-fails here.
    """
    from hal0 import slot_view

    slot_view._image_present_cache.clear()
    monkeypatch.setattr(container_mod.podman_introspect, "image_exists", lambda _i: True)

    present = slot_view._image_present_cached(ContainerProvider(), IMAGE)

    assert present is True
    assert ("present" if present else "missing") == "present"
    slot_view._image_present_cache.clear()
