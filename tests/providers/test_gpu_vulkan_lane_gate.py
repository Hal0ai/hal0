"""#1948 Phase D — the Vulkan LLM lane is served only from a gated image.

#1923 retired the lane outright: a runner whose Vulkan backend emits invalid
tokens for every model (#1888) made ``gpu-vulkan`` name a lane no llama.cpp
slot could validly run, and the guard refused it on every AMD host.

The lane is back, but what must never come back is the ability to serve it
from an image whose digest has not passed the hardware gate's Vulkan lane.
The guard is device-aware —

* ``gpu-rocm``            → ``/dev/kfd`` required, semantics unchanged.
* ``gpu-vulkan`` + llama  → a render node the runner can open, AND a resolved
  image in :func:`hal0.runners.pins.vulkan_capable_refs` (a pinned digest whose
  ``manifest.json`` row lists ``vulkan`` in ``lanes_gated``). No ``/dev/kfd``
  requirement.
* ``gpu-vulkan`` + non-llama → untouched (#1941/#1952 semantics).

The image gate is the half that makes the lane safe; the #1922 output-sanity
readiness gate is the permanent net BEHIND it.
"""

from __future__ import annotations

import os

import pytest

import hal0.runners.pins as pins_mod
from hal0.providers._gpu import (
    ENV_ALLOW_VULKAN_FALLBACK,
    GpuPreflightError,
    image_serves_vulkan_lane,
    render_node_present,
    require_kfd_for_gpu_slot,
)
from hal0.runners import RUNNER_IMAGES
from hal0.runners.pins import RunnerPin

_GATED_DIGEST = "sha256:" + "a" * 64

#: The ``standard`` pin the Vulkan gate passed (``lanes_gated`` lists vulkan).
GATED_PIN = RunnerPin(
    key="standard",
    image="ghcr.io/hal0ai/hal0-runner-standard",
    tag="b11552-r1",
    digest=_GATED_DIGEST,
    lanes_gated=("rocm", "vulkan"),
)

#: An image no pin row names: the stand-in for "a runner this lane must
#: refuse", used everywhere below. A LITERAL, deliberately: the defect belongs
#: to this ref, whatever the pin table currently says.
UNGATED_REF = "ghcr.io/hal0ai/hal0-runner-standard:b9999-r9"

#: Aliases kept so the assertions below read as "the validated image" / "the
#: refused image".
VULKAN_FIXED_IMAGE = GATED_PIN.ref
ADE07BA_REF = UNGATED_REF


@pytest.fixture(autouse=True)
def _gated_standard_pin(monkeypatch):
    """Make ``standard`` a Vulkan-gated pin for every test in this module."""
    table = {
        key: RunnerPin(key=key, image=f"ghcr.io/hal0ai/hal0-runner-{key}") for key in RUNNER_IMAGES
    }
    table["standard"] = GATED_PIN
    monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
    return table


def _render_node(tmp_path):
    """A ``/dev/dri``-shaped directory holding one openable render node.

    ``renderD128`` is a symlink to ``/dev/null`` because a real render node is
    a CHARACTER DEVICE and ``render_node_present`` requires one (review N3):
    ``resolve_gpu_device_paths`` forwards only character devices, so a regular
    file here would be a fixture that passes a check the real launch then
    contradicts. ``card0`` stays a plain file — it must be ignored regardless
    of what it is.
    """
    dri = tmp_path / "dri"
    dri.mkdir()
    (dri / "renderD128").symlink_to("/dev/null")
    (dri / "card0").write_text("")
    return str(dri)


