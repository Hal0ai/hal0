"""The slot activation predicate — one owner for "is this slot live config?".

#1369 removed ``SlotConfig.enabled``: a slot is activated by **binding a
model**, never by a separate boolean. Boot autostart used to ride the same
signal — the Quadlet ``[Install] WantedBy=hal0.target`` stanza existed
whenever :meth:`hal0.slots.manager.SlotManager.load` had a model to write a
unit for. Spec 2026-08-02 split the two: boot autostart is now the explicit,
independently-gated ``autoload`` field — this module owns
:func:`autoload_enabled`, the raw-dict predicate that mirrors
``SlotConfig._derive_autoload`` (explicit key wins; an absent key falls back
to the pre-field implicit signal, a bound model, so pre-field TOMLs keep their
boot behaviour). Activation itself is unchanged: ``[model].default`` is
still the real routability/loadability signal, and every check that used to
consult ``enabled`` was immediately followed by a model-presence gate
anyway.

Keeping the activation predicate in one place stops the ~8 call sites
(routing, the /v1 listing helpers, the slot-view aggregator, the NPU
exclusivity guard, the dispatcher's NPU swap tracker) from each re-deriving
``(cfg.get("model") or {}).get("default")`` with subtly different
None/whitespace handling.

All helpers take the **raw TOML dict** shape, which is what every call site
holds; pass ``SlotConfig.model_dump()`` for a typed config.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "NPU_MODALITY_KEY",
    "autoload_enabled",
    "claims_npu_anchor",
    "effective_npu_table",
    "is_activated",
    "npu_anchor_config",
    "npu_modality_active",
    "slot_model_id",
]

#: NPU slot type → the key in the ANCHOR's ``[npu]`` table that gates it.
#: The FLM trio is one process on one AMDXDNA column: the ``type=llm`` anchor
#: owns the hardware and its ``[npu]`` table is the operator's per-modality
#: switch. The ``transcription``/``embedding`` shadows are display + dispatch
#: records for that one process, so they are NOT independently activatable —
#: they always carry a placeholder ``[model].default`` (see
#: ``hal0.slots.npu.trio._TRIO_SHADOW_SPEC``) and therefore cannot express
#: "off" through model-presence the way a standalone slot can.
NPU_MODALITY_KEY: dict[str, str] = {
    "llm": "chat",
    "transcription": "asr",
    "embedding": "embed",
}


def slot_model_id(cfg: dict[str, Any] | None) -> str:
    """Return the slot's bound model id, or ``""`` when none is bound.

    Absorbs the raw-TOML shapes: a missing ``[model]`` table, a non-dict
    ``model`` value, a non-string ``default``, and surrounding whitespace all
    collapse to ``""``.
    """
    if not isinstance(cfg, dict):
        return ""
    section = cfg.get("model")
    if not isinstance(section, dict):
        return ""
    raw = section.get("default")
    return raw.strip() if isinstance(raw, str) else ""


def is_activated(cfg: dict[str, Any] | None) -> bool:
    """True when the slot has a model bound, i.e. it is live configuration.

    A false result is the "grey tile" state: the slot exists so the operator
    can see and configure it, but nothing routes to it and no unit is written.
    """
    return bool(slot_model_id(cfg))


def autoload_enabled(cfg: dict[str, Any] | None) -> bool:
    """Effective boot-start setting for a RAW slot TOML dict (spec 2026-08-02).

    Mirror of ``SlotConfig._derive_autoload`` for the raw-dict readers
    (unit render, slot_view lift): an explicit ``autoload`` key wins;
    an absent key falls back to the pre-field implicit signal — a bound
    model (:func:`is_activated`) — so pre-field TOMLs keep their boot
    behavior. Same raw-dict contract as :func:`hal0.slots.reaper.is_pinned`.
    """
    if not isinstance(cfg, dict):
        return False
    raw = cfg.get("autoload")
    if raw is not None:
        return bool(raw)
    return is_activated(cfg)


def claims_npu_anchor(cfg: dict[str, Any] | None) -> bool:
    """True when this slot claims the single AMDXDNA chat (NPU LLM) context.

    The exclusivity invariant (plan §5.3, ADR-0008 §5) admits exactly one
    such slot. The NPU trio's ``transcription`` / ``embedding`` shadows run
    coresident on the same column and are deliberately NOT anchors.
    """
    if not isinstance(cfg, dict):
        return False
    return cfg.get("device") == "npu" and cfg.get("type") == "llm" and is_activated(cfg)


def npu_anchor_config(configs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the NPU LLM anchor config from ``configs``, or ``None``.

    Prefers an anchor that actually claims the hardware (has a model bound);
    falls back to a model-less ``device=npu, type=llm`` slot so callers that
    only need the ``[npu]`` modality table (or the anchor's port/name) still
    find it. Exclusivity means there is at most one claiming anchor.
    """
    fallback: dict[str, Any] | None = None
    for cfg in configs:
        if not isinstance(cfg, dict):
            continue
        if cfg.get("device") != "npu" or cfg.get("type") != "llm":
            continue
        if is_activated(cfg):
            return cfg
        if fallback is None:
            fallback = cfg
    return fallback


