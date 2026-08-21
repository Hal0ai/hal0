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
from hal0.providers.podman_introspect import ImageProbe

SLOT: dict[str, Any] = {"id": 7, "name": "gpu-chat"}
IMAGE = "ghcr.io/thinmintdev/hal0-runner:v1.0.0-rc.6"


def _probe(presence: str, reason: str | None = None):
    """A canned ``podman_introspect.image_presence`` for monkeypatching."""
    return lambda _image: ImageProbe(presence, reason)  # type: ignore[arg-type]


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

    def _presence(image: str) -> ImageProbe:
        seen.append(image)
        return ImageProbe("present")

    monkeypatch.setattr(container_mod.podman_introspect, "image_presence", _presence)

    assert ContainerProvider().image_present(IMAGE) is True
    assert seen == [IMAGE]


def test_image_present_false_is_an_honest_rootful_negative(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    """A seam that answered "missing" is authoritative — we must NOT then
    re-ask the rootless store and let its (also wrong) answer stand in."""
    monkeypatch.setattr(container_mod.podman_introspect, "image_presence", _probe("missing"))

    assert ContainerProvider().image_present(IMAGE) is False


def test_image_present_falls_back_to_rootless_when_seam_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev checkout / CI / no grant: the seam cannot answer and the operator's
    own store is the right one to read."""
    monkeypatch.setattr(
        container_mod.podman_introspect, "image_presence", _probe("unknown", "not-service-user")
    )
    monkeypatch.setattr(container_mod, "is_hal0_service_user", lambda: False)
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


def test_image_present_unknown_when_seam_silent_and_no_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1939: no seam AND no podman binary means nothing on this box can be
    asked about the image. That is ``unknown``, not ``False`` — a box with no
    container runtime at all was previously reporting every slot image as
    authoritatively absent."""
    monkeypatch.setattr(
        container_mod.podman_introspect, "image_presence", _probe("unknown", "not-service-user")
    )
    monkeypatch.setattr(container_mod, "is_hal0_service_user", lambda: False)

    def _no_runtime() -> str:
        raise RuntimeError("no podman runtime found")

    monkeypatch.setattr(container_mod, "_container_runtime", _no_runtime)

    assert ContainerProvider().image_present(IMAGE) is None


def test_image_present_unknown_when_the_rootless_call_itself_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dev box whose podman binary blows up on exec has not answered either."""
    monkeypatch.setattr(
        container_mod.podman_introspect, "image_presence", _probe("unknown", "not-service-user")
    )
    monkeypatch.setattr(container_mod, "is_hal0_service_user", lambda: False)
    monkeypatch.setattr(container_mod, "_container_runtime", lambda: "/usr/bin/podman")

    def _boom(argv: list[str], **_kw: object) -> None:
        raise OSError("exec format error")

    monkeypatch.setattr(container_mod.subprocess, "run", _boom)

    assert ContainerProvider().image_present(IMAGE) is None


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
    monkeypatch.setattr(container_mod, "is_hal0_service_user", lambda: False)
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
    monkeypatch.setattr(container_mod, "is_hal0_service_user", lambda: False)
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
    monkeypatch.setattr(container_mod, "is_hal0_service_user", lambda: False)

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
    monkeypatch.setattr(container_mod.podman_introspect, "image_presence", _probe("present"))

    present = slot_view._image_present_cached(ContainerProvider(), IMAGE)

    assert present is True
    assert slot_view.image_status_for(present) == "present"
    slot_view._image_present_cache.clear()


def test_slot_view_reports_unknown_for_an_unanswerable_seam(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    """The same end-to-end walk for #1939: wrapper rc 66 on the service
    account must reach ``image_status`` as ``unknown``, through the real
    provider and the real TTL cache — never as ``missing``."""
    from hal0 import slot_view

    slot_view._image_present_cache.clear()
    monkeypatch.setattr(
        container_mod.podman_introspect, "image_presence", _probe("unknown", "podman-failed")
    )
    monkeypatch.setattr(container_mod, "is_hal0_service_user", lambda: True)

    present = slot_view._image_present_cached(ContainerProvider(), IMAGE)

    assert present is None
    assert slot_view.image_status_for(present) == "unknown"
    slot_view._image_present_cache.clear()


def test_image_present_cache_round_trips_unknown_without_flattening_it(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    """A cached ``unknown`` must come back out of the TTL cache as ``unknown``.
    ``bool()``-ing the cached value on the way in or out would silently restore
    the exact collapse #1939 is about, one poll later."""
    from hal0 import slot_view

    slot_view._image_present_cache.clear()
    calls: list[str] = []

    def _presence(image: str) -> ImageProbe:
        calls.append(image)
        return ImageProbe("unknown", "grant-denied")

    monkeypatch.setattr(container_mod.podman_introspect, "image_presence", _presence)
    monkeypatch.setattr(container_mod, "is_hal0_service_user", lambda: True)

    cp = ContainerProvider()
    assert slot_view._image_present_cached(cp, IMAGE) is None
    assert slot_view._image_present_cached(cp, IMAGE) is None
    assert calls == [IMAGE], "the second read must come from the cache"
    slot_view._image_present_cache.clear()


# ── #663 drift comparison, now that running_image is finally reachable ──────
#
# Before #1889 running_image() returned None on every deployed box, so
# _image_mismatch was dead code. Making it live without normalising both sides
# would trade an inert detector for a LYING one: podman reports the canonical
# `docker.io/library/alpine:latest` while hal0 profiles declare the shorthand
# an operator types.


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("alpine", "docker.io/library/alpine:latest"),
        ("alpine:3.19", "docker.io/library/alpine:3.19"),
        ("myorg/img", "docker.io/myorg/img:latest"),
        ("docker.io/library/alpine:latest", "docker.io/library/alpine:latest"),
        # Docker Hub's implicit library/ namespace, however the registry
        # got there — podman reports the fully-expanded form for all three.
        ("docker.io/alpine", "docker.io/library/alpine:latest"),
        ("docker.io/alpine:3.19", "docker.io/library/alpine:3.19"),
        ("ghcr.io/team/model", "ghcr.io/team/model:latest"),
        ("localhost/hal0-toolbox", "localhost/hal0-toolbox:latest"),
        ("localhost:5000/foo:v1", "localhost:5000/foo:v1"),
        ("registry.example.com:443/a/b:t", "registry.example.com:443/a/b:t"),
        ("alpine@sha256:" + "a" * 64, "docker.io/library/alpine@sha256:" + "a" * 64),
        ("  alpine:3.19  ", "docker.io/library/alpine:3.19"),
        ("", ""),
    ],
)
def test_canonical_image_ref(ref: str, expected: str) -> None:
    assert container_mod.canonical_image_ref(ref) == expected


