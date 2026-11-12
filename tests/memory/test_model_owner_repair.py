"""Tests for ``hal0.memory.model_owner_repair`` (#3576).

The repair sets ``trigger.exclude_mental_models`` on mental models whose
scope is otherwise confined to one agent, so the #3575 read rule lets that
agent read them again. The fake below stands in for hal0-api's admin memory
routes (``/api/memory/banks/...``), which forward to the engine.
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
    """hal0-api's admin memory routes over an in-memory engine."""

    def __init__(self) -> None:
        self.banks: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, Any, Any]] = []
        self.page = 2  # small pages, so paging is exercised

    def bank(self, bank: str, *, models: list[dict[str, Any]], docs: list[list[str]]) -> None:
        self.banks[bank] = {
            "models": {m["id"]: m for m in models},
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
            rows = list(bank["models"].values())
            if params.get("detail", "metadata") == "metadata":
                # Hindsight 1.2.0 lists a model's trigger as null at the
                # default detail; only content/full carry it.
                rows = [{**r, "trigger": None} for r in rows]
            off, lim = params.get("offset", 0), min(params.get("limit", 100), self.page)
            return {"items": [dict(r) for r in rows[off : off + lim]], "total": len(rows)}
        model = bank["models"][parts[6]]
        if method == "PATCH":
            # The engine patches the trigger over the current one.
            model["trigger"] = {**(model.get("trigger") or {}), **json["trigger"]}
            return dict(model)
        if method == "POST" and parts[7:] == ["refresh"]:
            return {"operation_id": f"op-{model['id']}"}
        if method == "GET" and len(parts) == 7:
            return dict(model)
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
    assert report.errors == []


def test_survey_reads_a_model_whose_listing_row_has_no_trigger():
    admin = _engine()
    real = admin.__call__

    def metadata_only(method: str, path: str, *, params: Any = None, json: Any = None) -> Any:
        if method == "GET" and path.endswith("/mental-models"):
            params = {**(params or {}), "detail": "metadata"}
        return real(method, path, params=params, json=json)

    report = survey(metadata_only)
    # alice-done is flagged: read on its own, it is not listed again.
    assert ("shared", "alice-done") not in {(f.bank, f.model_id) for f in report.findings}
    gets = [p for m, p, _q, _j in admin.calls if m == "GET" and "/mental-models/" in p]
    assert gets and all(p.split("/")[-1] for p in gets)


def test_survey_one_bank_only():
    report = survey(_engine(), banks=["hal0:other"])
    assert [(f.bank, f.model_id) for f in report.findings] == [("hal0:other", "carol-own")]


def test_survey_private_doc_without_tags_counts_as_foreign():
    admin = FakeAdmin()
    admin.bank("b", models=[_mm("m", ["agent:alice"])], docs=[])
    admin.banks["b"]["docs"] = [{"id": "x", "tags": [PRIVATE]}]  # private, no owner
    assert survey(admin).findings[0].hidden is True


def test_survey_bank_error_is_reported_not_raised():
    admin = _engine()

    def broken(method: str, path: str, **kw: Any) -> Any:
        if "hal0:other" in path or "hal0%3Aother" in path:
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


def test_apply_sets_the_flag_merged_over_the_trigger_and_refreshes():
    admin = _engine()
    results = apply_repairs(admin, survey(admin).findings)
    assert all(r.error is None for r in results)
    patched = {p.rsplit("/", 1)[1]: j for m, p, j in admin.writes() if m == "PATCH"}
    assert patched == {
        "alice-own": {"trigger": {"exclude_mental_models": True}},
        "bob-own": {"trigger": {"mode": "delta", "exclude_mental_models": True}},
        "carol-own": {"trigger": {"exclude_mental_models": True}},
    }
    refreshed = sorted(p for m, p, _ in admin.writes() if m == "POST")
    assert len(refreshed) == 3 and all(p.endswith("/refresh") for p in refreshed)
    # Only the three: shared and project-wide models were not touched.
    assert admin.banks["shared"]["models"]["whole"].get("trigger") is None
    assert admin.banks["shared"]["models"]["project"].get("trigger") is None
    # The owner can read them now.
    bob = admin.banks["shared"]["models"]["bob-own"]
    assert model_scope_confined_to(bob, "bob")


def test_apply_is_idempotent():
    admin = _engine()
    apply_repairs(admin, survey(admin).findings)
    assert survey(admin).findings == []


def test_apply_without_refresh():
    admin = _engine()
    apply_repairs(admin, survey(admin).findings, refresh=False)
    assert all(m == "PATCH" for m, _p, _j in admin.writes())


def test_apply_reports_a_failed_write_and_goes_on():
    admin = _engine()
    findings = survey(admin).findings

    def flaky(method: str, path: str, **kw: Any) -> Any:
        if method == "PATCH" and path.endswith("/bob-own"):
            raise RuntimeError("409 busy")
        return admin(method, path, **kw)

    results = {r.finding.model_id: r for r in apply_repairs(flaky, findings)}
    assert "409 busy" in (results["bob-own"].error or "")
    assert results["alice-own"].error is None and results["carol-own"].error is None


def test_repair_command_name():
    assert REPAIR_COMMAND == "hal0 memory mm repair"
