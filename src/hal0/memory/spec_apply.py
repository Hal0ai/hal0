"""hal0 memory spec apply — idempotent, upstream-native Hindsight bank spec.

Ported from ``work/notes/memory-showcase/spec/apply_spec.py`` (verified
idempotent against Hindsight 1.2.0) into pure functions + an ``httpx``
client so it can be unit-tested with a mocked transport and invoked from
the ``hal0 memory spec apply`` CLI.

Layout (packaged under ``hal0.memory.spec``):

    banks.json             role -> template name, knowledge-base pages, extra config
    templates/<name>.json  Hindsight bank-template manifest (version "1")

The spec names roles, not banks (ADR-0058 decisions 7 and 8): ``platform`` is
the bank behind the ``shared`` namespace and ``private`` the bank behind
``private:<agent>``. :func:`spec_targets` turns them into this box's bank ids
from its bank layout (:mod:`hal0.memory.bank_layout`): ``[memory] bank`` and
``[memory] private_bank`` on a two-bank store; the recorded platform bank and
Hermes' ``private__hermes`` on a store still on its old layout.

Two parts of a template are refused where they would leak a private memory,
and the bank report says so (``refused``):

* a per-project ``default_observation_scopes`` (``[["project:*"]]``) on a
  unified platform bank of the old layout. That bank holds every agent's private facts,
  and a private write there is sent with no scope of its own, so the bank
  default would put a private, project-tagged fact into an observation that
  carries only the project tag (#3547);
* the template's mental models on the two-bank private bank. A mental model is
  written from the whole bank, and that bank holds every agent's private
  memory.

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
from urllib.parse import quote

import httpx

DEFAULT_API = "http://127.0.0.1:9177"

#: The spec's two roles (``banks.json`` keys).
ROLE_PLATFORM = "platform"
ROLE_PRIVATE = "private"
#: Hermes' private bank on an old split store, the private role's target on
#: a store still on its old layout.
LEGACY_PRIVATE_BANK = "private__hermes"

REFUSED_PROJECT_SCOPE = "default_observation_scopes"
REFUSED_MENTAL_MODELS = "mental_models"


@dataclass(frozen=True)
class SpecTargets:
    """This box's bank for each spec role, and what each bank holds.

    ``banks`` maps a role to its bank id. ``platform_holds_private`` is true on
    an old unified store, ``private_holds_every_agent`` on a two-bank store.
    """

    banks: dict[str, str]
    platform_holds_private: bool = False
    private_holds_every_agent: bool = False


def spec_targets(memory_cfg: Any) -> SpecTargets:
    """The spec's roles on this box (module docstring). Writes nothing."""
    from hal0.memory.bank_layout import current_layout

    layout = current_layout(memory_cfg)
    banks = layout.provider_kwargs(memory_cfg)
    if layout.two_bank:
        return SpecTargets(
            banks={ROLE_PLATFORM: banks["bank"], ROLE_PRIVATE: banks["private_bank"]},
            private_holds_every_agent=True,
        )
    return SpecTargets(
        banks={ROLE_PLATFORM: banks["bank"], ROLE_PRIVATE: LEGACY_PRIVATE_BANK},
        platform_holds_private=bool(banks["unified_bank"]),
    )


def _per_project_scope(scopes: Any) -> bool:
    """Whether a ``default_observation_scopes`` value scopes by project."""
    if not isinstance(scopes, list):
        return False
    return any(
        isinstance(scope, list)
        and any(isinstance(tag, str) and tag.startswith("project:") for tag in scope)
        for scope in scopes
    )


