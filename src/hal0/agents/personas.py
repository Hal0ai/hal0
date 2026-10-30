"""Hermes persona store + activation helpers (PR-3, v0.3).

Personas are hal0's user-facing concept layered on top of Hermes's
``agent.system_prompt`` overlay + tool gating + memory namespacing. Each
persona is a TOML file at ``/var/lib/hal0/.hermes/personas/<id>.toml``
plus a single-line pointer at ``personas/active.txt`` naming the active
one. The provisioner reads the active persona during Phase 7 (system
prompt injection) and Phase 8 (seed) so the rendered config.yaml
carries the right prompt + MCP usage block.

Activation: write ``active.txt`` atomically (:func:`activate`). Making that
live is :func:`hal0.agents.persona_live.refresh_persona_prompt`, which
re-renders the prompt into ``config.yaml`` and restarts Hermes (#2850).

``tools_allowed`` and ``preferred_model`` are steward-only: only hal0-brain
(``hal0.brain.chat``) enforces them. Hermes itself ignores both, so editing
them on any other persona changes nothing.

See ``docs/agents/hermes/CONFIG.md`` for the full TOML schema + write
ownership semantics.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from hal0.security import nofollow

log = structlog.get_logger(__name__)

# Canonical on-disk store. State-dir, not config-dir, so a fresh install
# can seed without manual ``/etc/hal0/...`` placement; operators wanting
# to back up customised personas should snapshot this tree alongside
# ``/var/lib/hal0/state``.
PERSONAS_ROOT = Path("/var/lib/hal0/.hermes/personas")
ACTIVE_POINTER = "active.txt"

#: The dashboard steward persona, the only one that enforces ``tools_allowed``
#: and ``preferred_model`` (``hal0.brain.chat.BRAIN_PERSONA_ID``).
STEWARD_PERSONA_ID = "hal0-brain"

# The dashboard steward is a first-class Hermes *profile* agent-id, not just a
# persona overlay: its memory is shared hal0 memory tagged with its own agent id
# (``hermes__hal0-brain``) and the installer registers it as an agent identity,
# the same as the default ``hermes`` agent. ``memory_namespace`` below stays
# ``private:<id>`` only as the source of that agent id. The persona
# id stays ``hal0-brain`` (board_chat + the UI key off it); this is the *memory*
# identity. See ``hermes_provision._phase_brain_profile_seed``.
BRAIN_PROFILE_AGENT_ID = "hermes__hal0-brain"

# Default tool patterns. Conservative — the persona file can opt into
# anything broader via the ``allowed`` glob or escalate by adding to
# ``auto_approve``. The dashboard reads these so the operator sees what
# the agent can do without an approval pip.
DEFAULT_AUTO_APPROVE = ("memory.read.*", "search.*", "slot.read.*")
DEFAULT_REQUIRE_APPROVAL = ("files.*", "shell.*", "admin.*")


class PersonaError(ValueError):
    """Raised when a persona TOML is malformed or fails validation."""


@dataclass
class PersonaApproval:
    """Subset of the ``[persona.approval]`` TOML table.

    ``default_policy`` is one of ``ask``/``auto-approve``/``never``;
    ``auto_approve`` and ``require_approval`` are glob patterns the
    dashboard's inline approval card matches against incoming tool
    calls.
    """

    default_policy: str = "ask"
    auto_approve: tuple[str, ...] = field(default_factory=lambda: DEFAULT_AUTO_APPROVE)
    require_approval: tuple[str, ...] = field(default_factory=lambda: DEFAULT_REQUIRE_APPROVAL)


@dataclass
class Persona:
    """In-memory shape of one persona TOML.

    Field names mirror the TOML schema 1:1 so tests can compare dataclass
    output to the parsed file without translation. Use :meth:`load` /
    :meth:`save` to round-trip; :meth:`from_dict` builds from a parsed
    TOML body (the activation API path calls this on a request body).
    """

    id: str
    display_name: str
    summary: str = ""
    system_prompt: str = ""
    tools_allowed: tuple[str, ...] = ("*",)
    memory_namespace: str = ""
    approval: PersonaApproval = field(default_factory=PersonaApproval)
    preferred_upstream: str = "hal0"
    # ``tools_allowed`` and ``preferred_model`` are steward-only: enforced for
    # hal0-brain (``hal0.brain.chat``), ignored by Hermes for every other persona.
    preferred_model: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Persona:
        """Build from a parsed TOML dict; raises :class:`PersonaError` on
        anything malformed.

        Accepts the shape documented in ``docs/agents/hermes/CONFIG.md``:
        a top-level ``[persona]`` table holds the identity bits, with
        ``[persona.prompt]``/``[persona.tools]``/``[persona.memory]``/
        ``[persona.approval]``/``[persona.model]`` sub-tables for the
        rest. We tolerate missing sub-tables (with sensible defaults) so
        operators can write minimal personas and not have to enumerate
        every knob.
        """
        if not isinstance(data, dict):
            raise PersonaError("persona TOML must be a table at root")
        persona = data.get("persona")
        if not isinstance(persona, dict):
            raise PersonaError("missing [persona] table")
        pid = persona.get("id")
        if not isinstance(pid, str) or not pid.strip():
            raise PersonaError("[persona].id is required and must be a non-empty string")

        prompt = persona.get("prompt") or {}
        tools = persona.get("tools") or {}
        memory = persona.get("memory") or {}
        approval_raw = persona.get("approval") or {}
        model = persona.get("model") or {}

        allowed = tools.get("allowed", ["*"])
        if isinstance(allowed, str):
            allowed = [allowed]
        if not isinstance(allowed, list) or not all(isinstance(s, str) for s in allowed):
            raise PersonaError("[persona.tools].allowed must be a list of strings")

        auto_approve = approval_raw.get("auto_approve", list(DEFAULT_AUTO_APPROVE))
        require_approval = approval_raw.get("require_approval", list(DEFAULT_REQUIRE_APPROVAL))
        if not isinstance(auto_approve, list) or not all(isinstance(s, str) for s in auto_approve):
            raise PersonaError("[persona.approval].auto_approve must be a list of strings")
        if not isinstance(require_approval, list) or not all(
            isinstance(s, str) for s in require_approval
        ):
            raise PersonaError("[persona.approval].require_approval must be a list of strings")

        default_policy = approval_raw.get("default_policy", "ask")
        if default_policy not in {"ask", "auto-approve", "never"}:
            raise PersonaError(
                f"[persona.approval].default_policy must be one of "
                f"ask/auto-approve/never; got {default_policy!r}"
            )

        return cls(
            id=pid.strip(),
            display_name=str(persona.get("display_name") or pid).strip(),
            summary=str(persona.get("summary") or "").strip(),
            system_prompt=str(prompt.get("system") or "").strip(),
            tools_allowed=tuple(allowed),
            memory_namespace=str(memory.get("namespace") or "").strip(),
            approval=PersonaApproval(
                default_policy=default_policy,
                auto_approve=tuple(auto_approve),
                require_approval=tuple(require_approval),
            ),
            preferred_upstream=str(model.get("preferred_upstream") or "hal0").strip(),
            preferred_model=str(model.get("preferred_model") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise back to the TOML shape :meth:`from_dict` accepts.

        Round-trip is important for the activation API: PR-4 will POST a
        TOML body, we validate via :meth:`from_dict`, and we write back
        via :func:`save_persona` which calls this method.
        """
        return {
            "persona": {
                "id": self.id,
                "display_name": self.display_name,
                "summary": self.summary,
                "prompt": {"system": self.system_prompt},
                "tools": {"allowed": list(self.tools_allowed)},
                "memory": {"namespace": self.memory_namespace},
                "approval": {
                    "default_policy": self.approval.default_policy,
                    "auto_approve": list(self.approval.auto_approve),
                    "require_approval": list(self.approval.require_approval),
                },
                "model": {
                    "preferred_upstream": self.preferred_upstream,
                    "preferred_model": self.preferred_model,
                },
            }
        }


