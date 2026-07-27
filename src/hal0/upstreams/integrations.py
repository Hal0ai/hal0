"""Catalog of well-known external AI providers.

_CATALOG is the built-in provider template registry.  It is never mutated
at runtime; user configuration lives in /etc/hal0/upstreams.toml.

Auth styles:
    "bearer"          → Authorization: Bearer <key>              (most providers)
    "anthropic"       → x-api-key: <key>  +  anthropic-version  (Anthropic only)
    "google_query"    → ?key=<key> on the URL                    (Google AI Studio)
    "header"          → custom header_name: <key>                ("custom" only)
    "none"            → no auth header                           ("custom" only)

API keys are NEVER written to TOML.  They live in the slot env file managed
by hal0.config.env.write_env_atomic; the key's env var name is stored in
the TOML as auth_value_env.

Port target: haloai lib/integrations.py.
See PLAN.md §3 (module port plan — "provider catalog").
"""

from __future__ import annotations

from typing import Any

# ── Catalog ────────────────────────────────────────────────────────────────────
#
# Each entry is a dict that can be used to construct an Upstream from
# upstreams.registry.  Keys:
#   id             — unique catalog id (used as TOML catalog_id reference)
#   name           — display name
#   base_url       — default base URL (empty for "custom")
#   auth           — auth style (see docstring above)
#   auth_header_name — explicit header name (only used when auth == "header")
#   models_path    — path for model listing relative to base_url; "" = unsupported
#   default_models — curated list of model ids to seed UI suggestions
#   default_model  — single recommended model id (UI default-selection)
#   capabilities   — list[str] subset of {"chat", "embed", "rerank",
#                    "stt", "tts", "vision", "tools"}
#   docs_url       — link to API key / docs page
#   category       — "cloud" | "local" | "custom"
#   notes          — short description shown in the Providers UI

_CATALOG: dict[str, dict[str, Any]] = {
    # ── Cloud providers ─────────────────────────────────────────────────────────
    "openai": {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "auth": "bearer",
        "auth_header_name": "Authorization",
        "models_path": "/models",
        "default_models": ["gpt-4o", "gpt-4o-mini", "o1-mini"],
        "default_model": "gpt-4o-mini",
        "capabilities": ["chat", "embed", "vision", "tools", "tts", "stt"],
        "docs_url": "https://platform.openai.com/api-keys",
        "category": "cloud",
        "notes": "Official OpenAI API.",
    },
    "anthropic": {
        "id": "anthropic",
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "auth": "anthropic",
        "auth_header_name": "x-api-key",
        "models_path": "/models",
        "default_models": [
            "claude-opus-4-7-20251101",
            "claude-sonnet-4-6-20251101",
            "claude-haiku-4-5-20251001",
        ],
        "default_model": "claude-sonnet-4-6-20251101",
        "capabilities": ["chat", "vision", "tools"],
        "docs_url": "https://console.anthropic.com/settings/keys",
        "category": "cloud",
        "notes": (
            "Official Anthropic API. Native /v1/messages protocol. "
            "For OpenAI-shaped /v1/chat/completions with Claude models, "
            "route via a compatible proxy."
        ),
    },
    "openrouter": {
        "id": "openrouter",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "auth": "bearer",
        "auth_header_name": "Authorization",
        "models_path": "/models",
        "default_models": [
            "openai/gpt-4o",
            "anthropic/claude-opus-4-7",
            "meta-llama/llama-3.1-70b-instruct",
        ],
        "default_model": "openai/gpt-4o-mini",
        "capabilities": ["chat", "vision", "tools"],
        "docs_url": "https://openrouter.ai/keys",
        "category": "cloud",
        "notes": "OpenRouter aggregates 200+ models behind one OpenAI-compatible endpoint.",
    },
    "google_ai_studio": {
        "id": "google_ai_studio",
        "name": "Google AI Studio",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "auth": "google_query",
        "auth_header_name": "",
        "models_path": "/models",
        "default_models": ["gemini-2.0-flash", "gemini-1.5-pro"],
        "default_model": "gemini-2.0-flash",
        "capabilities": ["chat", "vision"],
        "docs_url": "https://aistudio.google.com/app/apikey",
        "category": "cloud",
        "notes": "Google Gemini via the OpenAI-compatible REST layer.",
    },
    # ── Local / self-hosted ──────────────────────────────────────────────────────
    "ollama": {
        "id": "ollama",
        "name": "Ollama",
        "base_url": "http://localhost:11434/v1",
        "auth": "none",
        "auth_header_name": "",
        "models_path": "/models",
        "default_models": [],
        "default_model": "",
        "capabilities": ["chat", "embed"],
        "docs_url": "https://github.com/ollama/ollama",
        "category": "local",
        "notes": "Local Ollama server. Leave key blank if running unauthenticated.",
    },
    # ── Custom (escape hatch) ────────────────────────────────────────────────────
    "custom": {
        "id": "custom",
        "name": "Custom OpenAI-compatible",
        "base_url": "",
        "auth": "bearer",
        "auth_header_name": "Authorization",
        "models_path": "/models",
        "default_models": [],
        "default_model": "",
        "capabilities": ["chat"],
        "docs_url": "",
        "category": "custom",
        "notes": "Any OpenAI-compatible endpoint.",
    },
}


_VALID_CAPABILITIES: frozenset[str] = frozenset(
    {"chat", "embed", "rerank", "stt", "tts", "vision", "tools"}
)


def get_catalog() -> dict[str, dict[str, Any]]:
    """Return all catalog entries (read-only copy)."""
    return {k: dict(v) for k, v in _CATALOG.items()}


def get_catalog_entry(catalog_id: str) -> dict[str, Any] | None:
    """Return a single catalog entry by id, or None if not found."""
    entry = _CATALOG.get(catalog_id)
    return dict(entry) if entry is not None else None


def list_catalog_ids() -> list[str]:
    """Return all known catalog ids."""
    return list(_CATALOG.keys())


def validate_catalog() -> list[str]:
    """Return a list of validation problems with the static catalog.

    Empty list means all entries are well-formed. Called from
    tests/upstreams/test_integrations.py to assert catalog hygiene at
    import time.
    """
    problems: list[str] = []
    seen_names: set[str] = set()
    for cid, entry in _CATALOG.items():
        if entry.get("id") != cid:
            problems.append(f"{cid}: id field {entry.get('id')!r} does not match key")
        name = entry.get("name", "")
        if not name:
            problems.append(f"{cid}: missing name")
        if name in seen_names:
            problems.append(f"{cid}: duplicate name {name!r}")
        seen_names.add(name)
        category = entry.get("category", "")
        if category not in {"cloud", "local", "custom"}:
            problems.append(f"{cid}: invalid category {category!r}")
        # base_url is required except for "custom" templates
        if category != "custom" and not entry.get("base_url"):
            problems.append(f"{cid}: missing base_url")
        auth = entry.get("auth", "")
        if auth not in {"bearer", "anthropic", "google_query", "header", "none"}:
            problems.append(f"{cid}: invalid auth style {auth!r}")
        caps = entry.get("capabilities", [])
        if not isinstance(caps, list):
            problems.append(f"{cid}: capabilities must be a list")
        else:
            for c in caps:
                if c not in _VALID_CAPABILITIES:
                    problems.append(f"{cid}: invalid capability {c!r}")
    return problems


__all__ = [
    "get_catalog",
    "get_catalog_entry",
    "list_catalog_ids",
    "validate_catalog",
]