class TestVulkanCapableImageSet:
    """Membership in the gated pin set, deliberately — NOT a tag comparison."""

    def test_the_gated_pin_is_capable(self) -> None:
        assert GATED_PIN.ref in pins_mod.vulkan_capable_refs()
        assert image_serves_vulkan_lane(GATED_PIN.ref) is True

    def test_every_spelling_of_the_gated_pin_is_capable(self) -> None:
        """``repo:tag@digest``, ``repo:tag`` and ``repo@digest`` name the same bytes."""
        for ref in GATED_PIN.spellings():
            assert image_serves_vulkan_lane(ref) is True

    def test_the_bare_repo_is_not_capable(self) -> None:
        """The bare repo names whatever ``latest`` is, never a gated digest."""
        assert image_serves_vulkan_lane(GATED_PIN.image) is False

    def test_an_unnamed_ref_is_not_capable(self) -> None:
        assert UNGATED_REF not in pins_mod.vulkan_capable_refs()
        assert image_serves_vulkan_lane(UNGATED_REF) is False

    def test_a_pin_row_that_did_not_pass_the_vulkan_gate_is_not_capable(self, monkeypatch) -> None:
        """Admission is data next to the digest: ``lanes_gated`` without
        ``vulkan`` (ROCm-only) admits nothing to the Vulkan lane."""
        table = {
            "standard": RunnerPin(
                key="standard",
                image=GATED_PIN.image,
                tag=GATED_PIN.tag,
                digest=GATED_PIN.digest,
                lanes_gated=("rocm",),
            )
        }
        monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
        assert image_serves_vulkan_lane(GATED_PIN.ref) is False

    def test_a_runner_that_cannot_run_vulkan_never_admits_its_image(self, monkeypatch) -> None:
        """``cpu`` has no vulkan backend: a ``vulkan`` entry in its row is ignored."""
        cpu = RunnerPin(
            key="cpu",
            image="ghcr.io/hal0ai/hal0-runner-cpu",
            tag="b11552-r1",
            digest="sha256:" + "c" * 64,
            lanes_gated=("cpu", "vulkan"),
        )
        table = {"cpu": cpu}
        monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
        assert image_serves_vulkan_lane(cpu.ref) is False

    @pytest.mark.parametrize("ref", ["", None, "  ", "ghcr.io/someone/unknown:1"])
    def test_unknown_and_unresolved_refs_fail_closed(self, ref) -> None:
        """Fail-closed: an image nobody has validated is not a Vulkan image."""
        assert image_serves_vulkan_lane(ref) is False


class TestRenderNodePresent:
    def test_true_for_an_openable_render_node(self, tmp_path) -> None:
        assert render_node_present(_render_node(tmp_path)) is True

    def test_false_when_the_directory_has_no_render_node(self, tmp_path) -> None:
        dri = tmp_path / "dri"
        dri.mkdir()
        (dri / "card0").write_text("")
        assert render_node_present(str(dri)) is False

    def test_false_when_the_directory_is_absent(self, tmp_path) -> None:
        assert render_node_present(str(tmp_path / "nope")) is False

    def test_false_when_this_process_cannot_open_the_node(self, monkeypatch, tmp_path) -> None:
        """The permission branch, asked about THIS process (``for_uid=None``).

        ``for_uid`` is explicit here because #1981 made the DEFAULT the slot
        container's identity rather than the caller's — a bare call now
        answers on existence, which is the entire point of that change. The
        identity contract itself is pinned in ``TestRenderNodeIdentityRules``.

        ``os.access`` is patched rather than the node chmod'd: the node has to
        stay a real character device to get past the type check, and a
        character device's permissions cannot be varied inside a tmpdir
        without root. Patching the exact call the function makes keeps the
        test about the permission branch and nothing else.
        """
        dri = tmp_path / "dri"
        dri.mkdir()
        (dri / "renderD128").symlink_to("/dev/null")

        real_access = os.access
        monkeypatch.setattr(
            os,
            "access",
            lambda path, mode: (
                False if str(path).endswith("renderD128") else real_access(path, mode)
            ),
        )
        assert render_node_present(str(dri), for_uid=None) is False


