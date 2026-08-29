"""Pull-time stamping of specialty metadata — spec 2026-08-29 (#1946).

Fixtures copied from ``test_register_pulled_fileset_stamps_and_clears_quant``
in ``tests/registry/test_pull.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

from hal0.registry.fileset import FileSetEntry, FileSetPlan
from hal0.registry.pull import _register_pulled_fileset
from hal0.registry.store import ModelRegistry


def _payload(size: int = 2048) -> bytes:
    """Deterministic fake-GGUF bytes so SHA-256 assertions are reproducible."""
    return (b"GGUF" + b"\x00" * 4 + os.urandom(0)) + (b"a" * (size - 8))


def test_fileset_pull_stamps_specialty_metadata(tmp_hal0_home: str) -> None:
    """A specialty file-set pull stamps ``metadata.specialty``,
    ``metadata.companions`` (installed dest per companion role, excluding the
    env=None ``runtime_patch`` role), ``metadata.companion_sizes``, and fills
    ``quant`` from the kind's marker since it was unset (#1890)."""
    registry = ModelRegistry()
    tmp_path = Path(tmp_hal0_home)
    root = tmp_path / "fileset-models"
    root.mkdir(parents=True, exist_ok=True)

    entry_dest = root / "promptforge-model.pfs"
    entry_dest.write_bytes(_payload(2048))
    ffn_dest = root / "ffn.pfs"
    ffn_dest.write_bytes(_payload(1024))
    gdn_dest = root / "gdn.pfs"
    gdn_dest.write_bytes(_payload(512))
    output_k8_dest = root / "output-k8.pfs"
    output_k8_dest.write_bytes(_payload(256))
    runtime_patch_dest = root / "runtime.patch"
    runtime_patch_dest.write_bytes(_payload(64))

    entry_file = FileSetEntry(rel="promptforge-model.pfs", role="model", size_bytes=2048)
    ffn_file = FileSetEntry(rel="ffn.pfs", role="promptforge_ffn", size_bytes=1024)
    gdn_file = FileSetEntry(rel="gdn.pfs", role="promptforge_gdn", size_bytes=512)
    output_k8_file = FileSetEntry(
        rel="output-k8.pfs", role="promptforge_output_k8", size_bytes=256
    )
    runtime_patch_file = FileSetEntry(rel="runtime.patch", role="runtime_patch", size_bytes=64)

    plan = FileSetPlan(
        repo="org/promptforge-GGUF",
        revision="deadbeef",
        entry_rel="promptforge-model.pfs",
        files=[entry_file, ffn_file, gdn_file, output_k8_file, runtime_patch_file],
        specialty="promptforge",
    )

    model_id = "promptforge-model"
    _register_pulled_fileset(
        registry,
        model_id=model_id,
        fileset=plan,
        installed=[
            (entry_file, entry_dest),
            (ffn_file, ffn_dest),
            (gdn_file, gdn_dest),
            (output_k8_file, output_k8_dest),
            (runtime_patch_file, runtime_patch_dest),
        ],
        entry_dest=entry_dest,
        mmproj_dest=None,
    )

    model = registry.get(model_id)
    assert model.metadata["specialty"] == "promptforge"
    comps = model.metadata["companions"]
    assert set(comps) == {"promptforge_ffn", "promptforge_gdn", "promptforge_output_k8"}
    for p in comps.values():
        assert p.startswith(str(tmp_path))  # absolute installed dest
    sizes = model.metadata["companion_sizes"]
    assert set(sizes) == set(comps)
    assert all(isinstance(v, int) and v > 0 for v in sizes.values())
    assert model.quant == "ActiveFPX"  # filled because it was unset


def test_plain_pull_stamps_nothing(tmp_hal0_home: str) -> None:
    """A plain (non-specialty) file-set pull leaves metadata byte-identical —
    no ``specialty``/``companions``/``companion_sizes`` keys appear."""
    registry = ModelRegistry()
    tmp_path = Path(tmp_hal0_home)
    root = tmp_path / "fileset-models"
    root.mkdir(parents=True, exist_ok=True)

    entry_dest = root / "plain-model.gguf"
    entry_dest.write_bytes(_payload(2048))
    entry_file = FileSetEntry(rel="plain-model.gguf", role="model", size_bytes=2048)

    plan = FileSetPlan(
        repo="org/plain-GGUF",
        revision="deadbeef",
        entry_rel="plain-model.gguf",
        files=[entry_file],
        specialty=None,
    )

    model_id = "plain-model"
    _register_pulled_fileset(
        registry,
        model_id=model_id,
        fileset=plan,
        installed=[(entry_file, entry_dest)],
        entry_dest=entry_dest,
        mmproj_dest=None,
    )

    model = registry.get(model_id)
    assert "specialty" not in model.metadata
    assert "companions" not in model.metadata
    assert "companion_sizes" not in model.metadata