def _personas_root(root: Path | None = None) -> Path:
    return root if root is not None else PERSONAS_ROOT


def load_persona(persona_id: str, *, root: Path | None = None) -> Persona:
    """Read + validate ``<root>/<persona_id>.toml``.

    Raises :class:`FileNotFoundError` when the file is missing,
    :class:`PersonaError` when TOML is malformed or fails validation.
    """
    import tomllib

    target = _personas_root(root) / f"{persona_id}.toml"
    if not target.exists():
        raise FileNotFoundError(f"persona {persona_id!r} not found at {target}")
    try:
        body = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise PersonaError(f"{target}: malformed TOML — {exc}") from exc
    persona = Persona.from_dict(body)
    if persona.id != persona_id:
        raise PersonaError(
            f"{target}: [persona].id={persona.id!r} doesn't match filename {persona_id!r}"
        )
    return persona


def save_persona(persona: Persona, *, root: Path | None = None) -> Path:
    """Write a persona to ``<root>/<persona.id>.toml`` atomically.

    Uses :mod:`tomli_w` for serialisation (pinned in pyproject). The
    write is tmp+rename so a concurrent reader never sees a partial
    file.
    """
    import tomli_w

    target_root = _personas_root(root)
    target = target_root / f"{persona.id}.toml"
    # By descriptor, random temp name (#2975): ``--reset-personas`` runs this
    # as root inside the hal0-owned Hermes home.
    nofollow.write_file(target, tomli_w.dumps(persona.to_dict()))
    return target


