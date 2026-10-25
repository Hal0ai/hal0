"""OpenAI ``/v1/images/generations`` → ComfyUI prompt-graph translator.

ComfyUI exposes a node-graph IR over its ``POST /prompt`` endpoint. The
hal0 image-gen surface is the OpenAI shape — model id, prompt text, n,
size, response_format. Bridging the two is a parametric template fill:
each shipped template (``sdxl_turbo_simple.json``, ``sd15_simple.json``,
…) declares which graph nodes hold the prompt / latent dimensions /
seed / etc., and this module patches them in. Which template a model uses
is the catalog's concern (:attr:`hal0.comfyui.catalog.ImageModel.template`);
the translator is handed a stem and knows nothing about models.

The OpenAI shape we honour::

    {
      "model":           "<catalog model id, e.g. sdxl-turbo>",
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

    * Param substitution through the template's ``_meta.params`` pointers
      (prompt, width, height, batch, seed, steps, cfg, negative prompt, and
      the checkpoint on the templates that let the caller choose it).
    * ``steps`` / ``cfg`` defaults from the template's ``_meta.defaults``.
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
from typing import Any

from hal0.comfyui.catalog import load_template, parse_pointer
from hal0.errors import Hal0Error

# Hardcoded OpenAI-compat default size when caller omits ``size``.
_DEFAULT_SIZE = "1024x1024"


# ── Typed errors ──────────────────────────────────────────────────────────────


class WorkflowTemplateError(Hal0Error):
    """Translator failed to build a usable ComfyUI workflow."""

    code = "image.workflow_invalid"
    status = 422


class WorkflowTemplateNotFound(WorkflowTemplateError):
    """No template of that name ships with hal0."""

    code = "image.workflow_not_found"
    status = 404


# ── Template loading ──────────────────────────────────────────────────────────


def _load_template(stem: str) -> dict[str, Any]:
    """Read a workflow template, ``_meta`` included, via the catalog's loader.

    :func:`hal0.comfyui.catalog.load_template` owns reading the package
    resource; this wrapper maps its failures onto the translator's typed
    errors.
    """
    try:
        return load_template(stem)
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise WorkflowTemplateNotFound(
            f"workflow template {stem!r} not found in hal0.providers.workflows",
            details={"stem": stem},
        ) from exc
    except json.JSONDecodeError as exc:
        raise WorkflowTemplateError(
            f"workflow template {stem!r} is not valid JSON: {exc}",
            details={"stem": stem, "error": str(exc)},
        ) from exc
    except ValueError as exc:
        raise WorkflowTemplateError(str(exc), details={"stem": stem}) from exc


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


def effective_size(size: Any) -> str:
    """The ``"WxH"`` that :func:`build_workflow` renders for a request's ``size``.

    The same parse and clamp the translator applies (:func:`_parse_size`): an unreadable or
    missing size is the default, each side is clamped to 64..2048 and floored to a multiple
    of 8. A caller that must judge the render before it runs (the fit preflight) reads it here
    rather than repeating the arithmetic.
    """
    width, height = _parse_size(size)
    return f"{width}x{height}"


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_number(value: Any, default: float) -> float:
    """``value`` as a number, an ``int`` staying an ``int``; ``default`` untouched when it is none.

    A graph's own ``cfg`` can be an int (Qwen's is ``1``), and patching ``1.0`` over it would
    change the graph for no reason.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ── The translator ────────────────────────────────────────────────────────────


def _default(stem: str, defaults: dict[str, Any], key: str) -> Any:
    """``_meta.defaults[key]``; a template without it is an authoring error."""
    if key not in defaults:
        raise WorkflowTemplateError(
            f"workflow template {stem!r} has no _meta.defaults.{key}",
            details={"stem": stem, "key": key},
        )
    return defaults[key]


def build_workflow(
    *,
    body: dict[str, Any],
    template: str,
    request_tag: str,
    ckpt_override: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialise a ComfyUI prompt graph for one image-gen request.

    Args:
        body: OpenAI-shaped request body (already parsed JSON).
        template: Workflow template stem (a file under
            ``hal0.providers.workflows``), normally the catalog entry's
            :attr:`~hal0.comfyui.catalog.ImageModel.template`.
        request_tag: Unique short token (8-12 chars) used as the
            ``filename_prefix`` so the response handler can find the
            output(s) via ``/view`` after polling history.
        ckpt_override: Checkpoint filename to load instead of the one baked
            into the template. Only templates that expose a ``ckpt_name``
            pointer accept it (``USER_CKPT_TEMPLATES``).

    Returns:
        ``(prompt_graph, debug_meta)`` where ``prompt_graph`` is the dict
        ready to ship to ComfyUI's ``/prompt`` endpoint, and ``debug_meta``
        carries the resolved parameters (template, seed, dimensions, …) for
        logging and for the OpenAI response envelope.

    Raises:
        WorkflowTemplateNotFound: Template stem doesn't exist on disk.
        WorkflowTemplateError:    Template JSON is malformed or missing
                                  the expected node ids, the prompt is empty,
                                  or ``ckpt_override`` is given for a template
                                  with no ``ckpt_name`` pointer.
    """
    stem = template
    template_doc = _load_template(stem)
    meta = template_doc.get("_meta") or {}
    defaults = meta.get("defaults") or {}
    params = meta.get("params") or {}

    # Strip _meta from the graph we ship — ComfyUI's /prompt would reject
    # it as an invalid node id (it prefers numeric strings).
    graph: dict[str, Any] = {k: v for k, v in template_doc.items() if k != "_meta"}

    # ── Parameter resolution ─────────────────────────────────────────────
    prompt_text = (body.get("prompt") or "").strip()
    if not prompt_text:
        raise WorkflowTemplateError(
            "body.prompt is required and must be a non-empty string",
            details={"got": body.get("prompt")},
        )
    if ckpt_override is not None and not isinstance(params.get("ckpt_name"), str):
        raise WorkflowTemplateError(
            f"workflow template {stem!r} does not take a checkpoint override "
            "(no ckpt_name pointer in _meta.params)",
            details={"stem": stem, "ckpt_override": ckpt_override},
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
    steps = _coerce_int(extra.get("steps"), int(_default(stem, defaults, "steps")))
    cfg = _coerce_number(extra.get("cfg"), _default(stem, defaults, "cfg"))
    negative_prompt = (extra.get("negative_prompt") or "").strip()

    # ── Patch nodes via the meta.params pointers ─────────────────────────
    # Each pointer string is "node:<node_id>.inputs.<field>"; parse_pointer
    # splits it — no eval(), no string interpolation into a query path.
    # Templates that omit a param pointer just don't get patched (graceful
    # degradation when a template legitimately doesn't expose that knob).
    def _set(pointer_key: str, value: Any) -> None:
        ptr = params.get(pointer_key)
        if not isinstance(ptr, str):
            return
        try:
            node_id, field = parse_pointer(ptr)
        except ValueError as exc:
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

    if ckpt_override is not None:
        _set("ckpt_name", ckpt_override)
    _set("positive_prompt", prompt_text)
    if negative_prompt:
        _set("negative_prompt", negative_prompt)
    _set("width", width)
    _set("height", height)
    _set("batch_size", batch_size)
    _set("seed", seed)
    _set("steps", steps)
    _set("cfg", cfg)
    _set("filename_prefix", request_tag)

    debug_meta = {
        "template": stem,
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
    "WorkflowTemplateError",
    "WorkflowTemplateNotFound",
    "build_workflow",
    "effective_size",
]