class TestRenderNodeIdentityRules:
    """#1981 — which identity the render-node probe answers for.

    The render-node twin of #1953, and it is pinned here for the same reason
    that one is: the process that OPENS the node is the rootful slot
    container, not ``hal0-api``. A caller-identity probe refuses a Vulkan slot
    the container would open fine on any host where ``hal0`` is not in the
    node's owning group — the halo143 shape, where ``renderD128``'s gid 993 is
    named ``clock`` rather than ``render``.

    Mirrors ``TestKfdStatusIdentityRules`` case for case, deliberately: two
    probes answering the same class of question must not develop two different
    identity contracts.
    """

    @staticmethod
    def _unopenable_node(tmp_path):
        """A real character device that THIS process cannot open.

        ``os.access`` is patched rather than the node chmod'd — it must stay a
        character device to get past the type check, and a char device's
        permissions cannot be varied inside a tmpdir without root.
        """
        dri = tmp_path / "dri"
        dri.mkdir()
        (dri / "renderD128").symlink_to("/dev/null")
        return str(dri)

    def test_asking_about_this_process_consults_the_os_even_when_root(
        self, tmp_path, monkeypatch
    ) -> None:
        """No uid-0 short-circuit for the CURRENT process: ask ``os.access``.

        Root bypasses DAC only with ``CAP_DAC_OVERRIDE``, which a hardened
        container drops — CI runs in exactly such a container. A blanket "root
        is fine" would report a genuinely unopenable node as usable, failing
        OPEN into the lane this gate exists to police.
        """
        dri = self._unopenable_node(tmp_path)
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setattr(os, "access", lambda *a, **k: False)

        assert render_node_present(dri, for_uid=None) is False
        # for_uid=0 is NOT the same question, even from a root caller: it names
        # the rootful slot container, whose CAP_DAC_OVERRIDE this process's
        # denial says nothing about. Answering False here would collapse the
        # parameter for root callers (install.sh, install/profile_derive.py) —
        # the exact harm the parameter exists to fix.
        assert render_node_present(dri, for_uid=0) is True

    def test_asking_about_a_different_root_identity_still_assumes_override(
        self, tmp_path, monkeypatch
    ) -> None:
        """The slot container's root is not this process — it cannot be probed
        from here, so the usual DAC override is assumed. That is the whole
        point of the runner-identity parameter."""
        dri = self._unopenable_node(tmp_path)
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
        monkeypatch.setattr(os, "access", lambda *a, **k: False)

        assert render_node_present(dri, for_uid=0) is True

    def test_the_default_identity_is_the_slot_runner_not_the_caller(
        self, tmp_path, monkeypatch
    ) -> None:
        """#1981's actual fix: the DEFAULT must be the container's identity.

        Before this, the default was an implicit "this process", so a slot was
        refused on exactly the boxes where the container would have worked.
        """
        from hal0.providers._gpu import SLOT_RUNNER_UID

        dri = self._unopenable_node(tmp_path)
        monkeypatch.setattr(os, "access", lambda *a, **k: False)

        assert SLOT_RUNNER_UID == 0
        assert render_node_present(dri) is True
        assert render_node_present(dri, for_uid=None) is False

    def test_a_non_root_identity_is_judged_on_the_mode_bits(self, tmp_path, monkeypatch) -> None:
        """A USER-declaring image runs as a non-root uid that cannot be probed
        with ``os.access`` either, so the mode bits decide — the same fallback
        ``kfd_status`` uses."""
        from hal0.providers import _gpu as gpu_mod

        dri = self._unopenable_node(tmp_path)
        seen: list[int] = []

        def _fake_mode_grants_rw(path: str, uid: int) -> bool:
            seen.append(uid)
            return uid == 1000

        monkeypatch.setattr(gpu_mod, "_mode_grants_rw", _fake_mode_grants_rw)

        assert render_node_present(dri, for_uid=1000) is True
        assert render_node_present(dri, for_uid=1001) is False
        assert seen == [1000, 1001]

    def test_existence_is_still_required_for_the_container_identity(self, tmp_path) -> None:
        """Failing open on permissions is not failing open on presence: an
        absent node is absent for every identity, and the two gates behind
        this one (image allowlist, #1922 sanity probe) cannot conjure a
        device either."""
        assert render_node_present(str(tmp_path / "nope"), for_uid=0) is False

        dri = tmp_path / "dri"
        dri.mkdir()
        (dri / "renderD128").write_text("")  # named right, not a char device
        assert render_node_present(str(dri), for_uid=0) is False


