"""``server_models.json`` generator from hal0's model registry.

Produces a hal0-customized ``server_models.json`` (Lemonade Server's
curated catalog) by reading ``/var/lib/hal0/registry/registry.toml``
and emitting one Lemonade entry per registered model with the correct
``labels`` so Lemonade infers the right model ``type`` at load time.

Why this exists
---------------
Lemonade's bundled ``server_models.json`` does not list hal0's curated
picks (``hermes-4-14b``, ``qwen3.6-35b-a3b``, ``qwen3-coder-next-reap``,
``bge-reranker-v2-m3``, …). The 2026-05-22 spike found that
``extra_models_dir`` discovery silently classifies every GGUF as
``type=llm`` because custom-discovered models get only the
``["custom"]`` label — so ``--reranking`` and ``--embedding`` never make
it to the child llama-server and Lemonade serves rerank/embed requests
as plain LLM completions (or 501s). See:

  * ADR-0006 §4 — Model registration via hal0-customized
    ``server_models.json``.
  * ``docs/internal/lemonade-spike-findings-2026-05-22.md`` §"Model
    discovery + typing" — the smoking-gun failure mode.
  * ``docs/internal/lemonade-repo-deep-dive-2026-05-22.md`` §3 — type
    classification is **label-driven**, not field-driven; the
    ``"type"`` field the brief mentions is conceptual. Lemonade's
    ``get_model_type_from_labels()`` (``src/cpp/include/lemon/model_types.h``)
    resolves first-match on labels:

        embeddings/embedding → EMBEDDING
        reranking            → RERANKING
        transcription        → TRANSCRIPTION
        image                → IMAGE
        tts                  → TTS
        else                 → LLM

    Chat-indicator labels (``vision``, ``reasoning``, ``tool-calling``,
    ``tools``, ``chat-transcription``) override the first-match to LLM,
    so we never set ``embeddings`` on a chat model.

Output schema (subset of Lemonade's ``server_models.json``):

::

    {
      "<model_id>": {
        "checkpoint": "<owner>/<repo>:<filename>"  | "/abs/path/to.gguf",
        "recipe":     "llamacpp" | "whispercpp" | "kokoro" | "sd-cpp" | "flm",
        "labels":     ["embeddings"|"reranking"|"transcription"|"image"|"tts"|...],
        "size":       <float, GB; 0.0 if unknown>,
        "suggested":  false,
        "max_context_window": <int, optional, llm only>
      },
      ...
    }

The generator is intentionally pure: it reads one file, returns a dict,
and the writer is a separate atomic-replace step. The install hook + a
new ``hal0 capabilities sync`` subcommand both call ``write_server_models``.

# NOTE: This module sits next to ``client.py`` but does NOT import it.
# Generation is a one-shot file → file transform; runtime client traffic
# stays in ``client.py``. Keeping the import boundary clean means tests
# can run without the httpx machinery from the client.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ── Mapping tables ─────────────────────────────────────────────────────────────

# hal0 capability → Lemonade label (the string the C++ classifier matches).
#
# hal0 historically uses the singular forms (``embed``, ``rerank``); the issue
# brief uses the plural English forms (``embedding``, ``reranking``). We
# accept both on input. Output is always the Lemonade-canonical token from
# ``get_model_type_from_labels()``.
_CAPABILITY_TO_LABEL: dict[str, str] = {
    # Embedding
    "embed": "embeddings",
    "embedding": "embeddings",
    "embeddings": "embeddings",
    # Reranking
    "rerank": "reranking",
    "reranking": "reranking",
    # ASR / transcription
    "asr": "transcription",
    "transcription": "transcription",
    "stt": "transcription",
    # TTS
    "tts": "tts",
    # Image gen
    "image": "image",
    "img": "image",
    # Vision (chat-indicator: forces LLM in Lemonade's classifier)
    "vision": "vision",
    # Chat — no Lemonade label needed; LLM is the default
    "chat": None,  # type: ignore[dict-item]
}

# Strength ranking — if a model advertises multiple capabilities, the
# strongest non-chat one wins. We prefer the most specific/limiting type,
# which matches Lemonade's "non-LLM modalities need explicit registration"
# semantics. Strongest first → we iterate this list and pick the first match.
#
# Vision is not in this list: a chat+vision model should stay LLM (Lemonade's
# classifier already treats ``vision`` as a chat-indicator label). We emit
# ``vision`` as a *secondary* label without using it to drive type.
_CAPABILITY_STRENGTH: tuple[str, ...] = (
    "rerank",
    "reranking",
    "embed",
    "embedding",
    "embeddings",
    "asr",
    "transcription",
    "stt",
    "tts",
    "image",
    "img",
    "chat",
)


# hal0 backend → Lemonade recipe. Multiple hal0 backends can map to the
# same Lemonade recipe (vulkan/rocm/cuda/cpu are all ``llamacpp`` recipe;
# the GPU choice is a load-time argument, not a recipe).
_BACKEND_TO_RECIPE: dict[str, str] = {
    "vulkan": "llamacpp",
    "rocm": "llamacpp",
    "cuda": "llamacpp",
    "cpu": "llamacpp",
    "llamacpp": "llamacpp",
    "moonshine": "whispercpp",  # hal0 used moonshine for ASR; Lemonade has whispercpp
    "whispercpp": "whispercpp",
    "whisper": "whispercpp",
    "kokoro": "kokoro",
    "sdcpp": "sd-cpp",
    "sd-cpp": "sd-cpp",
    "stable-diffusion": "sd-cpp",
    "comfyui": "sd-cpp",
    "flm": "flm",
    "ryzenai-llm": "ryzenai-llm",
}

# Per-modality default recipes when the registry doesn't advertise any
# compatible backend. Falls back to llamacpp for chat/embed/rerank since
# all hal0 GGUFs work there. ASR → whispercpp (Lemonade dropped moonshine).
_DEFAULT_RECIPE_BY_LABEL: dict[str, str] = {
    "embeddings": "llamacpp",
    "reranking": "llamacpp",
    "transcription": "whispercpp",
    "tts": "kokoro",
    "image": "sd-cpp",
}

# Default context window for chat models when registry doesn't specify.
_DEFAULT_LLM_CTX: int = 8192


# ── Stock fallback (issue #210) ─────────────────────────────────────────────
#
# On a fresh ``curl | bash`` install the hal0 registry is not yet seeded, so
# ``_read_registry`` yields zero models. install.sh writes our output over
# Lemonade's bundled ``server_models.json`` (which ships ~180 stock entries),
# so emitting an empty dict here would blank the catalog and leave the daemon
# with nothing to load - the user can't chat until they manually pull a model.
#
# To keep a fresh box usable we fall back to a small curated STOCK set drawn
# from ``hal0.registry.curated.CURATED_BY_ID``. These ids are the canonical
# curated ids (kept stable by #500), so the output is non-empty and the daemon
# has loadable models out of the box. Picked to cover the core modalities a
# first-run user reaches for: a sub-second smoke/chat model, a lean default
# chat model, an embed model, a reranker, an STT model, and a TTS voice.
#
# This fallback ONLY applies when the registry yields no usable models. A
# populated registry is emitted verbatim (unchanged behaviour).
STOCK_FALLBACK_IDS: tuple[str, ...] = (
    "qwen3.5-0.8b",  # sub-second smoke + Lite-bundle primary chat
    "qwen3.5-9b",  # lean default chat
    "nomic-embed-text-v1.5-q8_0",  # default embed
    "bge-reranker-v2-m3-q4_k_m",  # default reranker
    "Whisper-Large-v3-Turbo",  # STT
    "kokoro-v1",  # TTS voice
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _read_registry(registry_path: Path) -> dict[str, dict[str, Any]]:
    """Parse registry.toml and return ``{model_id: entry_dict}``.

    Returns an empty dict when the file is missing or malformed (log at
    WARN). This matches ``ModelRegistry._read_locked``'s never-blank-on-
    parse-error stance: an install hook running against a yet-to-be-
    populated registry should produce an empty ``server_models.json``,
    not an error.
    """
    try:
        with open(registry_path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        log.warning("registry not found at %s — emitting empty catalog", registry_path)
        return {}
    except (tomllib.TOMLDecodeError, OSError) as exc:
        log.warning(
            "registry parse failed at %s: %s — emitting empty catalog",
            registry_path,
            exc,
        )
        return {}

    raw = data.get("models", {}) if isinstance(data, dict) else {}
    out: dict[str, dict[str, Any]] = {}

    if isinstance(raw, dict):
        for mid, entry in raw.items():
            if isinstance(entry, dict):
                out[mid] = entry
    elif isinstance(raw, list):
        # ModelRegistry accepts list-of-tables for backcompat with haloai;
        # mirror that here so the generator works against either shape.
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            mid = entry.get("id")
            if isinstance(mid, str) and mid:
                out[mid] = entry

    return out


def _pick_primary_capability(caps: list[str]) -> str | None:
    """Return the strongest non-chat capability, or 'chat' if only chat, or None."""
    seen = {c.lower() for c in caps if isinstance(c, str)}
    if not seen:
        return None
    for candidate in _CAPABILITY_STRENGTH:
        if candidate in seen:
            return candidate
    # Capability present but unknown — fall through to LLM default.
    return None


def _resolve_recipe(backends: list[str], primary_label: str | None) -> str:
    """Pick a Lemonade recipe from a hal0 backends list.

    ``flm`` wins if present (NPU is exclusive; hal0 only puts ``flm`` in
    a model's backends list when the GGUF actually has FLM weights).
    Otherwise the first known mapping wins. Falls back to the
    label-default (llamacpp/whispercpp/kokoro/sd-cpp) when none of the
    registry's backends are recognised.
    """
    lowered = [b.lower() for b in backends if isinstance(b, str)]
    if "flm" in lowered:
        return "flm"
    for b in lowered:
        recipe = _BACKEND_TO_RECIPE.get(b)
        if recipe:
            return recipe
    if primary_label and primary_label in _DEFAULT_RECIPE_BY_LABEL:
        return _DEFAULT_RECIPE_BY_LABEL[primary_label]
    return "llamacpp"


def _resolve_checkpoint(entry: dict[str, Any]) -> str:
    """Build Lemonade's ``checkpoint`` string from a registry entry.

    Format precedence (matching Lemonade's own server_models.json conventions):

    1. ``hf_repo`` + ``hf_filename`` → ``"<owner>/<repo>:<filename>"``
    2. ``hf_repo`` alone             → ``"<owner>/<repo>"``
    3. ``path`` (local-only model)   → absolute path string

    The local-path fallback covers models registered via direct file
    scan (no HF coords). Lemonade's loader accepts an absolute path
    where it would normally take an HF repo id; the checkpoint resolver
    in ``src/cpp/server/checkpoint_resolver.cpp`` short-circuits to file
    loading when the string starts with ``/``.
    """
    repo = entry.get("hf_repo") or ""
    filename = entry.get("hf_filename") or ""
    if repo and filename:
        return f"{repo}:{filename}"
    if repo:
        return repo
    path = entry.get("path") or ""
    return str(path)


def _resolve_size_gb(entry: dict[str, Any]) -> float:
    """Return model size in GB (1024^3 base) for Lemonade's ``size`` field.

    Lemonade displays this in its UI; if the registry says 0 we just emit
    0.0 — Lemonade falls back to the on-disk file size at load time.
    """
    size_bytes = entry.get("size_bytes")
    if not isinstance(size_bytes, int | float) or size_bytes <= 0:
        return 0.0
    # Round to 4 sig figs to keep diffs stable across pulls.
    return round(float(size_bytes) / (1024**3), 4)


def _resolve_ctx(entry: dict[str, Any], primary_label: str | None) -> int | None:
    """Return ``max_context_window`` value for the entry, or None.

    Priority order:
      1. ``defaults.context_size`` — operator-set launcher default
      2. ``metadata.context_length`` — GGUF arch max (set by detect.py)
      3. ``_DEFAULT_LLM_CTX`` for chat models with no other signal
      4. None for non-llm modalities (embeddings/rerank/asr/tts/image)
    """
    defaults = entry.get("defaults") or {}
    if isinstance(defaults, dict):
        ctx = defaults.get("context_size")
        if isinstance(ctx, int) and ctx > 0:
            return ctx

    metadata = entry.get("metadata") or {}
    if isinstance(metadata, dict):
        ctx = metadata.get("context_length")
        if isinstance(ctx, int) and ctx > 0:
            return ctx

    # Only llm models get a default context. Embeddings/rerank/etc carry
    # their own context window in the model file; Lemonade picks it up.
    if primary_label is None:
        # No capability listed → treat as LLM, return default.
        return _DEFAULT_LLM_CTX
    if primary_label == "chat":
        return _DEFAULT_LLM_CTX
    return None


def _entry_labels(caps: list[str], tags: list[str], primary_label: str | None) -> list[str]:
    """Build the ``labels`` list for one Lemonade entry.

    Rules:
      * The primary Lemonade type label (one of ``embeddings`` / ``reranking``
        / ``transcription`` / ``tts`` / ``image``) goes first when present.
      * ``vision`` is preserved as a secondary label so Lemonade's
        chat-indicator classifier keeps a vision-LLM as LLM.
      * Other hal0 tags get a passthrough copy (deduped, no None values).
    """
    out: list[str] = []
    if primary_label and primary_label != "chat":
        mapped = _CAPABILITY_TO_LABEL.get(primary_label)
        if mapped:
            out.append(mapped)

    # Always preserve vision as a chat-indicator label.
    if any(isinstance(c, str) and c.lower() == "vision" for c in caps) and "vision" not in out:
        out.append("vision")

    # Tag passthrough: lowercase, dedupe.
    for tag in tags or []:
        if not isinstance(tag, str):
            continue
        tl = tag.lower()
        if tl and tl not in out:
            out.append(tl)

    return out


# ── Public API ─────────────────────────────────────────────────────────────────


def _lemon_entry_from_registry(entry: dict[str, Any]) -> dict[str, Any]:
    """Convert one registry entry dict into its Lemonade server-model entry."""
    caps = list(entry.get("capabilities") or [])
    primary = _pick_primary_capability(caps)
    primary_label = _CAPABILITY_TO_LABEL.get(primary or "chat")
    # primary_label is None when capability is 'chat' (no Lemonade label
    # needed) or unrecognised. Either way recipe-resolve handles it.

    backends = list(entry.get("backends") or [])
    recipe = _resolve_recipe(backends, primary_label)

    checkpoint = _resolve_checkpoint(entry)
    size_gb = _resolve_size_gb(entry)
    labels = _entry_labels(caps, list(entry.get("tags") or []), primary)

    lemon: dict[str, Any] = {
        "checkpoint": checkpoint,
        "recipe": recipe,
        "labels": labels,
        "size": size_gb,
        "suggested": False,
    }

    ctx = _resolve_ctx(entry, primary)
    # Lemonade reads max_context_window via recipe_options at load time;
    # we surface it as a sibling key for documentation + hal0 UI use.
    if ctx is not None and recipe in ("llamacpp", "flm"):
        lemon["max_context_window"] = ctx

    return lemon


def _curated_to_registry_entry(model: Any) -> dict[str, Any]:
    """Adapt a ``CuratedModel`` into the registry-entry dict the generator
    consumes, so a curated pick flows through the same per-entry machinery.

    The curated schema uses singular field names (``hf_file``, ``capability``,
    ``backend``) where the registry uses plural ones (``hf_filename``,
    ``capabilities``, ``backends``); this bridges the two.
    """
    size_bytes = round(float(model.size_gb) * (1024**3)) if model.size_gb else 0
    entry: dict[str, Any] = {
        "capabilities": [model.capability] if model.capability else ["chat"],
        "backends": [model.backend] if model.backend else [],
        "hf_repo": model.hf_repo,
        "hf_filename": model.hf_file,
        "tags": list(model.tags),
        "size_bytes": size_bytes,
    }
    if getattr(model, "context_length", 0):
        entry["metadata"] = {"context_length": model.context_length}
    return entry


def _stock_fallback_catalog() -> dict[str, dict[str, Any]]:
    """Build the non-empty stock catalog used when the registry is empty.

    Draws the canonical curated ids in :data:`STOCK_FALLBACK_IDS` from
    ``hal0.registry.curated.CURATED_BY_ID`` and shapes each as a Lemonade
    server-model entry. Imported lazily so the registry package is not pulled
    in on the common (populated-registry) path.
    """
    # Local import to keep the populated-registry path import-light and avoid
    # a module-level dependency cycle between lemonade and registry.
    from hal0.registry.curated import CURATED_BY_ID

    out: dict[str, dict[str, Any]] = {}
    for mid in STOCK_FALLBACK_IDS:
        model = CURATED_BY_ID.get(mid)
        if model is None:
            # Defensive: a curated id was renamed without updating the
            # fallback list. Skip it rather than emit a broken entry.
            log.warning("stock fallback id %s not in curated catalog - skipping", mid)
            continue
        out[mid] = _lemon_entry_from_registry(_curated_to_registry_entry(model))
    return out


def generate_server_models(registry_path: Path) -> dict[str, dict[str, Any]]:
    """Read ``registry.toml`` and return the Lemonade ``server_models.json`` dict.

    The returned dict is sorted by model id (deterministic output) so the
    file is diff-friendly across runs. The caller serialises with
    ``json.dumps(..., indent=4)`` to match Lemonade's own formatting.

    When the registry yields no usable models (missing/malformed file, or a
    not-yet-seeded fresh install), a curated STOCK set is returned instead of
    an empty dict - see :data:`STOCK_FALLBACK_IDS` and issue #210. install.sh
    overwrites Lemonade's bundled catalog with this output, so a blank result
    would leave the daemon with nothing to load.

    Args:
        registry_path: Absolute path to ``registry.toml``. A missing or empty
            registry yields the stock fallback (warning logged).

    Returns:
        ``{model_id: {checkpoint, recipe, labels, size, suggested, ...}}``.
    """
    registry_path = Path(registry_path)
    entries = _read_registry(registry_path)

    if not entries:
        log.warning(
            "registry yielded no models - emitting %d stock fallback entries (#210)",
            len(STOCK_FALLBACK_IDS),
        )
        return _stock_fallback_catalog()

    out: dict[str, dict[str, Any]] = {}
    for mid in sorted(entries):
        out[mid] = _lemon_entry_from_registry(entries[mid])

    return out


def write_server_models(
    registry_path: Path,
    output_path: Path,
) -> None:
    """Generate + atomically write ``server_models.json``.

    The write goes through a sibling tempfile + ``os.replace``: a partial
    write never overwrites the live file, so a crash mid-run leaves
    Lemonade's existing catalog intact. Same pattern as
    ``ModelRegistry._atomic_write``.

    The output file is chmod'd ``0o644`` (world-readable) after the
    replace. ``mkstemp`` leaves the temp at ``0o600``, which ``os.replace``
    preserves; without this chmod, a root-owned write during install lands
    ``0600 root:root`` and ``hal0-lemonade.service`` (running as
    ``hal0:hal0``) can't read it — issue #211. The catalog is non-secret
    (model ids, recipes, HF coords), so world-readable is correct.

    Idempotent: re-running with an unchanged registry produces the same
    bytes (sorted keys + stable float rounding). Safe to call from the
    install.sh hook OR from ``hal0 capabilities sync``.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    catalog = generate_server_models(registry_path)
    payload = json.dumps(catalog, indent=4, sort_keys=False) + "\n"

    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_path.parent,
        )
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, output_path)
        tmp_path = None
        # mkstemp → 0o600, os.replace preserves mode. The catalog is
        # non-secret (model ids + recipes + HF coords) and the hal0
        # service user must be able to read it (#211).
        os.chmod(output_path, 0o644)
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)


