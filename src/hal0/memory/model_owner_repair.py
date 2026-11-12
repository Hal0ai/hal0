"""Give agents back their own mental models after the private read rule (#3576).

Since #3575 an agent may read a mental model (or the knowledge page it backs)
while its bank holds another agent's private memory only when the model's
scope is confined to that agent
(:func:`hal0.memory.read_scope.model_scope_confined_to`). Confinement needs
``trigger.exclude_mental_models: true``, which hal0 sets on every model an
agent creates through it. Models made before #3575, or written straight to
the engine, lack it, so their own agent gets a 404 on every route and cannot
add the flag itself.

This module finds those models and sets the flag. A model qualifies when
setting the flag would make it confined to exactly one agent: its scope is a
strict leaf (``all_strict``, ``exact``, or ``any_strict`` with that one tag)
on that agent's ``agent:<id>`` tag, and its own tags let that agent read it.
Whole-bank, project-wide and shared models never qualify and are never
written. A flagged model no longer qualifies, so a second run finds nothing.

Setting the flag changes what the model's next refresh reads, not the content
it has now. That content was written by a refresh that could also read the
bank's other mental models, so the repair queues a refresh of every model it
flags: the content the owner then reads is rebuilt under the confined scope.

Everything goes through hal0-api's admin memory routes
(``/api/memory/banks/...``), which forward to the engine without the agent
read rules. The work is done through one injected ``request`` callable, so
``hal0 memory mm repair`` and the ``memory_model_owners`` row of
``hal0 doctor all`` share it. Nothing here runs on its own: not at start, not
from ``hal0 update``. The operator runs the command.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from hal0.memory.envelope import AGENT_TAG_PREFIX, is_identified
from hal0.memory.read_scope import VISIBILITY_PRIVATE_TAG, model_scope_confined_to

#: The command that applies the repair, named by the doctor row.
REPAIR_COMMAND = "hal0 memory mm repair"

#: ``request(method, path, *, params=None, json=None) -> parsed JSON``. Raises
#: on a non-2xx answer.
Request = Callable[..., Any]

#: Page size of every listing read here.
_PAGE = 100
#: Page cap of the scan for private documents. Past it the bank counts as
#: holding another agent's private memory, as the read rule assumes.
_PRIVATE_SCAN_PAGES = 10
#: Page cap of the model and bank listings (100,000 rows).
_LIST_PAGES = 1000

_ADMIN = "/api/memory/banks"


@dataclass(frozen=True)
class Finding:
    """A model that needs the flag. ``hidden`` is whether its owner cannot
    read it now: its bank holds another agent's private memory."""

    bank: str
    model_id: str
    name: str
    owner: str
    hidden: bool
    trigger: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass
class Survey:
    findings: list[Finding] = field(default_factory=list)
    #: ``(bank, message)`` for every bank that could not be read.
    errors: list[tuple[str, str]] = field(default_factory=list)
    banks: int = 0

    @property
    def hidden(self) -> int:
        return sum(1 for f in self.findings if f.hidden)


@dataclass(frozen=True)
class Result:
    finding: Finding
    error: str | None = None
    operation_id: str | None = None


def _agent_of(tags: Any) -> str | None:
    """The first ``agent:<id>`` tag, as ``HindsightProvider._is_visible_to``
    reads a private item's owner."""
    for tag in tags if isinstance(tags, list) else ():
        if isinstance(tag, str) and tag.startswith(AGENT_TAG_PREFIX):
            return tag[len(AGENT_TAG_PREFIX) :]
    return None


def _readable_by(tags: Any, agent: str) -> bool:
    """The model's own tags pass the private rule for ``agent``
    (``HindsightProvider._is_visible_to``)."""
    if not isinstance(tags, list) or VISIBILITY_PRIVATE_TAG not in tags:
        return True
    owner = _agent_of(tags)
    return is_identified(owner) and owner == agent


def _candidate_agents(model: dict[str, Any], trigger: dict[str, Any]) -> set[str]:
    tags: list[Any] = list(model.get("tags") or []) if isinstance(model.get("tags"), list) else []
    groups = trigger.get("tag_groups")
    for group in groups if isinstance(groups, list) else ():
        if isinstance(group, dict) and isinstance(group.get("tags"), list):
            tags.extend(group["tags"])
    return {
        t[len(AGENT_TAG_PREFIX) :]
        for t in tags
        if isinstance(t, str) and t.startswith(AGENT_TAG_PREFIX)
    }


def _trigger(model: dict[str, Any]) -> dict[str, Any]:
    trigger = model.get("trigger")
    return dict(trigger) if isinstance(trigger, dict) else {}


def model_owner(model: Any) -> str | None:
    """The one agent ``model`` is confined to once it carries
    ``exclude_mental_models``, else ``None``.

    Confinement is judged by the read rule itself
    (:func:`~hal0.memory.read_scope.model_scope_confined_to`) on the model
    with the flag set. A model confined to two agents at once, or to none,
    has no owner.
    """
    if not isinstance(model, dict):
        return None
    trigger = _trigger(model)
    flagged = {**model, "trigger": {**trigger, "exclude_mental_models": True}}
    owners = [
        agent
        for agent in sorted(_candidate_agents(model, trigger))
        if model_scope_confined_to(flagged, agent) and _readable_by(model.get("tags"), agent)
    ]
    return owners[0] if len(owners) == 1 else None


def _needs_flag(model: dict[str, Any]) -> str | None:
    if _trigger(model).get("exclude_mental_models") is True:
        return None
    return model_owner(model)


def _bank_path(bank: str) -> str:
    return f"{_ADMIN}/{quote(bank, safe='')}"