class TestTheGuardUsesTheRunnerIdentity:
    """The contract has to reach the guard, not just the probe."""

    def test_the_render_check_is_made_for_the_runner_uid(self, tmp_path, monkeypatch) -> None:
        from hal0.providers import _gpu as gpu_mod

        seen: dict[str, object] = {}

        def _spy(dri_dir: str, *, for_uid: int | None = 0) -> bool:
            seen["for_uid"] = for_uid
            return True

        monkeypatch.setattr(gpu_mod, "render_node_present", _spy)

        require_kfd_for_gpu_slot(
            "utility",
            device="gpu-vulkan",
            runtime_lane="llama",
            kfd_path=str(tmp_path / "gone"),
            dri_dir=str(tmp_path),
            image=VULKAN_FIXED_IMAGE,
            env={},
            amd_host=True,
            runner_uid=1234,
        )
        assert seen == {"for_uid": 1234}

    def test_the_uid_probe_is_not_paid_for_when_the_image_is_refused(
        self, tmp_path, monkeypatch
    ) -> None:
        """Laziness contract, matching the kfd path: ``runner_uid`` may be a
        sudo round-trip plus two podman calls, and a slot rejected for its
        image never needs to know which uid would have opened the node."""
        calls: list[int] = []

        def _expensive() -> int:
            calls.append(1)
            return 0

        with pytest.raises(GpuPreflightError):
            require_kfd_for_gpu_slot(
                "utility",
                device="gpu-vulkan",
                runtime_lane="llama",
                kfd_path=str(tmp_path / "gone"),
                dri_dir=_render_node(tmp_path),
                image=ADE07BA_REF,
                env={},
                amd_host=True,
                runner_uid=_expensive,
            )
        assert calls == []

    def test_the_uid_probe_is_resolved_when_the_image_passes(self, tmp_path) -> None:
        calls: list[int] = []

        def _expensive() -> int:
            calls.append(1)
            return 0

        require_kfd_for_gpu_slot(
            "utility",
            device="gpu-vulkan",
            runtime_lane="llama",
            kfd_path=str(tmp_path / "gone"),
            dri_dir=_render_node(tmp_path),
            image=VULKAN_FIXED_IMAGE,
            env={},
            amd_host=True,
            runner_uid=_expensive,
        )
        assert calls == [1]


