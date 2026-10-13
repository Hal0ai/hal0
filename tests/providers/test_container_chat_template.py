"""Tests: container emits --chat-template-file from the resolved chat_template.

ADR-0029: the chat template is a fact about the model file, resolved ONLY
from the model's top-level ``chat_template`` into the model layer of the argv.

Covers:
  * When model_info carries chat_template the flag is emitted with
    ``--chat-template-file <store>/chat-templates/<id>.jinja``.
  * A slot still carrying ``chat_template`` is unconverted and refuses to
    launch; a profile carrying ``--chat-template-file`` refuses too.
  * When the model specifies no template (or 'auto' / None),
    ``--chat-template-file`` is ABSENT from the command.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from hal0.config.paths import model_store_root
from hal0.config.schema import ProfileConfig
from hal0.errors import BadRequest, UnprocessableEntity
from hal0.providers.container import ContainerProvider, _llama_launch_plan

# ── shared helpers ────────────────────────────────────────────────────────────


def _moe_profile() -> ProfileConfig:
    return ProfileConfig(
        flags="-fa on -ctk q8_0 -ctv q8_0 -b 512 -ub 512 --parallel 1 --no-mmap",
        mtp="off",
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
        patch(
            "hal0.providers.container.resolve_gpu_group_ids",
            return_value=[],
        ),
    ):
        return provider.container_spec(slot_cfg, model_info)


# ── _llama_launch_plan direct tests ──────────────────────────────────────────


class TestLlamaLaunchPlanChatTemplate:
    """Unit-test _llama_launch_plan directly with chat_template_path."""

    def test_chat_template_flag_present_when_path_set(self) -> None:
        """When chat_template_path is given, command contains the flag+value."""
        expected_path = str(model_store_root()) + "/chat-templates/chatml.jinja"
        plan = _llama_launch_plan(
            image="img:latest",
            port=8095,
            model_path="/mnt/ai-models/m.gguf",
            devices=[],
            group_ids=[],
            context_size=8192,
            chat_template_path=expected_path,
        )
        assert "--chat-template-file" in plan.command
        idx = plan.command.index("--chat-template-file")
        assert plan.command[idx + 1] == expected_path

    def test_chat_template_flag_absent_when_no_path(self) -> None:
        """When chat_template_path is None, --chat-template-file must not appear."""
        plan = _llama_launch_plan(
            image="img:latest",
            port=8095,
            model_path="/mnt/ai-models/m.gguf",
            devices=[],
            group_ids=[],
            context_size=8192,
            chat_template_path=None,
        )
        assert "--chat-template-file" not in plan.command

    def test_profile_chat_template_flag_refuses_to_launch(self) -> None:
        """The template is the model's fact (model layer). A profile that
        smuggles ``--chat-template-file`` would land AFTER it and win, so the
        launch refuses the profile instead."""
        tmpl_path = "/mnt/ai-models/chat-templates/llama3.jinja"
        smuggled = "/mnt/ai-models/chat-templates/override.jinja"
        with pytest.raises(BadRequest) as excinfo:
            _llama_launch_plan(
                image="img:latest",
                port=8095,
                model_path="/mnt/ai-models/m.gguf",
                devices=[],
                group_ids=[],
                context_size=8192,
                profile_flags=f"--chat-template-file {smuggled}",
                chat_template_path=tmpl_path,
            )
        assert excinfo.value.code == "profiles.model_fact_flag_denied"


# ── container_spec integration tests ─────────────────────────────────────────


class TestContainerSpecChatTemplate:
    """container_spec emits --chat-template-file from resolved slot/model template."""

    def test_slot_chat_template_refuses_to_launch(self) -> None:
        """A slot ``chat_template`` key is pre-conversion debris: the slot is
        unconverted and refuses to launch rather than silently ignoring it."""
        cfg = _slot_cfg(chat_template="chatml")
        with pytest.raises(UnprocessableEntity) as excinfo:
            _build_spec(cfg, _model_info(chat_template="llama3"))
        assert excinfo.value.code == "slot.unconverted"
        assert "chat_template" in excinfo.value.details["keys"]

    def test_model_chat_template_emitted_when_no_slot_override(self) -> None:
        """model_info['chat_template'] = 'llama3' → flag emitted."""
        cfg = _slot_cfg()  # no chat_template in slot
        mi = _model_info(chat_template="llama3")
        spec = _build_spec(cfg, mi)
        store = model_store_root()
        expected_path = str(store) + "/chat-templates/llama3.jinja"

        assert "--chat-template-file" in spec.command, (
            f"--chat-template-file missing from command: {spec.command}"
        )
        idx = spec.command.index("--chat-template-file")
        assert spec.command[idx + 1] == expected_path

    def test_no_chat_template_flag_when_neither_set(self) -> None:
        """slot_cfg and model_info both without chat_template → flag absent."""
        cfg = _slot_cfg()
        spec = _build_spec(cfg, _model_info())
        assert "--chat-template-file" not in spec.command, (
            f"--chat-template-file must be absent: {spec.command}"
        )

    def test_model_auto_chat_template_treated_as_none(self) -> None:
        """model chat_template='auto' is equivalent to no template."""
        cfg = _slot_cfg()
        mi = _model_info(chat_template="auto")
        spec = _build_spec(cfg, mi)
        assert "--chat-template-file" not in spec.command


# ── load_sync integration (unit file contains the flag) ──────────────────────


class TestLoadSyncChatTemplate:
    """load_sync with chat_template set → the written unit contains the flag."""

    _TEST_RUNTIME = "/usr/bin/docker"

    def test_unit_contains_chat_template_file_flag(self, tmp_path) -> None:
        """The template rides the model's chat_template fact, and load_sync
        writes it into the unit."""
        provider = ContainerProvider()
        profile = _moe_profile()
        unit_file = tmp_path / "test.service"

        from unittest.mock import MagicMock

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            m = MagicMock()
            m.returncode = 0
            return m

        store = model_store_root()
        expected_path = str(store) + "/chat-templates/chatml.jinja"

        with (
            patch("hal0.providers.container._resolve_profile", return_value=profile),
            # #1888 guard: these tests exercise UNIT RENDERING, not host GPU
            # validity — the box running the suite has no /dev/kfd.
            patch("hal0.providers.container.require_kfd_for_gpu_slot"),
            patch(
                "hal0.providers.container.resolve_gpu_device_paths",
                return_value=["/dev/kfd", "/dev/dri/renderD128"],
            ),
            patch(
                "hal0.providers.container.resolve_gpu_group_ids",
                return_value=[],
            ),
            patch("hal0.providers.container._container_runtime", return_value=self._TEST_RUNTIME),
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=unit_file),
        ):
            provider.load_sync(
                {
                    "name": "test-container",
                    "port": 8095,
                    "profile": "rocm",
                    "device": "gpu-rocm",
                    "model": {"default": "chadrock-35b.gguf"},
                },
                {
                    "path": "/mnt/ai-models/chadrock-35b.gguf",
                    "_model_key": "chadrock-35b",
                    "chat_template": "chatml",
                },
            )

        unit = unit_file.read_text()
        assert "--chat-template-file" in unit, f"flag not in unit:\n{unit}"
        # The path must also appear in the unit
        assert "chatml.jinja" in unit, f"template jinja path not in unit:\n{unit}"
        # Verify the exact path is present (not some different store)
        assert expected_path in unit, f"expected path {expected_path!r} not in unit:\n{unit}"

    def test_unit_no_chat_template_flag_when_unset(self, tmp_path) -> None:
        provider = ContainerProvider()
        profile = _moe_profile()
        unit_file = tmp_path / "test.service"

        from unittest.mock import MagicMock

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            m = MagicMock()
            m.returncode = 0
            return m

        with (
            patch("hal0.providers.container._resolve_profile", return_value=profile),
            # #1888 guard: these tests exercise UNIT RENDERING, not host GPU
            # validity — the box running the suite has no /dev/kfd.
            patch("hal0.providers.container.require_kfd_for_gpu_slot"),
            patch(
                "hal0.providers.container.resolve_gpu_device_paths",
                return_value=["/dev/kfd", "/dev/dri/renderD128"],
            ),
            patch(
                "hal0.providers.container.resolve_gpu_group_ids",
                return_value=[],
            ),
            patch("hal0.providers.container._container_runtime", return_value=self._TEST_RUNTIME),
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=unit_file),
        ):
            provider.load_sync(
                {
                    "name": "test-container",
                    "port": 8095,
                    "profile": "rocm",
                    "device": "gpu-rocm",
                    "model": {"default": "chadrock-35b.gguf"},
                    # no chat_template
                },
                {"path": "/mnt/ai-models/chadrock-35b.gguf", "_model_key": "chadrock-35b"},
            )

        unit = unit_file.read_text()
        assert "--chat-template-file" not in unit, (
            f"--chat-template-file must be absent when unset:\n{unit}"
        )
