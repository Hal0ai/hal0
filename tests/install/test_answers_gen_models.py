"""``gen.models`` in the answer file: a list of image-catalog ids (ADR-0037)."""

from __future__ import annotations

import textwrap
import warnings
from pathlib import Path

import pytest
import yaml

from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.install.answers import (
    AnswersError,
    dump_answers,
    gen_download_requested,
    load_answers,
    write_answers,
)
from hal0.install.orchestrate import Selections


def _hw() -> HardwareInfo:
    return HardwareInfo(
        platform="strix-halo",
        ram_mb=96 * 1024,
        ram_available_mb=96 * 1024,
        unified_memory_mb=96 * 1024,
        gpus=[GPUInfo(vendor="amd", vram_mb=512, compute_capable=True, vulkan_capable=True)],
        npu=NPUInfo(present=False),
    )


def _answers(tmp_path: Path, gen: str) -> str:
    path = tmp_path / "hal0-setup.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            version: 1
            model_store: { path: /var/lib/hal0/models }
            slots: []
            npu: { opt_in: false }
            apps: { openwebui: { enabled: true }, hermes: { enabled: false } }
            """
        )
        + f"gen: {gen}\n"
    )
    return str(path)


def test_answers_gen_models_ok(tmp_path: Path) -> None:
    path = _answers(
        tmp_path,
        "{ mode: scaffold_and_download, models: [sdxl-lightning, sd-1.5-pruned-emaonly] }",
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a recognised key must not warn
        sel = load_answers(path, _hw())

    assert sel.image_models == ("sdxl-lightning", "sd-1.5-pruned-emaonly")
    assert sel.extensions["comfyui"] is True
    assert gen_download_requested(path) is True


def test_answers_gen_models_absent_or_empty_means_none(tmp_path: Path) -> None:
    assert load_answers(_answers(tmp_path, "{ mode: scaffold_only }"), _hw()).image_models == ()
    empty = _answers(tmp_path, "{ mode: scaffold_only, models: [] }")
    assert load_answers(empty, _hw()).image_models == ()


@pytest.mark.parametrize("mode", ["scaffold_only", "off"])
def test_answers_gen_models_warns_when_the_mode_will_not_download_them(
    tmp_path: Path, mode: str
) -> None:
    """Models named but never fetched would otherwise be dropped without a word (M13a)."""
    path = _answers(tmp_path, f"{{ mode: {mode}, models: [sdxl-lightning, esrgan-4x] }}")

    with pytest.warns(UserWarning, match=r"gen\.models.*will not be downloaded") as caught:
        sel = load_answers(path, _hw())

    assert mode in str(caught[0].message)
    assert "scaffold_and_download" in str(caught[0].message)  # says how to get them
    assert sel.image_models == ("sdxl-lightning", "esrgan-4x")
    assert gen_download_requested(path) is False


@pytest.mark.parametrize("gen", ["{ mode: scaffold_only }", "{ mode: off, models: [] }"])
def test_answers_gen_without_models_does_not_warn(tmp_path: Path, gen: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        load_answers(_answers(tmp_path, gen), _hw())


def test_answers_gen_models_repeated_id_installs_once(tmp_path: Path) -> None:
    path = _answers(tmp_path, "{ mode: scaffold_and_download, models: [sdxl-turbo, sdxl-turbo] }")

    assert load_answers(path, _hw()).image_models == ("sdxl-turbo",)


def test_answers_gen_models_unknown_id_raises(tmp_path: Path) -> None:
    path = _answers(tmp_path, "{ mode: scaffold_and_download, models: [sdxl-lightning, nope-xl] }")

    with pytest.raises(AnswersError) as exc:
        load_answers(path, _hw())

    message = str(exc.value)
    assert "nope-xl" in message
    assert "sdxl-lightning" in message  # lists what is known


@pytest.mark.parametrize("models", ["sdxl-lightning", "{ txt2img: sdxl-lightning }", "[7]"])
def test_answers_gen_models_must_be_a_list_of_ids(tmp_path: Path, models: str) -> None:
    path = _answers(tmp_path, f"{{ mode: scaffold_and_download, models: {models} }}")

    with pytest.raises(AnswersError, match=r"gen\.models"):
        load_answers(path, _hw())


@pytest.mark.parametrize("capabilities", ["{ txt2img: auto }", "{}"])
def test_answers_gen_capabilities_raises_with_hint(tmp_path: Path, capabilities: str) -> None:
    path = _answers(tmp_path, f"{{ mode: scaffold_and_download, capabilities: {capabilities} }}")

    with pytest.raises(AnswersError) as exc:
        load_answers(path, _hw())

    message = str(exc.value)
    assert "gen.capabilities was replaced by gen.models" in message
    assert "sdxl-lightning" in message


def _selections(**overrides: object) -> Selections:
    fields: dict[str, object] = {
        "storage_dir": "/var/lib/hal0/models",
        "slots": [],
        "extensions": {"openwebui": True, "comfyui": True, "hermes": False},
        "npu_opt_in": False,
    }
    return Selections(**{**fields, **overrides})  # type: ignore[arg-type]


def test_write_answers_emits_gen_models_and_round_trips(tmp_path: Path) -> None:
    sel = _selections(image_models=("sdxl-lightning", "esrgan-4x"))
    path = tmp_path / "out" / "hal0-setup.yaml"

    write_answers(sel, str(path))

    gen = yaml.safe_load(path.read_text())["gen"]
    assert gen["models"] == ["sdxl-lightning", "esrgan-4x"]
    assert "capabilities" not in gen
    # Models to install means the replay has to download them, not only scaffold.
    assert gen["mode"] == "scaffold_and_download"
    assert load_answers(str(path), _hw()).image_models == sel.image_models


def test_dump_answers_gen_modes() -> None:
    assert dump_answers(_selections())["gen"] == {"mode": "scaffold_only", "models": []}
    off = _selections(extensions={"openwebui": True, "comfyui": False})
    assert dump_answers(off)["gen"] == {"mode": "off", "models": []}
