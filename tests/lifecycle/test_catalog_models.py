from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.lifecycle.types import ModelFile, PackageDefinition


def test_catalog_models_are_frozen() -> None:
    package = PackageDefinition(
        id="cpu",
        repository="ghcr.io/hal0ai/cpu",
        digest="sha256:" + "a" * 64,
        package_kind="runner",
        platforms=("linux/amd64",),
    )

    with pytest.raises(ValidationError, match="frozen"):
        package.id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("digest", ["latest", "sha256:ABC", "sha256:" + "a" * 63])
def test_package_digest_requires_immutable_sha256(digest: str) -> None:
    with pytest.raises(ValidationError):
        PackageDefinition(
            id="cpu",
            repository="ghcr.io/hal0ai/cpu",
            digest=digest,
            package_kind="runner",
            platforms=("linux/amd64",),
        )


def test_model_file_requires_exact_metadata() -> None:
    with pytest.raises(ValidationError):
        ModelFile(
            filename="model.gguf", sha256="missing", size_bytes=0, format="gguf", quantization="f16"
        )


def test_rocmfpx_brain_model_pins_consolidated_agent_artifact(catalog) -> None:
    model = catalog.model("hal0-brain-rocmfpx-agent")
    assert model.source == "Hal0ai/hal0-brain-sft-ROCmFPX-GGUF"
    assert model.runners == ("rocmfpx", "vulkanfpx")
    assert len(model.files) == 1
    artifact = model.files[0]
    assert artifact.filename == "hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf"
    assert artifact.format == "rocmfpx-gguf"
    assert artifact.quantization == "q8_0_rocmfpx_agent"
