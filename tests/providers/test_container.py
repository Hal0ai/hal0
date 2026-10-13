"""Unit tests for ``hal0.providers.container.ContainerProvider``.

Issue #655 — tracer bullet: ContainerProvider unit-render + control-plane.

Covers:
  * _render_quadlet_from_plan produces the expected Quadlet .container keys +
    Exec= argv (flags merged from profile, identical-path /mnt/ai-models:ro,z
    mount, loopback PublishPort, numeric GroupAdd, apparmor/seccomp unconfined)
  * the MTP draft bundle (profile layer) and draft device (slot layer)
  * ContainerProvider.container_spec returns a RuntimeLaunchPlan with correct
    image, command, mounts, and security opts
  * load_sync / unload_sync call the right systemctl commands (mocked)
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest

from hal0.agents.anchor_window import HERMES_MINIMUM_CONTEXT_LENGTH
from hal0.config.schema import (
    FAMILY_DEFAULTS,
    MTP_DRAFT_BUNDLE,
    SEED_PROFILES,
    ProfileConfig,
    family_flags,
    model_family,
    mtp_draft_device,
)
from hal0.errors import BadRequest, UnprocessableEntity
from hal0.providers import container as _container_mod
from hal0.providers._gpu import resolve_gpu_group_ids
from hal0.providers.base import RuntimeLaunchPlan
from hal0.providers.container import (
    _CTX_DENSE_CAP,
    _CTX_SAFE_FALLBACK,
    _MODEL_STORE_MOUNT,
    _UNIT_START_TIMEOUT_S,
    _UNIT_STOP_TIMEOUT_S,
    ContainerProvider,
    _best_effort_model_info,
    _container_runtime,
    _image_mismatch,
    _llama_argv_segments,
    _llama_launch_plan,
    _loopback_fence_command,
    _render_quadlet_from_plan,
    _resolve_model_path,
    profile_context,
    resolved_command_for_slot,
    slot_effective_context,
)
from hal0.registry.model import Model
from hal0.slots.argv import resolve_argv
from hal0.system.seam import SeamTimeout

# Podman is the only supported runtime under Quadlet; the shims below ignore
# ``runtime_bin`` (Quadlet doesn't put the runtime binary in the unit), but the
# param is kept so existing call sites need no edit.
_TEST_RUNTIME = "/usr/bin/podman"
_TEST_IMAGE = "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server"


def _render_from_plan(token, plan, *, runtime_bin=None, publish_host="127.0.0.1"):
    """Test shim: render a plan to Quadlet ``.container`` text (was ``_render_unit_from_plan``)."""
    return _render_quadlet_from_plan(token, plan, publish_host=publish_host)


def _render_llama(
    token,
    image,
    port,
    model_path,
    flags_str,
    *,
    runtime_bin=None,
    device_paths=None,
    context_size=None,
    extra_args=None,
    model_alias=None,
    publish_host="127.0.0.1",
    autoload=True,
    mtp=False,
    draft_device=None,
):
    """Test shim: build the llama launch plan, then render it to Quadlet text.

    ADR-0029: the profile is the whole tune, so ``flags_str`` (and any
    ``extra_args`` a test appends) ride the profile layer of the argv. An
    unset ``context_size`` stands for an unconfigured window (the 8192 floor).
    """
    # Resolve through the container module so tests patching
    # ``hal0.providers.container.resolve_gpu_device_paths`` take effect.
    devices = (
        device_paths if device_paths is not None else _container_mod.resolve_gpu_device_paths()
    )
    merged_tune = " ".join(p for p in ((flags_str or "").strip(), (extra_args or "").strip()) if p)
    plan = _llama_launch_plan(
        image=image,
        port=port,
        model_path=model_path,
        devices=list(devices),
        group_ids=[str(g) for g in resolve_gpu_group_ids()],
        context_size=context_size if context_size is not None else _CTX_SAFE_FALLBACK,
        model_alias=model_alias,
        mtp=mtp,
        profile_flags=merged_tune,
        draft_device=draft_device,
    )
    return _render_quadlet_from_plan(token, plan, publish_host=publish_host, autoload=autoload)


def _exec_line(unit_text: str) -> str:
    """Return the ``Exec=`` value (the in-container argv) from Quadlet unit text."""
    for line in unit_text.splitlines():
        if line.startswith("Exec="):
            return line[len("Exec=") :]
    raise AssertionError("Exec= not found in unit text")


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _moe_profile() -> ProfileConfig:
    return ProfileConfig(
        flags="-fa on -ctk q8_0 -ctv q8_0 -b 512 -ub 512 --parallel 1 --no-mmap",
        mtp="off",
    )


def _mtp_profile() -> ProfileConfig:
    return ProfileConfig(
        flags="-fa on -ctk q8_0 -ctv q8_0 -b 512 -ub 512 --parallel 1 --no-mmap",
        mtp="auto",
    )


def _slot_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "test-container",
        "port": 8095,
        "profile": "rocm",
        "runtime": "container",
        "device": "gpu-rocm",
        "model": {"default": "chadrock-35b-ace-saber-imatrix-q4_k_xl-00001-of-00002.gguf"},
    }
    base.update(overrides)
    return base


def _model_info(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "path": "/mnt/ai-models/chadrock-35b-ace-saber-imatrix-q4_k_xl-00001-of-00002.gguf",
        "_model_key": "chadrock-35b-ace-saber",
    }
    base.update(overrides)
    return base


# ── MTP draft bundle (ADR-0029: profile layer + slot-layer draft device) ─────


class TestMtpDraftBundle:
    def test_bundle_constant_is_the_draft_mtp_tune_without_a_device(self) -> None:
        assert "--spec-type draft-mtp" in MTP_DRAFT_BUNDLE
        # The draft device is placement: the slot layer derives it.
        assert "--spec-draft-device" not in MTP_DRAFT_BUNDLE

    @pytest.mark.parametrize(
        ("backend", "device"),
        [("rocm", "ROCm0"), ("vulkan", "Vulkan0"), ("cuda", "CUDA0"), ("ROCM", "ROCm0")],
    )
    def test_draft_device_follows_the_slot_backend(self, backend: str, device: str) -> None:
        assert mtp_draft_device(backend) == device

    def test_draft_device_defaults_to_rocm0_for_an_unknown_backend(self) -> None:
        assert mtp_draft_device(None) == "ROCm0"
        assert mtp_draft_device("cpu") == "ROCm0"

    def test_no_mtp_means_no_bundle_and_no_draft_device(self) -> None:
        segs = dict(
            _llama_argv_segments(
                port=8081,
                model_path="/m.gguf",
                context_size=8192,
                profile_flags=_moe_profile().flags,
                draft_device="ROCm0",
            )
        )
        assert "--spec-type" not in segs["profile"]
        assert "--spec-draft-device" not in segs["slot"]

    def test_bundle_rides_the_profile_layer_before_the_profile_flags(self) -> None:
        segs = dict(
            _llama_argv_segments(
                port=8081,
                model_path="/m.gguf",
                context_size=8192,
                mtp=True,
                profile_flags="-fa on",
                draft_device="Vulkan0",
            )
        )
        profile = segs["profile"]
        assert profile[: len(shlex.split(MTP_DRAFT_BUNDLE))] == shlex.split(MTP_DRAFT_BUNDLE)
        assert profile[-2:] == ["-fa", "on"]
        assert segs["slot"][-2:] == ["--spec-draft-device", "Vulkan0"]

    def test_profile_spec_draft_value_wins_over_the_bundle_default(self) -> None:
        argv = resolve_argv(
            _llama_argv_segments(
                port=8081,
                model_path="/m.gguf",
                context_size=8192,
                mtp=True,
                profile_flags="--spec-draft-n-max 8",
                draft_device="ROCm0",
            )
        ).argv
        assert argv.count("--spec-draft-n-max") == 1
        assert argv[argv.index("--spec-draft-n-max") + 1] == "8"


# ── Runtime probe (_container_runtime) ─────────────────────────────────────────


class TestContainerRuntimeProbe:
    """``_container_runtime`` resolves podman wherever PATH puts it (snap,
    /usr/local/bin, nix, ...). Docker is unsupported under Quadlet — the fallback
    is gone; only ``/usr/bin/podman`` and a bare ``podman`` PATH lookup remain."""

    def test_env_override_wins_over_everything(self, monkeypatch) -> None:
        monkeypatch.setenv("HAL0_CONTAINER_RUNTIME", "/opt/custom/podman")
        monkeypatch.setattr("hal0.providers.container.shutil.which", lambda _c: "/usr/bin/podman")
        assert _container_runtime() == "/opt/custom/podman"

    def test_prefers_absolute_usr_bin_podman(self, monkeypatch) -> None:
        monkeypatch.delenv("HAL0_CONTAINER_RUNTIME", raising=False)
        monkeypatch.setattr(
            "hal0.providers.container.shutil.which",
            lambda c: c if c == "/usr/bin/podman" else None,
        )
        assert _container_runtime() == "/usr/bin/podman"

    def test_falls_back_to_bare_podman_on_path(self, monkeypatch) -> None:
        """podman installed somewhere other than /usr/bin/ (snap, nix, ...)
        must still resolve via a bare PATH lookup — a pinned absolute-path
        check misses it entirely otherwise."""
        monkeypatch.delenv("HAL0_CONTAINER_RUNTIME", raising=False)

        def fake_which(c: str) -> str | None:
            if c == "/usr/bin/podman":
                return None
            if c == "podman":
                return "/snap/bin/podman"
            return None

        monkeypatch.setattr("hal0.providers.container.shutil.which", fake_which)
        assert _container_runtime() == "/snap/bin/podman"

    def test_docker_is_not_a_candidate(self, monkeypatch) -> None:
        """Docker is unsupported: even when only docker is on PATH, resolution
        must NOT pick it up — it raises instead (podman-only under Quadlet)."""
        monkeypatch.delenv("HAL0_CONTAINER_RUNTIME", raising=False)

        def fake_which(c: str) -> str | None:
            return "/usr/local/bin/docker" if c == "docker" else None

        monkeypatch.setattr("hal0.providers.container.shutil.which", fake_which)
        try:
            _container_runtime()
        except RuntimeError as exc:
            assert "no podman runtime found" in str(exc)
        else:
            raise AssertionError("docker must not be a runtime candidate")

    def test_raises_when_no_runtime_found_anywhere(self, monkeypatch) -> None:
        monkeypatch.delenv("HAL0_CONTAINER_RUNTIME", raising=False)
        monkeypatch.setattr("hal0.providers.container.shutil.which", lambda _c: None)
        try:
            _container_runtime()
        except RuntimeError as exc:
            assert "no podman runtime found" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


# ── Unit rendering ────────────────────────────────────────────────────────────


class TestRenderUnit:
    """The Quadlet ``.container`` renderer produces correct declarative keys +
    the in-container ``Exec=`` argv (the podman-run ExecStart string is gone)."""

    def test_image_and_exec_present(self) -> None:
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        assert f"Image={_TEST_IMAGE}" in unit.splitlines()
        assert "--port 8095" in _exec_line(unit)

    def test_container_name_key(self) -> None:
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        assert "ContainerName=hal0-slot-test-slot" in unit.splitlines()

    def test_identical_path_mount_readonly(self, monkeypatch) -> None:
        """Model store mounted identical-path, read-only, SELinux-relabelled."""
        monkeypatch.setenv("HAL0_MODEL_STORE", _MODEL_STORE_MOUNT)  # pin the default
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        assert any(
            ln
            in {
                f"Volume={_MODEL_STORE_MOUNT}:{_MODEL_STORE_MOUNT}:ro",
                f"Volume={_MODEL_STORE_MOUNT}:{_MODEL_STORE_MOUNT}:ro,z",
            }
            for ln in unit.splitlines()
        )

    def test_mount_honours_custom_model_store(self, monkeypatch) -> None:
        """A custom HAL0_MODEL_STORE is what the slot bind-mounts — so a model
        dir outside /mnt/ai-models is visible inside the container (the Fedora
        'No such file or directory' bug). Regression guard for #768."""
        custom = "/srv/models"
        monkeypatch.setenv("HAL0_MODEL_STORE", custom)
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("agent0", _TEST_IMAGE, 8095, f"{custom}/Qwen3.6-35B.gguf", flags)
        assert f"Volume={custom}:{custom}:ro,z" in unit.splitlines()
        assert not any(ln.startswith("Volume=/mnt/ai-models") for ln in unit.splitlines())

    def test_render_mounts_store_and_pull_root(self, monkeypatch) -> None:
        """O25: when store != pull_root, BOTH roots render a Volume so a model
        file under the external pull_root tree is reachable in-container."""
        monkeypatch.setattr(
            _container_mod,
            "model_mount_roots",
            lambda: ["/var/lib/hal0/models", "/mnt/ai-models"],
        )
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("brain", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        vols = [ln for ln in unit.splitlines() if ln.startswith("Volume=")]
        assert any(
            ln
            in {
                "Volume=/mnt/ai-models:/mnt/ai-models:ro",
                "Volume=/mnt/ai-models:/mnt/ai-models:ro,z",
            }
            for ln in vols
        )
        assert "Volume=/var/lib/hal0/models:/var/lib/hal0/models:ro,z" in vols

    def test_render_dedups_store_equals_pull_root(self, monkeypatch) -> None:
        """store == pull_root → exactly one model-store Volume (no dup)."""
        monkeypatch.setattr(
            _container_mod,
            "model_mount_roots",
            lambda: ["/mnt/ai-models"],
        )
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("brain", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        vols = [ln for ln in unit.splitlines() if ln.startswith("Volume=")]
        assert vols in (
            ["Volume=/mnt/ai-models:/mnt/ai-models:ro"],
            ["Volume=/mnt/ai-models:/mnt/ai-models:ro,z"],
        )

    def test_loopback_port_publish(self) -> None:
        """Port must be published on 127.0.0.1 only (not LAN-exposed)."""
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        assert "PublishPort=127.0.0.1:8095:8095" in unit.splitlines()

    def test_healthcheck_targets_slot_port_not_image_default(self) -> None:
        """The toolbox image bakes a HEALTHCHECK probing a hardcoded :8080, but
        hal0 runs llama-server on the slot port — so the unit must override
        HealthCmd= to probe the real port (else `podman ps` shows a permanent
        false (unhealthy))."""
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        lines = unit.splitlines()
        health_cmd = [ln for ln in lines if ln.startswith("HealthCmd=")]
        assert health_cmd, f"no HealthCmd override in: {lines}"
        assert "127.0.0.1:8095/health" in health_cmd[0], health_cmd[0]
        assert ":8080/" not in health_cmd[0], "must not probe the image's :8080 default"
        assert any(ln.startswith("HealthStartPeriod=") for ln in lines), lines

    def test_device_passthrough(self) -> None:
        """Default device source is resolve_gpu_device_paths(); each node is
        passed explicitly via AddDevice=, never the bare /dev/dri directory."""
        profile = _moe_profile()
        flags = profile.flags
        with patch(
            "hal0.providers.container.resolve_gpu_device_paths",
            return_value=["/dev/kfd", "/dev/dri/renderD128"],
        ):
            unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        lines = unit.splitlines()
        assert "AddDevice=/dev/kfd" in lines
        assert "AddDevice=/dev/dri/renderD128" in lines
        assert "AddDevice=/dev/dri" not in lines

    def test_explicit_device_nodes_emitted_no_bare_dri_dir(self) -> None:
        """With explicit device_paths, the unit passes each node verbatim and
        never the bare /dev/dri directory (which podman cannot recurse)."""
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama(
            "test-slot",
            _TEST_IMAGE,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            device_paths=["/dev/kfd", "/dev/dri/renderD128"],
        )
        lines = unit.splitlines()
        assert "AddDevice=/dev/kfd" in lines
        assert "AddDevice=/dev/dri/renderD128" in lines
        assert "AddDevice=/dev/dri" not in lines

    def test_model_alias_in_exec(self) -> None:
        """The container must advertise the hal0 registry model id via --alias,
        else the dispatcher can't match hal0/* names."""
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama(
            "test-slot",
            _TEST_IMAGE,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            device_paths=["/dev/kfd", "/dev/dri/renderD128"],
            model_alias="qwopus3.6-27b-v2",
        )
        tokens = shlex.split(_exec_line(unit))
        assert "--alias" in tokens
        assert tokens[tokens.index("--alias") + 1] == "qwopus3.6-27b-v2"

    def test_ctx_size_in_exec(self) -> None:
        """The slot's context_size must reach the container as --ctx-size,
        else llama-server boots at its 4096 default (severe ctx regression)."""
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama(
            "test-slot",
            _TEST_IMAGE,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            device_paths=["/dev/kfd", "/dev/dri/renderD128"],
            context_size=131072,
        )
        tokens = shlex.split(_exec_line(unit))
        assert "--ctx-size" in tokens
        assert tokens[tokens.index("--ctx-size") + 1] == "131072"

    def test_append_list_profile_flag_reaches_exec(self) -> None:
        """An append-list profile flag (``--override-kv``) reaches the Exec=
        argv verbatim, value and all."""
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama(
            "test-slot",
            _TEST_IMAGE,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            device_paths=["/dev/kfd", "/dev/dri/renderD128"],
            extra_args="--override-kv tokenizer.ggml.add_bos=bool:false",
        )
        tokens = shlex.split(_exec_line(unit))
        assert "--override-kv" in tokens
        assert "tokenizer.ggml.add_bos=bool:false" in tokens

    def test_json_extra_arg_preserves_quoting(self) -> None:
        """A space-less JSON profile-flag value must survive systemd's Exec= parser
        intact (rework board bug).  ``--chat-template-kwargs
        '{"enable_thinking":false}'`` shlex-splits into a bare, space-less
        ``{"enable_thinking":false}`` token; the old emitter only quoted tokens
        containing a space, so the double-quotes were emitted un-escaped and
        systemd stripped them to ``{enable_thinking:false}`` → llama-server JSON
        parse error → the slot never starts.  The Quadlet Exec= emitter must
        ``shlex.quote`` every token so the JSON reaches the process byte-for-byte."""
        profile = _moe_profile()
        flags = profile.flags
        json_kwargs = '{"enable_thinking":false}'
        unit = _render_llama(
            "test-slot",
            _TEST_IMAGE,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            device_paths=["/dev/kfd", "/dev/dri/renderD128"],
            extra_args=f"--chat-template-kwargs '{json_kwargs}'",
        )
        exec_line = _exec_line(unit)
        # The raw line must carry the (now single-quoted) JSON with its double
        # quotes intact — never the double-quote-stripped form.
        assert json_kwargs in exec_line
        assert "{enable_thinking:false}" not in exec_line
        # And a shell/systemd-style re-parse recovers the exact JSON token.
        tokens = shlex.split(exec_line)
        assert "--chat-template-kwargs" in tokens
        assert tokens[tokens.index("--chat-template-kwargs") + 1] == json_kwargs

    def test_security_opts(self) -> None:
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        # Uniform render (O8+O11): security opts ride PodmanArgs= on every
        # substrate — native SecurityOpt= keys are deliberately not emitted.
        assert "--security-opt apparmor=unconfined" in unit
        assert "--security-opt seccomp=unconfined" in unit
        assert "SecurityOpt=" not in unit

    def test_model_arg_in_exec(self) -> None:
        profile = _moe_profile()
        flags = profile.flags
        model_path = "/mnt/ai-models/model.gguf"
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, model_path, flags)
        # llama-server uses space-separated --model PATH (not --model=PATH)
        assert f"--model {model_path}" in _exec_line(unit)

    def test_profile_flags_in_exec(self) -> None:
        """Bench-tuned profile flags must appear in the Exec= argv."""
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        exec_line = _exec_line(unit)
        assert "-fa" in exec_line
        assert "--no-mmap" in exec_line
        assert "-ctk" in exec_line

    def test_mtp_flags_in_exec_when_mtp_true(self) -> None:
        profile = _mtp_profile()
        unit = _render_llama(
            "test-slot",
            _TEST_IMAGE,
            8095,
            "/mnt/ai-models/model.gguf",
            profile.flags,
            mtp=True,
            draft_device="ROCm0",
        )
        exec_line = _exec_line(unit)
        assert "--spec-type draft-mtp" in exec_line
        assert "--spec-draft-device ROCm0" in exec_line

    def test_unit_has_expected_sections(self) -> None:
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        assert "[Unit]" in unit
        assert "[Container]" in unit
        assert "[Service]" in unit
        assert "[Install]" in unit
        # Quadlet owns crash recovery now (was the hand-rendered Restart=no).
        assert "Restart=always" in unit.splitlines()

    def test_install_stanza_present_by_default(self) -> None:
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        assert "[Install]" in unit
        assert "WantedBy=hal0.target" in unit.splitlines()

    def test_autoload_false_omits_install_stanza(self) -> None:
        """autoload=false → no [Install] → nothing boot-starts the slot.

        The unit must remain manually startable: [Service]/Restart survive.
        """
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama(
            "test-slot",
            _TEST_IMAGE,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            autoload=False,
        )
        assert "[Install]" not in unit
        assert "WantedBy=hal0.target" not in unit
        assert "[Service]" in unit
        assert "Restart=always" in unit.splitlines()

    def test_startlimit_keys_land_in_unit_section_not_service(self) -> None:
        """StartLimit*= are systemd.unit(5) [Unit] directives, not [Service].

        Emitting them under [Service] makes systemd log "Unknown key" and
        silently drop them, disabling the slot's restart rate-limiting
        (install-validation m2, halo150, 2026-07-19). Assert they render in
        the [Unit] block and never leak into [Service].
        """
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        unit_section, _, remainder = unit.partition("[Container]")
        service_section = remainder.partition("[Service]")[2].partition("[Install]")[0]
        assert "StartLimitIntervalSec=1800" in unit_section
        assert "StartLimitBurst=10" in unit_section
        assert "StartLimitIntervalSec" not in service_section
        assert "StartLimitBurst" not in service_section

    def test_restart_prevent_exit_status_lands_in_service_section(self) -> None:
        """#2037: the runner entrypoint translates died-during-load into exit
        64; ``RestartPreventExitStatus=`` lets systemd fail-fast a doomed
        model instead of burning the whole backoff ramp. Old images never emit
        64, so the directive is inert against them — safe version skew.
        """
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        service_section = unit.partition("[Service]")[2].partition("[Install]")[0]
        assert "RestartPreventExitStatus=64 132" in service_section

    def test_sigterm_stop_is_a_success_exit_status(self) -> None:
        """#2130: `systemctl stop` sends SIGTERM and podman propagates the
        container's graceful shutdown as exit CODE 143 — without
        ``SuccessExitStatus=143`` systemd records ``Failed with result
        'exit-code'`` and every deliberately-stopped slot (the documented
        ``migrate-flags --stop-services`` path included) parks in ``failed``,
        which `hal0 slot list` painted as a red ERROR on a healthy box.
        """
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        service_section = unit.partition("[Service]")[2].partition("[Install]")[0]
        assert "SuccessExitStatus=143" in service_section.splitlines()

    def test_sigill_is_terminal_host_side(self) -> None:
        """#2126: exit 64 only exists inside images that SHIP the runner
        entrypoint. The `cpu` runner resolves to a foreign GPU toolbox, which
        has no entrypoint to translate anything — podman propagates 128+signal
        and systemd sees a plain ``status=132``. Naming 132 host-side is the
        version-skew-proof half, and it is the reason #2126's brain slot
        crash-looped instead of parking.
        """
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        service_section = unit.partition("[Service]")[2].partition("[Install]")[0]
        statuses = [
            line.partition("=")[2].split()
            for line in service_section.splitlines()
            if line.startswith("RestartPreventExitStatus=")
        ]
        assert statuses == [["64", "132"]]

    def test_transient_signal_deaths_keep_the_restart_runway(self) -> None:
        """The line #2126 does NOT cross host-side. ``RestartPreventExitStatus``
        is unconditional — it applies to a slot that has served for days, not
        just one dying at load. SIGKILL (137) is the OOM-killer, and SIGSEGV
        (139) / SIGABRT (134) can be a single bad request post-load; all three
        can recover on a restart, so they must not be listed here. Their
        load-phase (deterministic) case is handled in the runner entrypoint.
        """
        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        service_section = unit.partition("[Service]")[2].partition("[Install]")[0]
        line = next(
            ln for ln in service_section.splitlines() if ln.startswith("RestartPreventExitStatus=")
        )
        statuses = line.partition("=")[2].split()
        for transient in ("134", "137", "139", "143"):
            assert transient not in statuses

    def test_numeric_group_add_present(self) -> None:
        """GroupAdd= must use numeric GIDs (toolbox images lack group names)."""
        from hal0.providers._gpu import resolve_gpu_group_ids

        profile = _moe_profile()
        flags = profile.flags
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        # Uniform render: numeric GIDs ride PodmanArgs=--group-add (O8+O11).
        for gid in resolve_gpu_group_ids():
            assert f"--group-add {gid}" in unit, f"GID {gid} missing: {unit}"
        assert "GroupAdd=" not in unit


# ── ContainerProvider.container_spec ─────────────────────────────────────────


class TestContainerSpec:
    def _provider(self) -> ContainerProvider:
        return ContainerProvider()

    def _build_spec(self, cfg: dict[str, Any] | None = None):
        provider = self._provider()
        profile = _moe_profile()
        with patch(
            "hal0.providers.container._resolve_profile",
            return_value=profile,
        ):
            return provider.container_spec(
                cfg or _slot_cfg(),
                _model_info(),
            )

    def test_image_ignores_profile_pin_uses_hw_default(self) -> None:
        # spec-hw-slot-ownership §3: _TEST_IMAGE is DELETED from the chain.
        # With no slot image_pin/binary, the image is the HW-gated default —
        # NOT the (now-ignored) _TEST_IMAGE.
        from hal0.runners import RUNNER_IMAGES, resolve_runner_image

        spec = self._build_spec()
        assert spec.image != _TEST_IMAGE
        assert spec.image == resolve_runner_image(RUNNER_IMAGES["standard"])

    def test_model_arg_in_command(self) -> None:
        spec = self._build_spec()
        # llama-server uses space-separated args: --model PATH
        # So command is [..., "--model", "/mnt/ai-models/..."]
        assert "--model" in spec.command
        model_idx = spec.command.index("--model")
        model_val = spec.command[model_idx + 1] if model_idx + 1 < len(spec.command) else ""
        assert "/mnt/ai-models/" in model_val

    def test_mount_identical_path(self, monkeypatch) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", _MODEL_STORE_MOUNT)  # pin the default
        spec = self._build_spec()
        # Identical-path model-store mount, read-only via first-class Mount flag.
        store_mount = next(m for m in spec.mounts if m.source == _MODEL_STORE_MOUNT)
        assert store_mount.target == _MODEL_STORE_MOUNT
        assert store_mount.read_only is True

    def test_devices_present(self) -> None:
        with (
            patch(
                "hal0.providers.container.resolve_gpu_device_paths",
                return_value=["/dev/kfd", "/dev/dri/renderD128"],
            ),
            # GPU device nodes are existence-filtered at the container_spec call
            # site; force present so the CI host (no /dev/kfd) still keeps them.
            patch("hal0.providers.container._dev_node_exists", return_value=True),
        ):
            spec = self._build_spec()
        assert spec.devices == ["/dev/kfd", "/dev/dri/renderD128"]

    def test_security_opts(self) -> None:
        spec = self._build_spec()
        assert "apparmor=unconfined" in spec.security_opt
        assert "seccomp=unconfined" in spec.security_opt

    def test_loopback_publish_derived_from_port(self) -> None:
        """Loopback publish is derived from port + empty network_mode by the
        renderer (declarative)."""
        spec = self._build_spec()
        assert spec.port == 8095
        assert spec.network_mode == ""
        unit = _render_from_plan("test-slot", spec)
        assert "PublishPort=127.0.0.1:8095:8095" in unit.splitlines()

    def test_publish_host_default_is_loopback(self) -> None:
        """Absent a publish_host override the renderer keeps the safe default."""
        spec = self._build_spec()
        unit = _render_from_plan("test-slot", spec)
        assert "PublishPort=127.0.0.1:8095:8095" in unit.splitlines()

    def test_publish_host_override_widens_bind(self) -> None:
        """[slots].publish_host=0.0.0.0 → the slot publishes on all interfaces."""
        spec = self._build_spec()
        unit = _render_from_plan("test-slot", spec, publish_host="0.0.0.0")
        assert "PublishPort=0.0.0.0:8095:8095" in unit.splitlines()
        assert "PublishPort=127.0.0.1:8095:8095" not in unit.splitlines()

    def test_network_mode_empty(self) -> None:
        """network_mode must be empty (not 'host') so loopback publish is used."""
        spec = self._build_spec()
        assert spec.network_mode == ""

    def test_expected_argv_uses_launch_plan_context_derive(self) -> None:
        """#863: drift's rendered side must match the real load-path derive."""
        provider = self._provider()
        with patch(
            "hal0.providers.container._resolve_profile",
            return_value=_moe_profile(),
        ):
            argv = provider.expected_argv(
                _slot_cfg(),
                _model_info(metadata={"context_length": 131072}),
            )

        assert argv is not None
        assert argv[argv.index("--ctx-size") + 1] == "32768"

    def test_expected_argv_emits_config_drift_watched_flag_spellings(self) -> None:
        """#863: drift watches exact argv spellings, so renderer renames must fail.
        ADR-0029: -b/-ub ride the profile's flags."""
        provider = self._provider()
        with patch(
            "hal0.providers.container._resolve_profile",
            return_value=_moe_profile(),
        ):
            argv = provider.expected_argv(
                _slot_cfg(model={"default": "chadrock-35b-ace-saber"}),
                _model_info(),
            )

        assert argv is not None
        assert "--model" in argv
        assert "--alias" in argv
        assert "--ctx-size" in argv
        assert "-b" in argv
        assert "-ub" in argv
        assert "-c" not in argv
        assert "--batch-size" not in argv
        assert "--ubatch-size" not in argv


# ── load_sync / unload_sync systemd interaction ───────────────────────────────


class TestLoadSync:
    """Verify load_sync writes unit and calls systemctl correctly."""

    def test_load_sync_calls_systemctl_restart(self, tmp_path: Path) -> None:
        profile = _moe_profile()
        provider = ContainerProvider()

        calls_made: list[list[str]] = []

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            calls_made.append(list(args))
            m = MagicMock()
            m.returncode = 0
            return m

        with (
            patch("hal0.providers.container._resolve_profile", return_value=profile),
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=tmp_path / "test.service"),
        ):
            provider.load_sync(
                {"name": "test-container", "port": 8095, "profile": "rocm"},
                {"path": "/mnt/ai-models/model.gguf", "_model_key": "model"},
            )

        cmds = [" ".join(c) for c in calls_made]
        assert any("daemon-reload" in c for c in cmds), f"daemon-reload not in {cmds}"
        assert any("restart" in c for c in cmds), f"restart not in {cmds}"
        assert (tmp_path / "test.service").exists()

    def test_load_sync_bounds_every_systemctl_call(self, tmp_path: Path) -> None:
        """#1869: no ``_run`` call in the load path may go through with
        ``timeout=None`` — a wedged ``daemon-reload``/``restart`` used to block
        the load path forever. ``restart`` gets the wider spawn budget
        (``_UNIT_START_TIMEOUT_S``); the fast admin calls around it get
        ``_UNIT_STOP_TIMEOUT_S``."""
        profile = _moe_profile()
        provider = ContainerProvider()

        calls: list[tuple[list[str], float | None]] = []

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            calls.append((list(args), timeout))
            m = MagicMock()
            m.returncode = 0
            return m

        with (
            patch("hal0.providers.container._resolve_profile", return_value=profile),
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=tmp_path / "test.service"),
        ):
            provider.load_sync(
                {"name": "test-container", "port": 8095, "profile": "rocm"},
                {"path": "/mnt/ai-models/model.gguf", "_model_key": "model"},
            )

        assert calls, "load_sync made no _run calls"
        assert all(timeout is not None for _args, timeout in calls), calls
        for args, timeout in calls:
            if "restart" in args:
                assert timeout == _UNIT_START_TIMEOUT_S, (args, timeout)
            else:
                assert timeout == _UNIT_STOP_TIMEOUT_S, (args, timeout)

    def test_load_sync_lets_a_seam_timeout_on_restart_propagate_untouched(
        self, tmp_path: Path
    ) -> None:
        """A wedged ``systemctl restart`` raises :class:`SeamTimeout` (#1869) —
        a distinct failure mode from ``CalledProcessError`` (#1424's
        ``SlotSpawnFailed`` wrap), so ``_write_and_start_unit`` must NOT catch
        it: it propagates with its own ``slot.seam_timeout`` code straight
        through to ``SlotManager.load``'s generic ``except Exception -> ERROR``
        handling."""
        profile = _moe_profile()
        provider = ContainerProvider()

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            if "restart" in args:
                raise SeamTimeout(list(args), timeout or 0.0, "activating")
            m = MagicMock()
            m.returncode = 0
            return m

        with (
            patch("hal0.providers.container._resolve_profile", return_value=profile),
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=tmp_path / "test.service"),
            pytest.raises(SeamTimeout) as excinfo,
        ):
            provider.load_sync(
                {"name": "test-container", "port": 8095, "profile": "rocm"},
                {"path": "/mnt/ai-models/model.gguf", "_model_key": "model"},
            )

        assert excinfo.value.code == "slot.seam_timeout"
        assert "activating" in str(excinfo.value)

    def test_load_sync_threads_ctx_size_and_profile_tune(self, tmp_path: Path) -> None:
        """load_sync bakes the profile's window (base --ctx-size) AND the
        profile's flags into the rendered unit (ADR-0029: the profile is the
        whole tune)."""
        profile = ProfileConfig(flags="--override-kv k=bool:false -b 512", ctx_size=131072)
        provider = ContainerProvider()
        unit_file = tmp_path / "test.service"

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            m = MagicMock()
            m.returncode = 0
            return m

        with (
            patch("hal0.providers.container._resolve_profile", return_value=profile),
            patch(
                "hal0.providers.container.resolve_gpu_device_paths",
                return_value=["/dev/kfd", "/dev/dri/renderD128"],
            ),
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=unit_file),
        ):
            provider.load_sync(
                {
                    "name": "test-container",
                    "port": 8095,
                    "profile": "rocm",
                    "model": {"default": "model"},
                },
                {
                    "path": "/mnt/ai-models/model.gguf",
                    "_model_key": "model",
                    "metadata": {"context_length": 32768},
                },
            )

        unit = unit_file.read_text()
        # The profile's window reaches base, past the model's native window.
        assert "--ctx-size 131072" in unit
        assert "--override-kv k=bool:false" in unit  # profile tune reaches launch
        assert "-b 512" in unit

    def test_load_sync_refuses_an_unconverted_slot(self, tmp_path: Path) -> None:
        """A slot file still carrying a tuning key launches nothing: it fails
        with ``slot.unconverted`` instead of silently dropping the value."""
        provider = ContainerProvider()
        with (
            patch("hal0.providers.container._resolve_profile", return_value=_moe_profile()),
            patch.object(provider, "_run") as run,
            patch.object(provider, "_unit_path", return_value=tmp_path / "test.service"),
            pytest.raises(UnprocessableEntity) as excinfo,
        ):
            provider.load_sync(
                {
                    "name": "test-container",
                    "port": 8095,
                    "profile": "rocm",
                    "model": {"default": "model"},
                    "server": {"extra_args": "-ctk q8_0"},
                },
                {"path": "/mnt/ai-models/model.gguf", "_model_key": "model"},
            )
        assert excinfo.value.code == "slot.unconverted"
        assert "[server].extra_args" in excinfo.value.details["keys"]
        run.assert_not_called()
        assert not (tmp_path / "test.service").exists()

    def test_load_sync_advertises_model_id_alias(self, tmp_path: Path) -> None:
        """load_sync must pass the registry model id (model_info._model_key)
        as --alias so the dispatcher can route hal0/* names to the container."""
        profile = _moe_profile()
        provider = ContainerProvider()
        unit_file = tmp_path / "test.service"

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            m = MagicMock()
            m.returncode = 0
            return m

        with (
            patch("hal0.providers.container._resolve_profile", return_value=profile),
            patch(
                "hal0.providers.container.resolve_gpu_device_paths",
                return_value=["/dev/kfd", "/dev/dri/renderD128"],
            ),
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=unit_file),
        ):
            provider.load_sync(
                {"name": "agent", "port": 8101, "profile": "rocm"},
                {
                    "path": "/mnt/ai-models/m.gguf",
                    "_model_key": "chadrock-35b-ace-saber",
                },
            )

        assert "--alias chadrock-35b-ace-saber" in unit_file.read_text()

    def test_install_and_update_render_byte_identical_units(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """WS-J (#1103): install (``load_sync``) and update
        (``rerender_unit_sync``) render **byte-identical** unit files for the
        same slot config, because both go through the one renderer
        ``_render_quadlet_text``.

        Regression guard for the specific divergence WS-J removes: on a
        LAN-exposed box (``[slots].publish_host = 0.0.0.0``) the pre-fix update
        path dropped ``publish_host`` and silently narrowed the bind back to
        loopback — so a fresh box and an updated box ended up with different
        units for the same slot.
        """
        profile = ProfileConfig(flags="--override-kv k=bool:false", ctx_size=131072)
        slot_cfg = {
            "name": "test-container",
            "port": 8095,
            "profile": "rocm",
            "model": {"default": "m"},
        }
        model_info = {"path": "/mnt/ai-models/model.gguf", "_model_key": "m"}

        # LAN-exposed box: the operator widened the publish bind. Both render
        # paths must honour it identically.
        monkeypatch.setattr("hal0.providers.container._slot_publish_host", lambda: "0.0.0.0")

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            m = MagicMock()
            m.returncode = 0
            return m

        # ── fresh install: load_sync writes the unit (systemctl mocked) ──
        fresh_provider = ContainerProvider()
        fresh_unit = tmp_path / "fresh.service"
        with (
            patch("hal0.providers.container._resolve_profile", return_value=profile),
            patch(
                "hal0.providers.container.resolve_gpu_device_paths",
                return_value=["/dev/kfd", "/dev/dri/renderD128"],
            ),
            patch.object(fresh_provider, "_run", side_effect=fake_run),
            patch.object(fresh_provider, "_unit_path", return_value=fresh_unit),
        ):
            fresh_provider.load_sync(slot_cfg, model_info)
        fresh_text = fresh_unit.read_text()
        # Sanity: the widened bind actually rendered on the fresh install.
        assert "PublishPort=0.0.0.0:8095:8095" in fresh_text.splitlines()

        # ── updated box: a STALE unit already exists; rerender rewrites it ──
        upd_provider = ContainerProvider()
        upd_unit = tmp_path / "updated.service"
        upd_unit.write_text("# stale pre-update unit — forces a rewrite\n")
        with (
            patch("hal0.providers.container._resolve_profile", return_value=profile),
            patch(
                "hal0.providers.container.resolve_gpu_device_paths",
                return_value=["/dev/kfd", "/dev/dri/renderD128"],
            ),
            patch.object(upd_provider, "_unit_path", return_value=upd_unit),
        ):
            changed = upd_provider.rerender_unit_sync(slot_cfg, model_info)

        assert changed is True
        # The WS-J guarantee: byte-for-byte identical units.
        assert upd_unit.read_text() == fresh_text

    def test_resolved_command_includes_ctx_size(self) -> None:
        """The displayed resolved_command must show --ctx-size so it matches
        what actually launches.

        ``m`` is a registry miss and the profile sets no window, so the
        resolver lands on the 8192 safe floor. Preview and launch share
        ``profile_context``, so what an operator sees here is exactly what the
        unit gets."""
        profile = _moe_profile()
        cfg = {"profile": "rocm", "port": 8095, "model": {"default": "m"}}
        with patch("hal0.providers.container._resolve_profile", return_value=profile):
            argv = resolved_command_for_slot(cfg, model_path="/mnt/ai-models/m.gguf")
        assert argv is not None
        assert "--ctx-size" in argv
        assert argv[argv.index("--ctx-size") + 1] == "8192"
        assert argv[argv.index("--ctx-size") + 1] != "4096"  # chat@4096 incident

    def test_resolved_command_shows_the_profile_window(self) -> None:
        profile = ProfileConfig(flags="-fa on", ctx_size=65536)
        cfg = {"profile": "rocm", "port": 8095, "model": {"default": "m"}}
        with patch("hal0.providers.container._resolve_profile", return_value=profile):
            argv = resolved_command_for_slot(cfg, model_path="/mnt/ai-models/m.gguf")
        assert argv is not None
        assert argv[argv.index("--ctx-size") + 1] == "65536"

    def test_resolved_command_is_none_for_an_unconverted_slot(self) -> None:
        cfg = {"profile": "rocm", "port": 8095, "model": {"default": "m", "context_size": 4096}}
        with patch("hal0.providers.container._resolve_profile", return_value=_moe_profile()):
            assert resolved_command_for_slot(cfg, model_path="/mnt/ai-models/m.gguf") is None

    def test_resolved_command_includes_model_alias(self) -> None:
        """resolved_command shows --alias <model id> so it matches the unit."""
        profile = _moe_profile()
        cfg = {
            "profile": "rocm",
            "port": 8095,
            "model": {"default": "chadrock-35b-ace-saber"},
        }
        with patch("hal0.providers.container._resolve_profile", return_value=profile):
            argv = resolved_command_for_slot(cfg)
        assert argv is not None
        assert "--alias" in argv
        assert argv[argv.index("--alias") + 1] == "chadrock-35b-ace-saber"

    def test_unload_sync_calls_stop(self, tmp_path: Path) -> None:
        provider = ContainerProvider()
        unit_file = tmp_path / "hal0-slot@test-container.service"
        unit_file.write_text("[Unit]\n")

        calls_made: list[list[str]] = []
        timeouts: list[float | None] = []

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            calls_made.append(list(args))
            timeouts.append(timeout)
            m = MagicMock()
            m.returncode = 0
            return m

        with (
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=unit_file),
        ):
            provider.unload_sync({"name": "test-container"})

        cmds = [" ".join(c) for c in calls_made]
        assert any("stop" in c for c in cmds), f"stop not in {cmds}"
        # A deliberate stop must clear failed runtime state: the bounded stop
        # escalates to SIGKILL (exit 137) and daemon-reload does NOT drop a
        # failed unit from memory, so without reset-failed every cleanly
        # stopped slot rendered "crashed"/red on the dashboard.
        assert any("reset-failed" in c for c in cmds), f"reset-failed not in {cmds}"
        # Unit file must be deleted
        assert not unit_file.exists()
        # #1224: every teardown verb is bounded — an unbounded `systemctl stop`
        # against an already-failed unit is what wedged slot restart.
        assert timeouts and all(t == _UNIT_STOP_TIMEOUT_S for t in timeouts), timeouts

    def test_unload_sync_survives_a_stop_that_times_out(self, tmp_path: Path) -> None:
        """A wedged `systemctl stop` must not abort teardown (#1224).

        Removing the Quadlet source + daemon-reload drops the generated
        (possibly `failed`) `.service` and lets Quadlet reap the container, so
        the subsequent load still converges — the stop is best-effort.
        """
        provider = ContainerProvider()
        unit_file = tmp_path / "hal0-slot@test-container.container"
        unit_file.write_text("[Container]\n")

        calls_made: list[list[str]] = []

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            calls_made.append(list(args))
            if "stop" in args:
                raise subprocess.TimeoutExpired(cmd=list(args), timeout=timeout or 0)
            m = MagicMock()
            m.returncode = 0
            return m

        with (
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=unit_file),
        ):
            provider.unload_sync({"name": "test-container"})

        assert not unit_file.exists(), "quadlet source must still be removed"
        assert any("daemon-reload" in c for c in calls_made), calls_made

    def test_unload_sync_survives_a_stop_that_raises_seam_timeout(self, tmp_path: Path) -> None:
        """Same best-effort teardown as above, but for the REAL (unmocked seam)
        failure mode (#1869): ``SystemCtlSeam.systemctl`` now raises
        :class:`SeamTimeout`, not the bare ``subprocess.TimeoutExpired`` the
        sibling test above stubs directly at ``provider._run``."""
        provider = ContainerProvider()
        unit_file = tmp_path / "hal0-slot@test-container.container"
        unit_file.write_text("[Container]\n")

        calls_made: list[list[str]] = []

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            calls_made.append(list(args))
            if "stop" in args:
                raise SeamTimeout(list(args), timeout or 0.0, "deactivating")
            m = MagicMock()
            m.returncode = 0
            return m

        with (
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=unit_file),
        ):
            provider.unload_sync({"name": "test-container"})

        assert not unit_file.exists(), "quadlet source must still be removed"
        assert any("daemon-reload" in c for c in calls_made), calls_made

    def test_running_argv_reads_podman_config_cmd(self) -> None:
        provider = ContainerProvider()
        result = MagicMock()
        result.returncode = 0
        result.stdout = '["--ctx-size","4096","-b","512"]\n'

        with (
            patch("hal0.providers.container._container_runtime", return_value="/usr/bin/podman"),
            patch("hal0.providers.container.subprocess.run", return_value=result) as run,
        ):
            argv = provider.running_argv("chat")

        assert argv == ["--ctx-size", "4096", "-b", "512"]
        run.assert_called_once()
        assert "hal0-slot-chat" in run.call_args.args[0]

    def test_running_argv_returns_none_on_unexpected_inspect_payload(self) -> None:
        provider = ContainerProvider()
        result = MagicMock()
        result.returncode = 0
        result.stdout = '{"Cmd":["--ctx-size","4096"]}\n'

        with (
            patch("hal0.providers.container._container_runtime", return_value="/usr/bin/podman"),
            patch("hal0.providers.container.subprocess.run", return_value=result),
        ):
            assert provider.running_argv("chat") is None