class TestVulkanLaneGuard:
    """``gpu-vulkan`` + the llama lane on an AMD host."""

    def test_a_stale_image_is_refused_at_preflight(self, tmp_path) -> None:
        """The whole point: a box with a perfectly good render node still may
        NOT serve the Vulkan lane from an image that never passed the gate."""
        with pytest.raises(GpuPreflightError) as exc:
            require_kfd_for_gpu_slot(
                "utility",
                device="gpu-vulkan",
                runtime_lane="llama",
                kfd_path=str(tmp_path / "kfd"),
                dri_dir=_render_node(tmp_path),
                image=ADE07BA_REF,
                env={},
                amd_host=True,
            )
        msg = str(exc.value)
        assert "utility" in msg
        assert "1888" in msg  # name the defect this refusal exists to prevent
        assert ADE07BA_REF in msg  # name the image actually resolved
        assert "lanes_gated" in msg  # name the way out: the gate lives in manifest.json

    def test_an_unresolvable_image_is_refused(self, tmp_path) -> None:
        """Fail-closed, exactly like ``runtime_lane``'s ``"llama"`` default: a
        caller that cannot say which image this slot launches must not get a
        silent pass into the lane #1888 poisoned."""
        with pytest.raises(GpuPreflightError):
            require_kfd_for_gpu_slot(
                "utility",
                device="gpu-vulkan",
                runtime_lane="llama",
                kfd_path=str(tmp_path / "kfd"),
                dri_dir=_render_node(tmp_path),
                image=None,
                env={},
                amd_host=True,
            )

    def test_the_fixed_image_passes_without_any_kfd(self, tmp_path) -> None:
        """The ct151 shape: no /dev/kfd at all, Vulkan is the ONLY lane, and on
        :0822 it serves correct output — so the load must proceed."""
        require_kfd_for_gpu_slot(
            "utility",
            device="gpu-vulkan",
            runtime_lane="llama",
            kfd_path=str(tmp_path / "kfd"),
            dri_dir=_render_node(tmp_path),
            image=VULKAN_FIXED_IMAGE,
            env={},
            amd_host=True,
        )

    def test_the_fixed_image_still_needs_a_render_node(self, tmp_path) -> None:
        """Vulkan needs /dev/dri/renderD* the way ROCm needs /dev/kfd. Without
        it there is no device to run on, and the refusal must say so rather
        than blame the image."""
        with pytest.raises(GpuPreflightError) as exc:
            require_kfd_for_gpu_slot(
                "utility",
                device="gpu-vulkan",
                runtime_lane="llama",
                kfd_path=str(tmp_path / "kfd"),
                dri_dir=str(tmp_path / "no-dri"),
                image=VULKAN_FIXED_IMAGE,
                env={},
                amd_host=True,
            )
        msg = str(exc.value)
        assert "renderD" in msg
        assert "1888" not in msg  # this is a passthrough problem, not the defect

    def test_the_stale_image_refusal_honours_the_inspection_opt_in(self, tmp_path) -> None:
        """``HAL0_ALLOW_VULKAN_FALLBACK`` keeps its meaning — "I knowingly want
        this lane despite the invalid output" — so it downgrades the IMAGE
        refusal to a warning. It cannot conjure a missing render node, so it
        does not apply to that one."""
        from structlog.testing import capture_logs

        with capture_logs() as logs:
            require_kfd_for_gpu_slot(
                "utility",
                device="gpu-vulkan",
                runtime_lane="llama",
                kfd_path=str(tmp_path / "kfd"),
                dri_dir=_render_node(tmp_path),
                image=ADE07BA_REF,
                env={ENV_ALLOW_VULKAN_FALLBACK: "1"},
                amd_host=True,
            )
        warnings = [e for e in logs if e.get("log_level") == "warning"]
        assert len(warnings) == 1
        assert "1888" in warnings[0]["detail"]

    def test_the_opt_in_does_not_conjure_a_render_node(self, tmp_path) -> None:
        with pytest.raises(GpuPreflightError):
            require_kfd_for_gpu_slot(
                "utility",
                device="gpu-vulkan",
                runtime_lane="llama",
                kfd_path=str(tmp_path / "kfd"),
                dri_dir=str(tmp_path / "no-dri"),
                image=VULKAN_FIXED_IMAGE,
                env={ENV_ALLOW_VULKAN_FALLBACK: "1"},
                amd_host=True,
            )

    def test_a_non_amd_host_keeps_its_ungated_vulkan_lane(self, tmp_path) -> None:
        """#1925: the Intel/NVIDIA Vulkan lane was never characterised and runs
        a different image entirely (FALLBACK_VULKAN_IMAGE). Applying the AMD
        image allowlist there would strand unaffected hardware."""
        require_kfd_for_gpu_slot(
            "utility",
            device="gpu-vulkan",
            runtime_lane="llama",
            kfd_path=str(tmp_path / "kfd"),
            dri_dir=str(tmp_path / "no-dri"),
            image=ADE07BA_REF,
            env={},
            amd_host=False,
        )


