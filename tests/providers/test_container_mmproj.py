"""Tests: container provider emits --mmproj from the model's sidecar (#900).

Mirrors the native llama_server.py provider, which already appends
``--mmproj <path>`` when ``model_info["mmproj"]`` is set. This is what lets a
container-runtime llama-server slot load the multimodal projector and report
``modalities.vision: true`` without the hand-written ``[server].extra_args``
hack that previously lived in the live chat.toml.

Covers:
  * ``_llama_launch_plan`` appends ``--mmproj <path>`` when given, omits it when None.
  * A manual ``--mmproj`` in ``extra_args`` overrides the sidecar; normalize_argv
    dedups to one flag (last-wins) so there's no duplicate in the command.
  * ``container_spec`` reads ``model_info["mmproj"]`` and emits the flag.
  * No flag when the model carries no sidecar (no regression for text-only slots).
  * ``load_sync`` writes a unit file containing the flag when a sidecar is present.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from hal0.config.schema import ProfileConfig
from hal0.providers.container import ContainerProvider, _llama_launch_plan

_SIDECAR = "/mnt/ai-models/qwopus3.6-27b-v2/mmproj-F32.mmproj"


# ── shared helpers (parallel to test_container_chat_template.py) ───────────────


def _moe_profile() -> ProfileConfig:
    return ProfileConfig(
        flags="-fa on -ctk q8_0 -ctv q8_0 -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap",
        mtp=False,
    )


def _slot_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "test-container",
        "port": 8095,
        "profile": "rocm",
        "runtime": "container",
        "device": "gpu-rocm",
        "model": {"default": "chadrock-35b.gguf"},
    }
    base.update(overrides)
    return base


def _model_info(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "path": "/mnt/ai-models/chadrock-35b.gguf",
        "_model_key": "chadrock-35b",
    }
    base.update(overrides)
    return base


def _build_spec(slot_cfg: dict[str, Any], model_info: dict[str, Any]):
    provider = ContainerProvider()
    with (
        patch("hal0.providers.container._resolve_profile", return_value=_moe_profile()),
        patch(
            "hal0.providers.container.resolve_gpu_device_paths",
            return_value=["/dev/kfd", "/dev/dri/renderD128"],
        ),
        patch("hal0.providers.container.resolve_gpu_group_ids", return_value=[]),
    ):
        return provider.container_spec(slot_cfg, model_info)


# ── _llama_launch_plan direct tests ──────────────────────────────────────────


class TestLlamaLaunchPlanMmproj:
    def test_mmproj_flag_present_when_path_set(self) -> None:
        plan = _llama_launch_plan(
            image="img:latest",
            port=8095,
            model_path="/mnt/ai-models/m.gguf",
            flags_str="",
            devices=[],
            group_ids=[],
            mmproj=_SIDECAR,
        )
        assert "--mmproj" in plan.command
        idx = plan.command.index("--mmproj")
        assert plan.command[idx + 1] == _SIDECAR

    def test_mmproj_flag_absent_when_none(self) -> None:
        plan = _llama_launch_plan(
            image="img:latest",
            port=8095,
            model_path="/mnt/ai-models/m.gguf",
            flags_str="",
            devices=[],
            group_ids=[],
            mmproj=None,
        )
        assert "--mmproj" not in plan.command

    def test_mmproj_sidecar_wins_over_tune_duplicate(self) -> None:
        """FLAGS-own: the mmproj sidecar rides the typed ``mmproj`` segment,
        which sits AFTER ``model_extra_args``. So a redundant ``--mmproj``
        smuggled into the model's freeform ``defaults.extra_args`` is deduped
        away and the resolved sidecar wins (the sidecar is model-intrinsic)."""
        smuggled = "/mnt/ai-models/override/mmproj.gguf"
        plan = _llama_launch_plan(
            image="img:latest",
            port=8095,
            model_path="/mnt/ai-models/m.gguf",
            flags_str="",
            devices=[],
            group_ids=[],
            model_defaults={"extra_args": f"--mmproj {smuggled}"},
            mmproj=_SIDECAR,
        )
        cmd = plan.command
        assert cmd.count("--mmproj") == 1
        idx = cmd.index("--mmproj")
        assert cmd[idx + 1] == _SIDECAR  # resolved sidecar wins, smuggled deduped


# ── container_spec integration tests ─────────────────────────────────────────


class TestContainerSpecMmproj:
    def test_mmproj_emitted_from_model_info(self) -> None:
        spec = _build_spec(_slot_cfg(), _model_info(mmproj=_SIDECAR))
        assert "--mmproj" in spec.command, f"--mmproj missing from command: {spec.command}"
        idx = spec.command.index("--mmproj")
        assert spec.command[idx + 1] == _SIDECAR

    def test_no_mmproj_when_model_has_no_sidecar(self) -> None:
        spec = _build_spec(_slot_cfg(), _model_info())  # mmproj absent
        assert "--mmproj" not in spec.command, (
            f"--mmproj must be absent for a text-only model: {spec.command}"
        )

    def test_no_mmproj_when_sidecar_is_none(self) -> None:
        spec = _build_spec(_slot_cfg(), _model_info(mmproj=None))
        assert "--mmproj" not in spec.command


# ── load_sync integration (unit file contains the flag) ──────────────────────


class TestLoadSyncMmproj:
    _TEST_RUNTIME = "/usr/bin/docker"

    def _load(self, tmp_path, model_info: dict[str, Any]) -> str:
        provider = ContainerProvider()
        unit_file = tmp_path / "test.service"

        def fake_run(*args: str, check: bool = True) -> MagicMock:
            m = MagicMock()
            m.returncode = 0
            return m

        with (
            patch("hal0.providers.container._resolve_profile", return_value=_moe_profile()),
            patch(
                "hal0.providers.container.resolve_gpu_device_paths",
                return_value=["/dev/kfd", "/dev/dri/renderD128"],
            ),
            patch("hal0.providers.container.resolve_gpu_group_ids", return_value=[]),
            patch("hal0.providers.container._container_runtime", return_value=self._TEST_RUNTIME),
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=unit_file),
        ):
            provider.load_sync(_slot_cfg(), model_info)
        return unit_file.read_text()

    def test_unit_contains_mmproj_flag(self, tmp_path) -> None:
        unit = self._load(tmp_path, _model_info(mmproj=_SIDECAR))
        assert "--mmproj" in unit, f"flag not in unit:\n{unit}"
        assert _SIDECAR in unit, f"sidecar path not in unit:\n{unit}"

    def test_unit_no_mmproj_flag_when_unset(self, tmp_path) -> None:
        unit = self._load(tmp_path, _model_info())
        assert "--mmproj" not in unit, f"--mmproj must be absent when unset:\n{unit}"
