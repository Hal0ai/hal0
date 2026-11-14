"""``hal0 memory knowledge rehome``: flags, exit codes and output.

The work is :class:`hal0.memory.knowledge_rehome.KnowledgeRehome`, tested
against the fold-in's fake engine in ``tests/memory/test_knowledge_rehome.py``;
here the engine call is replaced, and only the command's own job is checked.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import memory_knowledge_commands as cmd
from hal0.cli.main import app
from hal0.memory.knowledge_rehome import RehomeRefused

runner = CliRunner()

PLAN: dict[str, Any] = {
    "bank": "Hal0::Agents",
    "target": "hal0:shared",
    "project": "hal0",
    "applied": False,
    "refusals": [],
    "nodes": {
        "total": 32,
        "pages": 31,
        "folders": 1,
        "top_level": 6,
        "to_move": 6,
        "already_placed": 0,
        "in_moving_subtrees": 32,
    },
    "folder": {"name": "project:hal0", "id": "kf-1", "exists": True},
    "tops": [{"shown_name": "Initiatives", "kind": "folder", "state": "move"}],
    "renames": [{"id": "kp-1", "from": "Core concepts", "to": "Core concepts (Hal0::Agents)"}],
    "owner": {"given": "claude-code", "owners": {"claude-code": 31}, "pseudo_owned": []},
    "content": {"cleared": 31, "kept": False},
    "keep_content": {"allowed": True, "private_facts": 0, "why": "no private facts"},
    "page_map": {"path": "/x/map.json", "rows": 32, "to_write": 32},
    "changes": 99,
}


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[bool, dict[str, Any]]]:
    seen: list[tuple[bool, dict[str, Any]]] = []

    def drive(apply: bool, **kwargs: Any) -> dict[str, Any]:
        seen.append((apply, kwargs))
        return {**PLAN, "applied": apply, "changed": apply, "verified": apply}

    monkeypatch.setattr(cmd, "_drive", drive)
    return seen


ARGS = ["memory", "knowledge", "rehome", "--bank", "Hal0::Agents", "--project", "hal0"]


def test_the_default_is_a_dry_run_that_says_how_to_apply(calls: list[Any]) -> None:
    out = runner.invoke(app, [*ARGS, "--owner", "claude-code"])
    assert out.exit_code == 0, out.output
    assert calls == [
        (
            False,
            {
                "bank": "Hal0::Agents",
                "project": "hal0",
                "owner": "claude-code",
                "keep_content": False,
            },
        )
    ]
    assert "--apply" in out.output and "Core concepts (Hal0::Agents)" in out.output


def test_apply_and_keep_content_are_passed_on(calls: list[Any]) -> None:
    out = runner.invoke(app, [*ARGS, "--apply", "--keep-content", "--json"])
    assert out.exit_code == 0, out.output
    assert calls[0][0] is True and calls[0][1]["keep_content"] is True
    assert json.loads(out.output)["verified"] is True


def test_a_plan_with_a_refusal_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    refused = {**PLAN, "refusals": [{"code": "not_two_bank", "message": "not two_bank"}]}
    monkeypatch.setattr(cmd, "_drive", lambda apply, **kw: refused)
    out = runner.invoke(app, ARGS)
    assert out.exit_code == 1
    assert "not_two_bank" in out.output


def test_a_refused_apply_exits_1_with_the_plan_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def drive(apply: bool, **kw: Any) -> dict[str, Any]:
        raise RehomeRefused("run this as root (sudo)", plan={**PLAN, "applied": False})

    monkeypatch.setattr(cmd, "_drive", drive)
    out = runner.invoke(app, [*ARGS, "--apply", "--json"])
    assert out.exit_code == 1
    body = json.loads(out.output)
    assert body["status"] == "refused" and body["error"] == "run this as root (sudo)"
    assert body["nodes"]["total"] == 32


def test_bank_and_project_are_required() -> None:
    out = runner.invoke(app, ["memory", "knowledge", "rehome", "--bank", "Hal0::Agents"])
    assert out.exit_code == 2


def test_a_node_written_after_the_fold_in_is_named_as_staying_in_the_old_bank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    late = {"old_id": "kp-late", "kind": "page", "name": "NPU exclusion (#3666)"}
    monkeypatch.setattr(cmd, "_drive", lambda apply, **_kw: {**PLAN, "not_folded": [late]})
    out = runner.invoke(app, ARGS)
    assert out.exit_code == 0, out.output
    flat = " ".join(out.output.split())
    assert "NPU exclusion (#3666)" in flat and "kp-late" in flat
    assert "stay in the old bank" in flat
