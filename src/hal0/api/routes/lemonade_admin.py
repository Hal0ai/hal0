"""Lemonade admin endpoints (mounted under /api/lemonade).

PR-13 (plan §11 + plan §2.2 + ADR-0008 §1/§7): config read/write surface
for the Settings → Lemonade admin panel. Two routes:

  ``GET  /api/lemonade/config``   — full runtime config snapshot from
                                    ``lemond /internal/config``. Returned
                                    verbatim plus the immediate-vs-deferred
                                    key-split metadata the UI needs to
                                    render the "takes effect now" / "next
                                    load" labels (plan §2.2).
  ``POST /api/lemonade/config``   — body ``{key: value, ...}``. Forwarded
                                    to ``lemond /internal/set`` after
                                    validation. The response ECHOES which
                                    keys took immediate effect and which
                                    are deferred until next load — the UI
                                    surfaces this in the success toast.

Both routes are admin-gated by the parent ``_admin_auth`` dependency
applied at ``include_router`` time (see :mod:`hal0.api.__init__`), so we
don't redeclare auth here. Read is GET; the mutating route is also
gated by ``require_writer`` so a cookie-based session needs CSRF in
addition to the admin scope — matching the pattern in
``/api/settings``.

Validation guardrails (plan §11 PR-13 brief + ADR-0008 §3 + §7):

  - Unknown keys (not in either admin list) are refused 400.
  - ``llamacpp_args`` MUST contain ``--threads N`` with N >= 2 — per the
    ``hal0_lemonade_threads_deadlock`` memory, omitting --threads or
    dropping below 2 oversubscribes the LXC's cores and trips a 30s
    Vulkan deadlock on the first dual-child load.
  - ``flm_args`` MUST contain BOTH ``--asr 1`` AND ``--embed 1`` — the
    FLM trio is mandatory in v0.2 (plan §5, ADR-0009 §1). Without these
    flags the NPU stt/embed slots have no backend.
  - ``extra_models_dir`` MUST equal ``/var/lib/hal0/models`` — the
    symlink farm root (plan §3 + §6.1). Other values would silently
    repurpose lemond's auto-discovery against hal0's curated layout.
    Refused 400 rather than warned because flipping this in production
    leaves the dashboard rendering models lemond can't actually load.

Validation failures return the hal0 error envelope with
``code: "lemonade.config_invalid"`` and per-field ``details``.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request

from hal0.errors import BadRequest, Hal0Error

router = APIRouter()

# See settings.py for the writer-gate rationale: GET stays on the parent
# admin gate (require_token), POST additionally requires writer scope so
# cookie sessions ride through the CSRF tripwire alongside Bearer tokens.


# ── key taxonomy (plan §2.2) ──────────────────────────────────────────

# Keys whose value lemond applies immediately on POST /internal/set. The
# admin UI shows "Immediate" beside these.
IMMEDIATE_KEYS: frozenset[str] = frozenset(
    {
        "port",
        "host",
        "log_level",
        "global_timeout",
        "no_broadcast",
        "extra_models_dir",
    }
)

# Keys whose value lemond persists but does NOT apply until the next
# /v1/load. The admin UI shows "Deferred (next load)" beside these so
# the operator isn't surprised when the value sticks but observed
# behaviour stays on the prior value until a load lands.
DEFERRED_KEYS: frozenset[str] = frozenset(
    {
        "max_loaded_models",
        "ctx_size",
        "llamacpp_backend",
        "llamacpp_args",
        "sdcpp_backend",
        "whispercpp_backend",
        "steps",
        "cfg_scale",
        "width",
        "height",
        "flm_args",
    }
)

# Union of every admin-editable key — gates "unknown key" rejection.
ADMIN_KEYS: frozenset[str] = IMMEDIATE_KEYS | DEFERRED_KEYS

# Locked invariant for extra_models_dir per plan §3 + §6.1. The
# installer writes this path into the seeded config.json and the symlink
# farm depends on it; flipping it from the admin panel would silently
# desync the dashboard's model list from what lemond can actually load.
#
# v0.3 update: the locked value is now derived from
# ``[models].store`` (or the legacy ``pull_root`` fallback) via
# :func:`_locked_extra_models_dir`. The constant below is the
# installer-default and the test fallback when no hal0.toml exists.
# Settings → Models is the *one* place to change the store path; the
# Lemonade admin panel still refuses any divergent value because the
# whole point of /api/settings/models/store is to keep all consumers in
# lockstep.
LOCKED_EXTRA_MODELS_DIR: str = "/var/lib/hal0/models"


def _locked_extra_models_dir() -> str:
    """Return the path Lemonade's ``extra_models_dir`` must equal.

    Resolves from ``[models].effective_store()`` so a user who set the
    store via Settings → Models is allowed to edit Lemonade's config
    coherently. Falls back to :data:`LOCKED_EXTRA_MODELS_DIR` when no
    hal0.toml exists (fresh install / test environment), matching what
    the installer writes.
    """
    try:
        from hal0.config.loader import load_hal0_config

        cfg = load_hal0_config()
        return cfg.models.effective_store()
    except Exception:
        return LOCKED_EXTRA_MODELS_DIR


class LemonadeConfigInvalidError(Hal0Error):
    """Validation failure on POST /api/lemonade/config — typed so the
    envelope carries a per-key reason map."""

    code = "lemonade.config_invalid"
    status = 400


# ── llamacpp_args parser ──────────────────────────────────────────────


# Match ``--threads N`` (single dash long form). Lemonade serialises
# llamacpp_args as a single space-separated string per the
# ``hal0_lemonade_v1_load_schema`` memory; we accept the same form here.
# The lookahead-anchored boundary keeps ``--threads-extra`` (a
# hypothetical future flag) from matching.
_THREADS_RE = re.compile(r"(?:^|\s)--threads(?:\s+|=)(\d+)(?=\s|$)")


def _extract_threads(llamacpp_args: str) -> int | None:
    """Return the parsed ``--threads N`` value, or None if absent.

    Matches both ``--threads 8`` and ``--threads=8`` so the operator
    isn't forced into one wire form. Returns None when the flag is
    missing entirely; returns the integer otherwise (caller validates
    the >=2 bound).
    """
    m = _THREADS_RE.search(llamacpp_args)
    if m is None:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


# ── per-key validators ────────────────────────────────────────────────


def _validate_llamacpp_args(value: object) -> str | None:
    """Return an error message if ``llamacpp_args`` would break the
    locked invariant, else None.

    Per ``hal0_lemonade_threads_deadlock``: lemond's default child
    invocation omits ``--threads``, which on a 12-core LXC starts
    multiple Vulkan dispatch threads per child and oversubscribes the
    cores. The recipe is ``--parallel 1 --threads N`` with N computed
    from cores; we refuse anything that drops or zeroes the flag.
    """
    if not isinstance(value, str):
        return "must be a string"
    n = _extract_threads(value)
    if n is None:
        return (
            "must include --threads N where N >= 2 "
            "(per hal0_lemonade_threads_deadlock — omitting --threads "
            "trips a Vulkan dispatch deadlock under concurrent load)"
        )
    if n < 2:
        return f"--threads {n} is below the required minimum of 2"
    return None


def _validate_flm_args(value: object) -> str | None:
    """Return an error message if ``flm_args`` is malformed, else None.

    The NPU has a single AMDXDNA hardware context per host (ADR-0009 §1) —
    one ``flm serve`` process. The trio flags ``--asr <0|1>`` / ``--embed <0|1>``
    toggle whether ASR and embed ride coresident in that one process.

    Relaxed 2026-06-05 (Spec 2: NPU/FLM stack section): the dashboard NPU
    section sets these flags explicitly, so a chat-only or chat+one-modality
    stack is a valid configuration — we no longer mandate ``--asr 1 --embed 1``.
    We reject only MALFORMED values. NOTE: when a modality is set to 0, the
    caller MUST also disable the corresponding NPU ``transcription`` /
    ``embedding`` slot so dispatch gating (``v1._is_npu_trio_request``) doesn't
    route requests to a modality the FLM child isn't serving (which would 404).
    The dashboard NPU section keeps these in sync.
    """
    if not isinstance(value, str):
        return "must be a string"
    # Tolerate any whitespace between flag and arg ("--asr 1", "--asr  1",
    # "--asr\t1"). Accept 0 (disable) or 1 (enable); reject anything else.
    for flag in ("asr", "embed"):
        m = re.search(rf"--{flag}\s+(\S+)", value)
        if m and m.group(1) not in ("0", "1"):
            return f"--{flag} must be 0 or 1 (got {m.group(1)!r})"
    return None


def _validate_extra_models_dir(value: object) -> str | None:
    """Return an error message if ``extra_models_dir`` would diverge
    from the hal0 store root, else None.

    The locked value is derived from ``[models].effective_store()`` —
    so an operator who set the store via Settings → Models can edit
    Lemonade's config coherently. To change the store path itself, use
    POST /api/settings/models/store; that endpoint propagates the new
    value to Lemonade for you.
    """
    if not isinstance(value, str):
        return "must be a string"
    locked = _locked_extra_models_dir()
    if value != locked:
        return (
            f"must equal {locked!r} "
            "(the hal0 model store is the single source of truth for the catalog; "
            "use POST /api/settings/models/store to change the path)"
        )
    return None


# Map of key -> validator. Keys not in the map skip per-value validation
# but still go through the unknown-key gate above.
_VALIDATORS: dict[str, Any] = {
    "llamacpp_args": _validate_llamacpp_args,
    "flm_args": _validate_flm_args,
    "extra_models_dir": _validate_extra_models_dir,
}


def _validate_patch(body: dict[str, Any]) -> dict[str, str]:
    """Return a ``{key: reason}`` map of validation errors. Empty when
    the patch is acceptable.

    Order:
      1. Unknown key gate (key not in IMMEDIATE_KEYS | DEFERRED_KEYS).
      2. Per-key validator for the locked-invariant keys.

    Keys without a dedicated validator are passed through — lemond's own
    typed schema is the second line of defence (an out-of-range
    ``global_timeout`` will surface as a 4xx from /internal/set), but we
    don't reject ergonomic values like ``port=13306`` at this layer.
    """
    errors: dict[str, str] = {}
    for key, value in body.items():
        if key not in ADMIN_KEYS:
            errors[key] = f"unknown key — admin-editable keys are {sorted(ADMIN_KEYS)}"
            continue
        validator = _VALIDATORS.get(key)
        if validator is None:
            continue
        reason = validator(value)
        if reason is not None:
            errors[key] = reason
    return errors


# ── effect-classification helper ──────────────────────────────────────


def _classify_effects(keys: list[str]) -> dict[str, list[str]]:
    """Split a list of touched keys into ``{immediate: [...], deferred:
    [...]}``. Used to echo what the operator's POST actually changed."""
    immediate = sorted(k for k in keys if k in IMMEDIATE_KEYS)
    deferred = sorted(k for k in keys if k in DEFERRED_KEYS)
    return {"immediate": immediate, "deferred": deferred}


