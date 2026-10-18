"""The llama argv a model's file facts produce is unchanged by ADR-0029 §6.

§6 moved ``chat_template``, ``jinja``, ``mtp`` and ``tokenizer_repo`` out of
the nested ``Model.defaults`` object onto top-level ``Model`` fields. The
golden commands below were generated from the code before that change, with
the same facts stored under ``defaults``; this test feeds the new top-level
shape through the real path (``Model`` → SQLite registry row → ``model_dump``
→ ``container_spec``) and requires the identical argv.

The three cases cover each fact's launch effect: a pinned template with
``jinja = false`` and explicit MTP heads, the ``mtp`` tag deciding when the fact
is unset, and an explicit ``mtp = false`` overriding the tag.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hal0.config.paths import model_store_root
from hal0.config.schema import ProfileConfig
from hal0.providers.container import ContainerProvider
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry

_MTP_DRAFT = [
    "--spec-type", "draft-mtp",
    "--spec-draft-ngl", "all",
    "--spec-draft-n-max", "4",
    "--spec-draft-n-min", "0",
    "--spec-draft-p-min", "0.0",
    "--spec-draft-p-split", "0.10",
    "--spec-draft-type-k", "q8_0",
    "--spec-draft-type-v", "q8_0",
    "--spec-draft-threads", "16",
    "--spec-draft-threads-batch", "32",
    "--spec-draft-poll", "1",
    "--spec-draft-poll-batch", "1",
]  # fmt: skip

_CASES: dict[str, dict[str, Any]] = {
    "pinned": {
        "facts": {
            "chat_template": "chatml",
            "jinja": False,
            "mtp": True,
            "tokenizer_repo": "org/repo",
        },
        "tags": [],
        "model_layer": ["--chat-template-file", "<chatml>", "--mmproj", "/models/mmproj.gguf"],
        "drafts": True,
    },
    "tagged": {
        "facts": {"chat_template": "auto", "jinja": True, "mtp": None},
        "tags": ["mtp"],
        "model_layer": ["--jinja", "--mmproj", "/models/mmproj.gguf"],
        "drafts": True,
    },
    "vetoed": {
        "facts": {"chat_template": None, "jinja": None, "mtp": False},
        "tags": ["mtp"],
        "model_layer": ["--jinja", "--mmproj", "/models/mmproj.gguf"],
        "drafts": False,
    },
}


def _golden(name: str) -> list[str]:
    """The argv the pre-§6 code launched for the same facts under ``defaults``."""
    case = _CASES[name]
    template = str(Path(model_store_root()) / "chat-templates" / "chatml.jinja")
    model_layer = [template if tok == "<chatml>" else tok for tok in case["model_layer"]]
    base = [
        "--host", "0.0.0.0",
        "--port", "8101",
        "--model", f"/models/m-{name}.gguf",
        "--alias", f"m-{name}",
        "--ctx-size", "32768",
    ]  # fmt: skip
    profile = [*(_MTP_DRAFT if case["drafts"] else []), "-fa", "on", "--parallel", "1"]
    slot = ["-ngl", "99", "--metrics"]
    if case["drafts"]:
        slot += ["--spec-draft-device", "ROCm0"]
    return [*base, *model_layer, *profile, *slot]


@pytest.mark.parametrize("name", sorted(_CASES))
def test_top_level_facts_launch_the_pre_adr_argv(name: str, tmp_path: Path) -> None:
    case = _CASES[name]
    registry = ModelRegistry(registry_dir=tmp_path / "registry")
    registry.add(
        Model(
            id=f"m-{name}",
            path=f"/models/m-{name}.gguf",
            tags=case["tags"],
            mmproj="/models/mmproj.gguf",
            metadata={"context_length": 131072},
            **case["facts"],
        )
    )
    stored = registry.get(f"m-{name}")
    for fact, value in case["facts"].items():
        assert getattr(stored, fact) == value, fact

    model_info = {"_model_key": stored.id, **stored.model_dump()}
    assert "defaults" not in model_info
    cfg = {
        "name": "chat",
        "port": 8101,
        "profile": "p",
        "device": "gpu-rocm",
        "n_gpu_layers": 99,
        "model": {"default": stored.id},
    }
    profile = ProfileConfig(flags="-fa on --parallel 1", speculation="auto", ctx_size=32768)
    with patch("hal0.providers.container._resolve_profile", return_value=profile):
        plan = ContainerProvider().container_spec(cfg, model_info)

    assert plan.command == _golden(name)


def test_a_paired_drafter_launches_from_the_registry_row(tmp_path: Path) -> None:
    """ADR-0031 §3 through the real row: ``draft`` / ``draft_kind`` and
    ``metadata.draft`` survive the registry round trip and launch the drafter
    in the model layer, the per-kind bundle in the profile layer."""
    registry = ModelRegistry(registry_dir=tmp_path / "registry")
    registry.add(
        Model(
            id="m-draft",
            path="/models/m-draft.gguf",
            tags=["mtp"],
            draft="/models/m-draft-dflash.gguf",
            draft_kind="dflash",
            metadata={"context_length": 131072, "draft": {"variant": "dflash", "block_size": 8}},
        )
    )
    model_info = {"_model_key": "m-draft", **registry.get("m-draft").model_dump()}
    cfg = {
        "name": "chat",
        "port": 8101,
        "profile": "p",
        "device": "gpu-rocm",
        "n_gpu_layers": 99,
        "model": {"default": "m-draft"},
    }
    profile = ProfileConfig(flags="-fa on", speculation="auto", ctx_size=32768)
    with patch("hal0.providers.container._resolve_profile", return_value=profile):
        argv = list(ContainerProvider().container_spec(cfg, model_info).command)

    assert argv.count("--spec-type") == 1
    assert argv[argv.index("--spec-type") + 1] == "draft-dflash"
    assert argv[argv.index("--spec-draft-model") + 1] == "/models/m-draft-dflash.gguf"
    assert argv[argv.index("--spec-draft-n-max") + 1] == "7"
    # An external drafter follows the model's devices: no pin.
    assert "--spec-draft-device" not in argv