def list_personas(*, root: Path | None = None) -> list[Persona]:
    """Enumerate every persona under ``<root>/*.toml``.

    Sorted by id for stable CLI / API output. Skips files that fail to
    parse with a structured log line so a single bad persona doesn't
    hide the others; callers that need strict semantics should call
    :func:`load_persona` per-id.
    """
    target_root = _personas_root(root)
    if not target_root.exists():
        return []
    out: list[Persona] = []
    for path in sorted(target_root.glob("*.toml")):
        try:
            out.append(load_persona(path.stem, root=target_root))
        except (PersonaError, FileNotFoundError) as exc:
            log.warning("personas.skip_malformed", path=str(path), error=str(exc))
    return out


def get_active(*, root: Path | None = None) -> str | None:
    """Read the active persona id from ``<root>/active.txt``.

    Returns ``None`` if the file is missing — callers should treat that
    as "no persona seeded yet" and either skip the prompt injection or
    fall back to the first persona returned by :func:`list_personas`.
    """
    pointer = _personas_root(root) / ACTIVE_POINTER
    if not pointer.exists():
        return None
    try:
        return pointer.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def set_active(persona_id: str, *, root: Path | None = None) -> Path:
    """Atomically swap ``active.txt`` to ``persona_id``.

    Raises :class:`FileNotFoundError` when the named persona doesn't
    exist — refusing to point at a missing file prevents the
    provisioner from rendering an empty system prompt later.
    """
    target_root = _personas_root(root)
    os.close(nofollow.open_dir(target_root, create=True))
    persona_path = target_root / f"{persona_id}.toml"
    if not persona_path.exists():
        raise FileNotFoundError(f"can't activate {persona_id!r}: {persona_path} doesn't exist")
    pointer = target_root / ACTIVE_POINTER
    nofollow.write_file(pointer, persona_id + "\n")
    return pointer


