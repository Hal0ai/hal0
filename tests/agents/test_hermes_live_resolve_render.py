"""Live-resolve behaviour of the config-set overlay builder.

Post config-set redesign there is no whole-file render: ``_build_config_overlay``
returns the ``(dotted_key, value)`` pairs applied via ``hermes config set``.
These assert the same live-resolve contract the old Jinja render guaranteed.
"""

from hal0.agents.hermes_provision import _build_config_overlay


def _overlay(*, live_resolve_enabled, **over):
    base = dict(
        primary={
            "model_id": "phys-35b",
            "backend_url": "http://127.0.0.1:8080/v1",
            "context_length": 65536,
        },
        chat_slots=[],
        delegation=None,
        auxiliary_tasks={},
        mcp_servers=[],
        agent_id="hermes",
        system_prompt="x",
        personality_name="default",
    )
    base.update(over)
    pairs = _build_config_overlay(live_resolve_enabled=live_resolve_enabled, **base)
    return dict(pairs)


def test_live_resolve_uses_virtual_default():
    keys = _overlay(live_resolve_enabled=True)
    # ADR-0023: the canonical default virtual is hal0/agent (was hal0/chat).
    assert keys["model.default"] == "hal0/agent"
    assert keys["model.provider"] == "custom"
    assert keys["model.base_url"] == "http://127.0.0.1:8080/v1"


def test_disabled_uses_physical_default():
    keys = _overlay(live_resolve_enabled=False)
    assert keys["model.default"] == "phys-35b"
    assert "hal0/agent" not in keys.values()


def test_live_resolve_enables_live_model_discovery():
    """Under live-resolve, providers.custom carries api_key + discover_models so
    Hermes's picker runs live /v1/models discovery against the gateway."""
    keys = _overlay(live_resolve_enabled=True)
    assert keys["providers.custom.discover_models"] is True
    assert keys["providers.custom.api_key"] == "hal0-local"


def test_live_resolve_tags_discovery_with_owned_by_filter():
    """#1148: under live-resolve, providers.custom.extra_headers carries the
    X-hal0-Model-Filter: hal0 header so /v1/models discovery returns only
    owned_by==hal0 rows — remote passthroughs stay out of the picker."""
    keys = _overlay(live_resolve_enabled=True)
    assert keys["providers.custom.extra_headers.X-hal0-Model-Filter"] == "hal0"


def test_disabled_omits_live_model_discovery():
    """With live-resolve OFF, base_url is a single physical slot backend, so
    live discovery is intentionally not enabled."""
    keys = _overlay(live_resolve_enabled=False)
    assert "providers.custom.discover_models" not in keys
    assert "providers.custom.api_key" not in keys
    assert "providers.custom.extra_headers.X-hal0-Model-Filter" not in keys


def test_live_resolve_forces_gateway_for_nonlocal_primary():
    """A non-8080 primary backend_url must NOT leak into either base_url under
    live-resolve — both model.base_url and providers.custom.base_url point at
    the gateway so Hermes's base_url cross-match stays consistent."""
    primary = {"model_id": "m", "backend_url": "http://127.0.0.1:8001/v1", "context_length": 4096}
    on = _overlay(live_resolve_enabled=True, primary=primary)
    assert on["model.base_url"] == "http://127.0.0.1:8080/v1"
    assert on["providers.custom.base_url"] == "http://127.0.0.1:8080/v1"
    assert "8001" not in on["model.base_url"]

    # With the flag OFF the same primary DOES surface its 8001 URL.
    off = _overlay(live_resolve_enabled=False, primary=primary)
    assert off["model.base_url"] == "http://127.0.0.1:8001/v1"
    assert off["providers.custom.base_url"] == "http://127.0.0.1:8001/v1"
