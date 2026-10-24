"""Which Stable Diffusion family a checkpoint belongs to, read from its safetensors header (ADR-0037).

A user drops a ``.safetensors`` checkpoint into ``checkpoints/`` and the image API can render
it only through the generic template of its family, so the family has to be known. The
tensor names in the header give it away without reading any weights: SDXL carries a second
text encoder under ``conditioner.embedders.1.``, SD 1.5 keeps its only one under
``cond_stage_model.transformer.``. Anything else (Flux, a LoRA saved as a checkpoint, a
corrupt file) is not renderable here and reports ``None``.

Only a regular ``.safetensors`` file is read. A ``.ckpt`` is a pickle, and opening one runs
arbitrary code; a FIFO or device would block or misbehave on open.
"""

from __future__ import annotations

import json
import stat
import struct
from pathlib import Path
from typing import Literal

# The largest header worth parsing. Real checkpoints carry a few hundred KB; a declared
# length past this is a corrupt or hostile file, refused before any of it is read.
_MAX_HEADER_BYTES = 100 * 1024 * 1024

_SDXL_PREFIX = "conditioner.embedders.1."
_SD15_PREFIX = "cond_stage_model.transformer."


def sniff_checkpoint_arch(path: Path) -> Literal["sdxl", "sd15"] | None:
    """``"sdxl"`` or ``"sd15"`` for a checkpoint of that family, else ``None``.

    ``None`` also covers a file that is not a regular ``.safetensors`` file, one that cannot
    be read, and one whose header is truncated, over 100 MiB, longer than the file, not JSON,
    or not a JSON object. SDXL wins when a header carries both prefixes.
    """
    if path.suffix.lower() != ".safetensors":
        return None
    try:
        st = path.stat()
        if not stat.S_ISREG(st.st_mode):
            return None
        with path.open("rb") as fh:
            prefix = fh.read(8)
            if len(prefix) != 8:
                return None
            (length,) = struct.unpack("<Q", prefix)
            # A header cannot be longer than what follows its length prefix, so a tiny file
            # that declares a large one never makes us allocate it.
            if length > _MAX_HEADER_BYTES or length > st.st_size - 8:
                return None
            raw = fh.read(length)
    except OSError:
        return None
    if len(raw) != length:
        return None
    try:
        header = json.loads(raw)
    except (ValueError, RecursionError):  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        return None
    if not isinstance(header, dict):
        return None
    if any(key.startswith(_SDXL_PREFIX) for key in header):
        return "sdxl"
    if any(key.startswith(_SD15_PREFIX) for key in header):
        return "sd15"
    return None


__all__ = ["sniff_checkpoint_arch"]
