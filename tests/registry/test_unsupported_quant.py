"""Fork-only quants (ROCmFPX family, IU4, ActiveFPX) are refused (ADR-0028).

No runner hal0 ships can load them, so model add, pull and scan refuse them up
front; the launch-time backstop lives in tests/slots (``slot.unsupported_quant``).
"""

from __future__ import annotations

import struct
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.config.schema import ModelsConfig
from hal0.registry import pull as pull_module
from hal0.registry.detect import (
    unsupported_gguf_contents,
    unsupported_quant,
    unsupported_quant_message,
)
from hal0.registry.discover import scan_and_register
from hal0.registry.gguf_header import read_gguf_tensor_types
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


# ── contents, not names ───────────────────────────────────────────────────────


def _gguf(tensor_types: list[int]) -> bytes:
    """A minimal GGUF v3: one string KV, then one tensor info per type."""

    def _s(text: str) -> bytes:
        raw = text.encode()
        return struct.pack("<Q", len(raw)) + raw

    out = b"GGUF" + struct.pack("<I", 3)
    out += struct.pack("<Q", len(tensor_types)) + struct.pack("<Q", 1)
    out += _s("general.architecture") + struct.pack("<I", 8) + _s("llama")
    for i, ttype in enumerate(tensor_types):
        out += _s(f"blk.{i}.weight") + struct.pack("<I", 2)
        out += struct.pack("<QQ", 64, 64) + struct.pack("<I", ttype) + struct.pack("<Q", 0)
    return out + b"\x00" * 64


def test_tensor_types_are_read_from_the_tensor_info_block(tmp_path: Path) -> None:
    path = tmp_path / "m.gguf"
    path.write_bytes(_gguf([0, 12, 14]))
    assert read_gguf_tensor_types(path) == {0, 12, 14}


def test_not_a_gguf_has_no_tensor_types(tmp_path: Path) -> None:
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x" * 64)
    assert read_gguf_tensor_types(path) is None
    assert unsupported_gguf_contents(path) is None


def test_a_stock_gguf_is_loadable(tmp_path: Path) -> None:
    path = tmp_path / "model.gguf"
    path.write_bytes(_gguf([0, 1, 12, 14, 30, 39]))
    assert unsupported_gguf_contents(path) is None


def test_a_renamed_fork_build_is_caught_by_its_tensor_types(tmp_path: Path) -> None:
    path = tmp_path / "model.gguf"
    path.write_bytes(_gguf([0, 12, 77]))
    label = unsupported_gguf_contents(path)
    assert label is not None and "77" in label


def test_scan_skips_a_renamed_fork_build(tmp_path: Path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    (root / "model.gguf").write_bytes(_gguf([0, 77]))
    (root / "plain.gguf").write_bytes(_gguf([0, 12]))
    registry = ModelRegistry(registry_dir=tmp_path / "registry")

    result = scan_and_register(registry, ModelsConfig(roots=[str(root)]))

    assert {Path(m.path).name for m in registry.list()} == {"plain.gguf"}
    assert any(s["reason"].startswith("unsupported_quant:") for s in result["skipped"])


def test_scan_checks_the_symlink_name_of_an_hf_cache_blob(tmp_path: Path) -> None:
    """A snapshot symlink resolves to a hash-named blob; its own name tells."""
    root = tmp_path / "models"
    blobs = root / "blobs"
    snap = root / "snapshots" / "rev"
    blobs.mkdir(parents=True)
    snap.mkdir(parents=True)
    (blobs / "0f3a9c").write_bytes(b"x" * 64)
    (snap / "Qwen-ROCmFP4.gguf").symlink_to(blobs / "0f3a9c")
    registry = ModelRegistry(registry_dir=tmp_path / "registry")

    scan_and_register(registry, ModelsConfig(roots=[str(root)]))

    assert registry.list() == []


def test_add_from_path_refuses_a_renamed_fork_build(client: TestClient, tmp_hal0_home: str) -> None:
    target = Path(tmp_hal0_home) / "model.gguf"
    target.write_bytes(_gguf([0, 77]))
    r = client.post("/api/models/add-from-path", json={"path": str(target)})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "models.unsupported_quant"


def test_pull_judges_the_file_not_the_operator_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Add-from-HF modal suggests an id from the repo name; a plain file
    picked from a fork-named repo must not be refused for that id."""

    async def fake(job: Any, **kw: Any) -> None:
        job.state = "completed"
        job.finished_at = time.time()

    monkeypatch.setattr(pull_module, "run_pull", fake)
    from hal0.registry import pull_jobs

    monkeypatch.setattr(pull_jobs, "run_pull", fake)
    r = client.post(
        "/api/models/hal0-brain-sft-ROCmFPX/pull",
        json={
            "hf_repo": "Hal0ai/hal0-brain-sft-ROCmFPX-GGUF",
            "hf_filename": "hal0-brain-sft-F16.gguf",
        },
    )
    assert r.status_code in (200, 202), r.text


def test_launch_guard_reads_a_suffixless_blob_and_every_shard(tmp_path: Path) -> None:
    from hal0.errors import UnprocessableEntity
    from hal0.providers.container import _guard_unsupported_quant

    blob = tmp_path / "0f3a9c"  # an HF cache blob: no suffix
    blob.write_bytes(_gguf([0, 77]))
    with pytest.raises(UnprocessableEntity):
        _guard_unsupported_quant({"name": "s"}, {"_model_key": "m", "path": str(blob)})

    first = tmp_path / "m-00001-of-00002.gguf"
    first.write_bytes(_gguf([0, 12]))
    second = tmp_path / "m-00002-of-00002.gguf"
    second.write_bytes(_gguf([0, 77]))
    info = {"_model_key": "m", "path": str(first), "metadata": {"shards": [str(second)]}}
    with pytest.raises(UnprocessableEntity):
        _guard_unsupported_quant({"name": "s"}, info)


def test_scan_checks_every_shard(tmp_path: Path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    (root / "m-00001-of-00002.gguf").write_bytes(_gguf([0, 12]))
    (root / "m-00002-of-00002.gguf").write_bytes(_gguf([0, 77]))
    registry = ModelRegistry(registry_dir=tmp_path / "registry")

    scan_and_register(registry, ModelsConfig(roots=[str(root)]))

    assert registry.list() == []


def test_launch_guard_ignores_the_id_and_reads_the_file(tmp_path: Path) -> None:
    from hal0.errors import UnprocessableEntity
    from hal0.providers.container import _guard_unsupported_quant

    plain = tmp_path / "plain.gguf"
    plain.write_bytes(_gguf([0, 12]))
    # A fork-sounding id on a plain file launches.
    _guard_unsupported_quant({"name": "s"}, {"_model_key": "x-ROCmFPX", "path": str(plain)})

    renamed = tmp_path / "model.gguf"
    renamed.write_bytes(_gguf([0, 77]))
    with pytest.raises(UnprocessableEntity):
        _guard_unsupported_quant({"name": "s"}, {"_model_key": "m", "path": str(renamed)})