def _guarded_manifest(
    manifest: dict[str, Any], role: str, targets: SpecTargets
) -> tuple[dict[str, Any], list[str]]:
    """``manifest`` without what would leak a private memory on this bank,
    and the names of the parts left out."""
    out = dict(manifest)
    refused: list[str] = []
    bank_cfg = out.get("bank")
    if (
        role == ROLE_PLATFORM
        and targets.platform_holds_private
        and isinstance(bank_cfg, dict)
        and _per_project_scope(bank_cfg.get("default_observation_scopes"))
    ):
        out["bank"] = {k: v for k, v in bank_cfg.items() if k != "default_observation_scopes"}
        refused.append(REFUSED_PROJECT_SCOPE)
    if role == ROLE_PRIVATE and targets.private_holds_every_agent and out.get("mental_models"):
        out.pop("mental_models")
        refused.append(REFUSED_MENTAL_MODELS)
    return out, refused


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
    role: str = ""
    refused: list[str] = field(default_factory=list)
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

    def __enter__(self) -> SpecApplyClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = self._client.request(method, f"{self.base}{path}", json=body, params=params)
        resp.raise_for_status()
        return resp.json() if resp.content else {}


def apply_spec(
    *,
    targets: SpecTargets,
    api: str = DEFAULT_API,
    spec_dir: str | Path | None = None,
    dry_run: bool = False,
    bank_filter: str | None = None,
    client: httpx.Client | None = None,
) -> SpecApplyReport:
    """Apply (or dry-run) the bank spec against a live Hindsight API.

    ``targets`` (:func:`spec_targets`) names this box's bank for each role; a
    role with no bank is skipped. ``bank_filter`` keeps one bank, named by
    its id or its role.

    One HTTP failure for a bank is captured on its :class:`BankReport`
    (``error``) rather than aborting the whole run, so ``--bank`` isn't
    required just to work around one broken bank.
    """
    spec = load_spec(spec_dir)
    report = SpecApplyReport(api=api, dry_run=dry_run)
    with SpecApplyClient(api, client=client) as c:
        for role, tname in spec["banks"].items():
            bank = targets.banks.get(role)
            if not bank:
                continue
            if bank_filter and bank_filter not in (bank, role):
                continue
            br = BankReport(bank=bank, template=tname, dry_run=dry_run, role=role)
            report.banks.append(br)
            try:
                manifest, br.refused = _guarded_manifest(
                    load_template(spec_dir, tname), role, targets
                )
                path = f"/{quote(bank, safe='')}"
                br.plan = c.call("POST", f"{path}/import", manifest, {"dry_run": "true"})
                if dry_run:
                    continue
                br.applied = c.call("POST", f"{path}/import", manifest)

                extra = spec.get("config_extra", {}).get(role)
                if extra:
                    c.call("PATCH", f"{path}/config", {"updates": extra})
                    br.config_updated = sorted(extra)

                kb = spec.get("knowledge", {}).get(role)
                if kb:
                    tree = c.call("GET", f"{path}/knowledge-base/tree")
                    nodes = _walk(
                        tree.get("nodes")
                        or tree.get("roots")
                        or tree.get("children")
                        or tree.get("items")
                        or []
                    )
                    folder = next(
                        (
                            n
                            for n in nodes
                            if n.get("name") == kb["folder"] and _kind(n) == "folder"
                        ),
                        None,
                    )
                    if folder is None:
                        folder = c.call(
                            "POST", f"{path}/knowledge-base/folders", {"name": kb["folder"]}
                        )
                        br.folder_created = kb["folder"]
                    pages = {n.get("name"): n for n in nodes if _kind(n) == "page"}
                    for p in kb["pages"]:
                        if p["name"] in pages:
                            pid = pages[p["name"]].get("id") or pages[p["name"]].get("page_id")
                            # A page is changed through its tree node: a 1.x
                            # engine serves ``PATCH .../knowledge-base/nodes/
                            # {node_id}`` (UpdateNodeRequest), not ``PATCH
                            # .../pages/{id}`` (#3564).
                            c.call(
                                "PATCH",
                                f"{path}/knowledge-base/nodes/{pid}",
                                {
                                    k: p[k]
                                    for k in ("source_query", "tags", "max_tokens", "trigger")
                                    if k in p
                                },
                            )
                            br.pages_updated.append(p["name"])
                        else:
                            c.call(
                                "POST",
                                f"{path}/knowledge-base/pages",
                                dict(p, parent_id=folder.get("id")),
                            )
                            br.pages_created.append(p["name"])
            except httpx.HTTPError as exc:
                br.error = f"{type(exc).__name__}: {exc}"
    return report