class TestUnchangedNeighbours:
    """Everything this PR is NOT allowed to move."""

    def test_gpu_rocm_still_requires_kfd_on_the_fixed_image(self, tmp_path) -> None:
        """The image gate is Vulkan's. A ``gpu-rocm`` slot on :0822 is still a
        ROCm claim and still needs the compute node."""
        with pytest.raises(GpuPreflightError) as exc:
            require_kfd_for_gpu_slot(
                "agent",
                device="gpu-rocm",
                runtime_lane="llama",
                kfd_path=str(tmp_path / "kfd"),
                dri_dir=_render_node(tmp_path),
                image=VULKAN_FIXED_IMAGE,
                env={},
                amd_host=True,
            )
        assert "/kfd" in str(exc.value)

    def test_gpu_rocm_passes_with_kfd_regardless_of_image(self, tmp_path) -> None:
        node = tmp_path / "kfd"
        node.write_text("")
        require_kfd_for_gpu_slot(
            "agent",
            device="gpu-rocm",
            runtime_lane="llama",
            kfd_path=str(node),
            dri_dir=_render_node(tmp_path),
            image=ADE07BA_REF,
            env={},
            amd_host=True,
        )

    @pytest.mark.parametrize("image", [None, ADE07BA_REF, VULKAN_FIXED_IMAGE])
    def test_a_no_rocm_runtime_on_gpu_vulkan_is_never_image_gated(self, tmp_path, image) -> None:
        """Kokoro / Moonshine (#1941). Their ``gpu-vulkan`` label is the
        picker's GPU row, not a claim about a llama.cpp backend — the image
        allowlist is meaningless for them and must not fire."""
        require_kfd_for_gpu_slot(
            "voice",
            device="gpu-vulkan",
            runtime_lane="no-rocm",
            kfd_path=str(tmp_path / "kfd"),
            dri_dir=str(tmp_path / "no-dri"),
            image=image,
            env={},
            amd_host=True,
        )

    @pytest.mark.parametrize("image", [None, ADE07BA_REF, VULKAN_FIXED_IMAGE])
    def test_a_rocm_runtime_on_gpu_vulkan_still_needs_kfd(self, tmp_path, image) -> None:
        """ComfyUI / Qwen3-TTS (#1952). Their images resolve HIP, so they keep
        the kfd requirement and must NOT be diverted into the Vulkan gate — an
        image allowlist built for llama.cpp says nothing about them."""
        with pytest.raises(GpuPreflightError) as exc:
            require_kfd_for_gpu_slot(
                "imagegen",
                device="gpu-vulkan",
                runtime_lane="rocm",
                kfd_path=str(tmp_path / "kfd"),
                dri_dir=_render_node(tmp_path),
                image=image,
                env={},
                amd_host=True,
            )
        msg = str(exc.value)
        assert "/kfd" in msg
        assert "1888" not in msg
        assert "resolves ROCm at launch" in msg

    @pytest.mark.parametrize("device", ["cpu", "npu", "gpu-cuda", "", "img"])
    def test_non_gpu_devices_stay_untouched(self, tmp_path, device) -> None:
        require_kfd_for_gpu_slot(
            "x",
            device=device,
            kfd_path=str(tmp_path / "kfd"),
            dri_dir=str(tmp_path / "no-dri"),
            image=ADE07BA_REF,
            env={},
            amd_host=True,
        )


