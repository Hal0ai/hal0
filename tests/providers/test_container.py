"""Unit tests for ``hal0.providers.container.ContainerProvider``.

Issue #655 — tracer bullet: ContainerProvider unit-render + control-plane.

Covers:
  * _render_unit produces the expected podman ExecStart (flags merged from
    profile, identical-path /mnt/ai-models:ro mount, loopback port publish,
    numeric GIDs, apparmor/seccomp unconfined)
  * resolve_profile_flags MTP expansion
  * ContainerProvider.container_spec returns a ContainerSpec with correct
    image, command, mounts, and security opts
  * _is_container_slot routing helper (schema.py fields)
  * load_sync / unload_sync call the right systemctl commands (mocked)
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from hal0.config.schema import MTP_FLAG_BUNDLE, ProfileConfig, resolve_profile_flags
from hal0.providers.container import (
    _MODEL_STORE_MOUNT,
    ContainerProvider,
    _render_unit,
    resolved_command_for_slot,
)
from hal0.slots.manager import SlotManager

# Use a fixed runtime bin for tests so _render_unit doesn't need podman/docker.
_TEST_RUNTIME = "/usr/bin/docker"

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _moe_profile() -> ProfileConfig:
    return ProfileConfig(
        image="ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server",
        flags="-fa on -ctk q8_0 -ctv q8_0 -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap",
        mtp=False,
    )


def _mtp_profile() -> ProfileConfig:
    return ProfileConfig(
        image="ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server",
        flags="-fa on -ctk q8_0 -ctv q8_0 -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap",
        mtp=True,
    )


def _slot_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "test-container",
        "port": 8095,
        "profile": "moe-rocmfp4",
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
        profile = _mtp_profile()
        flags = resolve_profile_flags(profile)
        assert "--spec-type draft-mtp" in flags
        assert "--spec-draft-device ROCm0" in flags
        # Base flags are also present
        assert "-fa on" in flags

    def test_mtp_flag_bundle_constant_nonempty(self) -> None:
        assert "--spec-type draft-mtp" in MTP_FLAG_BUNDLE


# ── Unit rendering ────────────────────────────────────────────────────────────


class TestRenderUnit:
    """_render_unit produces correct podman ExecStart."""

    def _get_exec_start(self, unit_text: str) -> str:
        for line in unit_text.splitlines():
            if line.startswith("ExecStart="):
                return line[len("ExecStart=") :]
        raise AssertionError("ExecStart not found in unit text")

    def test_contains_container_run(self) -> None:
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_unit(
            "test-slot",
            profile.image,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            runtime_bin=_TEST_RUNTIME,
        )
        exec_start = self._get_exec_start(unit)
        assert exec_start.startswith(f"{_TEST_RUNTIME} run")

    def test_identical_path_mount_readonly(self) -> None:
        """Model store must be mounted at /mnt/ai-models:ro (identical path)."""
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_unit(
            "test-slot",
            profile.image,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            runtime_bin=_TEST_RUNTIME,
        )
        exec_start = self._get_exec_start(unit)
        tokens = shlex.split(exec_start)
        # --volume arg must be present with :ro suffix
        vol_args = [t for t in tokens if t.startswith(f"--volume={_MODEL_STORE_MOUNT}")]
        assert vol_args, f"no --volume for {_MODEL_STORE_MOUNT} in: {tokens}"
        assert ":ro" in vol_args[0], f"mount not read-only: {vol_args[0]}"

    def test_loopback_port_publish(self) -> None:
        """Port must be published on 127.0.0.1 only (not LAN-exposed)."""
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_unit(
            "test-slot",
            profile.image,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            runtime_bin=_TEST_RUNTIME,
        )
        exec_start = self._get_exec_start(unit)
        assert "127.0.0.1:8095:8095" in exec_start

    def test_device_passthrough(self) -> None:
        """Default device source is resolve_gpu_device_paths(); each node is
        passed explicitly via --device=, never the bare /dev/dri directory."""
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        with patch(
            "hal0.providers.container.resolve_gpu_device_paths",
            return_value=["/dev/kfd", "/dev/dri/renderD128"],
        ):
            unit = _render_unit(
                "test-slot",
                profile.image,
                8095,
                "/mnt/ai-models/model.gguf",
                flags,
                runtime_bin=_TEST_RUNTIME,
            )
        exec_start = self._get_exec_start(unit)
        tokens = shlex.split(exec_start)
        assert "--device=/dev/kfd" in tokens
        assert "--device=/dev/dri/renderD128" in tokens
        assert "--device=/dev/dri" not in tokens

    def test_explicit_device_nodes_emitted_no_bare_dri_dir(self) -> None:
        """With explicit device_paths, the unit passes each node verbatim and
        never the bare /dev/dri directory (which podman cannot recurse)."""
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_unit(
            "test-slot",
            profile.image,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            runtime_bin=_TEST_RUNTIME,
            device_paths=["/dev/kfd", "/dev/dri/renderD128"],
        )
        exec_start = self._get_exec_start(unit)
        tokens = shlex.split(exec_start)
        assert "--device=/dev/kfd" in tokens
        assert "--device=/dev/dri/renderD128" in tokens
        assert "--device=/dev/dri" not in tokens

    def test_ctx_size_in_exec_start(self) -> None:
        """The slot's context_size must reach the container as --ctx-size,
        else llama-server boots at its 4096 default (severe ctx regression)."""
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_unit(
            "test-slot",
            profile.image,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            runtime_bin=_TEST_RUNTIME,
            device_paths=["/dev/kfd", "/dev/dri/renderD128"],
            context_size=131072,
        )
        tokens = shlex.split(self._get_exec_start(unit))
        assert "--ctx-size" in tokens
        assert tokens[tokens.index("--ctx-size") + 1] == "131072"

    def test_server_extra_args_appended(self) -> None:
        """[server].extra_args is honored on the container path (override/legacy),
        appended after profile flags so slot-level flags win."""
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_unit(
            "test-slot",
            profile.image,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            runtime_bin=_TEST_RUNTIME,
            device_paths=["/dev/kfd", "/dev/dri/renderD128"],
            extra_args="--override-kv tokenizer.ggml.add_bos=bool:false",
        )
        tokens = shlex.split(self._get_exec_start(unit))
        assert "--override-kv" in tokens
        assert "tokenizer.ggml.add_bos=bool:false" in tokens

    def test_security_opts(self) -> None:
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_unit(
            "test-slot",
            profile.image,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            runtime_bin=_TEST_RUNTIME,
        )
        exec_start = self._get_exec_start(unit)
        assert "apparmor=unconfined" in exec_start
        assert "seccomp=unconfined" in exec_start

    def test_model_arg_in_exec_start(self) -> None:
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        model_path = "/mnt/ai-models/model.gguf"
        unit = _render_unit(
            "test-slot",
            profile.image,
            8095,
            model_path,
            flags,
            runtime_bin=_TEST_RUNTIME,
        )
        exec_start = self._get_exec_start(unit)
        # llama-server uses space-separated --model PATH (not --model=PATH)
        assert f"--model {model_path}" in exec_start

    def test_profile_flags_in_exec_start(self) -> None:
        """Bench-tuned profile flags must appear after image in ExecStart."""
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_unit(
            "test-slot",
            profile.image,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            runtime_bin=_TEST_RUNTIME,
        )
        exec_start = self._get_exec_start(unit)
        # Key profile flags from seed moe-rocmfp4
        assert "-fa" in exec_start
        assert "--no-mmap" in exec_start
        assert "-ctk" in exec_start

    def test_mtp_flags_in_exec_start_when_mtp_true(self) -> None:
        profile = _mtp_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_unit(
            "test-slot",
            profile.image,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            runtime_bin=_TEST_RUNTIME,
        )
        exec_start = self._get_exec_start(unit)
        assert "--spec-type" in exec_start

    def test_container_name_in_exec_stop(self) -> None:
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_unit(
            "test-slot",
            profile.image,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            runtime_bin=_TEST_RUNTIME,
        )
        for line in unit.splitlines():
            if line.startswith("ExecStop="):
                assert "hal0-slot-test-slot" in line
                break
        else:
            raise AssertionError("ExecStop not found in unit")

    def test_unit_has_service_section(self) -> None:
        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_unit(
            "test-slot",
            profile.image,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            runtime_bin=_TEST_RUNTIME,
        )
        assert "[Unit]" in unit
        assert "[Service]" in unit

    def test_numeric_group_add_present(self) -> None:
        """group-add must use numeric GIDs (toolbox images lack group names)."""
        from hal0.providers._gpu import resolve_gpu_group_ids

        profile = _moe_profile()
        flags = resolve_profile_flags(profile)
        unit = _render_unit(
            "test-slot",
            profile.image,
            8095,
            "/mnt/ai-models/model.gguf",
            flags,
            runtime_bin=_TEST_RUNTIME,
        )
        exec_start = self._get_exec_start(unit)
        gids = resolve_gpu_group_ids()
        for gid in gids:
            assert f"--group-add={gid}" in exec_start, f"GID {gid} not in ExecStart: {exec_start}"


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

    def test_image_matches_profile(self) -> None:
        spec = self._build_spec()
        assert spec.image == _moe_profile().image

    def test_model_arg_in_command(self) -> None:
        spec = self._build_spec()
        # llama-server uses space-separated args: --model PATH
        # So command is [..., "--model", "/mnt/ai-models/..."]
        assert "--model" in spec.command
        model_idx = spec.command.index("--model")
        model_val = spec.command[model_idx + 1] if model_idx + 1 < len(spec.command) else ""
        assert "/mnt/ai-models/" in model_val

    def test_mount_identical_path(self) -> None:
        spec = self._build_spec()
        mount_pairs = dict(spec.mounts)
        assert _MODEL_STORE_MOUNT in mount_pairs
        assert mount_pairs[_MODEL_STORE_MOUNT] == _MODEL_STORE_MOUNT

    def test_devices_present(self) -> None:
        with patch(
            "hal0.providers.container.resolve_gpu_device_paths",
            return_value=["/dev/kfd", "/dev/dri/renderD128"],
        ):
            spec = self._build_spec()
        assert spec.devices == ["/dev/kfd", "/dev/dri/renderD128"]

    def test_security_opts(self) -> None:
        spec = self._build_spec()
        assert "apparmor=unconfined" in spec.security_opt
        assert "seccomp=unconfined" in spec.security_opt

    def test_publish_in_extra_args(self) -> None:
        """Loopback port publish must be in extra_args (not network_mode=host)."""
        spec = self._build_spec()
        publish_args = [a for a in spec.extra_args if "127.0.0.1" in a]
        assert publish_args, f"no loopback publish in extra_args: {spec.extra_args}"
        assert "8095" in publish_args[0]

    def test_network_mode_empty(self) -> None:
        """network_mode must be empty (not 'host') so loopback publish is used."""
        spec = self._build_spec()
        assert spec.network_mode == ""


# ── _is_container_slot routing ───────────────────────────────────────────────


class TestIsContainerSlot:
    def test_profile_set_is_container(self) -> None:
        cfg = {"name": "test", "port": 8095, "profile": "moe-rocmfp4"}
        assert SlotManager._is_container_slot(cfg) is True

    def test_runtime_container_is_container(self) -> None:
        cfg = {"name": "test", "port": 8095, "runtime": "container"}
        assert SlotManager._is_container_slot(cfg) is True

    def test_default_is_lemonade(self) -> None:
        cfg = {"name": "test", "port": 8081, "device": "gpu-rocm"}
        assert SlotManager._is_container_slot(cfg) is False

    def test_runtime_lemonade_is_not_container(self) -> None:
        cfg = {"name": "test", "port": 8081, "runtime": "lemonade"}
        assert SlotManager._is_container_slot(cfg) is False

    def test_profile_none_not_container(self) -> None:
        cfg = {"name": "test", "port": 8081, "profile": None}
        assert SlotManager._is_container_slot(cfg) is False

    def test_profile_empty_string_not_container(self) -> None:
        cfg = {"name": "test", "port": 8081, "profile": ""}
        assert SlotManager._is_container_slot(cfg) is False


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
                {"name": "test-container", "port": 8095, "profile": "moe-rocmfp4"},
                {"path": "/mnt/ai-models/model.gguf", "_model_key": "model"},
            )

        cmds = [" ".join(c) for c in calls_made]
        assert any("daemon-reload" in c for c in cmds), f"daemon-reload not in {cmds}"
        assert any("restart" in c for c in cmds), f"restart not in {cmds}"
        assert (tmp_path / "test.service").exists()

    def test_load_sync_threads_ctx_size_and_extra_args(self, tmp_path: Path) -> None:
        """load_sync must pull context_size + [server].extra_args off the slot
        cfg and bake them into the rendered unit."""
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
                    "profile": "moe-rocmfp4",
                    "model": {"default": "model", "context_size": 131072},
                    "server": {"extra_args": "--override-kv k=bool:false"},
                },
                {"path": "/mnt/ai-models/model.gguf", "_model_key": "model"},
            )

        unit = unit_file.read_text()
        assert "--ctx-size 131072" in unit
        assert "--override-kv k=bool:false" in unit

    def test_resolved_command_includes_ctx_size(self) -> None:
        """The displayed resolved_command must show --ctx-size so it matches
        what actually launches."""
        profile = _moe_profile()
        cfg = {
            "profile": "moe-rocmfp4",
            "port": 8095,
            "model": {"default": "m", "context_size": 131072},
        }
        with patch("hal0.providers.container._resolve_profile", return_value=profile):
            argv = resolved_command_for_slot(cfg, model_path="/mnt/ai-models/m.gguf")
        assert argv is not None
        assert "--ctx-size" in argv
        assert argv[argv.index("--ctx-size") + 1] == "131072"

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
