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


def test_disabled_renders_physical_default():
    out = _render_config_yaml(live_resolve_enabled=False, **_ctx())
    assert "phys-35b" in out
    assert "hal0/primary" not in out
