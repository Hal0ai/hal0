"""Typed apply-plan registry for hal0 settings (issue #552).

Generalises the per-key immediate-vs-deferred taxonomy that
:mod:`hal0.api.routes.lemonade_admin` keeps for the Lemonade admin panel
to the *whole* settings surface. The UI needs to know what will happen
on save before the user commits — "live", "⟳ restart <service>", or "⚠
manual restart" — so it can render the right badge and gate the
confirm affordance.

Reuses, does not redefine, the Lemonade-side constants::

    from hal0.api.routes.lemonade_admin import IMMEDIATE_KEYS, DEFERRED_KEYS

so the Lemonade admin rows (issue #545) and the new apply plan stay
locked together. Mapping:

  * :data:`IMMEDIATE_KEYS`  → ``{"apply_class": "immediate",       "services": []}``
  * :data:`DEFERRED_KEYS`   → ``{"apply_class": "service-restart", "services": ["lemonade"]}``

The deferred keys don't apply until the next ``/v1/load`` OR a
``systemctl restart hal0-lemonade.service``; in the new taxonomy that
collapses to ``service-restart`` with ``lemonade`` as the affected
service, matching how a fresh install wires the unit.

The registry also covers the hal0 ``Hal0Config`` fields the operator
edits through Settings. Each key is annotated with the
``apply_class`` + ``services`` it affects; ``apply_plan()`` partitions
a set of touched keys into the three buckets the UI renders.

Apply-class semantics
---------------------

``immediate``
  The value is observed by the running service the next time it
  consults the config (e.g. on the next event, request, or refresh).
  No restart required.

``service-restart``
  The service must be bounced (``systemctl restart <name>``) — or
  re-loaded by the platform — to pick up the new value. The list of
  services is the *minimum* bounce set; hal0-api restart may be
  needed alongside, depending on what the value controls.

``manual-restart``
  The value can't be applied without a manual operator action beyond
  ``systemctl restart`` (port changes that would clash with the live
  listener, a schema bump that requires a migrator run, etc.). The
  confirm dialog MUST appear on save; the UI gates the save affordance
  on this.
"""

from __future__ import annotations

from typing import TypedDict

from hal0.api.routes.lemonade_admin import DEFERRED_KEYS, IMMEDIATE_KEYS

# Apply-class enum. Kept as plain string literals so JSON round-trips
# without an extra enum adapter and the type checker accepts the
# union at every registry site.
APPLY_CLASSES: tuple[str, ...] = ("immediate", "service-restart", "manual-restart")
SERVICE_LEMONADE: str = "lemonade"
SERVICE_HAL0_API: str = "hal0-api"

# ── Lemonade runtime-config keys (reused from lemonade_admin) ────────────────
#
# These mirror :data:`lemonade_admin.IMMEDIATE_KEYS` /
# :data:`lemonade_admin.DEFERRED_KEYS` *one-to-one*; the registry
# builder below iterates those sets so any future addition shows up
# here automatically. Do NOT enumerate the keys inline — that would
# let the two definitions drift.

_LEMONADE_IMMEDIATE_ENTRY: ApplyPlanEntry = {"apply_class": "immediate", "services": []}
_LEMONADE_DEFERRED_ENTRY: ApplyPlanEntry = {
    "apply_class": "service-restart",
    "services": [SERVICE_LEMONADE],
}

