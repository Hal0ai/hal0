"""The X-hal0-Private header must reach hermes' config.yaml as a STRING — #2085.

Root cause of #2085 (hermes runs with zero memory tools on fresh installs):
hal0 handed ``("mcp_servers.<name>.headers.X-hal0-Private", "1")`` to
``hermes config set``, and hermes coerces bare integers on the way in (the
documented behavior :func:`hp._fmt_config_value` relies on for timeouts) —
so config.yaml ends up with the unquoted YAML int ``1``. Hermes' own MCP
client then wedges post-initialize on the non-string header value and dies
with ``CancelledError`` at its outer ceiling, while ``hal0-admin`` (no
private header) connects fine. Reproduced 2026-08-28 against a live rc.10
gateway with the pinned hermes build: ``X-hal0-Private: 1`` hangs 40 s,
``X-hal0-Private: "1"`` connects and lists all 26 memory tools.

The fix moves the private header out of the ``config set`` pairs (which
cannot express a numeric-looking string) into the PyYAML deep-merge layer
(:func:`hp._config_list_keys` → :func:`hp._merge_config_yaml_layers`),
where ``safe_dump`` quotes the string — and which runs AFTER config set,
so a re-provision also repairs already-poisoned boxes. The brain-profile
render (:func:`hp._build_brain_profile_mcp_servers`) carried a literal
int ``1`` with the same downstream effect; it becomes ``"1"``.
"""

from __future__ import annotations

from typing import Any

import yaml

from hal0.agents import hermes_provision as hp

_MCP_SERVERS: list[dict[str, Any]] = [
    {"name": "hal0-admin", "url": "http://x/mcp/admin/mcp", "type": "http"},
    {
        "name": "hal0-memory",
        "url": "http://x/mcp/memory/mcp",
        "type": "http",
        "private": True,
        "timeout": 900,
    },
]


def _overlay(**over: Any) -> dict[str, Any]:
    base = dict(
        primary={
            "model_id": "qwen3:8b",
            "backend_url": "http://127.0.0.1:8080/v1",
            "context_length": 16384,
        },
        chat_slots=[],
        delegation=None,
        auxiliary_tasks={},
        mcp_servers=_MCP_SERVERS,
        agent_id="hermes-agent",
        system_prompt="",
        personality_name="",
        live_resolve_enabled=True,
    )
    base.update(over)
    return dict(hp._build_config_overlay(**base))


def test_overlay_never_routes_private_header_through_config_set() -> None:
    """`hermes config set` coerces "1" -> int; the key must not go that way."""
    overlay = _overlay()
    private_keys = [k for k in overlay if "X-hal0-Private" in k]
    assert private_keys == [], private_keys


def test_config_list_keys_carry_private_header_as_string() -> None:
    keys = hp._config_list_keys(
        terminal_enabled=False,
        existing_disabled=[],
        mcp_servers=_MCP_SERVERS,
    )
    hdr = keys["mcp_servers"]["hal0-memory"]["headers"]["X-hal0-Private"]
    assert hdr == "1"
    assert isinstance(hdr, str)
    # Non-private servers get no header overlay at all.
    assert "hal0-admin" not in keys["mcp_servers"]


def test_config_list_keys_omit_mcp_table_without_private_servers() -> None:
    keys = hp._config_list_keys(
        terminal_enabled=False,
        existing_disabled=[],
        mcp_servers=[{"name": "hal0-admin", "url": "http://x", "type": "http"}],
    )
    assert "mcp_servers" not in keys


def test_merge_repairs_an_int_poisoned_config(tmp_path) -> None:
    """Re-provision on an already-broken box must flip int 1 -> string "1"."""
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "mcp_servers": {
                    "hal0-memory": {
                        "type": "http",
                        "url": "http://x/mcp/memory/mcp",
                        "headers": {"X-hal0-Agent": "hermes", "X-hal0-Private": 1},
                        "timeout": 900,
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    changed = hp._merge_config_yaml_layers(
        config,
        list_keys=hp._config_list_keys(
            terminal_enabled=False, existing_disabled=[], mcp_servers=_MCP_SERVERS
        ),
        overrides_path=tmp_path / "overrides.yaml",
    )
    assert changed is True
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    hdr = data["mcp_servers"]["hal0-memory"]["headers"]["X-hal0-Private"]
    assert hdr == "1"
    assert isinstance(hdr, str)
    # The sibling header written by config set must survive the merge.
    assert data["mcp_servers"]["hal0-memory"]["headers"]["X-hal0-Agent"] == "hermes"
    # And the on-disk YAML must carry the quoted form hermes' client needs.
    assert "X-hal0-Private: '1'" in config.read_text(encoding="utf-8")


def test_overrides_cannot_repoison_the_private_header(tmp_path) -> None:
    """overrides.yaml copied from a pre-fix config.yaml carries the int 1
    (and overrides merge LAST, by design) — the known-poisonous leaf must
    still come out a string, or the #2085 wedge comes back silently."""
    config = tmp_path / "config.yaml"
    config.write_text("mcp_servers: {}\n", encoding="utf-8")
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(
        yaml.safe_dump(
            {"mcp_servers": {"hal0-memory": {"headers": {"X-hal0-Private": 1}}}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    hp._merge_config_yaml_layers(
        config,
        list_keys=hp._config_list_keys(
            terminal_enabled=False, existing_disabled=[], mcp_servers=_MCP_SERVERS
        ),
        overrides_path=overrides,
    )
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    hdr = data["mcp_servers"]["hal0-memory"]["headers"]["X-hal0-Private"]
    assert hdr == "1"
    assert isinstance(hdr, str)


def test_brain_profile_private_header_is_a_string() -> None:
    servers = hp._build_brain_profile_mcp_servers()
    hdr = servers["hal0-memory"]["headers"]["X-hal0-Private"]
    assert hdr == "1"
    assert isinstance(hdr, str)