def effective_npu_table(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """The slot's effective ``[npu]`` modality table, pre-``[npu]``-table
    configs included.

    ``providers/flm.py`` still honors the pre-container ``[defaults].load_asr``/
    ``load_embed`` keys at launch when a slot's TOML has no ``[npu]`` table at
    all — but nothing folded that fallback forward into the READ paths
    (``npu_modality_active`` here, and the ``entry["npu"]`` lift in
    ``slot_view``), so a pre-1.0 config with no ``[npu]`` table renders every
    NPU pill off even while ASR/embed are actually running (#1670).

    ``[npu]`` is PRIMARY whenever the table exists at all — an explicit
    ``asr = false`` must win even if a stale ``[defaults]`` pair says
    otherwise, same precedence ``flm.py`` itself uses. The pre-``[npu]``
    fallback is also scoped to ``device == "npu"`` slots only: a chat slot's
    unrelated ``[defaults]`` table (chat_template, etc.) must never be
    misread as NPU modality flags.
    """
    if not isinstance(cfg, dict):
        return {}
    table = cfg.get("npu")
    if not isinstance(table, dict):
        table = (cfg.get("extra") or {}).get("npu") if isinstance(cfg.get("extra"), dict) else None
    if isinstance(table, dict):
        return table
    if cfg.get("device") != "npu":
        return {}
    defaults = cfg.get("defaults")
    if not isinstance(defaults, dict) or (
        "load_asr" not in defaults and "load_embed" not in defaults
    ):
        return {}
    return {"asr": bool(defaults.get("load_asr")), "embed": bool(defaults.get("load_embed"))}


def npu_modality_active(configs: list[dict[str, Any]], slot_type: str) -> bool:
    """True when the NPU anchor is live AND ``slot_type``'s modality is on.

    The one gate for "should an NPU request of this type route to FLM". Reads
    the ANCHOR's ``[npu]`` table (falling back to the pre-``[npu]``-table
    ``[defaults].load_asr``/``load_embed`` keys, #1670 — see
    :func:`effective_npu_table`) rather than the child shadow's own config,
    because a shadow's placeholder model makes it look permanently activated
    (see :data:`NPU_MODALITY_KEY`).
    ``chat`` defaults ON when the key is absent, matching ``NpuConfig.chat``;
    ``asr``/``embed`` default OFF.
    """
    key = NPU_MODALITY_KEY.get(slot_type)
    if key is None:
        return False
    anchor = npu_anchor_config(configs)
    if anchor is None or not is_activated(anchor):
        return False
    table = effective_npu_table(anchor)
    if key == "chat":
        return table.get("chat", True) is not False
    return bool(table.get(key))
