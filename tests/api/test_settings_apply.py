"""Tests for the settings apply-plan registry + endpoint (issue #552).

The generalises the per-key immediate-vs-deferred taxonomy that lives
in :mod:`hal0.api.routes.lemonade_admin` for the Lemonade admin panel
so the whole settings surface can declare its apply class. The
registry + the GET endpoint are the single source of truth the UI's
``settings.jsx`` fetches once on mount to render per-row effect badges
(live / ⟳ restart <service> / ⚠ manual restart).

Coverage:

  * Registry entries — every known key maps to the expected
    ``{apply_class, services}`` shape. The Lemonade keys are derived
    from the imported :data:`lemonade_admin.IMMEDIATE_KEYS` /
    :data:`lemonade_admin.DEFERRED_KEYS` so a divergence here points
    at a drift between the two definitions (caught by the
    parametrised test).

  * :func:`apply_plan` partition — a list of touched keys splits
    deterministically into ``immediate`` / ``service_restart`` /
    ``manual_restart`` / ``unknown`` buckets, sorted, and the
    service→keys map is alphabetised so two calls with the same
    input return byte-identical results.

  * :func:`get_registry` — returns a defensive copy so callers can
    mutate without corrupting the module-level constant.

  * ``GET /api/settings/apply-plan`` — returns the full registry
    shape so the dashboard can render badges without a per-save
    round-trip. ``PUT /api/settings`` response carries
    ``_hal0.apply_plan`` so the success toast can show the precise
    effect split for just the keys that were touched (mirrors the
    ``_hal0.effects`` block ``lemonade_admin`` adds — #545).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api._settings_apply import (
    APPLY_CLASSES,
    REGISTRY,
    SERVICE_HAL0_API,
    SERVICE_LEMONADE,
    apply_plan,
    get_registry,
)
from hal0.api.routes.lemonade_admin import DEFERRED_KEYS, IMMEDIATE_KEYS

# ── registry shape ────────────────────────────────────────────────────


def test_registry_declares_three_apply_classes() -> None:
    """The closed enum never gains a fourth class without a deliberate
    registry upgrade — tests pin the surface."""
    assert APPLY_CLASSES == ("immediate", "service-restart", "manual-restart")


def test_registry_lemonade_immediate_keys_classified_immediate() -> None:
    """Every key lemonade_admin declares IMMEDIATE lands in the
    registry as ``immediate`` with no services. The mapping is
    auto-generated, so a drift between the two definitions shows up
    here as a missing entry."""
    for key in IMMEDIATE_KEYS:
        entry = REGISTRY.get(key)
        assert entry is not None, f"lemonade IMMEDIATE key {key!r} missing from registry"
        assert entry["apply_class"] == "immediate", key
        assert entry["services"] == [], key


def test_registry_lemonade_deferred_keys_classified_service_restart() -> None:
    """Every key lemonade_admin declares DEFERRED lands in the
    registry as ``service-restart`` on the ``lemonade`` service. The
    "next /v1/load" semantic collapses to a service bounce in the
    new taxonomy — both require lemond to re-read the config."""
    for key in DEFERRED_KEYS:
        entry = REGISTRY.get(key)
        assert entry is not None, f"lemonade DEFERRED key {key!r} missing from registry"
        assert entry["apply_class"] == "service-restart", key
        assert entry["services"] == [SERVICE_LEMONADE], key


def test_registry_no_key_in_both_lemonade_sets() -> None:
    """A key that's both immediate and deferred is a contradiction —
    this guards against an upstream split in lemonade_admin leaking
    into the unified registry as a duplicate entry with a later
    class winning."""
    seen: dict[str, str] = {}
    for key in IMMEDIATE_KEYS:
        seen[key] = "immediate"
    for key in DEFERRED_KEYS:
        if key in seen:
            pytest.fail(f"key {key!r} appears in both IMMEDIATE and DEFERRED sets")
        seen[key] = "deferred"


@pytest.mark.parametrize(
    "key, expected_class, expected_services",
    [
        # [telemetry]
        ("telemetry.enabled", "immediate", []),
        ("telemetry.channel", "immediate", []),
        # [dispatcher]
        ("dispatcher.prefetch_timeout_s", "immediate", []),
        ("dispatcher.prefetch_parallel_cap", "immediate", []),
        # [slots]
        ("slots.max_slots", "service-restart", [SERVICE_HAL0_API]),
        ("slots.port_range_start", "manual-restart", []),
        ("slots.port_range_end", "manual-restart", []),
        # [models]
        ("models.roots", "service-restart", [SERVICE_HAL0_API]),
        ("models.auto_scan_on_start", "immediate", []),
        ("models.file_extensions", "service-restart", [SERVICE_HAL0_API]),
        ("models.store", "service-restart", [SERVICE_LEMONADE]),
        ("models.pull_root", "service-restart", [SERVICE_LEMONADE]),
        # [memory.embedding]
        ("memory.embedding.model", "service-restart", [SERVICE_HAL0_API]),
        ("memory.embedding.rerank_enabled", "immediate", []),
        ("memory.embedding.rerank_url", "immediate", []),
        ("memory.embedding.rerank_over_fetch_factor", "immediate", []),
        ("memory.embedding.rerank_max_candidates", "immediate", []),
        ("memory.embedding.rerank_connect_timeout_s", "immediate", []),
        ("memory.embedding.rerank_read_timeout_s", "immediate", []),
        # [memory.graph]
        ("memory.graph.enabled", "immediate", []),
        ("memory.graph.route", "immediate", []),
        ("memory.graph.upstream", "immediate", []),
        # [meta]
        ("meta.schema_version", "manual-restart", []),
    ],
)
def test_registry_hal0_keys_have_expected_class(
    key: str, expected_class: str, expected_services: list[str]
) -> None:
    """Every dotted hal0 ``Hal0Config`` path is annotated with the
    class the operator-facing settings form will surface. If a new
    field lands in ``Hal0Config`` without a registry entry, this
    parametrised list is where to add the assertion first — the UI
    badge for that key is the user-facing consequence."""
    entry = REGISTRY.get(key)
    assert entry is not None, f"hal0 key {key!r} missing from registry"
    assert entry["apply_class"] == expected_class, key
    assert list(entry["services"]) == expected_services, key


def test_registry_service_restart_entries_have_at_least_one_service() -> None:
    """A ``service-restart`` with an empty services list is a bug —
    it would render as "⟳ restart" with no service name, which is
    not actionable. The parametrised tests above pin the service for
    each entry; this is the catch-all so a future addition can't
    sneak through with ``services: []``."""
    for key, entry in REGISTRY.items():
        if entry["apply_class"] == "service-restart":
            assert entry["services"], f"service-restart key {key!r} has no services"


def test_registry_manual_restart_entries_have_no_services() -> None:
    """``manual-restart`` means *operator* action, not a service
    bounce — the services list is empty by design. A non-empty list
    here would render a misleading "⟳ restart lemonade" badge
    next to a port-change warning."""
    for key, entry in REGISTRY.items():
        if entry["apply_class"] == "manual-restart":
            assert entry["services"] == [], f"manual-restart key {key!r} should have empty services"


def test_registry_immediate_entries_have_no_services() -> None:
    """Symmetric to manual-restart — ``immediate`` keys take effect
    on the next consult, no service bounce needed."""
    for key, entry in REGISTRY.items():
        if entry["apply_class"] == "immediate":
            assert entry["services"] == [], f"immediate key {key!r} should have empty services"


# ── apply_plan partition ─────────────────────────────────────────────


def test_apply_plan_partitions_immediate_service_and_manual() -> None:
    """A heterogeneous input lands in the right three buckets. The
    partition is the response shape the UI's success toast
    renders — a regression here would silently mis-label keys."""
    plan = apply_plan(
        [
            "log_level",  # immediate (lemonade)
            "llamacpp_args",  # service-restart[lemonade]
            "slots.max_slots",  # service-restart[hal0-api]
            "slots.port_range_start",  # manual-restart
        ]
    )
    assert plan["immediate"] == ["log_level"]
    assert plan["service_restart"] == {
        SERVICE_LEMONADE: ["llamacpp_args"],
        SERVICE_HAL0_API: ["slots.max_slots"],
    }
    assert plan["manual_restart"] == ["slots.port_range_start"]
    assert plan["unknown"] == []


def test_apply_plan_unknown_keys_segregated() -> None:
    """Keys the registry has no class for land in ``unknown`` rather
    than being silently dropped or guessed. The UI renders an
    informational chip for them; the route surfaces the list
    verbatim so a future schema change can't lose a key
    invisibly."""
    plan = apply_plan(["log_level", "not_a_real_key", "another_typo"])
    assert plan["immediate"] == ["log_level"]
    assert plan["service_restart"] == {}
    assert plan["manual_restart"] == []
    assert plan["unknown"] == ["another_typo", "not_a_real_key"]


def test_apply_plan_output_is_deterministically_sorted() -> None:
    """Two calls with the same input (different ordering) return
    byte-identical results — the response shape is the wire
    contract and the snapshot tests depend on it."""
    a = apply_plan(["slots.port_range_start", "log_level", "llamacpp_args"])
    b = apply_plan(["llamacpp_args", "log_level", "slots.port_range_start"])
    assert a == b
    # Each bucket is sorted ascending.
    assert a["immediate"] == sorted(a["immediate"])
    assert a["manual_restart"] == sorted(a["manual_restart"])
    assert a["unknown"] == sorted(a["unknown"])
    for keys in a["service_restart"].values():
        assert keys == sorted(keys)


def test_apply_plan_accepts_tuple_input() -> None:
    """Callers may pass a tuple (e.g. the keys enumerated from a
    dict's ``keys()`` view). The function signature uses
    ``list[str] | tuple[str, ...]`` to be explicit about that
    acceptance."""
    plan = apply_plan(("log_level", "llamacpp_args"))
    assert plan["immediate"] == ["log_level"]
    assert plan["service_restart"] == {SERVICE_LEMONADE: ["llamacpp_args"]}


def test_apply_plan_empty_input_returns_empty_buckets() -> None:
    """A empty PATCH (no keys touched) returns the empty-bucket
    shape — the route never 500s on this even though the deeper
    ``update_settings`` rejects an empty body earlier."""
    plan = apply_plan([])
    assert plan == {
        "immediate": [],
        "service_restart": {},
        "manual_restart": [],
        "unknown": [],
    }


def test_apply_plan_collapses_to_one_service_bucket_per_service() -> None:
    """Two keys both needing ``lemonade`` bounced land in the same
    ``lemonade`` bucket — the UI's success toast would say "⟳
    restart lemonade (llamacpp_args, flm_args)" rather than
    rendering two separate restart rows."""
    plan = apply_plan(["llamacpp_args", "flm_args"])
    assert plan["service_restart"] == {SERVICE_LEMONADE: ["flm_args", "llamacpp_args"]}


# ── get_registry defensive copy ──────────────────────────────────────


def test_get_registry_returns_defensive_copy() -> None:
    """Mutating the returned dict must not corrupt the module-level
    constant — a careless UI shouldn't be able to alter the
    server's view of the registry mid-session."""
    snapshot = get_registry()
    snapshot["__rogue_key__"] = {"apply_class": "immediate", "services": []}
    snapshot["log_level"]["services"].append("rogue-service")
    # Re-fetch — the module constant is untouched.
    fresh = get_registry()
    assert "__rogue_key__" not in fresh
    assert "rogue-service" not in fresh["log_level"]["services"]


def test_get_registry_covers_every_lemonade_admin_key() -> None:
    """A regression guard: the registry must carry every key the
    Lemonade admin accepts. If a future PR adds a key to
    ``IMMEDIATE_KEYS`` / ``DEFERRED_KEYS`` without a matching
    registry entry, this test points at the gap."""
    for key in IMMEDIATE_KEYS | DEFERRED_KEYS:
        assert key in get_registry(), f"lemonade key {key!r} not in registry"


# ── HTTP endpoints ───────────────────────────────────────────────────


@pytest.fixture
def isolated_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


def test_get_apply_plan_returns_full_registry(isolated_client: TestClient) -> None:
    """The dashboard fetches this once on mount. The shape has to
    carry every key the UI might badge — and the apply-classes
    enum — so the renderer can pick the right colour without a
    second request."""
    r = isolated_client.get("/api/settings/apply-plan")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["apply_classes"] == list(APPLY_CLASSES)
    assert "registry" in body
    registry = body["registry"]

    # Spot-check a few representative entries from each class so a
    # silent typo in the registry dict doesn't slip past.
    assert registry["log_level"]["apply_class"] == "immediate"
    assert registry["llamacpp_args"]["apply_class"] == "service-restart"
    assert registry["llamacpp_args"]["services"] == [SERVICE_LEMONADE]
    assert registry["slots.port_range_start"]["apply_class"] == "manual-restart"

    # Every entry has the right TypedDict shape.
    for key, entry in registry.items():
        assert "apply_class" in entry, key
        assert "services" in entry, key
        assert entry["apply_class"] in APPLY_CLASSES, key
        assert isinstance(entry["services"], list), key


def test_put_settings_response_includes_apply_plan(isolated_client: TestClient) -> None:
    """The PUT response carries ``_hal0.apply_plan`` so the success
    toast can render the per-save effect split without a follow-up
    round-trip — mirrors the ``_hal0.effects`` block on the
    Lemonade admin response (#545).

    The plan keys on the *top-level* fields the PATCH carried (e.g.
    ``telemetry``), not on the dotted leaf paths the registry uses
    (e.g. ``telemetry.enabled``). For a top-level-only touch the
    buckets are empty, but the shape stays consistent."""
    r = isolated_client.put(
        "/api/settings",
        json={"telemetry": {"enabled": True}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Existing top-level shape preserved.
    assert body["telemetry"]["enabled"] is True
    # New: per-save apply plan rides along.
    assert "_hal0" in body
    plan = body["_hal0"]["apply_plan"]
    assert plan == {
        "immediate": [],
        "service_restart": {},
        "manual_restart": [],
        "unknown": [],
    }


def test_put_settings_response_preserves_existing_top_level_shape(
    isolated_client: TestClient,
) -> None:
    """The existing PUT contract (response is the merged config
    dict at the top level) is unchanged. Only ``_hal0`` is added.
    Test asserts every previously-returned key is still present
    so a future refactor can't silently swallow them."""
    r = isolated_client.put(
        "/api/settings",
        json={
            "telemetry": {"enabled": True},
            "dispatcher": {"prefetch_timeout_s": 12.0},
        },
    )
    body = r.json()
    # The Hal0Config top-level tables are present (existing contract).
    for table in ("meta", "slots", "dispatcher", "telemetry", "models", "memory"):
        assert table in body, f"existing top-level {table!r} missing from PUT response"
    # And the new envelope is added without clobbering anything.
    assert body["_hal0"]["apply_plan"]["unknown"] == []