class TestImageMismatch:
    """#663 - _image_mismatch compares the running image ref vs the declared profile image.

    Seeded with the real refs observed live on CT105 (both agent + chat run
    ``ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server``) so a
    healthy slot never reports a false mismatch.
    """

    _ROCM = "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server"
    _VULKAN = "ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server"

    def test_no_mismatch_when_running_equals_declared(self) -> None:
        assert _image_mismatch(self._ROCM, self._ROCM) is False

    def test_mismatch_when_running_differs_from_declared(self) -> None:
        assert _image_mismatch(self._VULKAN, self._ROCM) is True

    def test_no_mismatch_when_running_unknown(self) -> None:
        # Container down / inspect failed -> never cry wolf.
        assert _image_mismatch(None, self._ROCM) is False
        assert _image_mismatch("", self._ROCM) is False

    def test_no_mismatch_when_declared_unknown(self) -> None:
        assert _image_mismatch(self._ROCM, None) is False

    def test_whitespace_is_ignored(self) -> None:
        assert _image_mismatch(self._ROCM + "\n", self._ROCM) is False


def test_resolve_model_path_registry_miss_falls_back_to_bare_id() -> None:
    """Registry-miss contract (Phase C final review): model_info without a
    ``path`` falls back to the bare model id, which llama-server cannot open
    unless the id happens to be a real path.  Container slots therefore
    REQUIRE their [model].default to be registry-resident with a resolved
    GGUF path — the C8 deploy precheck enforces this on CT105.
    """
    assert _resolve_model_path({"_model_key": "gemma-4-12b-it"}) == "gemma-4-12b-it"


