"""#1974 — the FLM-image probe must ask the right store and never cache
"could not ask" as "absent".

``available_backends`` gates the NPU backend on
:func:`hal0.capabilities.catalog._flm_image_present`. It used to shell a bare
``<runtime> image inspect``, which on a provisioned box reads hal0-api's own
ROOTLESS store (the #1889 trap — slot images live in root's store), mapped
every non-zero rc to ``False``, and cached that ``False`` for the process
lifetime. One broken podman call therefore dropped NPU from
``/api/capabilities`` until restart.

The probe now goes through
:meth:`hal0.providers.container.ContainerProvider.image_present` — the same
root-store seam + dev-box rootless fallback slots use — and caches only
definitive answers; an unanswerable probe is retried after a short window.

The probe runs on a background thread and never blocks a caller (the
non-blocking contract is pinned in ``test_flm_probe_off_loop.py``). These
tests are about WHAT the probe asks and caches, so every read goes through
:func:`_ids`, which first lets any due probe land via
:func:`catalog.prime_flm_image_probe`.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from hal0.capabilities import catalog
from hal0.providers import container as container_mod
from hal0.providers.podman_introspect import ImageProbe


def _npu_only_hw() -> Any:
    return types.SimpleNamespace(npu=types.SimpleNamespace(present=True), gpus=[])


def _ids() -> list[str]:
    """Backend ids once any due background probe has landed."""
    catalog.prime_flm_image_probe(timeout=5)
    return [b["id"] for b in catalog.available_backends()]


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(catalog, "load_hardware_info", _npu_only_hw)
    monkeypatch.setenv("HAL0_CONTAINER_RUNTIME", "podman")
    monkeypatch.setattr(catalog, "_flm_last_definitive", None)
    catalog.reset_flm_image_present_cache()
    yield
    if catalog._flm_probe_thread is not None:
        catalog._flm_probe_thread.join(timeout=5)
    catalog.reset_flm_image_present_cache()


class _Seam:
    """Scripted ``podman_introspect.image_presence``: one probe per call."""

    def __init__(self, *answers: ImageProbe) -> None:
        self._answers = list(answers)
        self.calls: list[str] = []

    def __call__(self, image: str) -> ImageProbe:
        self.calls.append(image)
        return self._answers[min(len(self.calls), len(self._answers)) - 1]


@pytest.fixture
def no_rootless(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Booby-trap the rootless podman path so a seam test cannot pass by
    silently falling through to hal0-api's own store."""
    calls: list[list[str]] = []

    def _boom(argv: list[str], **_kw: object) -> None:
        calls.append(list(argv))
        raise AssertionError(f"rootless podman was invoked: {argv}")

    monkeypatch.setattr(container_mod.subprocess, "run", _boom)
    return calls


def _install_seam(monkeypatch: pytest.MonkeyPatch, seam: _Seam) -> None:
    monkeypatch.setattr(container_mod.podman_introspect, "image_presence", seam)


# ── store selection ──────────────────────────────────────────────────────────


def test_present_in_root_store_advertises_npu_without_touching_rootless_store(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    seam = _Seam(ImageProbe("present"))
    _install_seam(monkeypatch, seam)

    assert _ids()[0] == "npu"
    assert seam.calls == [catalog._FLM_TOOLBOX_IMAGE]
    assert no_rootless == []


def test_missing_from_root_store_hides_npu_and_is_cached(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    seam = _Seam(ImageProbe("missing"), ImageProbe("present"))
    _install_seam(monkeypatch, seam)

    assert "npu" not in _ids()
    assert "npu" not in _ids()
    assert len(seam.calls) == 1, "a definitive 'missing' must be cached"


@pytest.mark.parametrize("reason", ["grant-denied", "podman-failed", "podman-absent", "seam-error"])
def test_seam_failure_never_falls_back_to_rootless_store(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]], reason: str
) -> None:
    _install_seam(monkeypatch, _Seam(ImageProbe("unknown", reason)))  # type: ignore[arg-type]

    assert "npu" not in _ids()
    assert no_rootless == []


# ── a failed probe is not "absent" ───────────────────────────────────────────


