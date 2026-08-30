from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hal0.registry.fileset import FileSetEntry, FileSetPlan
from hal0.registry.pull import _register_pulled_fileset
from hal0.registry.store import ModelRegistry
from hal0.slots.capacity import (
    _ctx_tokens_for,
    _kv_estimate_mb,
    build_per_slot,
    companion_bytes_mb,
    estimate_file_size_kv_mb,
)

PF_META = {
    "metadata": {
        "specialty": "promptforge",
        "companions": {
            "promptforge_ffn": "/m/ffn.pfs",
            "promptforge_gdn": "/m/gdn.pfs",
            "promptforge_output_k8": "/m/k8.pfs",
        },
        # card sizes: 17.1 GB + 4.0 GB + 0.7 GB
        "companion_sizes": {
            "promptforge_ffn": 17_100_000_000,
            "promptforge_gdn": 4_000_000_000,
            "promptforge_output_k8": 700_000_000,
        },
    }
}


def test_companion_bytes_summed():
    mb = companion_bytes_mb(PF_META)
    assert 20_000 < mb < 21_500  # 21.8e9 bytes ≈ 20790 MiB


def test_plain_model_zero():
    assert companion_bytes_mb({"metadata": {}}) == 0.0
    assert companion_bytes_mb(None) == 0.0


def test_companion_mb_argument_is_opt_in_and_defaults_off():
    """``companion_mb`` is an escape hatch, not the default (fix wave, C1).

    No in-tree caller passes it: ``Model.size_bytes`` — the source of every
    real ``model_mb`` — already contains the sidecar bytes. The argument
    stays for a hypothetical caller holding an entry-only size, so pin both
    halves: default 0.0 adds nothing, an explicit value still adds.
    """
    base = estimate_file_size_kv_mb(15_000.0, PF_META["metadata"])
    assert base == estimate_file_size_kv_mb(15_000.0, PF_META["metadata"], companion_mb=0.0)
    with_comp = estimate_file_size_kv_mb(
        15_000.0, PF_META["metadata"], companion_mb=companion_bytes_mb(PF_META)
    )
    assert with_comp > base + 20_000


def test_specialty_ctx_default():
    assert _ctx_tokens_for(PF_META["metadata"]) == 262_144


def test_explicit_defaults_context_size_still_wins():
    meta = {"defaults": {"context_size": 8192}, **PF_META["metadata"]}
    assert _ctx_tokens_for(meta) == 8192


# ── C1 regression: companions booked EXACTLY once, through the real pull ──
#
# The tests above hand ``model_mb`` and ``companion_mb`` in as independent
# numbers, so they can never ask where a real ``model_mb`` comes from — which
# is exactly how the double-count survived task-scoped review. This one runs
# the REAL stamping path (``_register_pulled_fileset``, the only writer of
# ``metadata.companion_sizes``), reads the row back out of the registry, and
# feeds it to the two estimators capacity actually calls.