class TestGpuPassthroughGate:
    """spec-hw-slot-ownership §2 — the /dev/kfd + /dev/dri passthrough gate is
    decided by the SLOT's ``device`` enum, NEVER by the profile.

    Before the 1.0 fix, ``_resolve_llama_scalars`` read ``profile.device_class``
    and defaulted it to ``"gpu"`` when unset, so a ``cpu-chat`` profile on a
    ``device="cpu"`` slot still requested real GPU device nodes. ADR-0029
    deleted the profile's ``device_class`` / ``backend`` hints; a profile
    object that still carries one (a stand-in below) must not move the gate.
    Both directions are asserted: a cpu/npu slot must get NOTHING, and a gpu
    slot must NOT be stranded on CPU.
    """

    _NODES: ClassVar[list[str]] = ["/dev/kfd", "/dev/dri/renderD128"]

    @staticmethod
    def _hinted(**hints: str) -> Any:
        """A profile object that still carries a hardware hint."""
        from types import SimpleNamespace

        return SimpleNamespace(
            flags="-fa on", ctx_size=None, vision="auto", mtp="auto", runner=None, **hints
        )

    def _spec(self, cfg: dict[str, Any], profile: Any):
        provider = ContainerProvider()
        with (
            patch("hal0.providers.container._resolve_profile", return_value=profile),
            patch(
                "hal0.providers.container.resolve_gpu_device_paths",
                return_value=list(self._NODES),
            ),
            # Existence-filtered at the call site; force present so a CI host
            # with no /dev/kfd still exercises the gate rather than the filter.
            patch("hal0.providers.container._dev_node_exists", return_value=True),
            patch("hal0.providers.container.resolve_gpu_group_ids", return_value=[993, 44]),
        ):
            return provider.container_spec(cfg, _model_info())

    # ── the headline bug: cpu-chat on a device="cpu" slot ────────────────────

    def test_cpu_slot_with_device_agnostic_profile_gets_no_gpu_nodes(self) -> None:
        """The exact shipped shape: the ``cpu-chat`` seed names no hardware,
        so the old ``or "gpu"`` floor mounted /dev/kfd + /dev/dri into a CPU
        slot's container."""
        cpu_chat = ProfileConfig.model_validate(SEED_PROFILES["cpu-chat"])
        assert "device_class" not in ProfileConfig.model_fields, "profiles name no hardware"
        spec = self._spec(_slot_cfg(device="cpu", profile="cpu-chat"), cpu_chat)
        assert spec.devices == []
        assert spec.group_add == []

    def test_cpu_slot_ignores_a_profile_that_claims_gpu(self) -> None:
        """A legacy/imported profile carrying ``device_class="gpu"`` (e.g. the
        published ``chat-long-context`` artifact) must NOT drag GPU nodes onto a
        ``device="cpu"`` slot — the hint is inert."""
        spec = self._spec(_slot_cfg(device="cpu"), self._hinted(device_class="gpu"))
        assert spec.devices == []
        assert spec.group_add == []

    def test_npu_slot_gets_no_gpu_nodes(self) -> None:
        spec = self._spec(_slot_cfg(device="npu"), self._hinted(device_class="gpu"))
        assert spec.devices == []
        assert spec.group_add == []

    # ── the other direction: never strand a real GPU slot on CPU ────────────

    def test_gpu_slot_with_device_agnostic_profile_still_gets_nodes(self) -> None:
        """No seed names hardware — a gpu-rocm slot bound to one must still get
        real passthrough."""
        spec = self._spec(_slot_cfg(device="gpu-rocm"), _moe_profile())
        assert spec.devices == self._NODES
        assert spec.group_add == ["993", "44"]

    def test_gpu_slot_ignores_a_profile_that_claims_cpu(self) -> None:
        spec = self._spec(_slot_cfg(device="gpu-vulkan"), self._hinted(device_class="cpu"))
        assert spec.devices == self._NODES

    def test_cuda_slot_uses_cdi_from_the_slot_device_not_profile_backend(self) -> None:
        """NVIDIA is selected by ``device="gpu-cuda"``. CDI names are not paths,
        so no existence filter and no --group-add."""
        spec = self._spec(_slot_cfg(device="gpu-cuda"), _moe_profile())
        assert spec.devices == ["nvidia.com/gpu=all"]
        assert spec.group_add == []

    def test_rocm_slot_does_not_take_cdi_from_a_profile_backend_cuda(self) -> None:
        """A profile claiming ``backend="cuda"`` must not flip a gpu-rocm slot
        onto the CDI path — the vendor comes from the slot's device."""
        spec = self._spec(_slot_cfg(device="gpu-rocm"), self._hinted(backend="cuda"))
        assert spec.devices == self._NODES
        assert "nvidia.com/gpu=all" not in spec.devices

    # ── back-compat: a hand-built / pre-pivot dict with no device at all ────

    def test_deviceless_slot_cfg_falls_back_to_gpu_not_cpu(self) -> None:
        """A pre-pivot slot dict with no ``device`` key keeps the historical
        gpu default rather than silently losing its GPU."""
        cfg = _slot_cfg()
        cfg.pop("device")
        spec = self._spec(cfg, _moe_profile())
        assert spec.devices == self._NODES