def test_seam_failure_is_not_cached_as_absent(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    """The issue's regression: a transient seam failure must not evict NPU
    for the process lifetime. Once the retry window passes, the next call
    re-probes and a now-answering seam brings NPU back."""
    seam = _Seam(ImageProbe("unknown", "podman-failed"), ImageProbe("present"))
    _install_seam(monkeypatch, seam)
    monkeypatch.setattr(catalog, "_FLM_PROBE_RETRY_S", 0.0, raising=False)

    assert "npu" not in _ids()
    assert _ids()[0] == "npu"
    assert len(seam.calls) == 2


def test_seam_failure_is_not_reprobed_inside_the_retry_window(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    """The GET must not spawn a probe per call while the seam is down."""
    seam = _Seam(ImageProbe("unknown", "podman-failed"), ImageProbe("present"))
    _install_seam(monkeypatch, seam)
    monkeypatch.setattr(catalog, "_FLM_PROBE_RETRY_S", 3600.0, raising=False)

    assert "npu" not in _ids()
    assert "npu" not in _ids()
    assert len(seam.calls) == 1


def test_later_seam_failure_does_not_evict_a_known_present_image(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    seam = _Seam(ImageProbe("present"), ImageProbe("unknown", "podman-failed"))
    _install_seam(monkeypatch, seam)
    monkeypatch.setattr(catalog, "_FLM_PROBE_RETRY_S", 0.0, raising=False)

    assert _ids()[0] == "npu"
    assert _ids()[0] == "npu"
    assert len(seam.calls) == 1


def test_reset_drops_a_cached_failure_too(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    """After an FLM pull the reset hook must re-probe even inside the window."""
    seam = _Seam(ImageProbe("unknown", "podman-failed"), ImageProbe("present"))
    _install_seam(monkeypatch, seam)
    monkeypatch.setattr(catalog, "_FLM_PROBE_RETRY_S", 3600.0, raising=False)

    assert "npu" not in _ids()
    catalog.reset_flm_image_present_cache()
    assert _ids()[0] == "npu"


# ── dev box (not the service user): the rootless store IS the store ─────────


def _dev_box(monkeypatch: pytest.MonkeyPatch, *rcs: int) -> list[list[str]]:
    _install_seam(monkeypatch, _Seam(ImageProbe("unknown", "not-service-user")))
    calls: list[list[str]] = []
    answers = list(rcs)

    def fake_run(argv: list[str], **_kw: object) -> Any:
        calls.append(list(argv))
        return types.SimpleNamespace(returncode=answers[min(len(calls), len(answers)) - 1])

    monkeypatch.setattr(container_mod.subprocess, "run", fake_run)
    return calls


def test_dev_box_uses_image_exists_not_inspect(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _dev_box(monkeypatch, 0)

    assert _ids()[0] == "npu"
    assert calls == [["podman", "image", "exists", catalog._FLM_TOOLBOX_IMAGE]]


def test_dev_box_absent_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _dev_box(monkeypatch, 1, 0)

    assert "npu" not in _ids()
    assert "npu" not in _ids()
    assert len(calls) == 1


def test_dev_box_podman_operational_failure_is_not_cached_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _dev_box(monkeypatch, 125, 0)
    monkeypatch.setattr(catalog, "_FLM_PROBE_RETRY_S", 0.0, raising=False)

    assert "npu" not in _ids()
    assert _ids()[0] == "npu"
    assert len(calls) == 2


def test_no_runtime_is_not_cached_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Old code cached ``False`` forever when no runtime resolved."""
    _install_seam(monkeypatch, _Seam(ImageProbe("unknown", "not-service-user")))
    monkeypatch.setattr(catalog, "_FLM_PROBE_RETRY_S", 0.0, raising=False)
    resolved: list[bool] = [False, True]

    def fake_runtime() -> str:
        if not resolved.pop(0):
            raise RuntimeError("no podman runtime found")
        return "podman"

    monkeypatch.setattr(container_mod, "_container_runtime", fake_runtime)
    monkeypatch.setattr(
        container_mod.subprocess, "run", lambda argv, **_kw: types.SimpleNamespace(returncode=0)
    )

    assert "npu" not in _ids()
    assert _ids()[0] == "npu"


# ── end to end: the real seam client, only the subprocess faked ─────────────


def test_end_to_end_seam_failure_then_recovery_through_real_seam_client(
    monkeypatch: pytest.MonkeyPatch, no_rootless: list[list[str]]
) -> None:
    """Exercise catalog → ContainerProvider.image_present → the REAL
    ``podman_introspect.image_presence`` (service-user gate, ``_seam_read``,
    the wrapper rc → reason map) with only the ``sudo`` call faked.

    rc 66 is the wrapper's ``podman-failed``: an operational failure that must
    hide NPU for now without being remembered as "absent". The re-probe then
    gets rc 0 / ``present`` and NPU comes back.
    """
    real_presence = container_mod.podman_introspect.image_presence
    seam_calls: list[list[str]] = []
    outcomes = [(66, ""), (0, "present\n")]

    def fake_seam_run(argv: list[str], **_kw: object) -> Any:
        seam_calls.append(list(argv))
        rc, out = outcomes[min(len(seam_calls), len(outcomes)) - 1]
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr="")

    def presence_via_real_client(image: str) -> ImageProbe:
        return real_presence(image, run=fake_seam_run, is_hal0_user=lambda: True)

    _install_seam(monkeypatch, presence_via_real_client)  # type: ignore[arg-type]
    monkeypatch.setattr(catalog, "_FLM_PROBE_RETRY_S", 0.0, raising=False)

    assert "npu" not in _ids()
    assert _ids()[0] == "npu"
    assert _ids()[0] == "npu"  # now cached: no third seam call
    assert (
        seam_calls
        == [
            [
                "sudo",
                "-n",
                container_mod.podman_introspect.SEAM_BIN,
                "image-exists",
                catalog._FLM_TOOLBOX_IMAGE,
            ]
        ]
        * 2
    )
    assert no_rootless == []
