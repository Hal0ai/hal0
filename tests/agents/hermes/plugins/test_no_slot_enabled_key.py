"""The Hermes plugin trees must never carry a slot ``enabled`` key (#1369).

``SlotConfig.enabled`` was removed: activation is model-presence, and stopping a
slot is ``POST /api/slots/{name}/unload``. The audit confirmed both plugin trees
are already clean — but "we grepped and found nothing" is an absence, not a
guarantee. These plugins are the surface most likely to silently regress it:
they are the only hal0 code that runs OUTSIDE hal0's venv (inside Hermes), they
ship as a byte-identical installer seed, and they talk to hal0 over REST where a
stale ``enabled`` in a payload would be accepted as `extra="allow"` debris
rather than rejected.

This turns the absence into an assertion, so a hand-edit or a copy-paste from
pre-#1369 code fails a test instead of shipping.

Scope note: this is a source-text check over the plugin trees, deliberately
coarse. It is NOT a check on hal0's own ``enabled`` uses elsewhere — several are
legitimate and unrelated (``BrainChatConfig.enabled``, ``mcp/admin.py``'s
upstream-provider routing kill-switch, ``CapabilitySelection.enabled`` in
capabilities.toml). Only these two trees are pinned.

Targeted file run:
    uv run pytest tests/agents/hermes/plugins/test_no_slot_enabled_key.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]

#: Both copies of both plugins. The installer seeds are separately parity-locked
#: to their canonical sources by ``test_hal0_provider_parity.py`` /
#: ``tests/agents/hermes_plugins/test_seed_parity.py``, but they are asserted
#: here too: a regression introduced in BOTH copies at once would satisfy parity
#: while still shipping the stale key.
_PLUGIN_TREES: list[Path] = [
    _REPO_ROOT / "src" / "hal0" / "agents" / "hermes" / "plugins",
    _REPO_ROOT / "installer" / "agents" / "hermes" / "plugins",
]

_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".toml"}


def _plugin_files() -> list[Path]:
    out: list[Path] = []
    for tree in _PLUGIN_TREES:
        assert tree.is_dir(), f"plugin tree moved or was renamed: {tree}"
        out.extend(
            p
            for p in sorted(tree.rglob("*"))
            if p.is_file() and p.suffix in _SUFFIXES and "__pycache__" not in p.parts
        )
    return out


def test_plugin_trees_are_discovered() -> None:
    """Guard the guard: an empty file list would make every check below vacuous."""
    files = _plugin_files()
    assert len(files) >= 8, [str(p) for p in files]
    names = {p.name for p in files}
    assert {"profile.py", "provider.py", "plugin.yaml"} <= names, sorted(names)


@pytest.mark.parametrize("path", _plugin_files(), ids=lambda p: str(p.relative_to(_REPO_ROOT)))
def test_no_enabled_token_in_plugin_source(path: Path) -> None:
    """No ``enabled`` anywhere in either Hermes plugin tree.

    A hit is not automatically a bug — but it IS a decision that needs a human,
    because the only ``enabled`` these plugins could plausibly mean is the slot
    field that no longer exists. If a genuinely unrelated ``enabled`` is ever
    needed here, add it to an explicit allowlist in this test with a reason.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    offenders = [
        f"{path.relative_to(_REPO_ROOT)}:{n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), start=1)
        if "enabled" in line
    ]
    assert not offenders, (
        "slot activation is model-presence since #1369 — bind or clear "
        "'[model].default' instead, and use POST /api/slots/{name}/unload to "
        "stop a slot:\n" + "\n".join(offenders)
    )


def test_provider_profile_payload_carries_no_enabled() -> None:
    """The live ``Hal0ProviderProfile`` instance exposes no ``enabled`` attribute.

    Complements the source scan with a behavioural check on the constructed
    object: the profile is what Hermes registers and reads fields off, so a
    dataclass field (or a vendored-ABC drift) reintroducing ``enabled`` would be
    caught even if it arrived via the upstream base class rather than this repo.
    """
    from hal0.agents.hermes.plugins.provider_hal0.profile import Hal0ProviderProfile

    profile = Hal0ProviderProfile()
    assert not hasattr(profile, "enabled"), (
        "ProviderProfile grew an 'enabled' field — confirm it is not the removed "
        "slot activation flag before allowlisting it"
    )
    # The discovery headers are the one payload this profile puts on the wire.
    headers = profile._discovery_headers()
    assert not any("enabled" in k.lower() for k in headers), headers