class TestContextSizeDerive:
    """Regression guard for the 2026-06-15 chat@4096 incident: a slot whose
    profile pins no ``ctx_size`` must NEVER silently inherit llama-server's
    4096 default. container_spec derives the model's native window
    (dense-capped), or falls back to a safe 8192 when the native window is
    unknown."""

    def _spec(
        self,
        cfg: dict[str, Any],
        model_info: dict[str, Any],
        profile: ProfileConfig | None = None,
    ):
        provider = ContainerProvider()
        with patch(
            "hal0.providers.container._resolve_profile",
            return_value=profile or _moe_profile(),
        ):
            return provider.container_spec(cfg, model_info)

    @staticmethod
    def _ctx(command: list[str]) -> str | None:
        return command[command.index("--ctx-size") + 1] if "--ctx-size" in command else None

    def test_unset_ctx_derives_native_dense_capped(self) -> None:
        cfg = _slot_cfg()
        mi = _model_info(metadata={"context_length": 131072})
        assert self._ctx(self._spec(cfg, mi).command) == "32768"

    def test_unset_ctx_unknown_native_falls_back_8192_not_4096(self) -> None:
        cfg = _slot_cfg()
        mi = _model_info()  # no metadata.context_length
        ctx = self._ctx(self._spec(cfg, mi).command)
        assert ctx == "8192"
        assert ctx != "4096"

    def test_profile_ctx_size_is_the_window(self) -> None:
        cfg = _slot_cfg()
        mi = _model_info(metadata={"context_length": 131072})
        profile = ProfileConfig(flags="-fa on", ctx_size=16384)
        assert self._ctx(self._spec(cfg, mi, profile).command) == "16384"

    def test_model_defaults_window_is_not_read(self) -> None:
        """The model holds no tune: a window on the model row is ignored."""
        cfg = _slot_cfg()
        mi = _model_info(defaults={"context_size": 16384})
        assert self._ctx(self._spec(cfg, mi).command) == str(_CTX_SAFE_FALLBACK)


