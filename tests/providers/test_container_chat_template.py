"""Tests: container emits --chat-template-file from the resolved chat_template.

Task 3 / Phase 3 — slot-config-phase3-templates. Updated for FLAGS-own §7
(slot-purity fold): the chat template is model-intrinsic, so it is resolved
ONLY from ``model.defaults.chat_template``. The per-slot ``chat_template`` is
sunset/inert at launch (the migrator folds it into the bound model).

Covers:
  * When model_info carries defaults.chat_template the flag is emitted with
    ``--chat-template-file <store>/chat-templates/<id>.jinja``.
  * A residual slot ``chat_template`` is inert — it neither emits the flag on
    its own nor overrides the model default.
  * When the model specifies no template (or 'auto' / None),
    ``--chat-template-file`` is ABSENT from the command.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from hal0.config.paths import model_store_root
from hal0.config.schema import ProfileConfig
from hal0.providers.container import ContainerProvider, _llama_launch_plan

# ── shared helpers ────────────────────────────────────────────────────────────


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
            flags_str="",
            devices=[],
            group_ids=[],
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
            flags_str="",
            devices=[],
            group_ids=[],
            chat_template_path=None,
        )
        assert "--chat-template-file" not in plan.command

    def test_chat_template_typed_segment_wins_over_tune_duplicate(self) -> None:
        """FLAGS-own: chat_template is a TYPED model field resolved into the
        ``chat_template`` segment, which sits AFTER ``model_extra_args``. So a
        redundant ``--chat-template-file`` smuggled into the model's freeform
        ``defaults.extra_args`` is deduped away and the typed resolution wins
        (the operator sets the typed chat_template, not a raw flag)."""
        tmpl_path = "/mnt/ai-models/chat-templates/llama3.jinja"
        smuggled = "/mnt/ai-models/chat-templates/override.jinja"
        plan = _llama_launch_plan(
            image="img:latest",
            port=8095,
            model_path="/mnt/ai-models/m.gguf",
            flags_str="",
            devices=[],
            group_ids=[],
            model_defaults={"extra_args": f"--chat-template-file {smuggled}"},
            chat_template_path=tmpl_path,
        )
        cmd = plan.command
        assert cmd.count("--chat-template-file") == 1
        idx = cmd.index("--chat-template-file")
        assert cmd[idx + 1] == tmpl_path  # typed resolution wins, smuggled deduped


# ── container_spec integration tests ─────────────────────────────────────────


class TestContainerSpecChatTemplate:
    """container_spec emits --chat-template-file from resolved slot/model template."""

    def test_slot_chat_template_is_inert(self) -> None:
        """FLAGS-own §7: chat_template is model-intrinsic, so a slot-only
        override is SUNSET/inert at launch — it no longer emits the flag. The
        migrator folds it into the model; only model.defaults.chat_template
        reaches the command now (see test below)."""
        cfg = _slot_cfg(chat_template="chatml")
        spec = _build_spec(cfg, _model_info())  # model carries no template
        assert "--chat-template-file" not in spec.command, (
            f"sunset slot chat_template must not reach the command: {spec.command}"
        )

    def test_model_defaults_chat_template_emitted_when_no_slot_override(self) -> None:
        """model_info['defaults']['chat_template'] = 'llama3' → flag emitted."""
        cfg = _slot_cfg()  # no chat_template in slot
        mi = _model_info(defaults={"chat_template": "llama3"})
        spec = _build_spec(cfg, mi)
        store = model_store_root()
        expected_path = str(store) + "/chat-templates/llama3.jinja"

        assert "--chat-template-file" in spec.command, (
            f"--chat-template-file missing from command: {spec.command}"
        )
        idx = spec.command.index("--chat-template-file")
        assert spec.command[idx + 1] == expected_path

    def test_model_default_wins_slot_override_inert(self) -> None:
        """FLAGS-own §7: the model default is now the single launch source —
        a residual slot chat_template is inert and does NOT override it.
        (Pre-§7 the slot's 'chatml' won; now the model's 'llama3' wins.)"""
        cfg = _slot_cfg(chat_template="chatml")
        mi = _model_info(defaults={"chat_template": "llama3"})
        spec = _build_spec(cfg, mi)

        assert "--chat-template-file" in spec.command
        idx = spec.command.index("--chat-template-file")
        assert "llama3.jinja" in spec.command[idx + 1], (
            "model default 'llama3' must win; the slot override is sunset/inert"
        )

    def test_no_chat_template_flag_when_neither_set(self) -> None:
        """slot_cfg and model_info both without chat_template → flag absent."""
        cfg = _slot_cfg()
        spec = _build_spec(cfg, _model_info())
        assert "--chat-template-file" not in spec.command, (
            f"--chat-template-file must be absent: {spec.command}"
        )

    def test_auto_chat_template_treated_as_none(self) -> None:
        """chat_template='auto' is equivalent to no template — flag absent."""
        cfg = _slot_cfg(chat_template="auto")
        spec = _build_spec(cfg, _model_info())
        assert "--chat-template-file" not in spec.command, (
            f"'auto' must not produce --chat-template-file: {spec.command}"
        )

    def test_model_auto_chat_template_treated_as_none(self) -> None:
        """model defaults.chat_template='auto' is equivalent to no template."""
        cfg = _slot_cfg()
        mi = _model_info(defaults={"chat_template": "auto"})
        spec = _build_spec(cfg, mi)
        assert "--chat-template-file" not in spec.command


# ── load_sync integration (unit file contains the flag) ──────────────────────


class TestLoadSyncChatTemplate:
    """load_sync with chat_template set → the written unit contains the flag."""

    _TEST_RUNTIME = "/usr/bin/docker"

    def test_unit_contains_chat_template_file_flag(self, tmp_path) -> None:
        """§7: the template rides model.defaults (model-intrinsic), and load_sync
        writes it into the unit. Pre-§7 this drove off a slot chat_template; the
        slot key is now sunset/inert so the model carries it."""
        provider = ContainerProvider()
        profile = _moe_profile()
        unit_file = tmp_path / "test.service"

        from unittest.mock import MagicMock

        def fake_run(*args: str, check: bool = True) -> MagicMock:
            m = MagicMock()
            m.returncode = 0
            return m

        store = model_store_root()
        expected_path = str(store) + "/chat-templates/chatml.jinja"

        with (
            patch("hal0.providers.container._resolve_profile", return_value=profile),
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
                    "defaults": {"chat_template": "chatml"},
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
