"""Request-path fragments that pin a request to a job's slot.

A leaf module: it imports nothing from hal0, so ``hal0.jobs`` (and through it
the doctor) can read the fragments without importing the dispatcher package,
whose router imports ``hal0.jobs`` back. The dispatcher re-exports the names.
"""

from __future__ import annotations

# Pin a request to the embed slot regardless of model.
EMBED_PATHS = ("/embeddings",)

# Pin a request to the dedicated rerank slot. Both /rerankings (hal0's public
# OpenAI-compat route) and /rerank (llama-server's native shape) route here.
RERANK_PATHS = ("/rerankings", "/rerank")

# Pin a request to the TTS slot. Only /audio/speech (synthesis); transcription
# is STT, not TTS.
TTS_PATHS = ("/audio/speech",)

# Pin a request to the image-gen slot (ComfyUI).
IMAGE_PATHS = ("/images/generations", "/images/edits", "/images/variations")

# Speech-to-text path (served by ``type = "transcription"`` slots).
STT_PATHS = ("/audio/transcriptions",)