class TestProfileContext:
    """``profile_context`` — the one window rule (ADR-0029 decision 1).

    ``(ctx_size, "profile")`` when the profile sets one; else
    ``(min(native, 32768), "native")``; else ``(8192, "fallback")``.
    """

    def test_profile_ctx_size_wins_outright(self) -> None:
        profile = ProfileConfig(ctx_size=65536)
        mi = _model_info(metadata={"context_length": 32768})
        # Not capped by the native window or the dense cap: an explicit choice.
        assert profile_context(profile, mi) == (65536, "profile")

    def test_profile_ctx_size_below_native_shrinks_the_window(self) -> None:
        profile = ProfileConfig(ctx_size=4096)
        mi = _model_info(metadata={"context_length": 131072})
        assert profile_context(profile, mi) == (4096, "profile")

    def test_unset_profile_follows_the_native_window(self) -> None:
        mi = _model_info(metadata={"context_length": 16384})
        assert profile_context(ProfileConfig(), mi) == (16384, "native")

    def test_native_window_is_dense_capped(self) -> None:
        mi = _model_info(metadata={"context_length": 262144})
        assert profile_context(ProfileConfig(), mi) == (_CTX_DENSE_CAP, "native")

    def test_unknown_native_window_is_the_safe_fallback(self) -> None:
        mi = {"path": "/mnt/ai-models/x.gguf", "_model_key": "not-a-curated-model"}
        assert profile_context(ProfileConfig(), mi) == (_CTX_SAFE_FALLBACK, "fallback")

    @pytest.mark.parametrize("bad", ["abc", "8k", None, 0])
    def test_unparseable_native_window_falls_back(self, bad: Any) -> None:
        mi = {
            "path": "/x.gguf",
            "_model_key": "not-a-curated-model",
            "metadata": {"context_length": bad},
        }
        assert profile_context(ProfileConfig(), mi) == (_CTX_SAFE_FALLBACK, "fallback")

    def test_model_defaults_window_is_ignored(self) -> None:
        mi = {"_model_key": "not-a-curated-model", "defaults": {"context_size": 65536}}
        assert profile_context(ProfileConfig(), mi) == (_CTX_SAFE_FALLBACK, "fallback")

    def test_curated_model_with_no_registry_metadata_uses_the_catalogue(self) -> None:
        """#1617: a curated pick registered before the pull path stamped
        ``metadata`` still gets its catalogue window (dense-capped)."""
        mi = {"path": "/mnt/ai-models/brain.gguf", "_model_key": _CURATED_BRAIN_ID}
        assert profile_context(ProfileConfig(), mi) == (_CTX_DENSE_CAP, "native")