@pytest.mark.parametrize(
    ("running", "declared"),
    [
        # what podman actually reports vs what a profile actually declares
        ("docker.io/library/alpine:latest", "alpine"),
        ("docker.io/library/alpine:latest", "alpine:latest"),
        ("docker.io/library/alpine:latest", "docker.io/alpine"),
        ("docker.io/library/alpine:3.19", "docker.io/alpine:3.19"),
        ("docker.io/myorg/img:latest", "myorg/img"),
        ("ghcr.io/team/model:latest", "ghcr.io/team/model"),
        ("localhost:5000/foo:v1", "localhost:5000/foo:v1"),
    ],
)
def test_no_false_drift_on_equivalent_refs(running: str, declared: str) -> None:
    assert container_mod._image_mismatch(running, declared) is False


@pytest.mark.parametrize(
    ("running", "declared"),
    [
        ("docker.io/library/alpine:3.19", "alpine:3.20"),
        ("ghcr.io/team/model:v1", "ghcr.io/team/other:v1"),
        ("docker.io/library/alpine:latest", "ghcr.io/team/model:latest"),
        # a registry port must not be amputated into a false match
        ("localhost:5000/foo:v1", "localhost:6000/foo:v1"),
    ],
)
def test_real_drift_still_fires(running: str, declared: str) -> None:
    assert container_mod._image_mismatch(running, declared) is True


def test_digest_vs_tag_compares_repository_only() -> None:
    """Deciding whether a tag and a digest name the same image needs a
    registry round-trip the status hot path will never make; guessing
    "drifted" there is the cry-wolf failure #663's contract forbids."""
    digest = "ghcr.io/team/model@sha256:" + "a" * 64
    assert container_mod._image_mismatch(digest, "ghcr.io/team/model:v1") is False
    assert container_mod._image_mismatch(digest, "ghcr.io/team/other:v1") is True


def test_unknown_running_image_is_never_drift() -> None:
    assert container_mod._image_mismatch(None, "alpine") is False
    assert container_mod._image_mismatch("alpine", None) is False


# ── the rootless store is never consulted ON a provisioned box ─────────────
#
# rc 66 ("podman ran but failed") and a missing sudo grant both surface as a
# None from the seam. Falling through to the rootless store there would
# collapse that distinction straight back into an authoritative-looking
# "missing" — #1889 with extra steps — because on a provisioned box that store
# definitionally cannot hold a slot image.