# ── routes ────────────────────────────────────────────────────────────


@router.get("/config")
async def get_lemonade_config(request: Request) -> dict[str, Any]:
    """Return the full ``lemond /internal/config`` snapshot.

    Verbatim payload from lemond plus a ``_hal0.effects`` block carrying
    the immediate-vs-deferred key partition so the UI doesn't have to
    re-encode the lists locally. The underscore prefix marks it as a
    hal0-added envelope field rather than something lemond owns.

    On lemond unavailable we surface the Lemonade error as-is rather
    than masking it as 500 — the admin panel's empty state needs to
    distinguish "control plane down" from "config save crashed".
    """
    from hal0.providers import lemonade_provider

    client = lemonade_provider().client()
    snapshot = await client.internal_config()
    return {
        **snapshot,
        "_hal0": {
            "effects": {
                "immediate": sorted(IMMEDIATE_KEYS),
                "deferred": sorted(DEFERRED_KEYS),
            },
            "locked": {
                "extra_models_dir": _locked_extra_models_dir(),
            },
        },
    }


@router.post("/config")
async def set_lemonade_config(request: Request) -> dict[str, Any]:
    """Apply a partial config update against ``lemond /internal/set``.

    Body shape: ``{key: value, ...}`` — only the keys being changed. The
    keys must all be in :data:`ADMIN_KEYS` and pass any locked-invariant
    validators (see :func:`_validate_patch`). Validation failures return
    400 ``lemonade.config_invalid`` with a per-key ``details`` map.

    Response shape::

        {
          "applied": {<the keys lemond echoed back>},
          "effects": {
            "immediate": ["log_level", ...],
            "deferred":  ["llamacpp_args", ...]
          }
        }

    The UI uses ``effects`` to render the success toast ("Saved — N
    immediate, M deferred until next load") and to know whether to
    surface a "restart a slot to apply" hint.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest(
            "request body must be valid JSON",
            details={"error": str(exc)},
            code="request.invalid_json",
        ) from exc
    if not isinstance(body, dict):
        raise BadRequest(
            "request body must be a JSON object",
            code="request.not_an_object",
        )
    if not body:
        # Empty PATCHes are a no-op; surface that explicitly rather
        # than calling /internal/set with `{}` (lemond's behaviour for
        # an empty body is unspecified).
        raise BadRequest(
            "request body must contain at least one key to set",
            code="lemonade.config_empty",
        )

    errors = _validate_patch(body)
    if errors:
        raise LemonadeConfigInvalidError(
            "one or more keys failed validation",
            details=errors,
        )

    from hal0.providers import lemonade_provider

    client = lemonade_provider().client()
    result = await client.internal_set(body)

    # Echo the immediate-vs-deferred split for exactly the keys this
    # request touched. We classify off the request body rather than the
    # response's ``applied`` field so the UI can render the toast even
    # if a future lemond version drops that field.
    effects = _classify_effects(list(body.keys()))

    return {
        "applied": result,
        "effects": effects,
    }


__all__ = [
    "ADMIN_KEYS",
    "DEFERRED_KEYS",
    "IMMEDIATE_KEYS",
    "LOCKED_EXTRA_MODELS_DIR",
    "LemonadeConfigInvalidError",
    "router",
]
