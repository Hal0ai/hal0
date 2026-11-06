"""hal0 memory spec apply — idempotent, upstream-native Hindsight bank spec.

Ported from ``work/notes/memory-showcase/spec/apply_spec.py`` (verified
idempotent against Hindsight 1.2.0) into pure functions + an ``httpx``
client so it can be unit-tested with a mocked transport and invoked from
the ``hal0 memory spec apply`` CLI.

Layout (packaged under ``hal0.memory.spec``):

    banks.json             bank -> template name, knowledge-base pages, extra config
    templates/<name>.json  Hindsight bank-template manifest (version "1")

Flow per bank: ``POST /banks/{id}/import?dry_run=true`` (plan), then (unless
``dry_run``) the real import, then ``PATCH /banks/{id}/config`` for
``config_extra``, then knowledge-base folder/page create-or-update.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import httpx

DEFAULT_API = "http://127.0.0.1:9177"


def _walk(nodes: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n in nodes or []:
        out.append(n)
        out.extend(_walk(n.get("children")))
    return out


def _kind(node: dict[str, Any]) -> str | None:
    return node.get("type", node.get("kind"))


def load_spec_dir(spec_dir: str | Path | None = None) -> Path:
    """Resolve the spec directory: an explicit path, or the packaged default."""
    if spec_dir is not None:
        return Path(spec_dir)
    with resources.as_file(resources.files("hal0.memory") / "spec") as p:
        return p


def load_spec(spec_dir: str | Path | None = None) -> dict[str, Any]:
    d = load_spec_dir(spec_dir)
    return json.loads((d / "banks.json").read_text())


def load_template(spec_dir: str | Path | None, name: str) -> dict[str, Any]:
    d = load_spec_dir(spec_dir)
    return json.loads((d / "templates" / f"{name}.json").read_text())


@dataclass
class BankReport:
    bank: str
    template: str
    dry_run: bool
    plan: dict[str, Any] | None = None
    applied: dict[str, Any] | None = None
    config_updated: list[str] = field(default_factory=list)
    folder_created: str | None = None
    pages_created: list[str] = field(default_factory=list)
    pages_updated: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class SpecApplyReport:
    api: str
    dry_run: bool
    banks: list[BankReport] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "api": self.api,
            "dry_run": self.dry_run,
            "banks": [b.__dict__ for b in self.banks],
        }


class SpecApplyClient:
    """Thin HTTP wrapper over Hindsight's ``/v1/default/banks`` API."""

    def __init__(self, api: str = DEFAULT_API, *, client: httpx.Client | None = None) -> None:
        self.base = f"{api.rstrip('/')}/v1/default/banks"
        self._client = client or httpx.Client(timeout=120.0)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "SpecApplyClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def call(
        self, method: str, path: str, body: dict[str, Any] | None = None, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        resp = self._client.request(method, f"{self.base}{path}", json=body, params=params)
        resp.raise_for_status()
        return resp.json() if resp.content else {}


def apply_spec(
    *,
    api: str = DEFAULT_API,
    spec_dir: str | Path | None = None,
    dry_run: bool = False,
    bank_filter: str | None = None,
    client: httpx.Client | None = None,
) -> SpecApplyReport:
    """Apply (or dry-run) the bank spec against a live Hindsight API.

    One HTTP failure for a bank is captured on its :class:`BankReport`
    (``error``) rather than aborting the whole run, so ``--bank`` isn't
    required just to work around one broken bank.
    """
    spec = load_spec(spec_dir)
    report = SpecApplyReport(api=api, dry_run=dry_run)
    with SpecApplyClient(api, client=client) as c:
        for bank, tname in spec["banks"].items():
            if bank_filter and bank != bank_filter:
                continue
            br = BankReport(bank=bank, template=tname, dry_run=dry_run)
            report.banks.append(br)
            try:
                manifest = load_template(spec_dir, tname)
                br.plan = c.call("POST", f"/{bank}/import", manifest, {"dry_run": "true"})
                if dry_run:
                    continue
                br.applied = c.call("POST", f"/{bank}/import", manifest)

                extra = spec.get("config_extra", {}).get(bank)
                if extra:
                    c.call("PATCH", f"/{bank}/config", {"updates": extra})
                    br.config_updated = sorted(extra)

                kb = spec.get("knowledge", {}).get(bank)
                if kb:
                    tree = c.call("GET", f"/{bank}/knowledge-base/tree")
                    nodes = _walk(
                        tree.get("nodes") or tree.get("roots") or tree.get("children") or tree.get("items") or []
                    )
                    folder = next(
                        (n for n in nodes if n.get("name") == kb["folder"] and _kind(n) == "folder"), None
                    )
                    if folder is None:
                        folder = c.call("POST", f"/{bank}/knowledge-base/folders", {"name": kb["folder"]})
                        br.folder_created = kb["folder"]
                    pages = {n.get("name"): n for n in nodes if _kind(n) == "page"}
                    for p in kb["pages"]:
                        if p["name"] in pages:
                            pid = pages[p["name"]].get("id") or pages[p["name"]].get("page_id")
                            c.call(
                                "PATCH",
                                f"/{bank}/knowledge-base/pages/{pid}",
                                {k: p[k] for k in ("source_query", "tags", "max_tokens", "trigger") if k in p},
                            )
                            br.pages_updated.append(p["name"])
                        else:
                            c.call(
                                "POST",
                                f"/{bank}/knowledge-base/pages",
                                dict(p, parent_id=folder.get("id")),
                            )
                            br.pages_created.append(p["name"])
            except httpx.HTTPError as exc:
                br.error = f"{type(exc).__name__}: {exc}"
    return report
