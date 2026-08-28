"""``repair_hermes_mcp_headers`` — heal a pre-#2088 hermes config on upgrade (#2090).

#2088 fixed the ``X-hal0-Private`` header where hermes is *provisioned*: the
value must reach ``config.yaml`` as the string ``"1"``, because the unquoted
YAML int wedges hermes' MCP client immediately after ``initialize`` (a 40 s
hang, then ``CancelledError``, with the agent silently left holding zero
memory tools).

``hal0 update`` never re-provisions hermes, so that fix reached fresh installs
only: every box provisioned before it keeps the poisoned int through any number
of upgrades. Measured on the fleet — a box upgraded rc.10 → rc.11 still failed
its live client at 41.6 s, and prod on rc.9 fails at 40.7 s — while a fresh
rc.11 install connects in 51 ms with 26 tools.

The documented manual workaround (``hal0 agent reprovision hermes``) is not a
dependable substitute: it exits 0 even when it fails (#2092). So the repair
belongs in the migration sequence both upgrade paths already converge on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hal0.updater.updater import repair_hermes_mcp_headers

POISONED = """\
mcp_servers:
  hal0-admin:
    type: http
    url: http://127.0.0.1:8080/mcp/admin/mcp
    headers:
      X-hal0-Agent: hermes
  hal0-memory:
    type: http
    url: http://127.0.0.1:8080/mcp/memory/mcp
    headers:
      X-hal0-Agent: hermes
      X-hal0-Private: 1
agent:
  name: Hermes
"""


def _write(tmp_path: Path, text: str) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text, encoding="utf-8")
    return cfg


def test_quotes_a_poisoned_private_header(tmp_path: Path) -> None:
    cfg = _write(tmp_path, POISONED)

    changed = repair_hermes_mcp_headers(config_path=cfg, restart_gateway=False)

    assert changed is True
    loaded = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    hdrs = loaded["mcp_servers"]["hal0-memory"]["headers"]
    assert hdrs["X-hal0-Private"] == "1"
    assert isinstance(hdrs["X-hal0-Private"], str)


def test_is_a_no_op_on_an_already_correct_config(tmp_path: Path) -> None:
    """A converged box must not be rewritten — no write, no gateway bounce."""
    cfg = _write(tmp_path, POISONED.replace("X-hal0-Private: 1", "X-hal0-Private: '1'"))
    before = cfg.read_text(encoding="utf-8")

    changed = repair_hermes_mcp_headers(config_path=cfg, restart_gateway=False)

    assert changed is False
    assert cfg.read_text(encoding="utf-8") == before


def test_preserves_every_other_setting(tmp_path: Path) -> None:
    cfg = _write(tmp_path, POISONED)

    repair_hermes_mcp_headers(config_path=cfg, restart_gateway=False)

    loaded = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert loaded["agent"]["name"] == "Hermes"
    assert loaded["mcp_servers"]["hal0-admin"]["url"] == "http://127.0.0.1:8080/mcp/admin/mcp"
    assert loaded["mcp_servers"]["hal0-memory"]["headers"]["X-hal0-Agent"] == "hermes"


@pytest.mark.parametrize(
    ("literal", "expected"),
    [("1", "1"), ("0", "0"), ("true", "true"), ("false", "false"), ("3.5", "3.5")],
)
def test_coerces_any_non_string_header_value(tmp_path: Path, literal: str, expected: str) -> None:
    """Headers are strings by definition; any YAML scalar that survives as a
    non-string is the same poison, so none of them are left behind."""
    cfg = _write(tmp_path, POISONED.replace("X-hal0-Private: 1", f"X-hal0-Private: {literal}"))

    repair_hermes_mcp_headers(config_path=cfg, restart_gateway=False)

    val = yaml.safe_load(cfg.read_text(encoding="utf-8"))["mcp_servers"]["hal0-memory"]["headers"][
        "X-hal0-Private"
    ]
    assert val == expected
    assert isinstance(val, str)


def test_missing_config_is_a_quiet_no_op(tmp_path: Path) -> None:
    """A box with no hermes install must not fail its update."""
    assert (
        repair_hermes_mcp_headers(config_path=tmp_path / "absent.yaml", restart_gateway=False)
        is False
    )


def test_unparseable_config_is_a_quiet_no_op(tmp_path: Path) -> None:
    """Never let a hand-edited config abort the migration sequence."""
    cfg = _write(tmp_path, "mcp_servers: [this is not: a mapping\n")
    before = cfg.read_text(encoding="utf-8")

    assert repair_hermes_mcp_headers(config_path=cfg, restart_gateway=False) is False
    assert cfg.read_text(encoding="utf-8") == before


def test_runs_as_part_of_the_post_activation_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of #2090: the repair must be reached by the sequence
    ``hal0 update`` and install.sh both call, not only by provisioning."""
    from hal0.updater import updater as mod

    seen: list[str | None] = []
    monkeypatch.setattr(
        mod, "repair_hermes_mcp_headers", lambda **kw: seen.append(kw.get("job_id")) or False
    )
    for name in (
        "ensure_seed_profiles",
        "clear_stale_mtp_overrides",
        "relabel_stale_vulkan_slots",
        "retag_stale_slot_images",
        "sanitize_model_extra_args",
    ):
        monkeypatch.setattr(mod, name, lambda **kw: 0)
    monkeypatch.setattr(mod, "_maybe_run_config_migrations", lambda *a, **kw: (1, 2))

    mod.run_post_activation_migrations(1, job_id="job-2090")

    assert seen == ["job-2090"]


