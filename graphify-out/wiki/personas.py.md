# personas.py

> 26 nodes · cohesion 0.13

## Key Concepts

- **personas.py** (12 connections) — `src/hal0/api/agents/personas.py`
- **update_agent_persona()** (10 connections) — `src/hal0/api/agents/personas.py`
- **activate_agent_persona()** (8 connections) — `src/hal0/api/agents/personas.py`
- **get_agent_persona()** (8 connections) — `src/hal0/api/agents/personas.py`
- **_resolve_agent()** (8 connections) — `src/hal0/api/agents/personas.py`
- **_persona_detail()** (6 connections) — `src/hal0/api/agents/personas.py`
- **Any** (6 connections)
- **_safe_error_message()** (6 connections) — `src/hal0/api/agents/personas.py`
- **list_agent_personas()** (5 connections) — `src/hal0/api/agents/personas.py`
- **_persona_summary()** (5 connections) — `src/hal0/api/agents/personas.py`
- **PersonaUpdateBody** (4 connections) — `src/hal0/api/agents/personas.py`
- **PersonaApprovalUpdate** (3 connections) — `src/hal0/api/agents/personas.py`
- **BaseModel** (2 connections)
- **Exception** (1 connections)
- **Path** (1 connections)
- **Persona endpoints for the bundled agents surface (v0.3 PR-4).  Thin FastAPI wrap** (1 connections) — `src/hal0/api/agents/personas.py`
- **Strip absolute filesystem paths out of a persona-error message.      :class:`hal** (1 connections) — `src/hal0/api/agents/personas.py`
- **List every persona registered for ``agent_id``.      Returns ``{"agent_id", "act** (1 connections) — `src/hal0/api/agents/personas.py`
- **Return parsed persona + raw TOML body for one persona.      404 if the agent id** (1 connections) — `src/hal0/api/agents/personas.py`
- **Partial ``[persona.approval]`` patch. Omitted fields are unchanged.** (1 connections) — `src/hal0/api/agents/personas.py`
- **Mutable persona fields. Mirrors the GET detail schema minus the     server-deriv** (1 connections) — `src/hal0/api/agents/personas.py`
- **Update the mutable fields of one persona, persisting to its TOML.      Backs the** (1 connections) — `src/hal0/api/agents/personas.py`
- **Swap the active persona for ``agent_id`` to ``persona_id``.      Body shape ``{"** (1 connections) — `src/hal0/api/agents/personas.py`
- **Map an agent id onto its personas store root.      Raises :class:`NotFound` with** (1 connections) — `src/hal0/api/agents/personas.py`
- **Compact row shape used by the list endpoint.      Matches the dashboard persona** (1 connections) — `src/hal0/api/agents/personas.py`
- *... and 1 more nodes in this community*

## Relationships

- [BoardStore](BoardStore.md) (4 shared connections)
- [errors.py](errors.py.md) (3 shared connections)
- [BadRequest](BadRequest.md) (3 shared connections)
- [Persona](Persona.md) (2 shared connections)

## Source Files

- `src/hal0/api/agents/personas.py`

## Audit Trail

- EXTRACTED: 87 (91%)
- INFERRED: 9 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*