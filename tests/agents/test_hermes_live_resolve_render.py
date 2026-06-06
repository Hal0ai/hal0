from hal0.agents.hermes_provision import _render_config_yaml


def _ctx(**over):
    base = dict(
        primary={
            "model_id": "phys-35b",
            "backend_url": "http://127.0.0.1:8080/v1",
            "context_length": 65536,
        },
        chat_slots=[],
        agent_id="hermes",
        mcp_servers=None,
        system_prompt="x",
        personality_name="default",
        delegation=None,
        auxiliary_tasks=None,
        custom_providers=None,
    )
    base.update(over)
    return base


def test_live_resolve_renders_virtual_default():
    out = _render_config_yaml(live_resolve_enabled=True, **_ctx())
    assert 'default: "hal0/primary"' in out
    assert 'provider: "custom"' in out
    assert 'base_url: "http://127.0.0.1:8080/v1"' in out


def test_disabled_renders_physical_default():
    out = _render_config_yaml(live_resolve_enabled=False, **_ctx())
    assert "phys-35b" in out
    assert "hal0/primary" not in out


def test_live_resolve_enables_live_model_discovery():
    """Under live-resolve, providers.custom carries an api_key + discover_models
    so Hermes's Section-3 picker runs live /v1/models discovery against the
    gateway (surfacing every slot, not just model.default)."""
    out = _render_config_yaml(live_resolve_enabled=True, **_ctx())
    assert "discover_models: true" in out
    assert 'api_key: "hal0-local"' in out


def test_disabled_omits_live_model_discovery():
    """With live-resolve OFF, base_url points at a single physical slot
    backend, so live discovery is intentionally not enabled."""
    out = _render_config_yaml(live_resolve_enabled=False, **_ctx())
    assert "discover_models" not in out
    assert "hal0-local" not in out


def test_live_resolve_forces_gateway_for_nonlocal_primary():
    """A non-8080 primary backend_url must NOT leak into either base_url
    under live_resolve — both model.base_url and providers.custom.base_url
    render the gateway so Hermes's base_url cross-match stays consistent."""
    primary = {
        "model_id": "m",
        "backend_url": "http://127.0.0.1:8001/v1",
        "context_length": 4096,
    }
    out_on = _render_config_yaml(live_resolve_enabled=True, **_ctx(primary=primary))
    assert "8001" not in out_on
    # Both base_url lines (model: and providers.custom:) point at the gateway.
    assert out_on.count('base_url: "http://127.0.0.1:8080/v1"') >= 2

    # Sanity: with the flag OFF the same primary DOES surface its 8001 URL.
    out_off = _render_config_yaml(live_resolve_enabled=False, **_ctx(primary=primary))
    assert "8001" in out_off
