"""hal0.comfyui.arch: which Stable Diffusion family a checkpoint belongs to, from its header alone."""

from __future__ import annotations

import json
import struct
from pathlib import Path

from hal0.comfyui.arch import sniff_checkpoint_arch


def _safetensors(path: Path, keys: list[str]) -> Path:
    """A minimal safetensors file whose header names ``keys``, each a 1-element F16 tensor."""
    header = {
        key: {"dtype": "F16", "shape": [1], "data_offsets": [2 * i, 2 * i + 2]}
        for i, key in enumerate(keys)
    }
    raw = json.dumps(header).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\x00" * (2 * len(keys)))
    return path


def test_sniff_sdxl(tmp_path: Path) -> None:
    path = _safetensors(tmp_path / "xl.safetensors", ["conditioner.embedders.1.x"])
    assert sniff_checkpoint_arch(path) == "sdxl"


def test_sniff_sd15(tmp_path: Path) -> None:
    path = _safetensors(tmp_path / "v15.safetensors", ["cond_stage_model.transformer.text_model.x"])
    assert sniff_checkpoint_arch(path) == "sd15"


def test_sniff_sdxl_wins_when_both_prefixes_are_present(tmp_path: Path) -> None:
    path = _safetensors(
        tmp_path / "both.safetensors",
        ["cond_stage_model.transformer.x", "conditioner.embedders.1.y"],
    )
    assert sniff_checkpoint_arch(path) == "sdxl"


def test_sniff_unknown_arch_returns_none(tmp_path: Path) -> None:
    path = _safetensors(tmp_path / "flux.safetensors", ["double_blocks.0.img_attn.qkv.weight"])
    assert sniff_checkpoint_arch(path) is None


def test_sniff_rejects_huge_header(tmp_path: Path) -> None:
    """A declared header over 100 MiB is refused without reading it."""
    path = tmp_path / "huge.safetensors"
    path.write_bytes(struct.pack("<Q", 101 * 1024 * 1024) + b"{}")
    assert sniff_checkpoint_arch(path) is None


def test_sniff_header_longer_than_the_file_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "short.safetensors"
    path.write_bytes(struct.pack("<Q", 5000) + b'{"a":')
    assert sniff_checkpoint_arch(path) is None


def test_sniff_non_json_header_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "junk.safetensors"
    path.write_bytes(struct.pack("<Q", 4) + b"\xff\xfe\xfd\xfc")
    assert sniff_checkpoint_arch(path) is None


def test_sniff_non_object_header_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "list.safetensors"
    path.write_bytes(struct.pack("<Q", 2) + b"[]")
    assert sniff_checkpoint_arch(path) is None


def test_sniff_truncated_length_prefix_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "tiny.safetensors"
    path.write_bytes(b"\x01\x02\x03")
    assert sniff_checkpoint_arch(path) is None


def test_sniff_only_reads_safetensors(tmp_path: Path) -> None:
    """A ``.ckpt`` is a pickle: it is never opened, whatever its bytes say."""
    path = _safetensors(tmp_path / "old.ckpt", ["conditioner.embedders.1.x"])
    assert sniff_checkpoint_arch(path) is None


def test_sniff_missing_file_returns_none(tmp_path: Path) -> None:
    assert sniff_checkpoint_arch(tmp_path / "gone.safetensors") is None