#: Hermes' hard gate — ``agent/model_metadata.py::MINIMUM_CONTEXT_LENGTH`` in the
#: bundled agent. Below it the agent REFUSES TO START (``agent_init.py`` raises);
#: it does not degrade, so a slot that lands under this is a cannot-chat box.
#: Imported, not re-typed: hal0 keeps exactly one copy of this number (#1867).
_HERMES_MIN_CONTEXT = HERMES_MINIMUM_CONTEXT_LENGTH

#: A curated brain pick whose catalogue row carries a ``context_length`` but
#: whose registry row on an older-build box carries no ``metadata`` at all
#: (#1617).
_CURATED_BRAIN_ID = "lfm2.5-2.6b"


class TestAgentWindowClearsTheHermesFloor:
    """#1827 — the dense cap must not undercut the window the agent needs.

    ADR-0029: the window the agent-facing slots run is their profile's
    ``ctx_size``, so the shipped ``agent``, ``utility`` and ``brain`` seeds
    carry one above Hermes' floor, and it reaches the launch command past the
    32768 dense cap even on a model whose native window is larger.
    """

    @pytest.mark.parametrize("seed", ["agent", "utility", "brain"])
    def test_agent_facing_seeds_clear_the_floor(self, seed: str) -> None:
        ctx = SEED_PROFILES[seed]["ctx_size"]
        assert isinstance(ctx, int)
        assert ctx > _CTX_DENSE_CAP
        assert ctx >= _HERMES_MIN_CONTEXT

    def test_seed_window_reaches_the_launch_command(self) -> None:
        profile = ProfileConfig.model_validate(SEED_PROFILES["brain"])
        cfg = _slot_cfg(name="brain", profile="brain", model={"default": "hal0-brain-sft-f16"})
        mi = _model_info(_model_key="hal0-brain-sft-f16", metadata={"context_length": 131072})
        with patch("hal0.providers.container._resolve_profile", return_value=profile):
            command = ContainerProvider().container_spec(cfg, mi).command
        served = int(command[command.index("--ctx-size") + 1])
        assert served == 65536
        assert served >= _HERMES_MIN_CONTEXT

    def test_unset_profile_window_is_still_dense_capped(self) -> None:
        mi = _model_info(metadata={"context_length": 131072})
        assert profile_context(ProfileConfig(), mi)[0] == _CTX_DENSE_CAP


class _FakeCtxModel:
    """Duck-typed registry entry — mirrors the real pydantic ``Model``."""

    def __init__(self, native: int | None) -> None:
        self.native = native

    def model_dump(self) -> dict[str, Any]:
        metadata = {"context_length": self.native} if self.native else {}
        return {"metadata": metadata}


class _FakeCtxRegistry:
    def __init__(self, models: dict[str, Any]) -> None:
        self._models = models

    def get(self, model_id: str) -> Any:
        if model_id not in self._models:
            raise KeyError(model_id)
        return self._models[model_id]


def _effective(
    cfg: dict[str, Any], registry: Any, profile: Any = None
) -> tuple[int | None, str | None]:
    with patch(
        "hal0.providers.container._resolve_profile",
        return_value=profile or ProfileConfig(),
    ):
        return slot_effective_context(cfg, registry)


class TestSlotEffectiveContext:
    """#1788 — the read-only surfaces (slot views, /v1/models, the agent anchor
    window, capacity) call ``slot_effective_context`` instead of
    re-implementing the window rule; it reports the source too."""

    @staticmethod
    def _cfg(model_id: str = "m", **overrides: Any) -> dict[str, Any]:
        cfg = {"name": "chat", "profile": "chat", "model": {"default": model_id}}
        cfg.update(overrides)
        return cfg

    def test_registered_model_reports_its_native_window(self) -> None:
        registry = _FakeCtxRegistry({"m": _FakeCtxModel(8000)})
        assert _effective(self._cfg(), registry) == (8000, "native")

    def test_profile_window_wins(self) -> None:
        registry = _FakeCtxRegistry({"m": _FakeCtxModel(8000)})
        profile = ProfileConfig(ctx_size=16384)
        assert _effective(self._cfg(), registry, profile) == (16384, "profile")

    def test_none_registry_degrades_to_safe_floor(self) -> None:
        assert _effective(self._cfg(), None) == (_CTX_SAFE_FALLBACK, "fallback")

    def test_empty_model_id_degrades_to_safe_floor(self) -> None:
        registry = _FakeCtxRegistry({"m": _FakeCtxModel(8000)})
        assert _effective(self._cfg(""), registry) == (_CTX_SAFE_FALLBACK, "fallback")

    def test_registry_miss_degrades_to_safe_floor(self) -> None:
        registry = _FakeCtxRegistry({"m": _FakeCtxModel(8000)})
        assert _effective(self._cfg("unregistered"), registry) == (_CTX_SAFE_FALLBACK, "fallback")

    def test_registry_get_raising_is_swallowed(self) -> None:
        class _BoomRegistry:
            def get(self, model_id: str) -> Any:
                raise RuntimeError("db down")

        assert _effective(self._cfg(), _BoomRegistry()) == (_CTX_SAFE_FALLBACK, "fallback")

    def test_missing_profile_reports_nothing(self) -> None:
        from hal0.errors import NotFound

        with patch(
            "hal0.providers.container._resolve_profile",
            side_effect=NotFound("gone", code="profiles.not_found"),
        ):
            assert slot_effective_context(self._cfg(), None) == (None, None)

    def test_npu_slot_reports_its_profile_window(self) -> None:
        profile = ProfileConfig(ctx_size=16384)
        cfg = self._cfg(device="npu", profile="flm")
        assert _effective(cfg, None, profile) == (16384, "profile")

    def test_npu_slot_without_a_profile_window_reports_flm_default(self) -> None:
        cfg = self._cfg(device="npu", profile="flm")
        assert _effective(cfg, None) == (8192, "fallback")


