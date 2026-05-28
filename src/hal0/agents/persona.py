"""Persona + skill enums consumed by the dashboard's Agent surface.

The dashboard's PersonaEditModal (#226) renders a Tone select + an
Allowed-tools picker; the Skills tab (#227) renders a catalogue of
agent-callable skills. Both used to hardcode their option lists in
JSX. This module is the single server-side source of truth so the
catalogue can grow without UI patches.

The enum membership matches what Hermes ships with today (see
``hermes_templates/config.yaml.j2``) plus the OmniRouter capability
ladder the dashboard's #426 inbox already reflects. Adding a new
persona tone or skill capability lands here first.
"""

from __future__ import annotations

from typing import TypedDict


class PersonaTone(TypedDict):
    id: str
    label: str
    desc: str


class PersonaTool(TypedDict):
    id: str
    label: str
    cap: str


class AgentSkill(TypedDict):
    name: str
    cap: str
    policy: str
    src: str


# Persona tone presets shown in PersonaEditModal's Tone select.
# ``id`` is what persists into the persona record; ``label`` + ``desc``
# are display-only. Keep ``operator`` first — it's the safe default
# baked into the modal's useState seed.
PERSONA_TONES: tuple[PersonaTone, ...] = (
    {"id": "operator", "label": "operator", "desc": "terse + technical"},
    {"id": "code-focused", "label": "code-focused", "desc": "refactors, reviews"},
    {"id": "low-latency", "label": "low-latency", "desc": "NPU coresident"},
    {"id": "vision", "label": "vision-first", "desc": "image-aware"},
    {"id": "conversational", "label": "conversational", "desc": "slower, fuller"},
)


# Allowed-tools picker membership. Mirrors the OmniRouter-callable tool
# set Hermes wires by default (see ``config.yaml.j2`` skills.search).
# ``cap`` is the capability bucket the approval queue gates on.
PERSONA_TOOLS: tuple[PersonaTool, ...] = (
    {"id": "read_file", "label": "read_file", "cap": "fs-read"},
    {"id": "write_file", "label": "write_file", "cap": "fs-write"},
    {"id": "edit_file", "label": "edit_file", "cap": "fs-write"},
    {"id": "shell_exec", "label": "shell_exec", "cap": "shell-exec"},
    {"id": "generate_image", "label": "generate_image", "cap": "tool-call"},
    {"id": "transcribe_audio", "label": "transcribe_audio", "cap": "tool-call"},
    {"id": "text_to_speech", "label": "text_to_speech", "cap": "tool-call"},
    {"id": "embed_text", "label": "embed_text", "cap": "tool-call"},
)


# Skills catalogue rendered in the Agent > Skills tab. Static for v0.3
# (registry-backed dynamic catalog tracked in #227 follow-up). Policy
# strings line up with the approval queue's enum: ``always`` (gated),
# ``remember`` (gated once + cached), ``auto`` (no gate), ``deny``.
AGENT_SKILLS: tuple[AgentSkill, ...] = (
    {"name": "read_file", "cap": "fs-read", "policy": "remember", "src": "builtin"},
    {"name": "write_file", "cap": "fs-write", "policy": "always", "src": "builtin"},
    {"name": "edit_file", "cap": "fs-write", "policy": "always", "src": "builtin"},
    {"name": "list_dir", "cap": "fs-read", "policy": "remember", "src": "builtin"},
    {"name": "shell_exec", "cap": "shell-exec", "policy": "always", "src": "builtin"},
    {"name": "model_pull", "cap": "registry-write", "policy": "always", "src": "hal0-router"},
    {"name": "restart_slot", "cap": "slot-control", "policy": "always", "src": "hal0-router"},
    {"name": "generate_image", "cap": "tool-call", "policy": "auto", "src": "omnirouter"},
    {"name": "transcribe_audio", "cap": "tool-call", "policy": "auto", "src": "omnirouter"},
    {"name": "text_to_speech", "cap": "tool-call", "policy": "auto", "src": "omnirouter"},
    {"name": "embed_text", "cap": "tool-call", "policy": "auto", "src": "omnirouter"},
    {"name": "rerank_documents", "cap": "tool-call", "policy": "auto", "src": "omnirouter"},
)


__all__ = [
    "AGENT_SKILLS",
    "PERSONA_TONES",
    "PERSONA_TOOLS",
    "AgentSkill",
    "PersonaTone",
    "PersonaTool",
]