def test_a_failing_repair_never_blocks_the_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """Best-effort, like every other data-cleanup pass: a raise here must not
    abort an activation that has already swapped the tree."""
    from hal0.updater import updater as mod

    def _boom(**kw):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(mod, "repair_hermes_mcp_headers", _boom)
    for name in (
        "ensure_seed_profiles",
        "clear_stale_mtp_overrides",
        "relabel_stale_vulkan_slots",
        "retag_stale_slot_images",
        "sanitize_model_extra_args",
    ):
        monkeypatch.setattr(mod, name, lambda **kw: 0)
    monkeypatch.setattr(mod, "_maybe_run_config_migrations", lambda *a, **kw: (1, 2))

    assert mod.run_post_activation_migrations(1, job_id="job-2090") == (1, 2)


def test_bounces_the_gateway_only_when_it_changed_something(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The repair only reaches the RUNNING agent after a restart — hermes reads
    config.yaml at start. A converged box must not be bounced for nothing."""
    from hal0.updater import updater as mod

    ran: list[list[str]] = []
    monkeypatch.setattr(mod.subprocess, "run", lambda argv, **kw: ran.append(argv))

    poisoned = _write(tmp_path, POISONED)
    assert repair_hermes_mcp_headers(config_path=poisoned) is True
    assert ran == [["systemctl", "restart", "hermes-gateway.service"]]

    ran.clear()
    assert repair_hermes_mcp_headers(config_path=poisoned) is False
    assert ran == []


def test_a_failing_bounce_still_counts_as_repaired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file rewrite is the durable half; if the bounce cannot run (the
    boot-time caller is unprivileged), the config still takes effect at the
    gateway's next restart, so the pass must not report failure."""
    from hal0.updater import updater as mod

    def _no_systemctl(argv, **kw):
        raise OSError("systemctl: permission denied")

    monkeypatch.setattr(mod.subprocess, "run", _no_systemctl)
    cfg = _write(tmp_path, POISONED)

    assert repair_hermes_mcp_headers(config_path=cfg) is True
    hdrs = yaml.safe_load(cfg.read_text(encoding="utf-8"))["mcp_servers"]["hal0-memory"]["headers"]
    assert hdrs["X-hal0-Private"] == "1"