class TestAdvertisedWindowMatchesTheServedWindow:
    """#1827 — the number Hermes gates on is the ADVERTISED one.

    Hermes reads ``/v1/models``, which goes through
    :func:`slot_effective_context`, NOT through the launch path. That
    resolver builds its ``model_info`` from ``Model.model_dump()`` — and
    ``_model_key`` is not a field on :class:`hal0.registry.model.Model`; only
    the launch path stamps it. These tests use a REAL pydantic ``Model``, so
    the curated backfill (#1617) is exercised through the shape the read-only
    path really sees.
    """

    @staticmethod
    def _registry(model: Model) -> Any:
        class _Registry:
            def get(self, model_id: str) -> Any:
                if model_id != model.id:
                    raise KeyError(model_id)
                return model

        return _Registry()

    @staticmethod
    def _ctx(command: list[str]) -> str | None:
        return command[command.index("--ctx-size") + 1] if "--ctx-size" in command else None

    def test_curated_backfill_is_reachable_from_the_read_only_surface(self) -> None:
        """Registry row with no metadata: the advertised window is the curated
        catalogue's (dense-capped), not the 8192 safe floor."""
        model = Model(id=_CURATED_BRAIN_ID, path="/mnt/ai-models/brain.gguf")
        assert not model.model_dump().get("metadata")
        cfg = _slot_cfg(name="brain", model={"default": _CURATED_BRAIN_ID})
        assert _effective(cfg, self._registry(model)) == (_CTX_DENSE_CAP, "native")

    @pytest.mark.parametrize("ctx_size", [None, 65536])
    def test_advertised_equals_what_llama_server_is_launched_with(
        self, ctx_size: int | None
    ) -> None:
        """The two halves of #1802's honest-advertising contract, both driven
        off the same registry entry and profile through their own code paths."""
        model = Model(id=_CURATED_BRAIN_ID, path="/mnt/ai-models/brain.gguf")
        cfg = _slot_cfg(name="brain", model={"default": _CURATED_BRAIN_ID})
        registry = self._registry(model)
        profile = ProfileConfig(flags="-fa on", ctx_size=ctx_size)

        advertised, _source = _effective(cfg, registry, profile)
        with (
            patch("hal0.registry.store.ModelRegistry", lambda *a, **k: registry),
            patch("hal0.providers.container._resolve_profile", return_value=profile),
        ):
            served = self._ctx(
                ContainerProvider().container_spec(cfg, _best_effort_model_info(cfg)).command
            )
        assert served == str(advertised)

    def test_registry_entry_without_an_id_key_still_degrades_safely(self) -> None:
        """A duck-typed entry whose ``model_dump()`` omits both ``id`` and
        ``metadata`` has nothing to look up — the safe floor, not a crash."""
        registry = _FakeCtxRegistry({"m": _FakeCtxModel(None)})
        cfg = {"name": "chat", "profile": "chat", "model": {"default": "m"}}
        assert _effective(cfg, registry) == (_CTX_SAFE_FALLBACK, "fallback")


class TestFamilyDefaults:
    """FAMILY_DEFAULTS — the per-family override layer (gemma → f16 KV)."""

    def test_model_family_token_scan(self) -> None:
        assert model_family("gemma-4-12b-it-ud-q4-k-xl") == "gemma"
        assert model_family("Qwen3.6-27B-UD-Q5_K_XL.gguf") == "qwen"
        assert model_family(None, "/mnt/ai-models/gemma4-v2/gemma4-v2-Q4_K_M.gguf") == "gemma"
        assert model_family("chadrock-35b-ace-saber") is None
        assert model_family(None) is None

    def test_family_flags_lookup(self) -> None:
        # Per spec §1.2: family_defaults.toml data cleared for 1.0
        # (gemma f16 KV override gone; family-specific quirks live in
        # profile.<family>-<variant> entries instead).
        assert "gemma" not in FAMILY_DEFAULTS
        assert family_flags("gemma-4-12b-it") == ""
        assert family_flags("qwen3-27b") == ""  # no qwen entry
        assert family_flags(None) == ""

    def test_gemma_profile_kv_reaches_argv_with_no_family_override(self) -> None:
        """ADR-0029: the profile's flags are the tune, so its ``-ctk q8_0``
        reaches the argv. ``family_defaults`` ships empty for 1.0, so no gemma
        f16 KV override lands on top of it."""
        profile = _moe_profile()  # ships -ctk q8_0 -ctv q8_0
        cfg = {"profile": "rocm", "port": 8095, "model": {"default": "gemma-4-12b-it"}}
        with patch("hal0.providers.container._resolve_profile", return_value=profile):
            argv = resolved_command_for_slot(cfg, model_path="/mnt/ai-models/gemma-4-12b-it.gguf")
        assert argv is not None
        assert argv[argv.index("-ctk") + 1] == "q8_0"
        assert argv[argv.index("-ctv") + 1] == "q8_0"
        assert "f16" not in argv  # no gemma family override
        assert "--cache-reuse" not in argv

    def test_non_gemma_gets_no_family_kv_leak(self) -> None:
        """A qwen model has no FAMILY_DEFAULTS entry: the profile's KV types
        are what launch, and no gemma f16 leaks onto a non-family model."""
        profile = _moe_profile()
        cfg = {"profile": "rocm", "port": 8095, "model": {"default": "qwen3-27b"}}
        with patch("hal0.providers.container._resolve_profile", return_value=profile):
            argv = resolved_command_for_slot(cfg, model_path="/mnt/ai-models/qwen3-27b.gguf")
        assert argv is not None
        assert argv.count("-ctk") == 1
        assert argv[argv.index("-ctk") + 1] == "q8_0"
        assert "f16" not in argv

    def test_slot_extra_args_refuses_the_preview(self) -> None:
        """A slot still carrying ``[server].extra_args`` was never converted:
        the preview (like the launch) resolves nothing rather than showing an
        argv the slot does not run."""
        profile = _moe_profile()
        cfg = {
            "profile": "rocm",
            "port": 8095,
            "model": {"default": "gemma-4-12b-it"},
            "server": {"extra_args": "-ctk q4_0 -ctv q4_0"},
        }
        with patch("hal0.providers.container._resolve_profile", return_value=profile):
            argv = resolved_command_for_slot(cfg, model_path="/mnt/ai-models/gemma-4-12b-it.gguf")
        assert argv is None

    def test_vulkan_seed_is_basic_no_forced_kv_quant(self) -> None:
        """The vulkan seed ships minimal flags with NO forced KV quant.

        The 2026-07-05 seed cleanup reduced the plain ``vulkan`` seed to basic
        flags, so f16 (the llama-server default) applies universally — it is
        gemma-safe without relying on a family guard against a q8 vulkan seed,
        and KV tuning lives on the profile a slot runs (ADR-0029). A slot on
        the basic seed therefore never gets a forced q8 KV.
        """
        vseed = SEED_PROFILES["chat"]
        assert "-ctk" not in vseed["flags"] and "q8_0" not in vseed["flags"]
        # Seeds request Flash Attention as `auto`, never forcing `on` (#1811):
        # a generic template must let llama.cpp's support probe fall back where
        # the backend lacks the kernel.
        assert "-fa auto" in vseed["flags"]

        profile = ProfileConfig(flags=vseed["flags"], mtp="off")
        cfg = {"profile": "chat", "port": 8096, "model": {"default": "qwen3-27b"}}
        with patch("hal0.providers.container._resolve_profile", return_value=profile):
            argv = resolved_command_for_slot(cfg, model_path="/mnt/ai-models/qwen3-27b.gguf")
        assert argv is not None
        assert "q8_0" not in argv  # basic seed forces no KV quant


class TestUniformQuadletRender:
    """halo150/143 O8+O11: ONE render for every substrate — no version branch.

    Native AutoRemove=/GroupAdd=/SecurityOpt= keys are deliberately never
    emitted: 4.x generators hard-fail the conversion on them, and the native
    render's systemd lifecycle broke on unprivileged podman-5-in-LXC
    (netavark /run/user/0/netns teardown race) while the PodmanArgs render
    ran healthy on both validation boxes.
    """

    def _rendered(self) -> str:
        return _render_llama("qtest", "img:latest", 18081, "/models/m.gguf", "-fa on")

    def test_no_native_5x_keys_ever(self):
        text = self._rendered()
        assert "AutoRemove" not in text
        assert "GroupAdd=" not in text
        assert "SecurityOpt=" not in text

    def test_gpu_groups_ride_podman_args(self):
        text = self._rendered()
        if "--group-add" in text:
            assert "PodmanArgs=" in text


def _exec_tokens(unit_text: str) -> list[str]:
    """shlex-split the rendered ``Exec=`` argv back into tokens."""
    return shlex.split(_exec_line(unit_text))


class TestHostNetLoopbackFence:
    """host-net ⇄ loopback-bind coupling (podman-unprivileged-findings.md, Issue 1).

    Under ``Network=host`` there is no ``PublishPort=127.0.0.1:…`` fence, so the
    process bind IS the fence — the renderer must flip ``--host 0.0.0.0`` (and
    ComfyUI's ``--listen 0.0.0.0``) to loopback. The invariant: a host-net plan
    can NEVER render a 0.0.0.0 bind. Bridge mode keeps 0.0.0.0 + the 127.0.0.1
    PublishPort pin unchanged.
    """

    def _llama_plan(self, network_mode: str = "") -> RuntimeLaunchPlan:
        return RuntimeLaunchPlan(
            image="img:latest",
            command=["--host", "0.0.0.0", "--port", "8095", "--model", "/models/m.gguf"],
            port=8095,
            network_mode=network_mode,
        )

    # ── INVARIANT: host-net plan never renders a 0.0.0.0 bind ────────────────
    def test_host_net_plan_never_renders_zero_bind(self) -> None:
        """THE invariant. A plan pinned network_mode=host binds loopback."""
        plan = self._llama_plan(network_mode="host")
        unit = _render_quadlet_from_plan("slot", plan)
        assert "Network=host" in unit.splitlines()
        assert "0.0.0.0" not in unit  # no bind, no publish, nowhere
        tokens = _exec_tokens(unit)
        assert tokens[tokens.index("--host") + 1] == "127.0.0.1"
        # host net → no PublishPort (would be a no-op).
        assert not any(ln.startswith("PublishPort=") for ln in unit.splitlines())

    def test_config_default_host_net_flips_bind_for_bridge_plan(self) -> None:
        """A plan with empty network_mode picks up the [slots].network_mode=host
        box default and STILL gets the loopback bind + Network=host — the
        deploy-time knob drives the fence, not just an explicit per-plan mode."""
        plan = self._llama_plan(network_mode="")  # bridge-shaped plan
        unit = _render_quadlet_from_plan("slot", plan, network_mode_default="host")
        assert "Network=host" in unit.splitlines()
        assert "0.0.0.0" not in unit
        tokens = _exec_tokens(unit)
        assert tokens[tokens.index("--host") + 1] == "127.0.0.1"

    # ── REGRESSION: bridge mode keeps 0.0.0.0 + loopback PublishPort ─────────
    def test_bridge_mode_keeps_zero_bind_and_loopback_publish(self) -> None:
        """Bridge (empty network_mode, no host default): the process binds
        0.0.0.0 inside its netns and the 127.0.0.1 PublishPort is the fence —
        unchanged from before this lane."""
        plan = self._llama_plan(network_mode="")
        unit = _render_quadlet_from_plan("slot", plan)  # network_mode_default=""
        assert not any(ln.startswith("Network=") for ln in unit.splitlines())
        assert "PublishPort=127.0.0.1:8095:8095" in unit.splitlines()
        tokens = _exec_tokens(unit)
        assert tokens[tokens.index("--host") + 1] == "0.0.0.0"

    def test_bridge_mode_publish_host_widen_still_binds_zero(self) -> None:
        """publish_host=0.0.0.0 widens the PUBLISH (bridge); the process bind is
        untouched (still 0.0.0.0 in-netns). Only host-net flips the bind."""
        plan = self._llama_plan(network_mode="")
        unit = _render_quadlet_from_plan("slot", plan, publish_host="0.0.0.0")
        assert "PublishPort=0.0.0.0:8095:8095" in unit.splitlines()

    # ── ComfyUI --listen inside a bash -lc payload string ───────────────────
    def test_comfyui_shell_payload_listen_flipped_under_host_net(self) -> None:
        """ComfyUI templates ``--listen 0.0.0.0`` inside a single bash -lc token
        AND always runs host net — the chokepoint must reach into the shell
        string too so its web UI port is loopback-fenced like every slot."""
        payload = "cd /opt/ComfyUI && exec python main.py --listen 0.0.0.0 --port 8188 -fa"
        plan = RuntimeLaunchPlan(
            image="comfy:latest",
            command=["bash", "-lc", payload],
            port=8188,
            network_mode="host",
        )
        unit = _render_quadlet_from_plan("img", plan)
        assert "Network=host" in unit.splitlines()
        assert "0.0.0.0" not in unit
        assert "--listen 127.0.0.1" in _exec_line(unit)


