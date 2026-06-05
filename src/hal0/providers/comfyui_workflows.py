"""OpenAI ``/v1/images/generations`` → ComfyUI prompt-graph translator.

ComfyUI exposes a node-graph IR over its ``POST /prompt`` endpoint. The
hal0 image-gen surface is the OpenAI shape — model id, prompt text, n,
size, response_format. Bridging the two is a parametric template fill:
each shipped template (``sdxl_turbo_simple.json``, ``sd15_simple.json``,
…) declares which graph nodes hold the prompt / latent dimensions /
seed / etc., and this module patches them in.

The OpenAI shape we honour::

    {
      "model":           "<curated model id, e.g. sdxl-turbo>",
      "prompt":          "<positive prompt text>",
      "n":               1,                   # batch_size
      "size":            "1024x1024",         # WxH; one image
      "response_format": "url" | "b64_json",
      "extra_body": {                          # hal0 extension
          "seed":            12345,
          "steps":           4,
          "cfg":             1.0,
          "negative_prompt": "ugly, blurry"
      }
    }

The translator owns:

    * Template lookup by ``model_class`` (curated entry pin).
    * Param substitution (prompt, width, height, batch, seed, steps, cfg,
      negative prompt, ckpt filename).
    * Random seed generation when none supplied.
    * Filename prefix that we use later to find the result via
      ``GET /view`` (the prefix becomes the unique key per request).

Everything else (HTTP submission to ComfyUI, history polling, /view
fetch) lives in :mod:`hal0.providers.comfyui` so this module stays a
pure pipeline of dict transforms — trivially unit-testable without a
ComfyUI instance.
"""

from __future__ import annotations

import json
import secrets
from importlib import resources
from typing import Any

from hal0.errors import Hal0Error

# ── Template registry ─────────────────────────────────────────────────────────
#
# Maps model_class → template stem (file under workflows/<stem>.json).
# Curated entries declare ``model_class`` so the resolution chain is:
#   OpenAI body.model → curated.model_class → MODEL_CLASS_TO_TEMPLATE → JSON file
#
# Default fallback is sdxl_turbo_simple — the lowest-ceremony usable
# graph in the SD ecosystem.
MODEL_CLASS_TO_TEMPLATE: dict[str, str] = {
    "sdxl-turbo": "sdxl_turbo_simple",
    "sdxl": "sdxl_turbo_simple",
    "sd-1.5": "sd15_simple",
    "sd15": "sd15_simple",
    # NOTE: Flux model classes (e.g. flux-klein) want their own template
    # (different VAE, text encoder, sampler defaults). Until we ship one,
    # route Flux callers through SDXL Turbo's graph as a soft fallback so
    # the API never 404s on a registered curated id.
    "flux-klein": "sdxl_turbo_simple",
}

# Hardcoded OpenAI-compat default size when caller omits ``size``.
_DEFAULT_SIZE = "1024x1024"


# ── Typed errors ──────────────────────────────────────────────────────────────


class WorkflowTemplateError(Hal0Error):
    """Translator failed to build a usable ComfyUI workflow."""

    code = "image.workflow_invalid"
    status = 422


class WorkflowTemplateNotFound(WorkflowTemplateError):
    """No template ships for the requested model_class."""

    code = "image.workflow_not_found"
    status = 404


# ── Template loading ──────────────────────────────────────────────────────────


