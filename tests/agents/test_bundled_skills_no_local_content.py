"""The bundled agent skills describe the product, not one operator's machine (#4013).

An agent reads ``installer/agent-skills/`` and follows it literally, with the
terminal tool in some setups. A path, address or tool name copied from one box
sends it to files that do not exist on the user's machine, or to a tool it does
not have. Every file under that tree is checked for these things:

* no path under ``/home/``, ``/root/``, ``/mnt/``, ``/Users/``, ``/opt/`` or ``/srv/``,
  no ``~/`` or ``$HOME`` path and no ``file:///home/`` URL: there is no allow-list, and a
  skill that needs the model store says "the model store" and points at the tool that
  reports it;
* no IPv4 address other than loopback and the documentation ranges (a version string
  such as ``v1.8.2.1`` is not an address);
* no ``mcp__local_mcp`` namespace, which only one box ever had;
* every ``mcp__<server>__<tool>`` name exists in the tools the matching hal0 MCP
  server registers, read in-process through its public listing;
* no ``--user`` on a ``systemctl`` or ``journalctl`` line: hal0's units are system units.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from pathlib import Path
from types import SimpleNamespace

from hal0.mcp import admin, agents, memory
from hal0.mcp.approval_queue import ApprovalQueue

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "installer" / "agent-skills"

#: Operator-local roots a skill must not name. ``/var/lib/hal0`` and ``/etc/hal0`` are
#: the product's own state and config directories and are not in this list.
LOCAL_ROOTS = ("home", "root", "mnt", "Users", "opt", "srv")

#: Ranges an address in a skill may come from: loopback and RFC 5737 documentation.
DOC_NETWORKS = tuple(
    ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)

_PATH = re.compile(rf"(?<![\w/.~$-])(/(?:{'|'.join(LOCAL_ROOTS)})(?![\w-])(?:/[^\s`'\")\]>,;|]*)?)")
_HOME_PATH = re.compile(
    r"(?<![\w.])(?:~/|\$\{?HOME\}?(?![\w]))|file:///(?:home|root|mnt|Users|opt|srv)\b"
)
# Not preceded by a word character or a dot (so ``v1.8.2.1`` is a version) and not
# followed by a word character or another ``.<digit>`` (so ``1.2.3.4.5`` is not an address).
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\d)")
_SYSTEMCTL_USER = re.compile(r"\b(?:systemctl|journalctl)\b[^\n]*?\s--user\b")
_MCP_NAME = re.compile(r"mcp__([a-z0-9]+(?:[_-][a-z0-9]+)*)__([a-z][a-z0-9_]*)")


def skill_files(root: Path = SKILLS) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def local_paths(text: str) -> list[str]:
    """Paths in ``text`` under an operator-local root that the product does not use."""
    found = [match.group(1).rstrip(".:") for match in _PATH.finditer(text)]
    found.extend(match.group(0) for match in _HOME_PATH.finditer(text))
    return found


def systemctl_user(text: str) -> list[str]:
    """``systemctl --user`` invocations: hal0 installs system units, never user units."""
    return [match.group(0) for match in _SYSTEMCTL_USER.finditer(text)]  # also journalctl


def foreign_addresses(text: str) -> list[str]:
    """IPv4 addresses in ``text`` that are neither loopback nor in a documentation range."""
    found = []
    for match in _IPV4.finditer(text):
        try:
            ip = ipaddress.ip_address(match.group(0))
        except ValueError:
            continue
        if ip.is_loopback or any(ip in net for net in DOC_NETWORKS):
            continue
        found.append(match.group(0))
    return found


def mcp_names(text: str) -> list[tuple[str, str]]:
    """(server, tool) for each ``mcp__<server>__<tool>`` name in ``text``."""
    return [(m.group(1).replace("-", "_"), m.group(2)) for m in _MCP_NAME.finditer(text)]


def _scan(files: dict[str, str], finder) -> list[str]:
    hits = []
    for name, text in files.items():
        for lineno, line in enumerate(text.splitlines(), 1):
            for found in finder(line):
                hits.append(f"{name}:{lineno}: {found}")
    return hits


def _read_all() -> dict[str, str]:
    return {str(p.relative_to(REPO)): p.read_text(encoding="utf-8") for p in skill_files()}


def registered_tools() -> dict[str, set[str]]:
    """Tool names each hal0 MCP server registers, keyed by the name Hermes gives the server."""

    async def _names(server) -> set[str]:
        return {tool.name for tool in await server.list_tools()}

    admin_server = admin.build_server(approval_queue=ApprovalQueue(), base_url="http://t")
    memory_server = memory.build_server()
    agents_server = agents.build_server(state=SimpleNamespace(bridge_service=None))

    async def _all() -> dict[str, set[str]]:
        return {
            "hal0_admin": await _names(admin_server),
            "hal0_memory": await _names(memory_server),
            "hal0_agents": await _names(agents_server),
        }

    return asyncio.run(_all())


def unknown_tools(text: str, tools: dict[str, set[str]]) -> list[str]:
    bad = []
    for server, tool in mcp_names(text):
        if server not in tools:
            bad.append(f"mcp__{server}__{tool} (no such server)")
        elif tool not in tools[server]:
            bad.append(f"mcp__{server}__{tool} (server registers no such tool)")
    return bad


# -- the checks run against the shipped tree ---------------------------------------------


def test_the_tree_is_not_empty() -> None:
    names = {p.name for p in skill_files()}
    assert {"SKILL.md", "hermes-gateway-platform-tokens.md"} <= names


def test_no_operator_local_paths() -> None:
    hits = _scan(_read_all(), local_paths)
    assert not hits, "operator-local paths in bundled skills:\n" + "\n".join(hits)


def test_no_lan_addresses() -> None:
    hits = _scan(_read_all(), foreign_addresses)
    assert not hits, "non-loopback addresses in bundled skills:\n" + "\n".join(hits)


def test_no_systemctl_user() -> None:
    hits = _scan(_read_all(), systemctl_user)
    assert not hits, "hal0's units are system units, not user units:\n" + "\n".join(hits)


def test_no_local_mcp_namespace() -> None:
    hits = _scan(_read_all(), lambda line: ["mcp__local_mcp"] if "mcp__local_mcp" in line else [])
    assert not hits, "the mcp__local_mcp namespace does not exist:\n" + "\n".join(hits)


def test_every_named_mcp_tool_is_registered() -> None:
    tools = registered_tools()
    hits = _scan(_read_all(), lambda line: unknown_tools(line, tools))
    assert not hits, "bundled skills name tools no server registers:\n" + "\n".join(hits)


def test_the_admin_skill_names_tools_in_the_persona_namespace() -> None:
    text = (SKILLS / "hal0-admin" / "SKILL.md").read_text(encoding="utf-8")
    assert "mcp__hal0_admin__slot_list" in text
    assert {s for s, _ in mcp_names(text)} == {"hal0_admin"}


# -- the checks themselves catch what they are for ---------------------------------------


def test_path_check_catches_local_paths_and_spares_product_ones() -> None:
    assert local_paths("cd /home/someone/work and /root/.config/x") == [
        "/home/someone/work",
        "/root/.config/x",
    ]
    assert local_paths("mount /mnt/lab/bench") == ["/mnt/lab/bench"]
    # No allow-list: even hal0's default model store is named by tool, not by path.
    assert local_paths("the default store /mnt/ai-models/flm/models") == [
        "/mnt/ai-models/flm/models"
    ]
    assert local_paths("see /var/lib/hal0/.hermes and /etc/hal0/agents and https://x/home/y") == []
    assert local_paths("a /rootless install and /optional and /srvx") == []


def test_path_check_catches_home_relative_and_other_local_roots() -> None:
    assert local_paths("cd ~/work and $HOME/.config and ${HOME}/x") == ["~/", "$HOME", "${HOME}"]
    assert local_paths("open file:///home/someone/x") == ["file:///home"]
    assert local_paths("/Users/someone/x, /opt/tools/bin and /srv/data") == [
        "/Users/someone/x",
        "/opt/tools/bin",
        "/srv/data",
    ]
    assert local_paths("$HERMES_HOME/.env and /var/lib/hal0/.hermes") == []


def test_systemctl_user_check() -> None:
    assert systemctl_user("run systemctl --user restart x") == ["systemctl --user"]
    assert systemctl_user("run systemctl -q --user status y") == ["systemctl -q --user"]
    # `--user` after the verb, and the same for journalctl, in either order.
    assert systemctl_user("run systemctl restart --user x") == ["systemctl restart --user"]
    assert systemctl_user("systemctl status hal0-agent@hermes --user") == [
        "systemctl status hal0-agent@hermes --user"
    ]
    assert systemctl_user("journalctl --user -u x") == ["journalctl --user"]
    assert systemctl_user("journalctl -u x -n 50 --user") == ["journalctl -u x -n 50 --user"]
    assert systemctl_user("systemctl status x\njournalctl -u x") == []
    assert (
        systemctl_user("units are system units, so there is no `--user`; systemctl status x") == []
    )


def test_address_check_allows_loopback_and_documentation_ranges_only() -> None:
    assert foreign_addresses("curl http://127.0.0.1:8080 and 192.0.2.10 and 203.0.113.7") == []
    assert foreign_addresses("ssh 10.0.1.142, gateway 149.154.166.110") == [
        "10.0.1.142",
        "149.154.166.110",
    ]


def test_address_check_does_not_fire_on_version_strings() -> None:
    assert foreign_addresses("hal0 v1.8.2.1 and build1.2.3.4 and x1.2.3.4") == []
    assert foreign_addresses("the five-part 1.2.3.4.5 is not an address") == []
    assert foreign_addresses("a bare 10.0.1.142. at a sentence end") == ["10.0.1.142"]


def test_tool_check_catches_a_name_the_server_does_not_register() -> None:
    tools = {"hal0_admin": {"slot_list"}}
    assert unknown_tools("`mcp__hal0_admin__slot_list`", tools) == []
    assert unknown_tools("mcp__hal0_admin__no_such_tool", tools) == [
        "mcp__hal0_admin__no_such_tool (server registers no such tool)"
    ]
    assert unknown_tools("mcp__hal0-admin__slot_list", tools) == []
    assert unknown_tools("mcp__local_mcp_slot_list", tools) == []  # not a namespace at all
    assert unknown_tools("mcp__other__slot_list", tools) == [
        "mcp__other__slot_list (no such server)"
    ]
    assert unknown_tools("the prefix `mcp__hal0_admin__` alone", tools) == []