class TestLoopbackFenceCommand:
    """Unit coverage for the fence helper across every bind-flag shape."""

    def test_split_token_host(self) -> None:
        assert _loopback_fence_command(["--host", "0.0.0.0", "--port", "9"]) == [
            "--host",
            "127.0.0.1",
            "--port",
            "9",
        ]

    def test_split_token_listen(self) -> None:
        assert _loopback_fence_command(["--listen", "0.0.0.0"]) == ["--listen", "127.0.0.1"]

    def test_inline_equals_form(self) -> None:
        assert _loopback_fence_command(["--host=0.0.0.0"]) == ["--host=127.0.0.1"]

    def test_embedded_shell_string(self) -> None:
        out = _loopback_fence_command(["bash", "-lc", "python m.py --listen 0.0.0.0 --port 8"])
        assert out[-1] == "python m.py --listen 127.0.0.1 --port 8"

    def test_no_bind_flag_untouched(self) -> None:
        # A bare 0.0.0.0 not preceded by a bind flag is left alone (defensive:
        # the fence targets bind addresses, not arbitrary values).
        assert _loopback_fence_command(["--some-ip", "0.0.0.0"]) == ["--some-ip", "0.0.0.0"]


class TestSlotHardwareSegment:
    """spec-hw-slot-ownership §2 / ADR-0029: the slot owns NGL + THREADS +
    METRICS, emitted in the ``slot`` layer of ``_llama_argv_segments``; a
    profile may not set them."""

    @staticmethod
    def _segments(**kw: Any) -> list[tuple[str, list[str]]]:
        kw.setdefault("context_size", 8192)
        return _llama_argv_segments(port=8081, model_path="/m.gguf", **kw)

    def _argv(self, **kw: Any) -> list[str]:
        return resolve_argv(self._segments(**kw)).argv

    def test_slot_ngl_emits_ngl(self) -> None:
        argv = self._argv(slot_n_gpu_layers=99)
        assert argv[argv.index("-ngl") + 1] == "99"

    def test_slot_ngl_negative_one_is_emitted_verbatim(self) -> None:
        # -1 (all layers) is a legitimate explicit NGL value, not a "skip".
        argv = self._argv(slot_n_gpu_layers=-1)
        assert argv[argv.index("-ngl") + 1] == "-1"

    def test_slot_threads_emits_threads(self) -> None:
        argv = self._argv(slot_threads=8)
        assert argv[argv.index("--threads") + 1] == "8"

    def test_slot_threads_zero_omits_flag(self) -> None:
        # 0 = unset → runtime default, no --threads emitted.
        argv = self._argv(slot_threads=0, slot_n_gpu_layers=-1)
        assert "--threads" not in argv

    @pytest.mark.parametrize("flags", ["-ngl 77", "--threads 3", "-t 3", "-dev Vulkan0"])
    def test_profile_hardware_flag_refuses_to_launch(self, flags: str) -> None:
        """A profile file is operator-editable on disk: a hardware flag in it
        is refused at launch, as at save."""
        with pytest.raises(BadRequest) as excinfo:
            self._argv(slot_threads=8, profile_flags=f"-fa on {flags}")
        assert excinfo.value.code == "slot.hardware_flag_denied"

    def test_segment_labels_are_the_four_adr_0029_layers(self) -> None:
        labels = [lbl for lbl, _toks in self._segments()]
        assert labels == ["base", "model", "profile", "slot"]

    def test_metrics_defaults_on(self) -> None:
        # #1810: no slot_metrics kwarg → the safe default (loopback-only,
        # negligible overhead) is ON, so --metrics reaches every slot unless
        # an operator explicitly opts out.
        argv = self._argv()
        assert "--metrics" in argv

    def test_metrics_explicit_true_emits_flag(self) -> None:
        argv = self._argv(slot_metrics=True)
        assert "--metrics" in argv

    def test_metrics_explicit_false_omits_flag(self) -> None:
        argv = self._argv(slot_metrics=False)
        assert "--metrics" not in argv

    def test_metrics_rides_the_trusted_slot_segment(self) -> None:
        # --metrics must come from the SAME trusted, hal0-computed segment as
        # -ngl/--threads — not the operator-editable profile layer.
        segs = dict(self._segments(slot_metrics=True))
        assert "--metrics" in segs["slot"]
        assert "--metrics" not in segs["profile"]


class TestUnsupportedQuantGuard:
    """hal0#1790 defense-in-depth: a fork-only quant (the ROCmFPX family's
    custom GGML tensor type ids 100/103 — see
    ``hal0.registry.detect.quant_from_rocmfpx_filename`` — plus IU4 and
    ActiveFPX) must never reach a launch. No runner hal0 ships can load them
    (ADR-0028), whatever the slot's device or runner.

    Model add, pull and scan refuse these up front; this guard is the backstop
    for a model registered before that refusal existed, a stale slot TOML, a
    manual ``HAL0_BRAIN_MODEL`` override, or a hand-swapped model — refuse the
    launch with a clear 422 instead of a SIGSEGV crash loop (exit 139).
    """

    _GPU_NODES: ClassVar[list[str]] = ["/dev/kfd", "/dev/dri/renderD128"]

    def _spec(self, cfg: dict[str, Any], model_info: dict[str, Any], profile: ProfileConfig):
        provider = ContainerProvider()
        with (
            patch("hal0.providers.container._resolve_profile", return_value=profile),
            patch(
                "hal0.providers.container.resolve_gpu_device_paths",
                return_value=list(self._GPU_NODES),
            ),
            patch("hal0.providers.container._dev_node_exists", return_value=True),
            patch("hal0.providers.container.resolve_gpu_group_ids", return_value=[993, 44]),
        ):
            return provider.container_spec(cfg, model_info)

    def test_rocmfpx_quant_on_cpu_slot_is_refused(self) -> None:
        """The exact hal0#1790 shape: a ROCmFP8 build on a device='cpu' slot
        must raise instead of spawning."""
        cpu_chat = ProfileConfig(flags="--threads-batch 8 -b 256 -ub 256")
        cfg = _slot_cfg(device="cpu", profile="cpu-chat", name="brain")
        model_info = _model_info(quant="ROCmFP8", _model_key="hal0-brain-sft-q8-rocmfpx")
        with pytest.raises(_container_mod.UnprocessableEntity) as exc_info:
            self._spec(cfg, model_info, cpu_chat)
        err = exc_info.value
        assert err.code == "slot.unsupported_quant"
        assert err.details["quant"] == "ROCmFP8"
        assert err.details["model"] == "hal0-brain-sft-q8-rocmfpx"
        assert err.details["slot"] == "brain"
        assert "no hal0 runner can load" in str(err)

    @pytest.mark.parametrize("quant", ["ROCmFP3", "ROCmFP4", "ROCmFP6", "ROCmFP8", "ROCmFPX"])
    def test_every_fpx_family_label_is_refused_on_cpu(self, quant: str) -> None:
        cpu_chat = ProfileConfig(flags="--threads-batch 8 -b 256 -ub 256")
        cfg = _slot_cfg(device="cpu", profile="cpu-chat")
        model_info = _model_info(quant=quant)
        with pytest.raises(_container_mod.UnprocessableEntity) as exc_info:
            self._spec(cfg, model_info, cpu_chat)
        assert exc_info.value.code == "slot.unsupported_quant"

    def test_rocmfpx_quant_on_gpu_rocm_slot_is_refused(self) -> None:
        """No runner accepts the fork family any more, the rocm lane included."""
        rocm = ProfileConfig.model_validate(SEED_PROFILES["chat"])
        cfg = _slot_cfg(device="gpu-rocm", profile="chat")
        model_info = _model_info(quant="ROCmFP8", _model_key="some-model")
        with pytest.raises(_container_mod.UnprocessableEntity) as exc_info:
            self._spec(cfg, model_info, rocm)
        assert exc_info.value.code == "slot.unsupported_quant"

    def test_rocmfpx_quant_on_gpu_vulkan_slot_is_refused(self) -> None:
        vulkan = ProfileConfig(flags="-fa on")
        cfg = _slot_cfg(device="gpu-vulkan", profile="vulkan-chat")
        model_info = _model_info(quant="ROCmFP4")
        with pytest.raises(_container_mod.UnprocessableEntity) as exc_info:
            self._spec(cfg, model_info, vulkan)
        assert exc_info.value.code == "slot.unsupported_quant"

    @pytest.mark.parametrize(
        "path",
        [
            "/mnt/ai-models/qwen3-ROCmFP4.gguf",
            "/mnt/ai-models/model-IU4.gguf",
            "/mnt/ai-models/model-ActiveFPX.gguf",
        ],
    )
    def test_a_fork_family_filename_is_refused_without_a_quant_label(self, path: str) -> None:
        """A registry row that never got a ``quant`` label is still caught by
        the file name."""
        cpu_chat = ProfileConfig(flags="--threads-batch 8 -b 256 -ub 256")
        cfg = _slot_cfg(device="cpu", profile="cpu-chat")
        model_info = _model_info(path=path)
        with pytest.raises(_container_mod.UnprocessableEntity) as exc_info:
            self._spec(cfg, model_info, cpu_chat)
        assert exc_info.value.code == "slot.unsupported_quant"

    def test_plain_quant_on_cpu_slot_is_unaffected(self) -> None:
        """A normal quant (no ROCmFP* marker) must never trip the guard."""
        cpu_chat = ProfileConfig(flags="--threads-batch 8 -b 256 -ub 256")
        cfg = _slot_cfg(device="cpu", profile="cpu-chat")
        model_info = _model_info(quant="Q4_K_M")
        spec = self._spec(cfg, model_info, cpu_chat)
        assert spec is not None

    def test_no_quant_on_cpu_slot_is_unaffected(self) -> None:
        cpu_chat = ProfileConfig(flags="--threads-batch 8 -b 256 -ub 256")
        cfg = _slot_cfg(device="cpu", profile="cpu-chat")
        model_info = _model_info()  # no "quant" key at all
        spec = self._spec(cfg, model_info, cpu_chat)
        assert spec is not None
