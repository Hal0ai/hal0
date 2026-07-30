"""The slot activation predicate — one owner for "is this slot live config?".

#1369 removed ``SlotConfig.enabled``: a slot is activated by **binding a
model**, never by a separate boolean. Boot autostart is the Quadlet
``[Install] WantedBy=hal0.target`` stanza, which only exists because
:meth:`hal0.slots.manager.SlotManager.load` refuses to write a unit for a
model-less slot — so ``[model].default`` was always the real signal, and
every routability check that consulted ``enabled`` was immediately followed
by a model-presence gate anyway.

Keeping that predicate in one place stops the ~8 call sites (routing, the
/v1 listing helpers, the slot-view aggregator, the NPU exclusivity guard,
the dispatcher's NPU swap tracker) from each re-deriving
``(cfg.get("model") or {}).get("default")`` with subtly different
None/whitespace handling.

All helpers take the **raw TOML dict** shape, which is what every call site
holds; pass ``SlotConfig.model_dump()`` for a typed config.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "NPU_MODALITY_KEY",
    "claims_npu_anchor",
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


def npu_modality_active(configs: list[dict[str, Any]], slot_type: str) -> bool:
    """True when the NPU anchor is live AND ``slot_type``'s modality is on.

    The one gate for "should an NPU request of this type route to FLM". Reads
    the ANCHOR's ``[npu]`` table rather than the child shadow's own config,
    because a shadow's placeholder model makes it look permanently activated
    (see :data:`NPU_MODALITY_KEY`). ``chat`` defaults ON when the key is
    absent, matching ``NpuConfig.chat``; ``asr``/``embed`` default OFF.
    """
    key = NPU_MODALITY_KEY.get(slot_type)
    if key is None:
        return False
    anchor = npu_anchor_config(configs)
    if anchor is None or not is_activated(anchor):
        return False
    table = anchor.get("npu")
    if not isinstance(table, dict):
        table = (
            (anchor.get("extra") or {}).get("npu")
            if isinstance(anchor.get("extra"), dict)
            else None
        )
    if not isinstance(table, dict):
        table = {}
    if key == "chat":
        return table.get("chat", True) is not False
    return bool(table.get(key))
