"""Tests for ``hal0.memory.model_owner_repair`` (#3576).

The repair sets ``trigger.exclude_mental_models`` on mental models whose
scope is otherwise confined to one agent, so the #3575 read rule lets that
agent read them again. The fake below stands in for hal0-api's admin memory
routes (``/api/memory/banks/...``), which forward to a Hindsight 1.2.0 engine.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.memory.model_owner_repair import (
    REPAIR_COMMAND,
    apply_repairs,
    model_owner,
    survey,
)
from hal0.memory.read_scope import model_scope_confined_to

PRIVATE = "visibility:private"


class FakeAdmin:
    """hal0-api's admin memory routes over an in-memory 1.2.0 engine."""

    def __init__(self) -> None:
        self.banks: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, Any, Any]] = []
        self.page = 2  # small pages, so paging is exercised

    def bank(self, bank: str, *, models: list[dict[str, Any]], docs: list[list[str]]) -> None:
        self.banks[bank] = {
            "models": {m["id"]: {"content": f"old {m['id']}", **m} for m in models},
            "docs": [{"id": f"d{i}", "tags": t} for i, t in enumerate(docs)],
        }

    def __call__(self, method: str, path: str, *, params: Any = None, json: Any = None) -> Any:
        self.calls.append((method, path, params, json))
        parts = path.split("/")
        assert parts[:4] == ["", "api", "memory", "banks"], path
        params = params or {}
        if len(parts) == 4:
            ids = sorted(self.banks)
            off, lim = params.get("offset", 0), params.get("limit", 100)
            return {"banks": [{"bank_id": b} for b in ids[off : off + lim]], "total": len(ids)}
        bank = self.banks[parts[4].replace("%3A", ":")]
        if parts[5] == "documents":
            assert params.get("tags") == [PRIVATE] and params.get("tags_match") == "all_strict"
            rows = [d for d in bank["docs"] if PRIVATE in (d["tags"] or [])]
            off, lim = params.get("offset", 0), params.get("limit", 100)
            return {"items": rows[off : off + lim], "total": len(rows)}
        assert parts[5] == "mental-models"
        if len(parts) == 6:
            assert "detail" not in params  # the default, metadata
            # 1.2.0 lists metadata only: no content, and trigger: null.
            rows = [{**r, "trigger": None, "content": None} for r in bank["models"].values()]
            off, lim = params.get("offset", 0), min(params.get("limit", 100), self.page)
            return {"items": rows[off : off + lim], "total": len(rows)}
        model = bank["models"][parts[6]]
        if method == "GET" and len(parts) == 7:
            return {"trigger": {}, **model}
        if method == "PATCH":
            # 1.2.0 merges a trigger patch over the stored trigger.
            model["trigger"] = {**(model.get("trigger") or {}), **json["trigger"]}
            return dict(model)
        if method == "POST" and parts[7:] == ["clear"]:
            model["content"] = ""
            return dict(model)
        if method == "POST" and parts[7:] == ["refresh"]:
            return {"operation_id": f"op-{model['id']}"}
        raise AssertionError(f"unexpected {method} {path}")

    def writes(self) -> list[tuple[str, str, Any]]:
        return [(m, p, j) for m, p, _q, j in self.calls if m != "GET"]


def _mm(mid: str, tags: list[str], trigger: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": mid, "name": mid.upper(), "tags": tags}
    if trigger is not None:
        out["trigger"] = trigger
    return out


# ── which models are an agent's own ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("model", "owner"),
    [
        (_mm("a", ["agent:alice"]), "alice"),  # tags, default all_strict
        (_mm("a", ["agent:alice", "project:x"]), "alice"),
        (_mm("a", ["agent:alice"], {"tags_match": "all_strict"}), "alice"),
        (_mm("a", ["agent:alice"], {"tags_match": "exact"}), "alice"),
        (_mm("a", ["agent:alice"], {"tags_match": "any_strict"}), "alice"),
        (_mm("a", ["agent:alice", PRIVATE]), "alice"),
        (
            _mm("a", [], {"tag_groups": [{"tags": ["agent:alice"], "match": "all_strict"}]}),
            "alice",
        ),
        # Not confined to one agent: never touched.
        (_mm("a", []), None),  # whole bank
        (_mm("a", ["project:x"]), None),  # project-wide
        (_mm("a", ["agent:alice"], {"tags_match": "any"}), None),  # admits untagged
        (_mm("a", ["agent:alice"], {"tags_match": "all"}), None),
        (_mm("a", ["agent:alice", "project:x"], {"tags_match": "any_strict"}), None),
        (_mm("a", ["agent:alice"], {"tag_groups": []}), None),  # [] is the whole bank
        (_mm("a", ["agent:alice", "agent:bob"]), None),  # two agents
        (_mm("a", ["agent:unknown"]), None),  # no identity
        # A private model tagged for bob first is bob's to read, not alice's.
        (_mm("a", [PRIVATE, "agent:bob"], {"tag_groups": [{"tags": ["agent:alice"]}]}), None),
    ],
)
def test_model_owner(model, owner):
    assert model_owner(model) == owner