@pytest.mark.parametrize(
    "reason", ["grant-denied", "invalid-argument", "podman-absent", "podman-failed", "seam-error"]
)
def test_image_present_is_unknown_not_false_when_the_seam_cannot_answer(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]], reason: str
) -> None:
    """#1939, the heart of it. Every unanswerable outcome — wrapper rc 66,
    a missing sudoers grant, wrapper drift — used to return ``False`` and
    surface as an authoritative ``image_status: "missing"``. The bool contract
    is now a tri-state and ``None`` means "could not ask".

    ``no_rootless`` raises if subprocess.run is reached at all, so this also
    pins that we still never consult the (wrong) rootless store here.
    """
    monkeypatch.setattr(
        container_mod.podman_introspect, "image_presence", _probe("unknown", reason)
    )
    monkeypatch.setattr(container_mod, "is_hal0_service_user", lambda: True)

    assert ContainerProvider().image_present(IMAGE) is None


def test_image_present_logs_the_seam_reason_when_it_does_not_answer(
    monkeypatch: pytest.MonkeyPatch,
    no_rootless: list[list[str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The payload says ``unknown``; the journal says WHICH unknown. The
    wrapper spends four exit codes distinguishing these — an operator
    debugging a box needs the distinction, and ``unknown`` alone doesn't
    carry it."""
    monkeypatch.setattr(
        container_mod.podman_introspect, "image_presence", _probe("unknown", "podman-failed")
    )
    monkeypatch.setattr(container_mod, "is_hal0_service_user", lambda: True)

    with caplog.at_level("WARNING"):
        ContainerProvider().image_present(IMAGE)

    assert "image_present_unanswered" in caplog.text
    assert "podman-failed" in caplog.text


@pytest.mark.parametrize("method", ["running_image", "running_argv"])
def test_container_reads_do_not_consult_rootless_store_as_service_user(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]], method: str
) -> None:
    monkeypatch.setattr(container_mod.podman_introspect, "container_image", lambda _t: None)
    monkeypatch.setattr(container_mod.podman_introspect, "container_argv", lambda _t: None)
    monkeypatch.setattr(container_mod, "is_hal0_service_user", lambda: True)

    assert getattr(ContainerProvider(), method)(SLOT) is None


@pytest.mark.parametrize("method", ["running_image", "running_argv"])
def test_container_reads_still_fall_back_off_the_service_account(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """Dev checkout: the operator's own store IS the store slots use."""
    monkeypatch.setattr(container_mod.podman_introspect, "container_image", lambda _t: None)
    monkeypatch.setattr(container_mod.podman_introspect, "container_argv", lambda _t: None)
    monkeypatch.setattr(container_mod, "is_hal0_service_user", lambda: False)
    monkeypatch.setattr(container_mod, "_container_runtime", lambda: "/usr/bin/podman")

    class _Proc:
        returncode = 0
        stdout = IMAGE if method == "running_image" else '["llama-server"]'

    monkeypatch.setattr(container_mod.subprocess, "run", lambda *_a, **_k: _Proc())

    expected = IMAGE if method == "running_image" else ["llama-server"]
    assert getattr(ContainerProvider(), method)(SLOT) == expected


def test_both_digest_pinned_compares_digest_not_tag_text() -> None:
    """A digest IS the immutable image id, so the tag text alongside it is
    noise — `repo:v1@sha256:D` and `repo@sha256:D` are the same image."""
    digest = "@sha256:" + "a" * 64
    other = "@sha256:" + "b" * 64
    repo = "ghcr.io/team/model"

    assert container_mod._image_mismatch(f"{repo}:v1{digest}", f"{repo}{digest}") is False
    assert container_mod._image_mismatch(f"{repo}:v1{digest}", f"{repo}:v2{digest}") is False
    # a genuinely different digest, or a different repository, is real drift
    assert container_mod._image_mismatch(f"{repo}{digest}", f"{repo}{other}") is True
    assert container_mod._image_mismatch(f"{repo}{digest}", f"ghcr.io/team/x{digest}") is True


def test_ipv6_registry_refs_canonicalise_and_compare() -> None:
    ref = "[2001:db8::1]:5000/team/model:v1"
    assert container_mod.canonical_image_ref(ref) == ref
    assert container_mod._image_mismatch(ref, ref) is False
    assert container_mod._image_mismatch(ref, "[2001:db8::2]:5000/team/model:v1") is True