def _load_template(stem: str) -> dict[str, Any]:
    """Read a workflow JSON from the package resources.

    ``importlib.resources`` keeps the template loader working when hal0
    is installed as a wheel (no on-disk path) and during dev (running
    from a source tree).
    """
    try:
        text = (
            resources.files("hal0.providers.workflows")
            .joinpath(f"{stem}.json")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise WorkflowTemplateNotFound(
            f"workflow template {stem!r} not found in hal0.providers.workflows",
            details={"stem": stem},
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkflowTemplateError(
            f"workflow template {stem!r} is not valid JSON: {exc}",
            details={"stem": stem, "error": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise WorkflowTemplateError(
            f"workflow template {stem!r} root is not an object",
            details={"stem": stem},
        )
    return data


def template_for_model_class(model_class: str | None) -> str:
    """Resolve a model_class string to a template stem.

    Falls back to ``sdxl_turbo_simple`` when the class is unknown so the
    caller never gets a 404 just because we haven't tagged a curated entry.
    """
    if not model_class:
        return "sdxl_turbo_simple"
    return MODEL_CLASS_TO_TEMPLATE.get(model_class.lower(), "sdxl_turbo_simple")


# ── Body parsing ──────────────────────────────────────────────────────────────


def _parse_size(size: str | None) -> tuple[int, int]:
    """Parse OpenAI-style ``"1024x1024"`` → ``(1024, 1024)``.

    Tolerates capital ``X``. Falls back to ``_DEFAULT_SIZE`` on anything
    we can't parse — the alternative is a 422 for a typo deep in a graph,
    which is bad operator UX.
    """
    raw = (size or _DEFAULT_SIZE).lower().strip()
    try:
        w_str, h_str = raw.split("x", 1)
        w, h = int(w_str), int(h_str)
    except (ValueError, AttributeError):
        w, h = (int(_DEFAULT_SIZE.split("x")[0]), int(_DEFAULT_SIZE.split("x")[1]))
    # SDXL/SD 1.5 both prefer multiples of 8 for the latent. Clamp to a
    # safe sane range so a ridiculous "10x10" or "20000x20000" can't crash
    # the slot.
    w = max(64, min(w, 2048)) // 8 * 8
    h = max(64, min(h, 2048)) // 8 * 8
    return w, h


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ── The translator ────────────────────────────────────────────────────────────


def build_workflow(
    *,
    body: dict[str, Any],
    model_class: str | None,
    ckpt_filename: str,
    request_tag: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialise a ComfyUI prompt graph for one image-gen request.

    Args:
        body: OpenAI-shaped request body (already parsed JSON).
        model_class: Curated entry's ``model_class`` string. Drives
            template selection (``sdxl_turbo_simple`` default).
        ckpt_filename: Filename inside ComfyUI's ``models/checkpoints``
            directory that the ``CheckpointLoaderSimple`` node will load.
            This is the ``hf_file`` from the curated entry.
        request_tag: Unique short token (8-12 chars) used as the
            ``filename_prefix`` so the response handler can find the
            output(s) via ``/view`` after polling history.

    Returns:
        ``(prompt_graph, debug_meta)`` where ``prompt_graph`` is the dict
        ready to ship to ComfyUI's ``/prompt`` endpoint, and ``debug_meta``
        carries the resolved parameters (seed, dimensions, …) for logging
        and for the OpenAI response envelope.

    Raises:
        WorkflowTemplateNotFound: Template stem doesn't exist on disk.
        WorkflowTemplateError:    Template JSON is malformed or missing
                                  the expected node ids.
    """
    stem = template_for_model_class(model_class)
    template = _load_template(stem)
    meta = template.get("_meta") or {}
    defaults = meta.get("defaults") or {}

    # Strip _meta from the graph we ship — ComfyUI's /prompt would reject
    # it as an invalid node id (it prefers numeric strings).
    graph: dict[str, Any] = {k: v for k, v in template.items() if k != "_meta"}

    # ── Parameter resolution ─────────────────────────────────────────────
    prompt_text = (body.get("prompt") or "").strip()
    if not prompt_text:
        raise WorkflowTemplateError(
            "body.prompt is required and must be a non-empty string",
            details={"got": body.get("prompt")},
        )
    extra = body.get("extra_body") or {}
    if not isinstance(extra, dict):
        extra = {}

    width, height = _parse_size(body.get("size"))
    batch_size = max(1, min(_coerce_int(body.get("n"), 1), 8))
    # 64-bit randomness range mirrors the ComfyUI UI (which uses Python
    # int range up to 2**64 - 1). secrets.randbelow() is cryptographically
    # solid and does not touch the global random state.
    seed = _coerce_int(extra.get("seed"), secrets.randbelow(2**63 - 1))
    steps = _coerce_int(extra.get("steps"), _coerce_int(defaults.get("steps"), 4))
    cfg = _coerce_float(extra.get("cfg"), _coerce_float(defaults.get("cfg"), 1.0))
    negative_prompt = (extra.get("negative_prompt") or "").strip()

    # ── Patch nodes via the meta.params pointers ─────────────────────────
    # Each pointer string is "node:<node_id>.inputs.<field>". We split it
    # to walk the dict — no eval(), no string interpolation into a query
    # path. Templates that omit a param pointer just don't get patched
    # (graceful degradation when a template legitimately doesn't expose
    # that knob).
    params = meta.get("params") or {}

    def _set(pointer_key: str, value: Any) -> None:
        ptr = params.get(pointer_key)
        if not isinstance(ptr, str):
            return
        if not ptr.startswith("node:"):
            return
        try:
            node_id, dotted = ptr[len("node:") :].split(".", 1)
            assert dotted.startswith("inputs.")
            field = dotted[len("inputs.") :]
        except (AssertionError, ValueError) as exc:
            raise WorkflowTemplateError(
                f"workflow template {stem!r} has malformed param pointer for {pointer_key!r}: {ptr}",
                details={"pointer": ptr, "key": pointer_key},
            ) from exc
        node = graph.get(node_id)
        if not isinstance(node, dict):
            raise WorkflowTemplateError(
                f"workflow template {stem!r} pointer {pointer_key!r} → node {node_id!r} not found",
                details={"pointer": ptr, "node_id": node_id},
            )
        inputs = node.setdefault("inputs", {})
        inputs[field] = value

    _set("ckpt_name", ckpt_filename)
    _set("positive_prompt", prompt_text)
    _set("width", width)
    _set("height", height)
    _set("batch_size", batch_size)
    _set("seed", seed)
    _set("steps", steps)
    _set("cfg", cfg)
    _set("filename_prefix", request_tag)

    # Negative prompt is optional in the OpenAI shape and present in
    # SD-style templates (CLIPTextEncode node 7 in our shipped graphs).
    # We don't surface a meta pointer for it (template authors using
    # different graph structures may not have a negative node), so
    # patch the conventional node 7 only when the template still uses
    # the default empty-string CLIP encode there.
    if negative_prompt:
        node7 = graph.get("7")
        if isinstance(node7, dict) and node7.get("class_type") == "CLIPTextEncode":
            node7.setdefault("inputs", {})["text"] = negative_prompt

    debug_meta = {
        "template": stem,
        "model_class": model_class or "",
        "ckpt_filename": ckpt_filename,
        "width": width,
        "height": height,
        "batch_size": batch_size,
        "seed": seed,
        "steps": steps,
        "cfg": cfg,
        "negative_prompt": negative_prompt,
        "filename_prefix": request_tag,
    }
    return graph, debug_meta


__all__ = [
    "MODEL_CLASS_TO_TEMPLATE",
    "WorkflowTemplateError",
    "WorkflowTemplateNotFound",
    "build_workflow",
    "template_for_model_class",
]