# ── CLI entry point ────────────────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m hal0.lemonade.server_models_gen",
        description=(
            "Generate Lemonade Server's server_models.json from hal0's "
            "registry.toml. Run at install time (before lemond.service "
            "starts) and on `hal0 capabilities sync`."
        ),
    )
    p.add_argument(
        "--registry",
        type=Path,
        default=Path("/var/lib/hal0/registry/registry.toml"),
        help="Path to hal0 registry.toml (default: /var/lib/hal0/registry/registry.toml).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("/opt/lemonade/resources/server_models.json"),
        help="Output path (default: /opt/lemonade/resources/server_models.json).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated JSON to stdout instead of writing.",
    )
    return p


def cli_main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m hal0.lemonade.server_models_gen``.

    Returns the process exit code (0 on success). Logs go to stderr at
    INFO so install.sh can quote them in its progress output.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    args = _build_arg_parser().parse_args(argv)

    if args.dry_run:
        catalog = generate_server_models(args.registry)
        json.dump(catalog, sys.stdout, indent=4)
        sys.stdout.write("\n")
        log.info(
            "server_models_gen: dry-run — %d entries from %s",
            len(catalog),
            args.registry,
        )
        return 0

    write_server_models(args.registry, args.output)
    # Re-read for the count without paying the round-trip cost again.
    catalog = generate_server_models(args.registry)
    log.info(
        "server_models_gen: wrote %d entries to %s (from %s)",
        len(catalog),
        args.output,
        args.registry,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via test_cli_main
    sys.exit(cli_main())


__all__ = [
    "STOCK_FALLBACK_IDS",
    "cli_main",
    "generate_server_models",
    "write_server_models",
]
