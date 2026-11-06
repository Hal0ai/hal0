"""Tests for hal0.memory.spec_apply — mocked HTTP, no live Hindsight needed."""

from __future__ import annotations

import json

import httpx

from hal0.memory import spec_apply as sa


def _spec_dir(tmp_path, banks=None, config_extra=None, knowledge=None):
    d = tmp_path / "spec"
    (d / "templates").mkdir(parents=True)
    banks = banks or {"shared": "shared"}
    (d / "banks.json").write_text(
        json.dumps(
            {"banks": banks, "config_extra": config_extra or {}, "knowledge": knowledge or {}}
        )
    )
    for tname in set(banks.values()):
        (d / "templates" / f"{tname}.json").write_text(json.dumps({"version": "1", "name": tname}))
    return d


def _handler_factory(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
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
    assert sa.load_spec(d)["banks"] == {"shared": "shared"}


def test_load_spec_dir_packaged_default():
    d = sa.load_spec_dir(None)
    assert (d / "banks.json").exists()
    spec = sa.load_spec(None)
    assert "banks" in spec and isinstance(spec["banks"], dict)


def test_apply_spec_dry_run_plans_only(tmp_path):
    d = _spec_dir(tmp_path)
    calls: list[tuple[str, str]] = []
    client = httpx.Client(transport=httpx.MockTransport(_handler_factory(calls)))
    report = sa.apply_spec(api="http://x", spec_dir=d, dry_run=True, client=client)
    assert report.dry_run is True
    assert len(report.banks) == 1
    br = report.banks[0]
    assert br.bank == "shared"
    assert br.plan == {"ok": True}
    assert br.applied is None
    assert br.error is None
    # Only the dry-run import call was made.
    assert [m for m, _ in calls] == ["POST"]


def test_apply_spec_real_applies_config_and_kb(tmp_path):
    d = _spec_dir(
        tmp_path,
        config_extra={"shared": {"retention_days": 30}},
        knowledge={
            "shared": {
                "folder": "Showcase",
                "pages": [{"name": "Overview", "source_query": "showcase", "tags": ["showcase"]}],
            }
        },
    )
    calls: list[tuple[str, str]] = []
    client = httpx.Client(transport=httpx.MockTransport(_handler_factory(calls)))
    report = sa.apply_spec(api="http://x", spec_dir=d, dry_run=False, client=client)
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
            "shared": {
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
    report = sa.apply_spec(api="http://x", spec_dir=d, dry_run=False, client=client)
    br = report.banks[0]
    assert br.pages_updated == ["Overview"]
    assert br.pages_created == []
    assert br.folder_created is None


def test_apply_spec_bank_filter(tmp_path):
    d = _spec_dir(tmp_path, banks={"shared": "shared", "private": "shared"})
    calls: list[tuple[str, str]] = []
    client = httpx.Client(transport=httpx.MockTransport(_handler_factory(calls)))
    report = sa.apply_spec(
        api="http://x", spec_dir=d, dry_run=True, bank_filter="private", client=client
    )
    assert [b.bank for b in report.banks] == ["private"]


def test_apply_spec_http_error_captured_per_bank(tmp_path):
    d = _spec_dir(tmp_path, banks={"shared": "shared", "private": "shared"})

    def handler(request: httpx.Request) -> httpx.Response:
        if "/private/" in str(request.url):
            return httpx.Response(500, json={"detail": "boom"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = sa.apply_spec(api="http://x", spec_dir=d, dry_run=True, client=client)
    by_bank = {b.bank: b for b in report.banks}
    assert by_bank["shared"].error is None
    assert by_bank["private"].error is not None
    assert "500" in by_bank["private"].error or "Server" in by_bank["private"].error


def test_report_to_dict_roundtrips():
    br = sa.BankReport(bank="shared", template="shared", dry_run=True, plan={"a": 1})
    report = sa.SpecApplyReport(api="http://x", dry_run=True, banks=[br])
    d = report.to_dict()
    assert d["api"] == "http://x"
    assert d["banks"][0]["bank"] == "shared"