def activate(persona_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """Point ``active.txt`` at ``persona_id`` and return the status payload.

    This only moves the pointer. Callers that must make the switch live
    follow it with :func:`hal0.agents.persona_live.refresh_persona_prompt`,
    which renders the prompt into Hermes's ``config.yaml`` and restarts the
    consumers; ``POST /api/agents/{id}/personas/<pid>/activate`` and
    ``hal0 agent personas activate`` both do (#2850).
    """
    set_active(persona_id, root=root)
    persona = load_persona(persona_id, root=root)
    return {
        "persona_id": persona.id,
        "display_name": persona.display_name,
        "active_path": str(_personas_root(root) / ACTIVE_POINTER),
    }


# ── Seed personas (Phase 8) ─────────────────────────────────────────────────
#
# The personas seeded at provision time: ``hermes`` (default) and
# ``hal0-brain`` (the dashboard's agent-chat slide-out). They share the
# hal0 MCP tooling but differ on tone, tools-allowed, and memory namespace
# so context from one surface doesn't bleed into another. ``coder`` (per
# master-plan §6) was a third seed until it was retired; its canonical
# definition stays below so the seeder can recognise — and remove —
# pristine copies left on boxes seeded before the retirement.


def _seed_hermes(agent_id: str) -> Persona:
    return Persona(
        id="hermes",
        display_name="Hermes",
        summary="Default helpful assistant for the hal0 home AI box.",
        system_prompt=(
            "You are Hermes, the resident agent of a hal0 home AI box. You speak "
            "with the operator (a person who runs this box themselves) and you have "
            "access to the hal0 MCP tools listed at the end of this prompt. You "
            "remember context across sessions via hal0-memory. When the operator "
            "asks you to take an action that touches files, services, or external "
            "systems, request approval before doing so. Be terse and technical when "
            "the operator clearly wants details; otherwise be friendly and concise."
        ),
        tools_allowed=("*",),
        memory_namespace=f"private:{agent_id}",
        approval=PersonaApproval(
            default_policy="ask",
            auto_approve=("memory.read.*", "search.*", "slot.read.*", "hal0_admin.read.*"),
            require_approval=("files.*", "shell.*", "admin.*", "hal0_admin.write.*"),
        ),
        preferred_upstream="hal0",
        preferred_model="",
    )


def _seed_coder(agent_id: str) -> Persona:
    """RETIRED seed — kept only so :func:`seed_default_personas` can match
    (and delete) untouched ``coder.toml`` files written by older seeders.
    Never add this back to the seeds list."""
    return Persona(
        id="coder",
        display_name="Coder",
        summary="Software-focused persona — code reading, refactors, file:line citations.",
        system_prompt=(
            "You are the hal0 coder persona — a focused software collaborator running "
            "on the hal0 home AI box. Cite source as file:line whenever you discuss "
            "code. Prefer small, reversible changes; prefer reading before writing. "
            "You have hal0-admin for inspecting platform state and a kanban MCP "
            "(when registered) for task tracking. Use hal0-memory under a separate "
            "namespace so coding context doesn't pollute the operator's general chat."
        ),
        tools_allowed=("*",),
        # A separate private bank keeps coding context out of general chat. The
        # bank itself is created lazily by Hindsight on the coder persona's first
        # write — provisioning never pre-creates it (see _phase_namespace_register,
        # which only writes the agent identity card to the shared agents dataset).
        memory_namespace=f"private:{agent_id}-coder",
        approval=PersonaApproval(
            default_policy="ask",
            auto_approve=(
                "memory.read.*",
                "search.*",
                "slot.read.*",
                "files.read.*",
                "shell.read.*",
                "hal0_admin.read.*",
                "kanban.read.*",
            ),
            require_approval=(
                "files.write.*",
                "shell.write.*",
                "admin.*",
                "hal0_admin.write.*",
                "kanban.write.*",
            ),
        ),
        preferred_upstream="hal0",
        preferred_model="",
    )


def _seed_hal0_brain(agent_id: str) -> Persona:
    """The dashboard agent-chat profile — the platform steward.

    This is the persona the top-bar slide-out chat embodies (see
    ``hal0.api.routes.board_chat``): its own memory bank so platform-admin
    context never bleeds into the operator's general Hermes chat, a
    hal0-heavy system prompt, and the dedicated ``brain`` slot as the
    default model (``hal0/brain`` falls back to the ``agent`` slot via the
    resolver chain when the brain slot isn't loaded).
    """
    return Persona(
        id="hal0-brain",
        display_name="hal0 Brain",
        summary="Platform steward behind the dashboard agent chat — slots, models, benchmarks.",
        system_prompt=(
            "You are hal0-brain, the resident platform steward of this hal0 home "
            "AI box, embedded in the dashboard's agent chat. You administer the "
            "instance itself: inference slots (create, configure, load/unload/"
            "restart), the model library (find, download, set up models), "
            "benchmarks (prepare a slot and interpret hal0-bench runs), hardware "
            "headroom (RAM/VRAM/GTT, GPU/NPU), the Operator Board, and "
            "orchestration settings. Read state before mutating it and never "
            "guess slot names or task ids. Slot load/unload/restart are "
            "disruptive — only on explicit operator request. For multi-step work "
            "(new slot, model setup, benchmark run) lay out a short plan, then "
            "execute step by step. Keep replies short and technical. "
            "You use shared hal0 memory, tagged as you (hermes__hal0-brain): "
            "memory_add(text) saves a fact, "
            "memory_search(query) and memory_list() read saved facts back. "
            "Call memory_add before you say you have noted or will remember "
            "anything, and memory_search before answering what the operator "
            "told you earlier; without a tool result you remember nothing."
        ),
        tools_allowed=("*",),
        # Identity source for the steward's agent id (``hermes__hal0-brain``).
        # Its memory tools write the shared pool tagged as that agent; reads
        # still see its own earlier private notes. Registered as a first-class
        # profile identity by the installer (``_phase_brain_profile_seed``), so
        # ``hal0-brain`` is a real profile, not just a persona.
        memory_namespace=f"private:{BRAIN_PROFILE_AGENT_ID}",
        # Patterns are fnmatch globs over ADMIN TOOL NAMES (slot_create,
        # model_pull, …) — enforced on the sidebar chat via
        # hal0.mcp.admin.ToolPolicy, layered onto the server-side
        # read/write/gated classification. Conservative canonical: nothing
        # is loosened (gated tools still queue for operator approval);
        # require_approval redundantly pins the destructive floor
        # (POLICY_NO_LOOSEN) so the operator sees it in the TOML. To let
        # the Brain e.g. download models without per-pull approval, add
        # "model_pull" to auto_approve.
        approval=PersonaApproval(
            default_policy="ask",
            auto_approve=(
                "memory_search",
                "memory_list",
                "memory_add",
            ),
            require_approval=(
                "model_delete",
                "slot_delete",
                "stack_delete",
                "profile_delete",
                "config_write",
                "provider_credential_write",
            ),
        ),
        preferred_upstream="hal0",
        preferred_model="hal0/brain",
    )


def seed_default_personas(
    agent_id: str = "hermes",
    *,
    root: Path | None = None,
    overwrite: bool = False,
) -> list[Persona]:
    """Idempotently seed ``hermes`` + ``hal0-brain`` + active pointer.

    On first install the files are written and ``active.txt`` is set to
    ``hermes``. On re-run (``overwrite=False``) existing files are left
    alone so operator edits survive — only missing personas get written.
    With ``overwrite=True`` the seeds are forcibly re-written; used by
    ``--repair`` to recover from a corrupted operator edit.

    Retired seeds (``coder``) are swept on every run: an on-disk copy is
    deleted only when it still equals its canonical seed AND isn't the
    active persona — an operator edit or an active selection turns the
    file into operator data the seeder must not touch.
    """
    seeds = [_seed_hermes(agent_id), _seed_hal0_brain(agent_id)]
    written: list[Persona] = []
    for persona in seeds:
        path = _personas_root(root) / f"{persona.id}.toml"
        if path.exists() and not overwrite:
            continue
        save_persona(persona, root=root)
        written.append(persona)
    for retired in (_seed_coder(agent_id),):
        path = _personas_root(root) / f"{retired.id}.toml"
        if not path.exists() or get_active(root=root) == retired.id:
            continue
        try:
            existing = load_persona(retired.id, root=root)
        except (FileNotFoundError, PersonaError, OSError):
            continue  # malformed = operator territory; leave it alone
        if existing == retired:
            path.unlink()
    # active pointer: only set if missing or pointing at a now-missing
    # persona. Operator-chosen active values survive re-seeding.
    pointer = _personas_root(root) / ACTIVE_POINTER
    current = get_active(root=root)
    needs_pointer = (
        not pointer.exists()
        or current is None
        or not (_personas_root(root) / f"{current}.toml").exists()
    )
    if needs_pointer:
        set_active("hermes", root=root)
    return written


# ── System-prompt injection helper (Phase 7) ────────────────────────────────


def build_prompt_addendum(
    persona: Persona,
    *,
    mcp_servers: list[dict[str, Any]] | None = None,
) -> str:
    """Compose the hal0 MCP usage block appended to the persona's system prompt.

    The result is written to Hermes's ``agent.system_prompt`` (a single
    string, hermes_cli/personality.py:118-123 at ``d923a5eb``), so this
    is the canonical place to surface "you have these MCP tools" + the
    persona's approval policy. The dashboard's HermesChat sidecar
    (PR-10) and the persona TOML stay the only places ANY of this
    information lives — same source of truth for the operator, the API,
    and the agent loop.
    """
    servers = mcp_servers or []
    lines = [persona.system_prompt.rstrip()]
    if servers:
        lines.append("")
        lines.append("You have access to the following hal0 MCP tools:")
        for entry in servers:
            name = entry.get("name", "")
            hint = entry.get("usage_hint") or entry.get("description") or ""
            if hint:
                lines.append(f"- {name}: {hint}")
            else:
                lines.append(f"- {name}")
    lines.append("")
    lines.append(f"Approval policy (active persona '{persona.id}'):")
    if persona.approval.auto_approve:
        lines.append("- Auto-approved tool patterns: " + ", ".join(persona.approval.auto_approve))
    if persona.approval.require_approval:
        lines.append("- Require approval: " + ", ".join(persona.approval.require_approval))
    lines.append(f"- Default policy: {persona.approval.default_policy}")
    if persona.memory_namespace:
        lines.append("")
        lines.append(
            "Your memory is shared by default: what you write with hal0-memory is "
            "visible to the other agents and tagged as you. A single write is "
            "private only when you pass private=true to memory_add, or when your "
            "profile is configured to keep all your memory private; never name a "
            "private: dataset yourself. Use hal0-memory read/search before asking "
            "the operator to repeat themselves; use hal0-memory write to persist "
            "durable facts."
        )
    return "\n".join(lines).rstrip() + "\n"