def _paged(request: Request, path: str, key: str, params: dict[str, Any], cap: int) -> list[Any]:
    """Every row of a paged listing; :class:`_TooMany` past ``cap`` pages.

    Reads on from the rows collected so far until a page comes back empty or
    the listing's own ``total`` is reached, so a server that caps its page
    below :data:`_PAGE` is still read to the end.
    """
    rows: list[Any] = []
    for _ in range(cap):
        resp = request("GET", path, params={**params, "limit": _PAGE, "offset": len(rows)})
        chunk = resp.get(key) if isinstance(resp, dict) else None
        chunk = chunk if isinstance(chunk, list) else []
        rows.extend(chunk)
        total = resp.get("total") if isinstance(resp, dict) else None
        if not chunk or (isinstance(total, int) and len(rows) >= total):
            return rows
    raise _TooMany


class _TooMany(Exception):
    pass


def _bank_ids(request: Request) -> list[str]:
    rows = _paged(request, _ADMIN, "banks", {}, _LIST_PAGES)
    return [str(r["bank_id"]) for r in rows if isinstance(r, dict) and r.get("bank_id")]


def _private_owners(request: Request, bank: str) -> set[str | None] | None:
    """Owners of the bank's private documents (``None`` for one with no
    readable owner), or ``None`` when the scan hit its cap."""
    try:
        rows = _paged(
            request,
            f"{_bank_path(bank)}/documents",
            "items",
            {"tags": [VISIBILITY_PRIVATE_TAG], "tags_match": "all_strict"},
            _PRIVATE_SCAN_PAGES,
        )
    except _TooMany:
        return None
    owners: set[str | None] = set()
    for row in rows:
        tags = row.get("tags") if isinstance(row, dict) else None
        owner = _agent_of(tags) if isinstance(tags, list) else None
        owners.add(owner if is_identified(owner) else None)
    return owners


def _hidden_from(owners: set[str | None] | None, agent: str) -> bool:
    return owners is None or any(owner != agent for owner in owners)


def _with_trigger(request: Request, bank: str, model: dict[str, Any]) -> dict[str, Any]:
    """``model`` with its stored trigger.

    At its default ``metadata`` detail the engine lists every model with
    ``trigger: null`` (Hindsight 1.2.0), which would read as "no flag, all
    defaults": a flagged model would be listed again, and a ``tags_match:
    any`` model would look confined. The survey lists at ``content`` detail,
    which carries the trigger; a row that still has none is read on its own,
    and if that has none either, none is stored.
    """
    if isinstance(model.get("trigger"), dict):
        return model
    mid = quote(str(model.get("id")), safe="")
    one = request("GET", f"{_bank_path(bank)}/mental-models/{mid}")
    return one if isinstance(one, dict) else model


def survey(request: Request, banks: Iterable[str] | None = None) -> Survey:
    """Every model that needs the flag, in ``banks`` (default: every bank).

    Reads only. A bank that cannot be read is listed in ``errors`` and the
    rest are still surveyed; when the bank list itself cannot be read the
    error is raised.
    """
    report = Survey()
    for bank in list(banks) if banks is not None else _bank_ids(request):
        report.banks += 1
        try:
            models = [
                _with_trigger(request, bank, m)
                for m in _paged(
                    request,
                    f"{_bank_path(bank)}/mental-models",
                    "items",
                    {"detail": "content"},
                    _LIST_PAGES,
                )
                if isinstance(m, dict)
            ]
            todo = [(m, owner) for m in models if isinstance(m, dict) for owner in [_needs_flag(m)]]
            todo = [(m, owner) for m, owner in todo if owner is not None]
            owners = _private_owners(request, bank) if todo else set()
        except _TooMany:
            report.errors.append((bank, "too many mental models to list"))
            continue
        except Exception as exc:  # one bank's failure must not hide the rest
            report.errors.append((bank, str(exc) or type(exc).__name__))
            continue
        for model, owner in todo:
            report.findings.append(
                Finding(
                    bank=bank,
                    model_id=str(model.get("id")),
                    name=str(model.get("name") or ""),
                    owner=owner,
                    hidden=_hidden_from(owners, owner),
                    trigger=_trigger(model),
                )
            )
    return report


def apply_repairs(
    request: Request, findings: Iterable[Finding], *, refresh: bool = True
) -> list[Result]:
    """Set ``exclude_mental_models`` on each finding's model, then (with
    ``refresh``) queue a refresh so its content is rebuilt under the confined
    scope. The trigger sent is the model's current one with the flag set, so
    an engine that replaces the trigger instead of patching it keeps the rest.
    A failure is recorded and the next model is still repaired.
    """
    results: list[Result] = []
    for finding in findings:
        path = f"{_bank_path(finding.bank)}/mental-models/{quote(finding.model_id, safe='')}"
        trigger = {**finding.trigger, "exclude_mental_models": True}
        try:
            request("PATCH", path, json={"trigger": trigger})
        except Exception as exc:
            results.append(Result(finding, error=str(exc) or type(exc).__name__))
            continue
        op_id: str | None = None
        if refresh:
            try:
                resp = request("POST", f"{path}/refresh")
            except Exception as exc:
                results.append(Result(finding, error=f"flag set; refresh failed: {exc}"))
                continue
            op_id = resp.get("operation_id") if isinstance(resp, dict) else None
        results.append(Result(finding, operation_id=op_id))
    return results


__all__ = [
    "REPAIR_COMMAND",
    "Finding",
    "Result",
    "Survey",
    "apply_repairs",
    "model_owner",
    "survey",
]
