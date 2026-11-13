"""Tests for hal0.memory.spec_apply — mocked HTTP, no live Hindsight needed."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from hal0.config.schema import MemoryConfig
from hal0.memory import bank_layout as bl
from hal0.memory import spec_apply as sa

#: A legacy split store: the platform bank and Hermes' own private bank.
TARGETS = sa.SpecTargets(banks={"platform": "shared", "private": "private__hermes"})


def _spec_dir(tmp_path, banks=None, config_extra=None, knowledge=None, templates=None):
    d = tmp_path / "spec"
    (d / "templates").mkdir(parents=True)
    banks = banks or {"platform": "shared"}
    (d / "banks.json").write_text(
        json.dumps(
            {"banks": banks, "config_extra": config_extra or {}, "knowledge": knowledge or {}}
        )
    )
    for tname in set(banks.values()):
        body = (templates or {}).get(tname) or {"version": "1", "name": tname}
        (d / "templates" / f"{tname}.json").write_text(json.dumps(body))
    return d


def _handler_factory(calls, bodies=None):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if bodies is not None and request.content:
            bodies.append((str(request.url), json.loads(request.content)))
        if request.url.path.endswith("/import"):
            return httpx.Response(200, json={"ok": True})
        if request.url.path.endswith("/config"):
            return httpx.Response(200, json={"ok": True})
        if request.url.path.endswith("/knowledge-base/tree"):
            return httpx.Response(200, json={"nodes": []})
        if "/knowledge-base/folders" in request.url.path:
            return httpx.Response(200, json={"id": "folder-1", "name": "Showcase"})
        if "/knowledge-base/pages" in request.url.path:
            return httpx.Response(200, json={"id": "page-1"})
        return httpx.Response(404, json={})

    return handler


def test_load_spec_dir_explicit(tmp_path):
    d = _spec_dir(tmp_path)
    assert sa.load_spec_dir(d) == d
    assert sa.load_spec(d)["banks"] == {"platform": "shared"}


def test_load_spec_dir_packaged_default():
    d = sa.load_spec_dir(None)
    assert (d / "banks.json").exists()
    spec = sa.load_spec(None)
    assert "banks" in spec and isinstance(spec["banks"], dict)


def test_the_packaged_spec_names_roles_not_banks():
    """ADR-0058 decision 8: no hard-coded ``shared`` bank."""
    spec = sa.load_spec(None)
    assert spec["banks"] == {"platform": "shared", "private": "private"}
    assert set(spec["knowledge"]) <= {"platform", "private"}
    assert set(spec["config_extra"]) <= {"platform", "private"}


def test_the_packaged_scopes_per_bank():
    """``hal0:shared`` consolidates per project; ``hal0:private`` per agent,
    never across agents."""
    shared = sa.load_template(None, "shared")["bank"]["default_observation_scopes"]
    private = sa.load_template(None, "private")["bank"]["default_observation_scopes"]
    assert shared == [["project:*"]]
    assert private == [["agent:*"]]


def test_apply_spec_dry_run_plans_only(tmp_path):
    d = _spec_dir(tmp_path)
    calls: list[tuple[str, str]] = []
    client = httpx.Client(transport=httpx.MockTransport(_handler_factory(calls)))
    report = sa.apply_spec(targets=TARGETS, api="http://x", spec_dir=d, dry_run=True, client=client)
    assert report.dry_run is True
    assert len(report.banks) == 1
    br = report.banks[0]
    assert (br.bank, br.role) == ("shared", "platform")
    assert br.plan == {"ok": True}
    assert br.applied is None
    assert br.error is None
    # Only the dry-run import call was made.
    assert [m for m, _ in calls] == ["POST"]


def test_apply_spec_real_applies_config_and_kb(tmp_path):
    d = _spec_dir(
        tmp_path,
        config_extra={"platform": {"retention_days": 30}},
        knowledge={
            "platform": {
                "folder": "Showcase",
                "pages": [{"name": "Overview", "source_query": "showcase", "tags": ["showcase"]}],
            }
        },
    )
    calls: list[tuple[str, str]] = []
    client = httpx.Client(transport=httpx.MockTransport(_handler_factory(calls)))
    report = sa.apply_spec(
        targets=TARGETS, api="http://x", spec_dir=d, dry_run=False, client=client
    )
    br = report.banks[0]
    assert br.error is None
    assert br.applied == {"ok": True}
    assert br.config_updated == ["retention_days"]
    assert br.folder_created == "Showcase"
    assert br.pages_created == ["Overview"]
    assert br.pages_updated == []


def test_apply_spec_updates_existing_page(tmp_path):
    d = _spec_dir(
        tmp_path,
        knowledge={
            "platform": {
                "folder": "Showcase",
                "pages": [{"name": "Overview", "source_query": "showcase"}],
            }
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/knowledge-base/tree"):
            return httpx.Response(
                200,
                json={
                    "nodes": [
                        {"id": "f1", "name": "Showcase", "type": "folder"},
                        {"id": "p1", "name": "Overview", "type": "page"},
                    ]
                },
            )
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = sa.apply_spec(
        targets=TARGETS, api="http://x", spec_dir=d, dry_run=False, client=client
    )
    br = report.banks[0]
    assert br.pages_updated == ["Overview"]
    assert br.pages_created == []
    assert br.folder_created is None


def test_apply_spec_updates_existing_page_through_its_node(tmp_path):
    """#3564: a 1.x engine changes a page with PATCH .../knowledge-base/nodes/{node_id}.

    The tree is the engine's own shape (``roots``, ``kind``, the page's node id
    distinct from its ``mental_model_id``); the PATCH goes to the node id with
    only UpdateNodeRequest fields.
    """
    page = {
        "name": "Overview",
        "source_query": "showcase",
        "tags": ["showcase"],
        "max_tokens": 1024,
        "trigger": {"refresh_after_consolidation": True},
    }
    d = _spec_dir(tmp_path, knowledge={"platform": {"folder": "Showcase", "pages": [page]}})
    writes: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/knowledge-base/tree"):
            return httpx.Response(
                200,
                json={
                    "roots": [
                        {
                            "id": "kf-1",
                            "kind": "folder",
                            "name": "Showcase",
                            "mental_model_id": None,
                            "children": [
                                {
                                    "id": "kp-1",
                                    "kind": "page",
                                    "name": "Overview",
                                    "mental_model_id": "mm-1",
                                    "children": [],
                                }
                            ],
                        }
                    ]
                },
            )
        if "/knowledge-base/" in request.url.path:
            writes.append(
                (request.method, request.url.path, json.loads(request.content or b"null"))
            )
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = sa.apply_spec(
        targets=TARGETS, api="http://x", spec_dir=d, dry_run=False, client=client
    )
    br = report.banks[0]
    assert br.error is None
    assert br.pages_updated == ["Overview"]
    assert writes == [
        (
            "PATCH",
            "/v1/default/banks/shared/knowledge-base/nodes/kp-1",
            {
                "source_query": "showcase",
                "tags": ["showcase"],
                "max_tokens": 1024,
                "trigger": {"refresh_after_consolidation": True},
            },
        )
    ]


@pytest.mark.parametrize("bank_filter", ["private", "private__hermes"])
def test_apply_spec_bank_filter_by_role_or_bank(tmp_path, bank_filter):
    d = _spec_dir(tmp_path, banks={"platform": "shared", "private": "shared"})
    calls: list[tuple[str, str]] = []
    client = httpx.Client(transport=httpx.MockTransport(_handler_factory(calls)))
    report = sa.apply_spec(
        targets=TARGETS,
        api="http://x",
        spec_dir=d,
        dry_run=True,
        bank_filter=bank_filter,
        client=client,
    )
    assert [b.bank for b in report.banks] == ["private__hermes"]


def test_apply_spec_http_error_captured_per_bank(tmp_path):
    d = _spec_dir(tmp_path, banks={"platform": "shared", "private": "shared"})

    def handler(request: httpx.Request) -> httpx.Response:
        if "/private__hermes/" in str(request.url):
            return httpx.Response(500, json={"detail": "boom"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = sa.apply_spec(targets=TARGETS, api="http://x", spec_dir=d, dry_run=True, client=client)
    by_bank = {b.bank: b for b in report.banks}
    assert by_bank["shared"].error is None
    assert by_bank["private__hermes"].error is not None
    assert "500" in by_bank["private__hermes"].error or "Server" in by_bank["private__hermes"].error


def test_report_to_dict_roundtrips():
    br = sa.BankReport(bank="shared", template="shared", dry_run=True, plan={"a": 1})
    report = sa.SpecApplyReport(api="http://x", dry_run=True, banks=[br])
    d = report.to_dict()
    assert d["api"] == "http://x"
    assert d["banks"][0]["bank"] == "shared"
    assert d["banks"][0]["refused"] == []


# ── the roles on this box (ADR-0058 decisions 7 and 8) ─────────────────────────


def _layout(monkeypatch, layout: bl.BankLayout) -> None:
    monkeypatch.setattr(bl, "current_layout", lambda _cfg, **_kw: layout)


def test_a_two_bank_store_targets_the_configured_banks(monkeypatch):
    _layout(monkeypatch, bl.BankLayout(bl.LAYOUT_TWO_BANK))
    targets = sa.spec_targets(MemoryConfig(bank="a:shared", private_bank="a:private"))
    assert targets.banks == {"platform": "a:shared", "private": "a:private"}
    assert (targets.platform_holds_private, targets.private_holds_every_agent) == (False, True)


@pytest.mark.parametrize("unified", [True, False])
def test_a_legacy_store_targets_its_recorded_bank_never_a_two_bank_one(monkeypatch, unified):
    _layout(
        monkeypatch,
        bl.BankLayout(bl.LAYOUT_LEGACY, legacy_platform_bank="shared", legacy_unified=unified),
    )
    targets = sa.spec_targets(MemoryConfig())
    assert targets.banks == {"platform": "shared", "private": "private__hermes"}
    assert targets.platform_holds_private is unified
    assert targets.private_holds_every_agent is False


def _apply(targets: sa.SpecTargets, templates: dict[str, Any], tmp_path) -> tuple[Any, list]:
    d = _spec_dir(tmp_path, banks={"platform": "shared", "private": "private"}, templates=templates)
    calls: list[tuple[str, str]] = []
    bodies: list[tuple[str, Any]] = []
    client = httpx.Client(transport=httpx.MockTransport(_handler_factory(calls, bodies)))
    report = sa.apply_spec(
        targets=targets, api="http://x", spec_dir=d, dry_run=False, client=client
    )
    return report, bodies


TEMPLATES = {
    "shared": {
        "version": "1",
        "bank": {"retain_mission": "m", "default_observation_scopes": [["project:*"]]},
        "mental_models": [{"id": "prefs", "name": "Prefs", "source_query": "q"}],
    },
    "private": {
        "version": "1",
        "bank": {"default_observation_scopes": [["agent:*"]]},
        "mental_models": [{"id": "ctx", "name": "Context", "source_query": "q"}],
    },
}


def test_a_legacy_unified_bank_refuses_the_per_project_scope(tmp_path):
    """#3547: that bank holds every agent's private facts, and a private
    write there is sent with no scope of its own."""
    targets = sa.SpecTargets(
        banks={"platform": "shared", "private": "private__hermes"}, platform_holds_private=True
    )
    report, bodies = _apply(targets, TEMPLATES, tmp_path)
    platform = next(b for b in report.banks if b.role == "platform")
    assert platform.refused == ["default_observation_scopes"]
    imported = [body for url, body in bodies if "/shared/import" in url]
    assert imported and all("default_observation_scopes" not in b["bank"] for b in imported)
    assert all(b["bank"]["retain_mission"] == "m" for b in imported)
    assert all(b.get("mental_models") for b in imported)


def test_a_legacy_split_bank_takes_the_whole_template(tmp_path):
    report, bodies = _apply(TARGETS, TEMPLATES, tmp_path)
    assert all(b.refused == [] for b in report.banks)
    imported = [body for url, body in bodies if "/shared/import" in url]
    assert all(b["bank"]["default_observation_scopes"] == [["project:*"]] for b in imported)


def test_the_two_bank_private_bank_gets_no_mental_models(tmp_path):
    """A mental model is written from the whole bank, and ``hal0:private``
    holds every agent's private memory."""
    targets = sa.SpecTargets(
        banks={"platform": "hal0:shared", "private": "hal0:private"},
        private_holds_every_agent=True,
    )
    report, bodies = _apply(targets, TEMPLATES, tmp_path)
    by_role = {b.role: b for b in report.banks}
    assert by_role["private"].refused == ["mental_models"]
    assert by_role["platform"].refused == []
    private = [body for url, body in bodies if "/hal0%3Aprivate/import" in url]
    assert private and all("mental_models" not in b for b in private)
    assert all(b["bank"]["default_observation_scopes"] == [["agent:*"]] for b in private)
    shared = [body for url, body in bodies if "/hal0%3Ashared/import" in url]
    assert shared and all(
        b["bank"]["default_observation_scopes"] == [["project:*"]] for b in shared
    )


def test_the_cli_refuses_a_bank_that_is_not_this_boxs(monkeypatch):
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from hal0.cli import memory_spec_commands as cmd
    from hal0.config import loader

    monkeypatch.setattr(loader, "load_hal0_config", lambda: SimpleNamespace(memory=MemoryConfig()))
    _layout(monkeypatch, bl.BankLayout(bl.LAYOUT_LEGACY, legacy_platform_bank="shared"))
    called: list[Any] = []
    monkeypatch.setattr(cmd, "apply_spec", lambda **kw: called.append(kw))
    result = CliRunner().invoke(cmd.app, ["--bank", "hal0:shared"])
    assert result.exit_code == 1
    assert "not one of this box's memory banks" in result.output
    assert called == []
