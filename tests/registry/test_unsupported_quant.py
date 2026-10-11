"""Fork-only quants (ROCmFPX family, IU4, ActiveFPX) are refused (ADR-0028).

No runner hal0 ships can load them, so model add, pull and scan refuse them up
front; the launch-time backstop lives in tests/slots (``slot.unsupported_quant``).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.config.schema import ModelsConfig
from hal0.registry import pull as pull_module
from hal0.registry.detect import unsupported_quant, unsupported_quant_message
from hal0.registry.discover import scan_and_register
from hal0.registry.store import ModelRegistry


@pytest.mark.parametrize(
    ("quant", "names", "expected"),
    [
        ("ROCmFP4", (), "ROCmFP4"),
        ("ROCmFPX", (), "ROCmFPX"),
        (None, ("Qwen3.6-27B-MTP-ROCmFP4-STRIX-imatrix.gguf",), "ROCmFP4"),
        (None, ("hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf",), "ROCmFP8"),
        (None, ("Qwen3.8-Flash-CIRU-STRIX-IU4.gguf",), "IU4"),
        (None, ("Qwen3.8-27B-CIRU-ActiveFPX-Q4.gguf",), "ActiveFPX"),
        ("Q4_K_M", ("Qwen3.8-27B-UD-Q4_K_XL.gguf",), None),
        (None, ("hal0-brain-sft-F16.gguf",), None),
        # "IU4" must be a whole token, not a substring of something else.
        (None, ("model-QIU4X.gguf",), None),
        (None, (None, ""), None),
    ],
)
def test_unsupported_quant(quant: str | None, names: tuple, expected: str | None) -> None:
    assert unsupported_quant(quant, *names) == expected


def test_message_names_the_model_and_the_fix() -> None:
    msg = unsupported_quant_message("m", "ROCmFP4")
    assert "'m'" in msg and "ROCmFP4" in msg and "plain GGUF" in msg


def test_scan_skips_fork_quant_files(tmp_path: Path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    (root / "Qwen3.6-27B-MTP-ROCmFP4-STRIX.gguf").write_bytes(b"x" * 64)
    (root / "Qwen3.6-27B-UD-Q4_K_XL.gguf").write_bytes(b"y" * 64)
    registry = ModelRegistry(registry_dir=tmp_path / "registry")

    result = scan_and_register(registry, ModelsConfig(roots=[str(root)]))

    names = {Path(m.path).name for m in registry.list()}
    assert names == {"Qwen3.6-27B-UD-Q4_K_XL.gguf"}
    assert any(s["reason"] == "unsupported_quant:ROCmFP4" for s in result["skipped"])


@pytest.fixture
def client(tmp_hal0_home: str) -> Iterator[TestClient]:
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        "[models]\nroots = []\nauto_scan_on_start = false\n", encoding="utf-8"
    )
    with TestClient(create_app()) as c:
        yield c


def test_add_from_path_refuses_a_fork_quant(client: TestClient, tmp_hal0_home: str) -> None:
    target = Path(tmp_hal0_home) / "Qwen3.6-27B-MTP-ROCmFP4-STRIX.gguf"
    target.write_bytes(b"\x00" * 256)
    r = client.post("/api/models/add-from-path", json={"path": str(target)})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "models.unsupported_quant"
    assert target.name not in client.get("/api/models").text


def test_pull_refuses_a_fork_quant_before_downloading(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    async def fake(job: Any, **kw: Any) -> None:
        calls.append(kw)
        job.state = "completed"
        job.finished_at = time.time()

    monkeypatch.setattr(pull_module, "run_pull", fake)
    from hal0.registry import pull_jobs

    monkeypatch.setattr(pull_jobs, "run_pull", fake)
    r = client.post(
        "/api/models/fork-quant/pull",
        json={"hf_repo": "someone/Qwen-ROCmFP4-GGUF", "hf_filename": "Qwen-ROCmFP4.gguf"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "models.unsupported_quant"
    assert calls == []


def test_pull_of_a_plain_file_from_a_fork_named_repo_is_allowed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The repo id is never judged: the brain sft repo holds a plain F16."""

    async def fake(job: Any, **kw: Any) -> None:
        job.state = "completed"
        job.finished_at = time.time()

    monkeypatch.setattr(pull_module, "run_pull", fake)
    from hal0.registry import pull_jobs

    monkeypatch.setattr(pull_jobs, "run_pull", fake)
    r = client.post("/api/models/hal0-brain-sft-f16/pull")
    assert r.status_code in (200, 202), r.text
