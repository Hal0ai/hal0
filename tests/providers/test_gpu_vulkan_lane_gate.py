"""#1948 Phase D — the Vulkan LLM lane is re-enabled, but only on a fixed image.

#1923 retired the lane outright: the release-pinned ROCmFPX runner
(``ade07ba``) is a single HIP+Vulkan build whose Vulkan backend emits invalid
tokens for every model (#1888), so ``gpu-vulkan`` named a lane no llama.cpp
slot could validly run and the guard refused it on every AMD host.

``ghcr.io/hal0ai/hal0-combined:0822`` restores a correct Vulkan backend
(validated on both a kfd-present and a kfd-ABSENT box — see the §3-C matrix on
#1948), so the lane comes back. What must never come back is the ability to
serve that lane from a KNOWN-BROKEN image: the guard is now device-aware —

* ``gpu-rocm``            → ``/dev/kfd`` required, semantics unchanged.
* ``gpu-vulkan`` + llama  → a render node the runner can open, AND a resolved
  image in :data:`VULKAN_CAPABLE_IMAGE_REFS`. No ``/dev/kfd`` requirement.
* ``gpu-vulkan`` + non-llama → untouched (#1941/#1952 semantics).

The image gate is the half that makes the re-enable safe; the #1922
output-sanity readiness gate (live on main) is the permanent net BEHIND it.
"""

from __future__ import annotations

import os

import pytest

from hal0.config.schema import (
    STALE_ROCMFPX_IMAGE_REFS,
    VULKAN_CAPABLE_IMAGE_REFS,
    VULKAN_FIXED_IMAGE,
)
from hal0.providers._gpu import (
    ENV_ALLOW_VULKAN_FALLBACK,
    GpuPreflightError,
    image_serves_vulkan_lane,
    render_node_present,
    require_kfd_for_gpu_slot,
)

#: The image whose Vulkan backend carries #1888 — the stand-in for "a runner
#: this lane must refuse", used everywhere below.
#:
#: A LITERAL, deliberately, and NOT ``DEFAULT_ROCMFPX_IMAGE``. The default pin
#: is a moving target: it is this ref on main today and becomes
#: ``VULKAN_FIXED_IMAGE`` the moment the repin lands, at which point every
#: "stale image is refused" assertion written against the default would invert
#: and start asserting that the fixed image is refused. The defect belongs to
#: THIS ref, so this ref is what the tests name.
ADE07BA_REF = "ghcr.io/hal0ai/hal0-rocmfpx:ade07ba"


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
    """An explicit allowlist, deliberately — NOT a tag ordering comparison."""

    def test_the_fixed_pin_is_capable(self) -> None:
        assert VULKAN_FIXED_IMAGE in VULKAN_CAPABLE_IMAGE_REFS
        assert image_serves_vulkan_lane(VULKAN_FIXED_IMAGE) is True

    def test_the_ade07ba_lineage_pin_is_not_capable(self) -> None:
        """#1888's carrier, named literally.

        This ref must never become capable, whatever the default pin is doing
        — which is the whole reason the gate is an allowlist of validated refs
        rather than a comparison against ``DEFAULT_ROCMFPX_IMAGE``.
        """
        assert ADE07BA_REF not in VULKAN_CAPABLE_IMAGE_REFS
        assert image_serves_vulkan_lane(ADE07BA_REF) is False

    def test_the_gate_does_not_key_off_the_moving_default_pin(self) -> None:
        """Whatever ``DEFAULT_ROCMFPX_IMAGE`` happens to be, capability is
        decided by membership and nothing else — so a pin bump can neither
        silently grant the lane nor silently revoke it."""
        from hal0.config import schema

        for ref in (ADE07BA_REF, VULKAN_FIXED_IMAGE, "ghcr.io/hal0ai/whatever:next"):
            assert image_serves_vulkan_lane(ref) is (ref in VULKAN_CAPABLE_IMAGE_REFS)
        assert schema.DEFAULT_ROCMFPX_IMAGE  # the constant exists; it is just not consulted

    @pytest.mark.parametrize("ref", sorted(STALE_ROCMFPX_IMAGE_REFS))
    def test_no_retired_runner_ref_can_serve_the_lane(self, ref: str) -> None:
        assert image_serves_vulkan_lane(ref) is False

    @pytest.mark.parametrize("ref", ["", None, "  ", "ghcr.io/someone/unknown:1"])
    def test_unknown_and_unresolved_refs_fail_closed(self, ref) -> None:
        """Fail-closed: an image nobody has validated is not a Vulkan image."""
        assert image_serves_vulkan_lane(ref) is False

    def test_the_capable_set_and_the_stale_set_are_disjoint(self) -> None:
        assert not (VULKAN_CAPABLE_IMAGE_REFS & STALE_ROCMFPX_IMAGE_REFS)


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

    def test_false_when_the_node_exists_but_is_not_openable(self, monkeypatch, tmp_path) -> None:
        """Same false-pass shape ``kfd_present`` guards against: an LXC
        passthrough with a mis-mapped gid leaves the node visible but
        unopenable by the runner identity.

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
        assert render_node_present(str(dri)) is False


class TestVulkanLaneGuard:
    """``gpu-vulkan`` + the llama lane on an AMD host."""

    def test_a_stale_image_is_refused_at_preflight(self, tmp_path) -> None:
        """The whole point: a box with a perfectly good render node still may
        NOT serve the Vulkan lane from the ade07ba lineage."""
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
        assert VULKAN_FIXED_IMAGE in msg  # name the way out

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
        for token in ("gpu-vulkan", "VULKAN_CAPABLE_IMAGE_REFS", "vulkan_radv"):
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
        require_kfd_for_gpu_slot(
            before["name"],
            device=before["device"],
            runtime_lane="llama",
            kfd_path=str(kfd),
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
            dri_dir=_render_node(tmp_path),
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