# ── hal0.toml Hal0Config fields ──────────────────────────────────────────────
#
# Mapping rules:
#   * ``[telemetry].{enabled,channel}``    → immediate (read on each save +
#                                            event bus emit; the channel
#                                            picker hot-reloads on next
#                                            check).
#   * ``[dispatcher].*``                    → immediate (dispatcher reads
#                                            these on each call).
#   * ``[slots].max_slots``                 → service-restart[hal0-api]
#                                            (SlotManager holds the count
#                                            in-memory; a new bound needs
#                                            a process bounce).
#   * ``[slots].port_range_*``              → manual-restart (existing slot
#                                            listeners would clash with the
#                                            new pool; require a clean
#                                            restart with the new range).
#   * ``[models].roots``                    → service-restart[hal0-api]
#                                            (auto-scan runs on startup).
#   * ``[models].auto_scan_on_start``       → immediate (the flag is read
#                                            at next scan; not yet
#                                            consulted, so safe).
#   * ``[models].file_extensions``          → service-restart[hal0-api]
#                                            (scanner iterates extensions
#                                            from the loaded config).
#   * ``[models].{store,pull_root}``        → service-restart[lemonade]
#                                            (extra_models_dir propagates
#                                            to lemond; the dedicated
#                                            /api/settings/models/store
#                                            endpoint handles the migrate
#                                            + restart plumbing, but the
#                                            generic PUT still tags the
#                                            class so the UI renders the
#                                            right badge).
#   * ``[memory.embedding].model``          → service-restart[hal0-api]
#                                            (Cognee pins the dimension
#                                            at index build; switching
#                                            silently corrupts the
#                                            LanceDB store — a process
#                                            bounce + re-embed is the
#                                            supported path).
#   * ``[memory.embedding].{rerank_*}``     → immediate (consumed on each
#                                            ``memory_search`` call).
#   * ``[memory.graph].{enabled,route,upstream}`` → immediate (the route
#                                            hot-reloads on next call;
#                                            ``upstream`` credentials
#                                            resolve lazily).
#   * ``[meta].schema_version``             → manual-restart (migrations
#                                            run on next hal0-api boot
#                                            after the value is bumped).
#
# Nested field names are written dot-notation (``slots.max_slots``)
# because the registry is keyed off the path the PUT body uses. The
# ``_KEY_TO_ENTRY`` projection below expands each dotted entry into
# per-leaf entries so callers can pass either form.

_HAL0_REGISTRY: dict[str, ApplyPlanEntry] = {
    # [telemetry]
    "telemetry.enabled": {"apply_class": "immediate", "services": []},
    "telemetry.channel": {"apply_class": "immediate", "services": []},
    # [dispatcher]
    "dispatcher.prefetch_timeout_s": {"apply_class": "immediate", "services": []},
    "dispatcher.prefetch_parallel_cap": {"apply_class": "immediate", "services": []},
    # [slots]
    "slots.max_slots": {"apply_class": "service-restart", "services": [SERVICE_HAL0_API]},
    "slots.port_range_start": {"apply_class": "manual-restart", "services": []},
    "slots.port_range_end": {"apply_class": "manual-restart", "services": []},
    # [models]
    "models.roots": {"apply_class": "service-restart", "services": [SERVICE_HAL0_API]},
    "models.auto_scan_on_start": {"apply_class": "immediate", "services": []},
    "models.file_extensions": {"apply_class": "service-restart", "services": [SERVICE_HAL0_API]},
    "models.store": {"apply_class": "service-restart", "services": [SERVICE_LEMONADE]},
    "models.pull_root": {"apply_class": "service-restart", "services": [SERVICE_LEMONADE]},
    # [memory.embedding]
    "memory.embedding.model": {"apply_class": "service-restart", "services": [SERVICE_HAL0_API]},
    "memory.embedding.rerank_enabled": {"apply_class": "immediate", "services": []},
    "memory.embedding.rerank_url": {"apply_class": "immediate", "services": []},
    "memory.embedding.rerank_over_fetch_factor": {"apply_class": "immediate", "services": []},
    "memory.embedding.rerank_max_candidates": {"apply_class": "immediate", "services": []},
    "memory.embedding.rerank_connect_timeout_s": {"apply_class": "immediate", "services": []},
    "memory.embedding.rerank_read_timeout_s": {"apply_class": "immediate", "services": []},
    # [memory.graph]
    "memory.graph.enabled": {"apply_class": "immediate", "services": []},
    "memory.graph.route": {"apply_class": "immediate", "services": []},
    "memory.graph.upstream": {"apply_class": "immediate", "services": []},
    # [meta]
    "meta.schema_version": {"apply_class": "manual-restart", "services": []},
}


def _expand_lemonade() -> dict[str, ApplyPlanEntry]:
    """Project the lemonade_admin frozensets into the registry dict.

    The Lemonade config surface is a flat key namespace (``port``,
    ``llamacpp_args``, ...) at the wire level, so the registry uses
    those bare names. Keys land in :data:`REGISTRY` under their
    canonical wire name — same name the GET /api/lemonade/config
    response uses and the UI badge in #545's RuntimeSection already
    derives its partition from.
    """
    out: dict[str, ApplyPlanEntry] = {}
    for key in IMMEDIATE_KEYS:
        out[key] = dict(_LEMONADE_IMMEDIATE_ENTRY)
    for key in DEFERRED_KEYS:
        out[key] = dict(_LEMONADE_DEFERRED_ENTRY)
    return out