def test_model_owner_is_the_read_rule_once_flagged():
    model = _mm("a", ["agent:alice"])
    assert not model_scope_confined_to(model, "alice")
    flagged = {**model, "trigger": {"exclude_mental_models": True}}
    assert model_scope_confined_to(flagged, "alice")


# ── survey ──────────────────────────────────────────────────────────────────


def _engine() -> FakeAdmin:
    admin = FakeAdmin()
    admin.bank(
        "shared",
        models=[
            _mm("alice-own", ["agent:alice"]),
            _mm("bob-own", ["agent:bob"], {"mode": "delta"}),
            _mm("alice-done", ["agent:alice"], {"exclude_mental_models": True}),
            _mm("whole", []),
            _mm("project", ["project:hal0"]),
        ],
        docs=[[PRIVATE, "agent:alice"], ["agent:bob"], []],
    )
    admin.bank("hal0:other", models=[_mm("carol-own", ["agent:carol"])], docs=[])
    return admin


def test_survey_lists_unflagged_own_models_and_which_are_hidden():
    report = survey(_engine())
    found = {(f.bank, f.model_id): f for f in report.findings}
    assert set(found) == {
        ("shared", "alice-own"),
        ("shared", "bob-own"),
        ("hal0:other", "carol-own"),
    }
    # shared holds alice's private memory: bob cannot read his model, alice can.
    assert found[("shared", "bob-own")].hidden is True
    assert found[("shared", "alice-own")].hidden is False
    assert found[("hal0:other", "carol-own")].hidden is False
    assert found[("shared", "bob-own")].owner == "bob"
    assert report.hidden == 1
    assert report.errors == [] and report.skipped == [] and not report.incomplete


def test_survey_reads_each_metadata_row_on_its_stored_trigger():
    admin = _engine()
    report = survey(admin)
    # alice-done is flagged: its stored trigger says so, so it is not listed.
    assert ("shared", "alice-done") not in {(f.bank, f.model_id) for f in report.findings}
    gets = {p.rsplit("/", 1)[1] for m, p, _q, _j in admin.calls if m == "GET" and p.count("/") == 6}
    assert gets == {"alice-own", "bob-own", "alice-done", "whole", "project", "carol-own"}


def test_survey_one_bank_only():
    report = survey(_engine(), banks=["hal0:other"])
    assert [(f.bank, f.model_id) for f in report.findings] == [("hal0:other", "carol-own")]


def test_survey_private_banks_only_skips_banks_without_private_memory():
    admin = _engine()
    report = survey(admin, private_banks_only=True)
    assert {f.bank for f in report.findings} == {"shared"}
    assert not any("hal0%3Aother/mental-models" in p for _m, p, _q, _j in admin.calls)


def test_survey_private_doc_without_tags_counts_as_foreign():
    admin = FakeAdmin()
    admin.bank("b", models=[_mm("m", ["agent:alice"])], docs=[])
    admin.banks["b"]["docs"] = [{"id": "x", "tags": [PRIVATE]}]  # private, no owner
    assert survey(admin).findings[0].hidden is True


def test_survey_skips_a_model_whose_scope_admits_another_agents_private_doc():
    """F3: a private document owned by bob that also carries agent:alice (an
    old or direct engine write) is inside alice's scope: flagging alice's
    model would not confine it, so it is skipped and reported."""
    admin = FakeAdmin()
    admin.bank(
        "b",
        models=[_mm("alice-own", ["agent:alice"])],
        docs=[[PRIVATE, "agent:bob", "agent:alice"]],
    )
    report = survey(admin)
    assert report.findings == []
    [(finding, reason)] = report.skipped
    assert finding.model_id == "alice-own" and "agent:alice" in reason and "d0" in reason
    apply_repairs(admin, report.findings)
    assert admin.writes() == []


def test_survey_stops_at_its_deadline():
    now = [0.0]

    def clock() -> float:
        now[0] += 1.0
        return now[0]

    report = survey(_engine(), deadline=4.0, clock=clock)
    assert report.incomplete