class TestLoadSyncThreadsTheResolvedImage:
    """The guard can only gate the image the slot ACTUALLY launches, so
    ``load_sync`` must resolve it the same way ``container_spec`` does — an
    ``image_pin`` to a stale ref has to reach the guard."""

    @staticmethod
    def _amd_box_without_kfd(monkeypatch) -> None:
        from hal0.providers import _gpu as gpu_mod

        monkeypatch.setattr(gpu_mod, "host_is_amd_gpu", lambda *_a, **_k: True)
        monkeypatch.setattr(gpu_mod, "kfd_present", lambda *_a, **_k: False)
        monkeypatch.setattr(gpu_mod, "render_node_present", lambda *_a, **_k: True)

    @staticmethod
    def _stub_unit_write(monkeypatch) -> list[str]:
        from hal0.providers import container as container_mod

        written: list[str] = []
        monkeypatch.setattr(
            container_mod.ContainerProvider,
            "_render_quadlet_text",
            lambda self, slot_cfg, model_info: "[Container]\n",
        )
        monkeypatch.setattr(
            container_mod.ContainerProvider,
            "_write_and_start_unit",
            lambda self, token, unit_text: written.append(token),
        )
        return written

    def test_a_stale_pin_is_refused_through_load_sync(self, monkeypatch) -> None:
        from hal0.providers import container as container_mod

        self._amd_box_without_kfd(monkeypatch)
        self._stub_unit_write(monkeypatch)

        with pytest.raises(GpuPreflightError) as exc:
            container_mod.ContainerProvider().load_sync(
                {
                    "name": "utility",
                    "device": "gpu-vulkan",
                    "port": 8082,
                    "image_pin": ADE07BA_REF,
                },
                {},
            )
        assert "1888" in str(exc.value)

    def test_the_fixed_pin_loads_on_a_kfd_less_box(self, monkeypatch) -> None:
        """The ct151 end-to-end shape, at the preflight seam."""
        from hal0.providers import container as container_mod

        self._amd_box_without_kfd(monkeypatch)
        written = self._stub_unit_write(monkeypatch)

        container_mod.ContainerProvider().load_sync(
            {
                "name": "utility",
                "device": "gpu-vulkan",
                "port": 8082,
                "image_pin": VULKAN_FIXED_IMAGE,
            },
            {},
        )
        assert written == ["utility"]

    @staticmethod
    def _override(monkeypatch, family: str, ref: str) -> None:
        """Install a [slots].default_images override the REAL chain will read.

        Patches ``hal0.config.loader.load_hal0_config`` (what
        ``_image.slot_default_images`` reads fresh on every resolve) —
        NOT the resolution tier itself — so the test exercises the whole
        override→resolve→preflight path, not a stub of it.
        """
        from types import SimpleNamespace

        monkeypatch.setattr(
            "hal0.config.loader.load_hal0_config",
            lambda: SimpleNamespace(slots=SimpleNamespace(default_images={family: ref})),
        )

    def test_a_broken_family_override_is_refused_through_load_sync(self, monkeypatch) -> None:
        """Spec §1 Safety (runner-image-catalogue v2): "the Vulkan-lane gate
        (``vulkan_capable_refs``) still applies at slot-load preflight — an
        override cannot silently re-arm #1888."

        A ``gpu-vulkan`` slot with NO pin resolves through the new
        ``[slots].default_images`` tier; when the operator points the
        ``standard`` family (the runner vulkan is served through, per
        ``supported_backends``) at an ungated image, ``load_sync``'s
        preflight must see THAT ref (it passes the raw ``_resolve_image_ref``
        result to ``require_kfd_for_gpu_slot``) and refuse with the typed
        error — never launch-and-emit-garbage. Verified red by reverting the
        override tier: the preflight then sees the capable baked default and
        admits the slot.
        """
        from hal0.providers import container as container_mod

        self._amd_box_without_kfd(monkeypatch)
        self._stub_unit_write(monkeypatch)
        self._override(monkeypatch, "standard", ADE07BA_REF)

        with pytest.raises(GpuPreflightError) as exc:
            container_mod.ContainerProvider().load_sync(
                {"name": "utility", "device": "gpu-vulkan", "port": 8082},
                {},
            )
        assert "1888" in str(exc.value)
        assert ADE07BA_REF in str(exc.value)  # the refusal names the OVERRIDE ref

    def test_a_capable_family_override_loads_on_a_kfd_less_box(self, monkeypatch) -> None:
        """The positive twin: a validated override passes the same gate."""
        from hal0.providers import container as container_mod

        self._amd_box_without_kfd(monkeypatch)
        written = self._stub_unit_write(monkeypatch)
        self._override(monkeypatch, "standard", VULKAN_FIXED_IMAGE)

        container_mod.ContainerProvider().load_sync(
            {"name": "utility", "device": "gpu-vulkan", "port": 8082},
            {},
        )
        assert written == ["utility"]