# The single source of truth. Read-only by convention — callers must go
# through :func:`apply_plan` or :func:`get_registry` to inspect.
REGISTRY: dict[str, ApplyPlanEntry] = {
    **_expand_lemonade(),
    **_HAL0_REGISTRY,
}


class ApplyPlanEntry(TypedDict):
    """One row of the apply plan.

    ``apply_class`` is one of :data:`APPLY_CLASSES`. ``services`` lists
    the services a ``service-restart`` entry needs bounced; the list
    is empty for ``immediate`` and ``manual-restart`` (those have no
    platform-level restart the operator can fire).
    """

    apply_class: str
    services: list[str]


class ApplyPlanResult(TypedDict, total=False):
    """The partition :func:`apply_plan` returns.

    All four lists/dicts are sorted deterministically so the response
    shape is stable for both the dashboard and snapshot tests.
    ``unknown`` is non-empty when the caller passed keys the registry
    doesn't know about — typically a typo or a brand-new field whose
    apply class hasn't been decided yet. The route surfaces the list
    verbatim; the UI can render an informational chip rather than
    guess at the class.
    """

    immediate: list[str]
    service_restart: dict[str, list[str]]
    manual_restart: list[str]
    unknown: list[str]


def get_registry() -> dict[str, ApplyPlanEntry]:
    """Return a copy of the registry keyed by settings path.

    The dashboard's Settings view fetches this once on mount so each
    row can render the right apply badge without a per-save server
    round-trip. Returns a fresh dict so callers can mutate without
    corrupting the module-level constant.
    """
    return {
        k: {"apply_class": v["apply_class"], "services": list(v["services"])}
        for k, v in REGISTRY.items()
    }


def apply_plan(touched_keys: list[str] | tuple[str, ...]) -> ApplyPlanResult:
    """Partition a set of touched keys into the three apply classes.

    Args:
        touched_keys: Setting paths the caller is about to write. The
            function accepts the dotted hal0 form (``slots.max_slots``)
            and the bare lemonade form (``llamacpp_args``) — both
            appear in real PUT bodies.

    Returns:
        An :class:`ApplyPlanResult` with the keys split into
        ``immediate`` (no restart), ``service_restart`` (one entry per
        affected service, mapping to the keys that need that service
        bounced), ``manual_restart`` (operator action required), and
        ``unknown`` (keys the registry has no class for).

    Examples:
        >>> apply_plan(["log_level", "llamacpp_args"])
        {'immediate': ['log_level'], 'service_restart': {'lemonade': ['llamacpp_args']}, 'manual_restart': [], 'unknown': []}

        >>> apply_plan(["slots.port_range_start"])
        {'immediate': [], 'service_restart': {}, 'manual_restart': ['slots.port_range_start'], 'unknown': []}
    """
    immediate: list[str] = []
    by_service: dict[str, list[str]] = {}
    manual: list[str] = []
    unknown: list[str] = []

    for key in touched_keys:
        entry = REGISTRY.get(key)
        if entry is None:
            unknown.append(key)
            continue
        cls = entry["apply_class"]
        services = entry.get("services") or []
        if cls == "immediate":
            immediate.append(key)
        elif cls == "service-restart":
            for svc in services:
                by_service.setdefault(svc, []).append(key)
        elif cls == "manual-restart":
            manual.append(key)
        else:
            # Defensive: an unrecognised class shouldn't appear because
            # the registry is built from a closed enum, but if a
            # future entry slips through we surface it as unknown
            # rather than silently dropping it.
            unknown.append(key)

    return {
        "immediate": sorted(immediate),
        "service_restart": {svc: sorted(ks) for svc, ks in sorted(by_service.items())},
        "manual_restart": sorted(manual),
        "unknown": sorted(unknown),
    }


__all__ = [
    "APPLY_CLASSES",
    "REGISTRY",
    "SERVICE_HAL0_API",
    "SERVICE_LEMONADE",
    "ApplyPlanEntry",
    "ApplyPlanResult",
    "apply_plan",
    "get_registry",
]