def test_survey_bank_error_is_reported_not_raised():
    admin = _engine()

    def broken(method: str, path: str, **kw: Any) -> Any:
        if "hal0%3Aother" in path:
            raise RuntimeError("engine said 500")
        return admin(method, path, **kw)

    report = survey(broken)
    assert {f.bank for f in report.findings} == {"shared"}
    assert report.errors and report.errors[0][0] == "hal0:other"


def test_survey_writes_nothing():
    admin = _engine()
    survey(admin)
    assert admin.writes() == []


# ── apply ───────────────────────────────────────────────────────────────────


def _model(admin: FakeAdmin, bank: str, mid: str) -> dict[str, Any]:
    return admin.banks[bank]["models"][mid]


def test_hidden_delta_model_is_cleared_then_flagged_then_fully_refreshed():
    admin = _engine()
    findings = [f for f in survey(admin).findings if f.model_id == "bob-own"]
    [result] = apply_repairs(admin, findings)
    assert result.error is None and result.cleared and result.flagged
    base = "/api/memory/banks/shared/mental-models/bob-own"
    assert admin.writes() == [
        ("POST", f"{base}/clear", None),
        ("PATCH", base, {"trigger": {"exclude_mental_models": True}}),
        ("POST", f"{base}/refresh", {"mode": "full"}),
    ]
    bob = _model(admin, "shared", "bob-own")
    assert bob["trigger"] == {"mode": "delta", "exclude_mental_models": True}  # mode kept
    assert bob["content"] == ""
    assert model_scope_confined_to(bob, "bob")


def test_readable_model_is_not_cleared():
    admin = _engine()
    findings = [f for f in survey(admin).findings if f.model_id == "alice-own"]
    apply_repairs(admin, findings)
    assert [m for m, _p, _j in admin.writes()] == ["PATCH", "POST"]
    assert _model(admin, "shared", "alice-own")["content"] == "old alice-own"


def test_refresh_failure_leaves_a_hidden_model_empty_not_stale():
    admin = _engine()
    findings = [f for f in survey(admin).findings if f.model_id == "bob-own"]

    def no_refresh(method: str, path: str, **kw: Any) -> Any:
        if path.endswith("/refresh"):
            raise RuntimeError("503 engine busy")
        return admin(method, path, **kw)

    [result] = apply_repairs(no_refresh, findings)
    assert result.cleared and result.flagged
    assert "refresh failed" in (result.error or "") and "503" in (result.error or "")
    assert _model(admin, "shared", "bob-own")["content"] == ""


def test_no_refresh_still_clears_a_hidden_model():
    admin = _engine()
    findings = [f for f in survey(admin).findings if f.model_id == "bob-own"]
    [result] = apply_repairs(admin, findings, refresh=False)
    assert result.cleared and result.flagged and result.error is None
    assert [p.rsplit("/", 1)[1] for m, p, _j in admin.writes()] == ["clear", "bob-own"]
    assert _model(admin, "shared", "bob-own")["content"] == ""


def test_a_failed_clear_does_not_set_the_flag():
    admin = _engine()
    findings = [f for f in survey(admin).findings if f.model_id == "bob-own"]

    def no_clear(method: str, path: str, **kw: Any) -> Any:
        if path.endswith("/clear"):
            raise RuntimeError("409 busy")
        return admin(method, path, **kw)

    [result] = apply_repairs(no_clear, findings)
    assert "409" in (result.error or "") and not result.cleared and not result.flagged
    assert _model(admin, "shared", "bob-own")["trigger"] == {"mode": "delta"}


def test_apply_touches_only_the_findings_and_is_idempotent():
    admin = _engine()
    results = apply_repairs(admin, survey(admin).findings)
    assert all(r.error is None for r in results) and len(results) == 3
    assert _model(admin, "shared", "whole").get("trigger") is None
    assert _model(admin, "shared", "project").get("trigger") is None
    assert survey(admin).findings == []


def test_apply_reports_a_failed_write_and_goes_on():
    admin = _engine()
    findings = survey(admin).findings

    def flaky(method: str, path: str, **kw: Any) -> Any:
        if method == "PATCH" and path.endswith("/alice-own"):
            raise RuntimeError("409 busy")
        return admin(method, path, **kw)

    results = {r.finding.model_id: r for r in apply_repairs(flaky, findings)}
    assert "409 busy" in (results["alice-own"].error or "")
    assert results["bob-own"].error is None and results["carol-own"].error is None


def test_repair_command_name():
    assert REPAIR_COMMAND == "hal0 memory mm repair"
