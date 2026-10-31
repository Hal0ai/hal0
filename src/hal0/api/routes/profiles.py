"""Profile catalog endpoints.

Mounted under /api/profiles:

    GET    ""              — list all profiles
    POST   ""              — create a custom profile (201)
    POST   "/generate"     — generate a draft profile from a model/HF repo (compute-only)
    POST   "/import"       — import a profile from a portable envelope
    GET    "/{name}"       — resolve a single profile
    POST   "/{name}/export"— export a profile to a portable envelope
    PUT    "/{name}"       — update a custom profile (200)
    DELETE "/{name}"       — delete a custom profile (204)

Seed profiles (defined in SEED_PROFILES) are immutable via the API.

Write flow delegates to :class:`hal0.profiles.ProfileCatalog`, which owns
seed immutability, duplicate checks, in-use scans, and full-catalog
atomic writes.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from hal0.api._audit import record_action
from hal0.config.schema import (
    CTX_SIZE_MAX,
    CTX_SIZE_MIN,
    REASONING_BUDGET_MAX,
    ProfileConfig,
    ReasoningEffort,
)
from hal0.errors import BadRequest, UnprocessableEntity
from hal0.profiles import (
    UNSET,
    ProfileCatalog,
    ProfilePatch,
    runtime_family_of,
    screen_profile_flags,
)
from hal0.profiles.generate import LlmCallContext, generate_draft_profile
from hal0.profiles.portable import (
    envelope_runner_status,
    export_envelope,
    import_profile,
    parse_envelope,
    verify_checksum,
)
from hal0.service_identity import service_auth_headers

log = logging.getLogger(__name__)

router = APIRouter()

#: Mirror of manager._SLOT_NAME_RE — kebab-case, leading alphanumeric, ≤32 chars.
_PROFILE_NAME_RE = r"^[a-z0-9][a-z0-9_-]{0,31}$"


# ── request models ────────────────────────────────────────────────────────────


def _refuse_renamed_mtp(data: Any) -> Any:
    """ADR-0031 §7: ``mtp`` left the profile; there is no alias, only this 422."""
    if isinstance(data, dict) and "mtp" in data:
        raise UnprocessableEntity(
            "profile 'mtp' is now 'speculation' (auto | off | ngram)",
            code="profiles.field_renamed",
            details={"field": "mtp", "renamed_to": "speculation"},
        )
    return data


class ProfileBody(BaseModel):
    """Body for POST /api/profiles (ADR-0029: a profile is a complete tune)."""

    _refuse_mtp = model_validator(mode="before")(_refuse_renamed_mtp)

    name: str = Field(
        ...,
        description="Profile key (kebab-case, ≤32 chars, leading alphanumeric).",
    )
    flags: str = Field(
        default="",
        description=(
            "Free llama-server flags (KV cache types, batch sizes, sampling, "
            "--parallel, ...). Hardware, managed and model-fact flags are refused."
        ),
    )
    ctx_size: int | None = Field(
        default=None,
        description=(
            f"Context window, {CTX_SIZE_MIN}..{CTX_SIZE_MAX}. Omit/null = the model's "
            "native window capped at 32768 (8192 when unknown)."
        ),
    )
    enable_thinking: bool | None = Field(
        default=None,
        description="Request-time reasoning default for slots running this profile.",
    )
    vision: Literal["auto", "off"] = Field(
        default="auto", description="'auto' loads the model's mmproj when it has one."
    )
    reasoning_effort: ReasoningEffort = Field(
        default="default",
        description=(
            "Reasoning effort hint (--reasoning-effort) for slots running this "
            "profile; 'default' sends none."
        ),
    )
    reasoning_budget: int = Field(
        default=-1,
        ge=-1,
        le=REASONING_BUDGET_MAX,
        description="Reasoning token budget (--reasoning-budget); -1 = unlimited, 0 = no thinking.",
    )
    speculation: Literal["auto", "off", "ngram"] = Field(
        default="auto",
        description=(
            "'auto' speculates with the model's drafter or MTP heads when it has "
            "them; 'ngram' uses n-gram lookup; 'off' never speculates."
        ),
    )
    runner: str | None = Field(
        default=None,
        description="Optional runtime (RUNNER_IMAGES key) this profile applies to the slot.",
    )
    cloned_from: str | None = Field(
        default=None,
        description="Provenance: profile this one was cloned from (informational).",
    )
    intent: str = Field(default="", description="Human label for the card headline.")

    @field_validator("name")
    @classmethod
    def name_kebab(cls, v: str) -> str:
        if not re.match(_PROFILE_NAME_RE, v):
            raise ValueError(
                "profile name must be kebab-case (a-z0-9_-), ≤32 chars, start with alphanumeric"
            )
        return v


class ProfileGenerateBody(BaseModel):
    """Body for POST /api/profiles/generate."""

    model_id: str | None = Field(
        default=None,
        description="Registered model id to base the draft profile on.",
    )
    hf_repo: str | None = Field(
        default=None,
        description="HuggingFace repo id ('org/name' or full URL) to base the draft on.",
    )
    name: str | None = Field(
        default=None,
        description="Suggested profile name; sanitized to kebab-case (<=32 chars) if needed.",
    )
    use_llm: bool = Field(
        default=False,
        description=(
            "Summarize the model card through the platform's utility LLM to fill the "
            "intent headline. Degrades to heuristics (plus a warning) when the LLM is "
            "unavailable — never fails the request."
        ),
    )

    @model_validator(mode="after")
    def _exactly_one_source(self) -> ProfileGenerateBody:
        has_model = bool(self.model_id and self.model_id.strip())
        has_repo = bool(self.hf_repo and self.hf_repo.strip())
        if has_model == has_repo:
            raise ValueError("exactly one of 'model_id' or 'hf_repo' is required")
        return self


class ProfileUpdateBody(BaseModel):
    """Body for PUT /api/profiles/{name} — name is taken from the URL.

    Sparse: an omitted field keeps its stored value. ``ctx_size`` and
    ``enable_thinking`` additionally take an explicit ``null`` to CLEAR the
    stored value, and so does ``reasoning_budget`` (back to -1) (omitted ≠ null, told apart through ``model_fields_set``).
    """

    _refuse_mtp = model_validator(mode="before")(_refuse_renamed_mtp)

    flags: str | None = Field(default=None, description="Free llama-server CLI flags.")
    ctx_size: int | None = Field(
        default=None,
        description=(
            f"Context window, {CTX_SIZE_MIN}..{CTX_SIZE_MAX}. Omit to leave alone; "
            "null clears it back to the model's native window."
        ),
    )
    enable_thinking: bool | None = Field(
        default=None,
        description="Reasoning default. Omit to leave alone; null clears it.",
    )
    vision: Literal["auto", "off"] | None = Field(
        default=None, description="'auto' | 'off'. Omit to leave alone."
    )
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description="Reasoning effort hint. Omit to leave alone; 'default' sends none.",
    )
    reasoning_budget: int | None = Field(
        default=None,
        ge=-1,
        le=REASONING_BUDGET_MAX,
        description="Reasoning token budget. Omit to leave alone; null clears it to -1 (unlimited).",
    )
    speculation: Literal["auto", "off", "ngram"] | None = Field(
        default=None, description="'auto' | 'off' | 'ngram'. Omit to leave alone."
    )
    runner: str | None = Field(
        default=None,
        description=(
            "Optional runtime (RUNNER_IMAGES key) this profile applies to the "
            "slot. None/omitted leaves the stored value unchanged; the empty "
            "string is the explicit CLEAR sentinel — it unpins the runtime "
            "(back to Auto) and is never screened, so it also works for a "
            "stored key this box's registry no longer carries."
        ),
    )
    intent: str | None = Field(default=None, description="Human label for the card headline.")


def _check_ctx_size(ctx_size: int | None) -> None:
    """400 ``profiles.ctx_size_out_of_range`` for a window outside the schema bounds."""
    if ctx_size is None or CTX_SIZE_MIN <= ctx_size <= CTX_SIZE_MAX:
        return
    raise BadRequest(
        f"ctx_size {ctx_size} is out of range ({CTX_SIZE_MIN}..{CTX_SIZE_MAX})",
        code="profiles.ctx_size_out_of_range",
        details={"ctx_size": ctx_size, "min": CTX_SIZE_MIN, "max": CTX_SIZE_MAX},
    )


# ── routes ────────────────────────────────────────────────────────────────────


@router.get("")
def list_profiles() -> list[dict[str, Any]]:
    """Return every profile in the catalog as a JSON array.

    Each item shape::

        {
            "name":                 "agent",
            "flags":                "-fa on -ctk q8_0 ...",
            "resolved_flags":       "-fa on -ctk q8_0 ...",  # == flags
            "ctx_size":             65536,       # int | null (null = model native, capped)
            "enable_thinking":      null,        # bool | null
            "vision":               "auto",      # auto | off
            "reasoning_effort":     "default",   # default|minimal|low|medium|high|xhigh|max
            "reasoning_budget":     -1,          # int >= -1 (-1 = unlimited)
            "speculation":          "auto",      # auto | off | ngram
            "runner":               null,        # RUNNER_IMAGES key | null
            "seed":                 true,
            "runtime_family":       "llama-server",
            "supported_slot_types": ["llm", "embedding", "reranking"],
            "cloned_from":          null,
            "intent":               "Long-context agent",
            "tps":                  52.8,        # bench tok/s (null when un-benched)
            "rtf":                  null,        # real-time factor for synth slots
            "used_by":              ["agent"]    # slots bound to this profile
        }

    An entry in profiles.toml that failed validation is set aside, not dropped
    (#2653). It is listed as a read-only row that carries no tune::

        {"name": "old", "quarantined": true, "error": "<why>", "seed": false,
         "used_by": []}

    ``DELETE /api/profiles/{name}`` removes it; ``POST /api/profiles`` under the
    same name replaces it.

    Raises:
        500 (ConfigParseError): if profiles.toml is present but malformed.
    """
    return [profile.to_dict() for profile in ProfileCatalog().list(include_quarantined=True)]


# `screen_profile_flags` (hal0.profiles) is the one flag screen for every write
# seam (this route, import, CLI). A profile may not carry hardware flags
# (-ngl/--device/--threads: the slot's typed fields), hal0-managed flags
# (--model/--host/--port/--alias/--ctx-size: use `ctx_size`), model-fact flags
# (--jinja, --chat-template*, --mmproj*, --no-mmproj*), --spec-draft-device or
# --reasoning-format none. Hardware/managed denials raise
# slot.hardware_flag_denied / slot.managed_arg_denied, model-fact flags
# profiles.model_fact_flag_denied, reasoning-format slot.reasoning_format_none_denied.


@router.post("", status_code=201)
async def create_profile(body: ProfileBody, request: Request) -> dict[str, Any]:
    """Create a custom profile.

    Returns the created profile item (same shape as list).

    Raises:
        409 profiles.exists: name already exists (seed or custom).
        422: pydantic validation failure (bad name, vision not auto|off, speculation not auto|off|ngram, a retired
            ``mtp`` key (``profiles.field_renamed``), …).
        400 profiles.ctx_size_out_of_range: ctx_size outside 128..16777216.
        400 slot.hardware_flag_denied: flags carry a slot-owned hardware flag.
        400 slot.managed_arg_denied: flags carry a hal0-managed flag.
        400 profiles.model_fact_flag_denied: flags carry a model-fact flag.
    """
    _check_ctx_size(body.ctx_size)
    screen_profile_flags(body.flags)
    async with record_action(
        request, category="profile", action="profile.create", target=body.name
    ) as rec:
        profile = ProfileCatalog().create(
            body.name,
            ProfileConfig(
                flags=body.flags,
                ctx_size=body.ctx_size,
                enable_thinking=body.enable_thinking,
                vision=body.vision,
                reasoning_effort=body.reasoning_effort,
                reasoning_budget=body.reasoning_budget,
                speculation=body.speculation,
                runner=body.runner,
                cloned_from=body.cloned_from,
                intent=body.intent,
            ),
        )
        rec.after = {
            "name": body.name,
            "ctx_size": body.ctx_size,
            "enable_thinking": body.enable_thinking,
            "vision": body.vision,
            "reasoning_effort": body.reasoning_effort,
            "reasoning_budget": body.reasoning_budget,
            "speculation": body.speculation,
        }
    return profile.to_dict()


@router.post("/generate")
async def generate_profile_route(body: ProfileGenerateBody, request: Request) -> dict[str, Any]:
    """Generate a draft profile from a registered model or a HuggingFace repo.

    Body (exactly one of ``model_id`` / ``hf_repo`` — 422 otherwise)::

        {"model_id": "qwen3-4b"}
        {"hf_repo": "unsloth/Qwen3-8B-GGUF"}
        {"hf_repo": "unsloth/Qwen3-8B-GGUF", "name": "my-qwen3", "use_llm": true}

    Response::

        {"profile": {...portable .hal0profile.json envelope...},
         "warnings": ["..."], "sources": ["registry:...", "huggingface:...", "llm:hal0/utility"]}

    Compute-only — never writes to the catalog. ``profile`` is the exact
    envelope shape :func:`export_profile` produces, so it can be handed
    straight to ``POST /api/profiles`` (after unwrapping ``.profile``) or
    ``POST /api/profiles/import`` (as ``envelope``) unmodified.

    ``use_llm: true`` asks the platform's ``hal0/utility`` slot to summarize
    the model card into the profile's ``intent`` headline. Any failure (slot
    unavailable, transport error, timeout, empty completion) degrades to the
    heuristic intent with the reason recorded in ``warnings`` — it never
    fails the request. Device/profile fit is derived for THIS host from the
    cached ``hardware.json`` (never a live re-probe).

    Raises:
        422: neither or both of model_id/hf_repo were given.
        404 model.not_found: model_id is not registered.
        400 hf.bad_request: hf_repo is not a valid org/name coordinate.
        404 hf.repo_not_found: HF repo does not exist.
        502 hf.unreachable / hf.upstream_error: HuggingFace lookup failed.
    """
    llm_ctx: LlmCallContext | None = None
    if body.use_llm:
        base_url = getattr(request.app.state, "self_api_base_url", "http://127.0.0.1:8080")
        # O17-style self-call auth: forward the caller's inbound bearer when
        # present, else the box service identity at the least-privilege
        # CLIENT tier — mirrors hal0.brain.chat._self_call_headers.
        inbound = request.headers.get("Authorization", "") or ""
        headers = (
            {"Authorization": inbound} if inbound.strip() else service_auth_headers(prefer="client")
        )
        llm_ctx = LlmCallContext(base_url=base_url, headers=headers)

    result = await generate_draft_profile(
        model_id=body.model_id,
        hf_repo=body.hf_repo,
        name=body.name,
        use_llm=body.use_llm,
        llm=llm_ctx,
        exported_at=datetime.now(UTC).isoformat(),
    )
    return {
        "profile": result.profile,
        "warnings": list(result.warnings),
        "sources": list(result.sources),
    }


@router.post("/import")
async def import_profile_route(request: Request, response: Response) -> dict[str, Any]:
    """Import a profile from an uploaded ``.hal0profile.json`` envelope.

    Body::

        { "envelope": {...}, "name": "name", "dry_run": false, "force": false }

    ``dry_run`` validates the envelope + checksum and reports whether the target
    name already exists, without creating anything (200). A commit creates the
    profile under ``name`` and returns the resolved profile item (201 — the
    same "resource created" status POST /api/profiles uses).

    The commit path VERIFIES the envelope checksum (#1416). It previously did
    not: ``verify_checksum`` was referenced only inside the ``dry_run`` branch,
    so the integrity stamp was decorative on the one path that writes — a
    hand-edited or transport-corrupted envelope created a profile with a 200 and
    no warning. A profile is a launch-flag template that gets stamped into a
    slot's argv, so importing one unverified is the wrong
    default. ``force: true`` is the escape hatch for a deliberately hand-edited
    envelope — it waives the checksum ONLY; the §5/§21.7 flag screen still
    applies (``ProfileCatalog.create``), so an envelope can never back-door a
    hardware or managed flag past the guards ``POST``/``PUT`` enforce.

    A commit verifies the envelope checksum as well (#1416) — ``dry_run`` used
    to be the only place it was checked, so a tampered file that failed the
    dry-run report still imported cleanly on the next call.

    A runtime this box does not have is STRIPPED, never a block: import stays
    portable, so a profile authored on a richer box still lands (flags and
    tune intact) — it just lands with no runtime pinned. Both the dry run
    and the commit response report it as ``runner_stripped``, alongside the
    envelope's own ``runner`` and the profile's ``runtime_family``.

    Raises:
        400 profiles.bad_envelope: not a valid hal0.profile envelope.
        400 profiles.envelope_schema_unsupported: a schema-1 envelope (re-export
            from hal0 1.5).
        400 profiles.checksum_mismatch: checksum does not cover the body (#1416).
        400 profiles.import_no_name: commit requested without a name.
        400 slot.hardware_flag_denied / slot.managed_arg_denied: the envelope's
            flags reach for slot- or authority-owned flags.
        409 profiles.exists: name already exists.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest(
            "request body must be valid JSON",
            code="request.invalid_json",
            details={"error": str(exc)},
        ) from exc
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object", code="request.not_an_object")

    envelope = body.get("envelope", body)
    dry_run = bool(body.get("dry_run", False))
    force = bool(body.get("force", False))
    name = body.get("name")

    if dry_run:
        env = parse_envelope(envelope)
        existing = {p.name for p in ProfileCatalog().list()}
        target = name or env.name
        collides = bool(target) and target in existing
        # Runtime facts the preview renders: the key the import will actually
        # STORE, whether importing has to drop it because this box has no such
        # runtime, and the family the profile resolves to. A dropped runtime
        # warns and proceeds — it never blocks.
        #
        # Reporting the kept key matters for a superseded alias: it folds to
        # its canonical entry rather than stripping, and the UI looks the
        # reported key up in this box's runtime registry to title and chip it.
        # The envelope's raw alias would miss that lookup and render "unknown"
        # for an import that lands the runtime perfectly well. When the key IS
        # stripped there is no canonical form to report, so the envelope's own
        # key is returned — which is exactly what the warning names.
        kept, runner_stripped = envelope_runner_status(env.profile)
        return {
            "dry_run": True,
            "valid": True,
            "checksum_ok": verify_checksum(envelope) if isinstance(envelope, dict) else False,
            "name": env.name or "",
            "schema_version": env.schema_version,
            "collides": collides,
            "runner": kept or env.profile.runner,
            "runner_stripped": runner_stripped,
            "runtime_family": runtime_family_of(env.name or "", env.profile),
        }

    if not name or not isinstance(name, str):
        raise BadRequest(
            "import commit requires a 'name'",
            code="profiles.import_no_name",
        )

    # #1416: verify on COMMIT, not just on the dry run. Parse FIRST so a
    # structurally wrong envelope keeps its own, more actionable
    # `profiles.bad_envelope` diagnostic — `verify_checksum` also returns False
    # for those, and reporting a checksum mismatch for `{"kind": "nope"}` would
    # point the operator at the wrong problem.
    env = parse_envelope(envelope)
    # A missing/empty checksum is not corruption (hand-authored envelopes
    # legitimately omit it) — only a checksum that is PRESENT and WRONG is
    # rejected. Mirrors import_profile()'s own check in profiles/portable.py.
    if isinstance(envelope, dict) and not force and env.checksum and not verify_checksum(envelope):
        raise BadRequest(
            "envelope checksum does not match its profile body — the file was "
            "hand-edited or corrupted in transit. Re-export it, or pass "
            "'force': true to import it anyway.",
            code="profiles.checksum_mismatch",
            details={"name": name, "envelope_name": env.name or ""},
        )
    if force:
        log.warning(
            "profile import bypassed the envelope checksum on operator request",
            extra={"event": "profile.import_checksum_forced", "profile": name},
        )

    async with record_action(
        request, category="profile", action="profile.import", target=name
    ) as rec:
        resolved = import_profile(envelope, name, ProfileCatalog(), force=force)
        rec.after = {"name": name}
    _kept, runner_stripped = envelope_runner_status(env.profile)
    if runner_stripped:
        log.warning(
            "profile import dropped a runtime this box does not have",
            extra={
                "event": "profile.import_runner_stripped",
                "profile": name,
                "runner": env.profile.runner,
            },
        )
    response.status_code = 201
    return {"dry_run": False, "profile": resolved.to_dict(), "runner_stripped": runner_stripped}


@router.get("/{name}")
def get_profile(name: str) -> dict[str, Any]:
    """Resolve a single profile by name.

    Returns the profile item (same shape as list).

    Raises:
        404 profiles.not_found: no such profile.
    """
    return ProfileCatalog().resolve(name).to_dict()


@router.post("/{name}/export")
def export_profile(name: str) -> dict[str, Any]:
    """Serialize a profile into its portable ``.hal0profile.json`` envelope.

    Embeds the profile template only (no secrets, no host paths) and stamps
    ``exported_at`` + a content checksum.

    Raises:
        404 profiles.not_found: no such profile.
    """
    resolved = ProfileCatalog().resolve(name)
    cfg = ProfileConfig(
        flags=resolved.flags,
        ctx_size=resolved.ctx_size,
        enable_thinking=resolved.enable_thinking,
        vision=resolved.vision,  # type: ignore[arg-type]  # ResolvedProfile carries the validated literal
        speculation=resolved.speculation,  # type: ignore[arg-type]
        reasoning_effort=resolved.reasoning_effort,  # type: ignore[arg-type]
        reasoning_budget=resolved.reasoning_budget,
        # The envelope is the import preview's only source of runtime truth,
        # so the export has to actually carry `runner` — it was omitted here
        # while every other ProfileConfig field was copied across.
        runner=resolved.runner,
        cloned_from=resolved.cloned_from,
        intent=resolved.intent,
    )
    return export_envelope(name, cfg, exported_at=datetime.now(UTC).isoformat())


@router.put("/{name}")
async def update_profile(name: str, body: ProfileUpdateBody, request: Request) -> dict[str, Any]:
    """Update an existing custom profile (shallow merge).

    Returns the updated profile item.

    Every field omitted keeps its stored value. ``ctx_size`` and
    ``enable_thinking`` take an explicit ``null`` to clear the stored value.
    ``runner`` additionally takes ``""`` as an explicit clear (#2186): the stored
    runtime is unpinned back to Auto. null is "leave alone" for ``runner``, so
    without the sentinel the drawer's Auto option silently no-op'd.

    Raises:
        409 profiles.seed_immutable: name is a seed profile.
        404 profiles.not_found: custom profile not found.
        422: pydantic validation failure (vision not auto|off, speculation not auto|off|ngram, a retired
            ``mtp`` key: ``profiles.field_renamed``).
        400 profiles.ctx_size_out_of_range: ctx_size outside 128..16777216.
        422 profiles.unknown_runner: `runner` CHANGES to a key this box's
            registry does not carry. Re-sending the profile's own stored key is
            grandfathered (#2183) so a runtime that left the registry never
            makes the profile un-editable.
        400 slot.hardware_flag_denied / slot.managed_arg_denied /
            profiles.model_fact_flag_denied / slot.reasoning_format_none_denied:
            the flags carry a flag a profile may not hold.
    """
    catalog = ProfileCatalog()
    before = None
    existing = next((p for p in catalog.list() if p.name == name), None)
    if existing is not None:
        before = existing.to_dict()
    sent = body.model_fields_set
    ctx_size = body.ctx_size if "ctx_size" in sent else UNSET
    enable_thinking = body.enable_thinking if "enable_thinking" in sent else UNSET
    reasoning_budget = body.reasoning_budget if "reasoning_budget" in sent else UNSET
    if ctx_size is not UNSET:
        _check_ctx_size(ctx_size)
    screen_profile_flags(body.flags)
    async with record_action(
        request,
        category="profile",
        action="profile.update",
        target=name,
        before=before,
    ) as rec:
        profile = catalog.update(
            name,
            ProfilePatch(
                flags=body.flags,
                ctx_size=ctx_size,
                enable_thinking=enable_thinking,
                vision=body.vision,
                reasoning_effort=body.reasoning_effort,
                reasoning_budget=reasoning_budget,
                speculation=body.speculation,
                runner=body.runner,
                intent=body.intent,
            ),
        )
        rec.after = profile.to_dict()
    return profile.to_dict()


@router.delete("/{name}", status_code=204)
async def delete_profile(name: str, request: Request) -> None:
    """Delete a custom profile.

    Raises:
        409 profiles.seed_immutable: name is a seed profile.
        404 profiles.not_found: custom profile not found.
        409 profiles.in_use: one or more slots reference this profile.
        409 profiles.slot_unreadable: a slot file cannot be parsed, so whether
            it references this profile is unknown (fail closed).
    """
    async with record_action(request, category="profile", action="profile.delete", target=name):
        ProfileCatalog().delete(name)


__all__ = ["router"]