class TestPreflightDoesNotDisplaceTheSanityGate:
    """#1922 composition. Preflight is a *cheap, static* admission check; it
    proves nothing about what the backend emits. The output-sanity readiness
    probe stays the thing that decides READY, on the Vulkan lane exactly as on
    every other lane — so a slot that clears preflight can still fail readiness
    with a garbage verdict."""

    def test_the_vulkan_lane_is_not_exempted_from_the_readiness_probe(self) -> None:
        import inspect

        from hal0.slots import output_sanity

        source = inspect.getsource(output_sanity)
        for token in ("gpu-vulkan", "vulkan_capable_refs", "vulkan_radv"):
            assert token not in source, (
                f"output_sanity references {token!r} — the readiness gate must stay "
                "lane-blind so a re-enabled Vulkan slot is probed like any other"
            )

    def test_switching_a_rocm_slot_onto_the_vulkan_lane_clears_both_gates(self, tmp_path) -> None:
        """The documented switch path, end to end at the two gates it crosses.

        An operator moving a slot from ROCm to Vulkan (dashboard Device
        picker, or ``hal0 slot edit <slot> --hardware vulkan``) is the
        first-class path now that the §3-C perf row measures Vulkan ahead on
        both metrics. What that switch must NOT do is buy speed by skipping a
        check: the switched slot passes preflight on a validated image, and is
        then held to exactly the same output-sanity verdict as before.
        """
        from hal0.slots.output_sanity import classify

        before = dict(name="agent", device="gpu-rocm", port=8081)
        after = {**before, "device": "gpu-vulkan"}

        # ROCm side: gated on the compute node, indifferent to the image.
        kfd = tmp_path / "kfd"
        kfd.write_text("")
        render_dir = _render_node(tmp_path)
        require_kfd_for_gpu_slot(
            before["name"],
            device=before["device"],
            runtime_lane="llama",
            kfd_path=str(kfd),
            dri_dir=render_dir,
            image=ADE07BA_REF,
            env={},
            amd_host=True,
        )

        # Vulkan side after the switch: no kfd needed, render node + a
        # validated image instead.
        require_kfd_for_gpu_slot(
            after["name"],
            device=after["device"],
            runtime_lane="llama",
            kfd_path=str(tmp_path / "gone"),
            dri_dir=render_dir,
            image=VULKAN_FIXED_IMAGE,
            env={},
            amd_host=True,
        )

        # And the readiness gate is unchanged by the switch — same verdicts on
        # the same text, because it never learns which lane it is probing.
        assert classify("Paris.").ok is True
        assert classify("根ovol主义的oksagoon相ufenroh隔抽取ynaaud").ok is False

    def test_switching_onto_the_vulkan_lane_is_refused_on_a_stale_image(self, tmp_path) -> None:
        """The other half of the documented path: the switch is not a way
        around the image gate. On an install still carrying the ade07ba
        lineage the operator is told so, by name, at the moment they try."""
        with pytest.raises(GpuPreflightError) as exc:
            require_kfd_for_gpu_slot(
                "agent",
                device="gpu-vulkan",
                runtime_lane="llama",
                kfd_path=str(tmp_path / "gone"),
                dri_dir=_render_node(tmp_path),
                image=ADE07BA_REF,
                env={},
                amd_host=True,
            )
        assert "1888" in str(exc.value)

    def test_a_preflight_pass_still_leaves_a_garbage_verdict_terminal(self, tmp_path) -> None:
        """Compose the two directly: the fixed image clears preflight, and the
        very same text #1888 produced still fails the sanity verdict."""
        from hal0.slots.output_sanity import classify

        require_kfd_for_gpu_slot(
            "utility",
            device="gpu-vulkan",
            runtime_lane="llama",
            kfd_path=str(tmp_path / "kfd"),
            dri_dir=_render_node(tmp_path),
            image=VULKAN_FIXED_IMAGE,
            env={},
            amd_host=True,
        )
        verdict = classify("根ovol主义的oksagoon相ufenroh隔抽取ynaaud")
        assert verdict.ok is False
        assert verdict.status == "incoherent_output"
