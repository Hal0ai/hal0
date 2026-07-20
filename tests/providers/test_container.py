"""Unit tests for ``hal0.providers.container.ContainerProvider``.

Issue #655 — tracer bullet: ContainerProvider unit-render + control-plane.

Covers:
  * _render_quadlet_from_plan produces the expected Quadlet .container keys +
    Exec= argv (flags merged from profile, identical-path /mnt/ai-models:ro,z
    mount, loopback PublishPort, numeric GroupAdd, apparmor/seccomp unconfined)
  * resolve_profile_flags MTP expansion
  * ContainerProvider.container_spec returns a ContainerSpec with correct
    image, command, mounts, and security opts
  * load_sync / unload_sync call the right systemctl commands (mocked)
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from hal0.config.schema import (
    FAMILY_DEFAULTS,
    MTP_FLAG_BUNDLE,
    SEED_PROFILES,
    ProfileConfig,
    family_flags,
    model_family,
    resolve_profile_flags,
)
from hal0.providers import container as _container_mod
from hal0.providers._gpu import resolve_gpu_group_ids
from hal0.providers.base import RuntimeLaunchPlan
from hal0.providers.container import (
    _MODEL_STORE_MOUNT,
    ContainerProvider,
    _container_runtime,
    _image_mismatch,
    _llama_argv_segments,
    _llama_launch_plan,
    _loopback_fence_command,
    _render_quadlet_from_plan,
    _resolve_model_path,
    resolved_command_for_slot,
)
from hal0.slots.argv import resolve_argv

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
):
    """Test shim mirroring the deleted ``_render_unit`` scalar shim, producing
    Quadlet text: build the llama launch plan, then render it.

    FLAGS-own: the profile ``flags_str`` and slot ``extra_args`` no longer reach
    launch — a model owns its tune. So this shim now routes both into the
    model's materialized ``defaults.extra_args`` (the surviving ``model_extra_args``
    launch segment), i.e. it simulates a STAMPED model. That keeps these render
    tests exercising the exact tune-emission + Exec= quoting path they always
    did, just through the one channel that survives.
    """
    # Resolve through the container module so tests patching
    # ``hal0.providers.container.resolve_gpu_device_paths`` take effect (mirrors
    # the deleted ``_render_unit`` shim, which resolved it there).
    devices = (
        device_paths if device_paths is not None else _container_mod.resolve_gpu_device_paths()
    )
    merged_tune = " ".join(p for p in ((flags_str or "").strip(), (extra_args or "").strip()) if p)
    plan = _llama_launch_plan(
        image=image,
        port=port,
        model_path=model_path,
        flags_str="",  # inert now
        devices=list(devices),
        group_ids=[str(g) for g in resolve_gpu_group_ids()],
        context_size=context_size,
        extra_args=None,  # inert now
        model_alias=model_alias,
        model_defaults={"extra_args": merged_tune} if merged_tune else None,
    )
    return _render_quadlet_from_plan(token, plan, publish_host=publish_host)


def _exec_line(unit_text: str) -> str:
    """Return the ``Exec=`` value (the in-container argv) from Quadlet unit text."""
    for line in unit_text.splitlines():
        if line.startswith("Exec="):
            return line[len("Exec=") :]
    raise AssertionError("Exec= not found in unit text")


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _moe_profile() -> ProfileConfig:
    return ProfileConfig(
        flags="-fa on -ctk q8_0 -ctv q8_0 -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap",
        mtp=False,
    )


def _mtp_profile() -> ProfileConfig:
    return ProfileConfig(
        flags="-fa on -ctk q8_0 -ctv q8_0 -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap",
        mtp=True,
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


# ── Profile flag resolution ───────────────────────────────────────────────────


class TestResolveProfileFlags:
    def test_moe_profile_no_mtp(self) -> None:
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        assert "-fa on" in flags
        assert "-ctk q8_0" in flags
        assert "--no-mmap" in flags
        # MTP bundle must NOT be present
        assert "--spec-type" not in flags

    def test_mtp_profile_expands_bundle(self) -> None:
        """§7.1a / ML-5: profile.mtp is informational only now — the caller
        must pass an explicit mtp_override=True (the real decision lives in
        providers.container._effective_mtp, driven by the model + runner,
        not the profile)."""
        profile = _mtp_profile()
        flags = resolve_profile_flags(profile, mtp_override=True)
        assert "--spec-type draft-mtp" in flags
        assert "--spec-draft-device ROCm0" in flags
        # Base flags are also present
        assert "-fa on" in flags

    def test_mtp_profile_no_override_no_longer_expands(self) -> None:
        """profile.mtp=True with NO explicit override no longer expands the
        bundle — MTP moved to a model capability (see the handback)."""
        profile = _mtp_profile()
        flags = resolve_profile_flags(profile)
        assert "--spec-type" not in flags

    def test_mtp_flag_bundle_constant_nonempty(self) -> None:
        assert "--spec-type draft-mtp" in MTP_FLAG_BUNDLE


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
        flags = resolve_profile_flags(profile)
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        assert f"Image={_TEST_IMAGE}" in unit.splitlines()
        assert "--port 8095" in _exec_line(unit)

    def test_container_name_key(self) -> None:
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        assert "ContainerName=hal0-slot-test-slot" in unit.splitlines()

    def test_identical_path_mount_readonly(self, monkeypatch) -> None:
        """Model store mounted identical-path, read-only, SELinux-relabelled."""
        monkeypatch.setenv("HAL0_MODEL_STORE", _MODEL_STORE_MOUNT)  # pin the default
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
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
        custom = "/home/cuken/ai/models"
        monkeypatch.setenv("HAL0_MODEL_STORE", custom)
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
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
        flags = resolve_profile_flags(profile)
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
        flags = resolve_profile_flags(profile)
        unit = _render_llama("brain", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        vols = [ln for ln in unit.splitlines() if ln.startswith("Volume=")]
        assert vols in (
            ["Volume=/mnt/ai-models:/mnt/ai-models:ro"],
            ["Volume=/mnt/ai-models:/mnt/ai-models:ro,z"],
        )

    def test_loopback_port_publish(self) -> None:
        """Port must be published on 127.0.0.1 only (not LAN-exposed)."""
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        assert "PublishPort=127.0.0.1:8095:8095" in unit.splitlines()

    def test_healthcheck_targets_slot_port_not_image_default(self) -> None:
        """The toolbox image bakes a HEALTHCHECK probing a hardcoded :8080, but
        hal0 runs llama-server on the slot port — so the unit must override
        HealthCmd= to probe the real port (else `podman ps` shows a permanent
        false (unhealthy))."""
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
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
        flags = resolve_profile_flags(profile)
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
        flags = resolve_profile_flags(profile)
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
        flags = resolve_profile_flags(profile)
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
        flags = resolve_profile_flags(profile)
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

    def test_server_extra_args_appended(self) -> None:
        """[server].extra_args is honored on the container path, appended after
        profile flags so slot-level flags win."""
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
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
        """A space-less JSON extra-arg value must survive systemd's Exec= parser
        intact (rework board bug).  ``--chat-template-kwargs
        '{"enable_thinking":false}'`` shlex-splits into a bare, space-less
        ``{"enable_thinking":false}`` token; the old emitter only quoted tokens
        containing a space, so the double-quotes were emitted un-escaped and
        systemd stripped them to ``{enable_thinking:false}`` → llama-server JSON
        parse error → the slot never starts.  The Quadlet Exec= emitter must
        ``shlex.quote`` every token so the JSON reaches the process byte-for-byte."""
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
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
        flags = resolve_profile_flags(profile)
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        # Uniform render (O8+O11): security opts ride PodmanArgs= on every
        # substrate — native SecurityOpt= keys are deliberately not emitted.
        assert "--security-opt apparmor=unconfined" in unit
        assert "--security-opt seccomp=unconfined" in unit
        assert "SecurityOpt=" not in unit

    def test_model_arg_in_exec(self) -> None:
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        model_path = "/mnt/ai-models/model.gguf"
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, model_path, flags)
        # llama-server uses space-separated --model PATH (not --model=PATH)
        assert f"--model {model_path}" in _exec_line(unit)

    def test_profile_flags_in_exec(self) -> None:
        """Bench-tuned profile flags must appear in the Exec= argv."""
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        exec_line = _exec_line(unit)
        assert "-fa" in exec_line
        assert "--no-mmap" in exec_line
        assert "-ctk" in exec_line

    def test_mtp_flags_in_exec_when_mtp_true(self) -> None:
        profile = _mtp_profile()
        flags = resolve_profile_flags(profile, mtp_override=True)
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        assert "--spec-type" in _exec_line(unit)

    def test_unit_has_expected_sections(self) -> None:
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        assert "[Unit]" in unit
        assert "[Container]" in unit
        assert "[Service]" in unit
        assert "[Install]" in unit
        # Quadlet owns crash recovery now (was the hand-rendered Restart=no).
        assert "Restart=always" in unit.splitlines()

    def test_startlimit_keys_land_in_unit_section_not_service(self) -> None:
        """StartLimit*= are systemd.unit(5) [Unit] directives, not [Service].

        Emitting them under [Service] makes systemd log "Unknown key" and
        silently drop them, disabling the slot's restart rate-limiting
        (install-validation m2, halo150, 2026-07-19). Assert they render in
        the [Unit] block and never leak into [Service].
        """
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_llama("test-slot", _TEST_IMAGE, 8095, "/mnt/ai-models/model.gguf", flags)
        unit_section, _, remainder = unit.partition("[Container]")
        service_section = remainder.partition("[Service]")[2].partition("[Install]")[0]
        assert "StartLimitIntervalSec=300" in unit_section
        assert "StartLimitBurst=5" in unit_section
        assert "StartLimitIntervalSec" not in service_section
        assert "StartLimitBurst" not in service_section

    def test_numeric_group_add_present(self) -> None:
        """GroupAdd= must use numeric GIDs (toolbox images lack group names)."""
        from hal0.providers._gpu import resolve_gpu_group_ids

        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
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
        from hal0.config.schema import DEFAULT_ROCMFPX_IMAGE

        spec = self._build_spec()
        assert spec.image != _TEST_IMAGE
        assert spec.image == DEFAULT_ROCMFPX_IMAGE

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
            patch("hal0.providers.container.os.path.exists", return_value=True),
        ):
            spec = self._build_spec()
        assert spec.devices == ["/dev/kfd", "/dev/dri/renderD128"]

    def test_security_opts(self) -> None:
        spec = self._build_spec()
        assert "apparmor=unconfined" in spec.security_opt
        assert "seccomp=unconfined" in spec.security_opt

    def test_loopback_publish_derived_from_port(self) -> None:
        """Loopback publish is derived from port + empty network_mode by the
        renderer (declarative) — not hand-rolled into extra_args."""
        spec = self._build_spec()
        assert spec.port == 8095
        assert spec.network_mode == ""
        assert not any("127.0.0.1" in a for a in spec.extra_args)
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
        FLAGS-own: -b/-ub now ride the model's materialized defaults.extra_args
        (the profile flag segment is gone), so the model carries them here."""
        provider = self._provider()
        with patch(
            "hal0.providers.container._resolve_profile",
            return_value=_moe_profile(),
        ):
            argv = provider.expected_argv(
                _slot_cfg(model={"default": "chadrock-35b-ace-saber", "context_size": 131072}),
                _model_info(defaults={"extra_args": "-b 512 -ub 512"}),
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

        def fake_run(*args: str, check: bool = True) -> MagicMock:
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

    def test_load_sync_threads_ctx_size_and_model_tune(self, tmp_path: Path) -> None:
        """load_sync bakes the slot ``context_size`` (base --ctx-size, still
        slot-resolved) AND the MODEL's materialized ``defaults.extra_args`` into
        the rendered unit. FLAGS-own: a slot ``[server].extra_args`` is inert —
        the model owns the tune, so the override-kv rides ``model_info.defaults``."""
        profile = _moe_profile()
        provider = ContainerProvider()
        unit_file = tmp_path / "test.service"

        def fake_run(*args: str, check: bool = True) -> MagicMock:
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
                    "model": {"default": "model", "context_size": 131072},
                    "server": {"extra_args": "--override-kv IGNORED=bool:true"},  # inert
                },
                {
                    "path": "/mnt/ai-models/model.gguf",
                    "_model_key": "model",
                    "defaults": {"extra_args": "--override-kv k=bool:false"},
                },
            )

        unit = unit_file.read_text()
        assert "--ctx-size 131072" in unit  # slot context_size still reaches base
        assert "--override-kv k=bool:false" in unit  # model tune reaches launch
        assert "IGNORED=bool:true" not in unit  # slot extra_args inert

    def test_load_sync_advertises_model_id_alias(self, tmp_path: Path) -> None:
        """load_sync must pass the registry model id (model_info._model_key)
        as --alias so the dispatcher can route hal0/* names to the container."""
        profile = _moe_profile()
        provider = ContainerProvider()
        unit_file = tmp_path / "test.service"

        def fake_run(*args: str, check: bool = True) -> MagicMock:
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
        profile = _moe_profile()
        slot_cfg = {
            "name": "test-container",
            "port": 8095,
            "profile": "rocm",
            "model": {"default": "m", "context_size": 131072},
            "server": {"extra_args": "--override-kv k=bool:false"},
        }
        model_info = {"path": "/mnt/ai-models/model.gguf", "_model_key": "m"}

        # LAN-exposed box: the operator widened the publish bind. Both render
        # paths must honour it identically.
        monkeypatch.setattr("hal0.providers.container._slot_publish_host", lambda: "0.0.0.0")

        def fake_run(*args: str, check: bool = True) -> MagicMock:
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
        what actually launches."""
        profile = _moe_profile()
        cfg = {
            "profile": "rocm",
            "port": 8095,
            "model": {"default": "m", "context_size": 131072},
        }
        with patch("hal0.providers.container._resolve_profile", return_value=profile):
            argv = resolved_command_for_slot(cfg, model_path="/mnt/ai-models/m.gguf")
        assert argv is not None
        assert "--ctx-size" in argv
        assert argv[argv.index("--ctx-size") + 1] == "131072"

    def test_resolved_command_includes_model_alias(self) -> None:
        """resolved_command shows --alias <model id> so it matches the unit."""
        profile = _moe_profile()
        cfg = {
            "profile": "rocm",
            "port": 8095,
            "model": {"default": "chadrock-35b-ace-saber", "context_size": 131072},
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

        def fake_run(*args: str, check: bool = True) -> MagicMock:
            calls_made.append(list(args))
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
        # Unit file must be deleted
        assert not unit_file.exists()

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


class TestContextSizeDerive:
    """Regression guard for the 2026-06-15 chat@4096 incident: a slot whose
    TOML pins no context_size must NEVER silently inherit llama-server's 4096
    default. container_spec derives the model's native window (dense-capped),
    or falls back to a safe 8192 when the native window is unknown."""

    def _spec(self, cfg: dict[str, Any], model_info: dict[str, Any]):
        provider = ContainerProvider()
        with patch(
            "hal0.providers.container._resolve_profile",
            return_value=_moe_profile(),
        ):
            return provider.container_spec(cfg, model_info)

    @staticmethod
    def _ctx(command: list[str]) -> str | None:
        return command[command.index("--ctx-size") + 1] if "--ctx-size" in command else None

    def test_unset_ctx_derives_native_dense_capped(self) -> None:
        cfg = _slot_cfg()  # [model] has no context_size
        mi = _model_info(metadata={"context_length": 131072})
        assert self._ctx(self._spec(cfg, mi).command) == "32768"

    def test_unset_ctx_unknown_native_falls_back_8192_not_4096(self) -> None:
        cfg = _slot_cfg()
        mi = _model_info()  # no metadata.context_length, no defaults.context_size
        ctx = self._ctx(self._spec(cfg, mi).command)
        assert ctx == "8192"
        assert ctx != "4096"

    def test_unset_ctx_uses_model_defaults_when_no_metadata(self) -> None:
        cfg = _slot_cfg()
        mi = _model_info(defaults={"context_size": 16384})
        assert self._ctx(self._spec(cfg, mi).command) == "16384"

    def test_explicit_ctx_always_wins_over_native(self) -> None:
        cfg = _slot_cfg(model={"default": "m.gguf", "context_size": 131072})
        mi = _model_info(metadata={"context_length": 262144})
        assert self._ctx(self._spec(cfg, mi).command) == "131072"


class TestFamilyDefaults:
    """FAMILY_DEFAULTS — the per-family override layer (gemma → f16 KV)."""

    def test_model_family_token_scan(self) -> None:
        assert model_family("gemma-4-12b-it-ud-q4-k-xl") == "gemma"
        assert model_family("Qwen3.6-27B-UD-Q5_K_XL.gguf") == "qwen"
        assert model_family(None, "/mnt/ai-models/gemma4-v2/gemma4-v2-Q4_K_M.gguf") == "gemma"
        assert model_family("chadrock-35b-ace-saber") is None
        assert model_family(None) is None

    def test_family_flags_lookup(self) -> None:
        assert "gemma" in FAMILY_DEFAULTS
        assert family_flags("gemma-4-12b-it") == FAMILY_DEFAULTS["gemma"]
        assert "-ctk f16" in family_flags("gemma-4-12b-it")
        assert family_flags("qwen3-27b") == ""  # no qwen entry today
        assert family_flags(None) == ""

    def test_gemma_on_q8_profile_pins_f16_kv(self) -> None:
        """A gemma model on a q8 profile resolves to f16 KV — profile's
        -ctk q8_0 is overridden and deduped away by the family layer."""
        profile = _moe_profile()  # ships -ctk q8_0 -ctv q8_0
        cfg = {"profile": "rocm", "port": 8095, "model": {"default": "gemma-4-12b-it"}}
        with patch("hal0.providers.container._resolve_profile", return_value=profile):
            argv = resolved_command_for_slot(cfg, model_path="/mnt/ai-models/gemma-4-12b-it.gguf")
        assert argv is not None
        assert "q8_0" not in argv  # deduped: family f16 wins, no stale q8 dup
        assert argv[argv.index("-ctk") + 1] == "f16"
        assert argv[argv.index("-ctv") + 1] == "f16"
        assert "--cache-reuse" in argv and argv[argv.index("--cache-reuse") + 1] == "0"

    def test_non_gemma_gets_no_family_kv_leak(self) -> None:
        """A qwen model has no FAMILY_DEFAULTS entry, so no family KV is forced.
        FLAGS-own: the profile's -ctk q8_0 is inert (the model owns its tune),
        and this registry-miss model carries none — so no -ctk leaks in, and
        crucially the gemma family f16 does NOT leak onto a non-family model."""
        profile = _moe_profile()
        cfg = {"profile": "rocm", "port": 8095, "model": {"default": "qwen3-27b"}}
        with patch("hal0.providers.container._resolve_profile", return_value=profile):
            argv = resolved_command_for_slot(cfg, model_path="/mnt/ai-models/qwen3-27b.gguf")
        assert argv is not None
        assert "-ctk" not in argv  # profile q8_0 inert; no model/family KV here
        assert "f16" not in argv  # no gemma family leak

    def test_slot_extra_args_is_inert_family_wins(self) -> None:
        """FLAGS-own: a slot [server].extra_args no longer beats the family
        default — it is inert. The gemma family f16 KV wins; a model that truly
        wants q4_0 carries it in its own defaults.extra_args (migrator-folded)."""
        profile = _moe_profile()
        cfg = {
            "profile": "rocm",
            "port": 8095,
            "model": {"default": "gemma-4-12b-it"},
            "server": {"extra_args": "-ctk q4_0 -ctv q4_0"},  # inert
        }
        with patch("hal0.providers.container._resolve_profile", return_value=profile):
            argv = resolved_command_for_slot(cfg, model_path="/mnt/ai-models/gemma-4-12b-it.gguf")
        assert argv is not None
        assert argv[argv.index("-ctk") + 1] == "f16"  # family wins, slot inert
        assert "q4_0" not in argv

    def test_vulkan_seed_is_basic_no_forced_kv_quant(self) -> None:
        """The vulkan seed ships minimal flags with NO forced KV quant.

        The 2026-07-05 seed cleanup reduced the plain ``vulkan`` seed to basic
        flags, so f16 (the llama-server default) applies universally — it is
        gemma-safe without relying on a family guard against a q8 vulkan seed,
        and per-model KV tuning now lives in the model's ``defaults.extra_args``.
        A slot on the basic vulkan seed therefore never gets a forced q8 KV.
        """
        vseed = SEED_PROFILES["chat"]
        assert "-ctk" not in vseed["flags"] and "q8_0" not in vseed["flags"]
        assert "-fa on" in vseed["flags"]

        profile = ProfileConfig(flags=vseed["flags"], mtp=False)
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
    """spec-hw-slot-ownership §2: the slot owns NGL + THREADS; the model no
    longer emits -ngl. Covers _llama_argv_segments' slot_hardware segment."""

    @staticmethod
    def _argv(**kw: Any) -> list[str]:
        segs = _llama_argv_segments(port=8081, model_path="/m.gguf", **kw)
        return resolve_argv(segs).argv

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

    def test_model_defaults_n_gpu_layers_does_not_emit_ngl(self) -> None:
        # defaults.n_gpu_layers is deleted; a stray key must NOT reach the argv.
        argv = self._argv(model_defaults={"n_gpu_layers": 77, "extra_args": "-fa on"})
        assert "-ngl" not in argv
        assert "-fa" in argv

    def test_slot_threads_wins_over_model_extra_args_collision(self) -> None:
        # slot_hardware sits after model_extra_args → the slot's --threads wins
        # last. (-ngl can't be tested this way: it is in MANAGED_ARGS_DENYLIST,
        # so a model extra_args -ngl is hard-rejected before it can collide.
        # --threads is not yet denylisted — Lane C adds SLOT_HARDWARE_FLAGS.)
        argv = self._argv(slot_threads=8, model_defaults={"extra_args": "--threads 3"})
        assert argv.count("--threads") == 1
        assert argv[argv.index("--threads") + 1] == "8"

    def test_segment_labels_include_slot_hardware_not_model_defaults(self) -> None:
        labels = [lbl for lbl, _toks in _llama_argv_segments(port=8081, model_path="/m.gguf")]
        assert "slot_hardware" in labels
        assert "model_defaults" not in labels
