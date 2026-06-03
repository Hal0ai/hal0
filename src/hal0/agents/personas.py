"""Hermes persona store + activation helpers (PR-3, v0.3).

Personas are hal0's user-facing concept layered on top of Hermes's
``system_prompt_prelude`` + tool gating + memory namespacing. Each
persona is a TOML file at ``/var/lib/hal0/.hermes/personas/<id>.toml``
plus a single-line pointer at ``personas/active.txt`` naming the active
one. The provisioner reads the active persona during Phase 7 (system
prompt injection) and Phase 8 (seed) so the rendered config.yaml
carries the right prompt + MCP usage block.

Activation hot-reload (PR-3 scope): write ``active.txt`` atomically;
best-effort POST ``reload.env`` to a running hermes JSON-RPC endpoint
when reachable. If hermes isn't running, the next service start picks
up the new active persona via the provision render path. PR-4 wires
the API endpoint that calls :func:`activate`.

See ``docs/agents/hermes/CONFIG.md`` for the full TOML schema + write
ownership semantics.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from hal0.agents.budget import Budget, parse_budget

log = structlog.get_logger(__name__)

# Canonical on-disk store. State-dir, not config-dir, so a fresh install
# can seed without manual ``/etc/hal0/...`` placement; operators wanting
# to back up customised personas should snapshot this tree alongside
# ``/var/lib/hal0/state``.
PERSONAS_ROOT = Path("/var/lib/hal0/.hermes/personas")
ACTIVE_POINTER = "active.txt"

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
    preferred_model: str = ""
    # Phase 0 OpenRouter prereq: per-persona spending caps. Empty Budget
    # means "no caps configured" — the round-trip preserves explicit
    # zeros (which translate to "block every paid request"). See
    # :mod:`hal0.agents.budget` for the dataclass shape + semantics.
    budget: Budget = field(default_factory=Budget)

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
        budget_raw = persona.get("budget")

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

        try:
            budget = parse_budget(budget_raw)
        except ValueError as exc:
            raise PersonaError(str(exc)) from exc

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
            budget=budget,
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
                # Always emit the budget table — round-trip preserves
                # operator-set caps + the explicit hard_cap toggle. An
                # empty budget renders as just ``hard_cap = true`` so
                # the seed persona file still documents the knob.
                "budget": self.budget.to_dict(),
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
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / f"{persona.id}.toml"
    body = tomli_w.dumps(persona.to_dict())
    tmp = target.with_suffix(".toml.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, target)
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
    target_root.mkdir(parents=True, exist_ok=True)
    persona_path = target_root / f"{persona_id}.toml"
    if not persona_path.exists():
        raise FileNotFoundError(f"can't activate {persona_id!r}: {persona_path} doesn't exist")
    pointer = target_root / ACTIVE_POINTER
    tmp = pointer.with_suffix(".tmp")
    tmp.write_text(persona_id + "\n", encoding="utf-8")
    os.replace(tmp, pointer)
    return pointer


# Hermes JSON-RPC endpoint used by the hot-reload helper. Defaults to a
# port that hal0's systemd unit binds; overridable for tests + alt
# deployments via :func:`hermes_reload`'s ``url`` arg.
DEFAULT_HERMES_RELOAD_URL = "http://127.0.0.1:8765/api/ws"


def hermes_reload(
    *,
    url: str = DEFAULT_HERMES_RELOAD_URL,
    method: str = "reload.env",
    timeout: float = 2.0,
) -> tuple[bool, str | None]:
    """Best-effort hot-reload nudge to a running Hermes process.

    Sends a JSON-RPC frame with ``method`` (defaults to ``reload.env``,
    which upstream documents as re-reading ``$HERMES_HOME/.env`` + a
    subset of config knobs without restarting the agent loop). PR-4
    will swap this for the proper ``session.compress`` once the
    upstream WS proxy lands; for now this is the only persona-swap
    nudge available without restarting the service.

    Returns ``(ok, error)`` so the API + CLI can decide how loud to be:
    a failed nudge is non-fatal — the persona file is on disk and the
    next service start (or next ``--repair``) picks it up. We DON'T
    raise here; the caller does.
    """
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}).encode(
        "utf-8"
    )
    req = Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:  # nosec B310 — localhost JSON-RPC
            resp.read()
    except (URLError, HTTPError, OSError, TimeoutError) as exc:
        return (False, str(exc))
    return (True, None)


def activate(
    persona_id: str,
    *,
    root: Path | None = None,
    reload_url: str | None = None,
) -> dict[str, Any]:
    """End-to-end persona switch: write active.txt + nudge hermes.

    This is the function PR-4's ``POST /api/agents/{id}/personas/<pid>/
    activate`` endpoint will call. The dashboard chat surface (PR-10)
    can ignore the reload outcome for v0.3 — the operator-visible
    effect is "next message uses the new prompt", which is achieved by
    the file write alone (any new session start re-reads active.txt
    during provision render).

    Returns a status payload the API can return verbatim.
    """
    set_active(persona_id, root=root)
    persona = load_persona(persona_id, root=root)
    url = reload_url or DEFAULT_HERMES_RELOAD_URL
    ok, error = hermes_reload(url=url)
    return {
        "persona_id": persona.id,
        "display_name": persona.display_name,
        "active_path": str(_personas_root(root) / ACTIVE_POINTER),
        "hot_reload": {"ok": ok, "error": error, "url": url},
    }


# ── Seed personas (Phase 8) ─────────────────────────────────────────────────
#
# The two personas seeded at provision time. Per master-plan §6 the
# user picked ``hermes`` (default) + ``coder`` — they share the hal0 MCP
# tooling but differ on tone, tools-allowed, and memory namespace so
# context from coding sessions doesn't bleed into general chat.


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


def seed_default_personas(
    agent_id: str = "hermes-agent",
    *,
    root: Path | None = None,
    overwrite: bool = False,
) -> list[Persona]:
    """Idempotently seed the ``hermes`` + ``coder`` personas + active pointer.

    On first install both files are written and ``active.txt`` is set to
    ``hermes``. On re-run (``overwrite=False``) existing files are left
    alone so operator edits survive — only missing personas get written.
    With ``overwrite=True`` the seeds are forcibly re-written; used by
    ``--repair`` to recover from a corrupted operator edit.
    """
    seeds = [_seed_hermes(agent_id), _seed_coder(agent_id)]
    written: list[Persona] = []
    for persona in seeds:
        path = _personas_root(root) / f"{persona.id}.toml"
        if path.exists() and not overwrite:
            continue
        save_persona(persona, root=root)
        written.append(persona)
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

    Hermes treats ``system_prompt_prelude`` as a single string, so this
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
            f"Your persistent memory namespace is {persona.memory_namespace}. "
            "Use hal0-memory read/search before asking the operator to repeat "
            "themselves; use hal0-memory write to persist durable facts."
        )
    return "\n".join(lines).rstrip() + "\n"