def _register_specialty_row(tmp_path: Path, registry: Any) -> tuple[str, dict[str, int]]:
    """Install a fake ActiveFPX file set through the real registration path."""
    root = tmp_path / "fileset-models"
    root.mkdir(parents=True, exist_ok=True)
    # MiB-scale so the MiB rounding in ``companion_bytes_mb`` has something
    # to see; written sparse (``os.truncate``) so nothing real hits the disk.
    mib = 1024 * 1024
    sizes = {
        "entry": 150 * mib,
        "promptforge_ffn": 171 * mib,
        "promptforge_gdn": 40 * mib,
        "promptforge_output_k8": 7 * mib,
        "runtime_patch": 1 * mib,
    }
    specs = [
        ("entry", "Qwen-CIRU-ActiveFPX-v3-Q8.gguf", "model"),
        ("promptforge_ffn", "Qwen-v3-FFN.pfs", "promptforge_ffn"),
        ("promptforge_gdn", "Qwen-v3-GDN.pfs", "promptforge_gdn"),
        ("promptforge_output_k8", "Qwen-v3-Output-K8.pfs", "promptforge_output_k8"),
        ("runtime_patch", "qwen38-runtime.patch", "runtime_patch"),
    ]
    installed = []
    files = []
    entry_dest = None
    for key, name, role in specs:
        dest = root / name
        dest.write_bytes(b"")
        os.truncate(dest, sizes[key])
        f = FileSetEntry(rel=name, role=role, size_bytes=sizes[key])
        files.append(f)
        installed.append((f, dest))
        if role == "model":
            entry_dest = dest
    assert entry_dest is not None
    plan = FileSetPlan(
        repo="jcbtc/qwen-activefpx",
        revision="deadbeef",
        entry_rel="Qwen-CIRU-ActiveFPX-v3-Q8.gguf",
        files=files,
        specialty="promptforge",
    )
    model_id = "qwen-activefpx"
    _register_pulled_fileset(
        registry,
        model_id=model_id,
        fileset=plan,
        installed=installed,
        entry_dest=entry_dest,
        mmproj_dest=None,
    )
    return model_id, sizes


def test_size_bytes_already_contains_the_companion_bytes(tmp_hal0_home: str) -> None:
    """The invariant C1 turned on: ``metadata.companion_sizes`` is a SUBSET of
    ``Model.size_bytes``, because the same call writes both."""
    registry = ModelRegistry()
    model_id, sizes = _register_specialty_row(Path(tmp_hal0_home), registry)
    model = registry.get(model_id)

    assert model.size_bytes == sum(sizes.values())  # every installed file
    row = model.model_dump()
    comp_mb = companion_bytes_mb(row)
    model_mb = model.size_bytes / (1024 * 1024)
    assert comp_mb > 0.0  # the stamped sidecars are visible …
    assert comp_mb <= model_mb  # … and they live INSIDE size_bytes


def test_estimate_books_companions_exactly_once(tmp_hal0_home: str) -> None:
    """The estimator both capacity call sites use adds the sidecar bytes once.

    Pre-fix this returned ``model_mb + companion_mb + kv`` — ~57 GiB for a
    ~36 GiB ActiveFPX slot — and pre-load admission evicted resident
    neighbours that never needed to go.
    """
    registry = ModelRegistry()
    model_id, _sizes = _register_specialty_row(Path(tmp_hal0_home), registry)
    row = registry.get(model_id).model_dump()
    model_mb = row["size_bytes"] / (1024 * 1024)
    comp_mb = companion_bytes_mb(row)
    kv_mb = _kv_estimate_mb(_ctx_tokens_for(row))

    est = estimate_file_size_kv_mb(model_mb, row)
    assert est == round(model_mb + kv_mb, 1)
    # and specifically NOT the double-booked figure
    assert est < round(model_mb + comp_mb + kv_mb, 1)


@pytest.mark.asyncio
async def test_build_per_slot_books_companions_once(tmp_hal0_home: str) -> None:
    """End-to-end through ``build_per_slot`` — the resident-footprint call
    site the eviction planner reads (capacity.py path 3)."""
    registry = ModelRegistry()
    model_id, _sizes = _register_specialty_row(Path(tmp_hal0_home), registry)
    row = registry.get(model_id).model_dump()
    model_mb = row["size_bytes"] / (1024 * 1024)
    expected = round(model_mb + _kv_estimate_mb(_ctx_tokens_for(row)), 1)

    slot = MagicMock()
    slot.name = "pf"
    slot.state = "ready"
    slot.model_id = model_id
    slot.backend = "rocm"
    slot.slot_id = None
    slot.metadata = {"provider": "llama-server", "backend": "rocm"}

    with patch(
        "hal0.slots.capacity._container_cgroup_mem_bytes",
        new_callable=AsyncMock,
        return_value=0,  # container absent → the estimate is the answer
    ):
        result = await build_per_slot([slot], registry=registry, gpu_capable=True)

    assert result["pf"]["mem_mb"] == expected
