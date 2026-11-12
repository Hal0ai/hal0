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

A model is skipped, and reported, when another agent's private document in
its bank also carries the owner's ``agent:<id>`` tag (written before the
write envelope stamped one owner, or straight to the engine): the owner's
scope would admit that document, so flagging would not confine the model.

The content a model has now was written by refreshes that could also read
the bank's other mental models, and a delta refresh keeps unchanged sections
as they are. So, per model, in this order:

1. a model its owner cannot read now (``hidden``) is emptied first
   (``POST .../clear``), while it is still hidden, so the flag never exposes
   the old content;
2. the flag is set (``PATCH`` of ``trigger.exclude_mental_models`` alone; the
   engine, pinned at 1.2.0, merges a trigger patch over the stored one);
3. a full refresh is queued (``{"mode": "full"}``; the stored mode is kept),
   so nothing of the old content survives. If it fails, a cleared model
   stays empty, not stale.

Everything goes through hal0-api's admin memory routes
(``/api/memory/banks/...``), which forward to the engine without the agent
read rules. The work is done through one injected ``request`` callable, so
``hal0 memory mm repair`` and the ``memory_model_owners`` row of
``hal0 doctor all`` share it. Nothing here runs on its own: not at start, not
from ``hal0 update``. The operator runs the command.
"""

from __future__ import annotations

import time
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


@dataclass
class Survey:
    findings: list[Finding] = field(default_factory=list)
    #: Models that would qualify but are left alone, with the reason.
    skipped: list[tuple[Finding, str]] = field(default_factory=list)
    #: ``(bank, message)`` for every bank that could not be read.
    errors: list[tuple[str, str]] = field(default_factory=list)
    banks: int = 0
    #: The survey stopped at its deadline; what it found is a lower bound.
    incomplete: bool = False

    @property
    def hidden(self) -> int:
        return sum(1 for f in self.findings if f.hidden)


@dataclass(frozen=True)
class Result:
    finding: Finding
    error: str | None = None
    operation_id: str | None = None
    #: What was done before ``error``, if anything.
    cleared: bool = False
    flagged: bool = False


class _TooMany(Exception):
    pass


class _Deadline(Exception):
    pass


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


def _trigger(model: dict[str, Any]) -> dict[str, Any]:
    trigger = model.get("trigger")
    return dict(trigger) if isinstance(trigger, dict) else {}


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


def _model_path(bank: str, model_id: str) -> str:
    return f"{_bank_path(bank)}/mental-models/{quote(model_id, safe='')}"


class _Reader:
    """The survey's reads, each checked against an optional deadline."""

    def __init__(
        self, request: Request, deadline: float | None, clock: Callable[[], float]
    ) -> None:
        self._request, self._deadline, self._clock = request, deadline, clock

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self._deadline is not None and self._clock() >= self._deadline:
            raise _Deadline
        return self._request("GET", path, params=params)

    def paged(self, path: str, key: str, params: dict[str, Any], cap: int) -> list[Any]:
        """Every row of a paged listing; :class:`_TooMany` past ``cap`` pages.

        Reads on from the rows collected so far until a page comes back empty
        or the listing's own ``total`` is reached, so a server that caps its
        page below :data:`_PAGE` is still read to the end.
        """
        rows: list[Any] = []
        for _ in range(cap):
            resp = self.get(path, {**params, "limit": _PAGE, "offset": len(rows)})
            chunk = resp.get(key) if isinstance(resp, dict) else None
            chunk = chunk if isinstance(chunk, list) else []
            rows.extend(chunk)
            total = resp.get("total") if isinstance(resp, dict) else None
            if not chunk or (isinstance(total, int) and len(rows) >= total):
                return rows
        raise _TooMany

    def with_trigger(self, bank: str, model: dict[str, Any]) -> dict[str, Any]:
        """``model`` with its stored trigger.

        The listing is read at the engine's default ``metadata`` detail,
        which carries no content and gives ``trigger: null`` (Hindsight
        1.2.0). A null trigger would read as "no flag, all defaults", so such
        a row is read on its own; if that has no trigger either, none is
        stored.
        """
        if isinstance(model.get("trigger"), dict):
            return model
        one = self.get(_model_path(bank, str(model.get("id"))))
        return one if isinstance(one, dict) else model


def _private_docs(reader: _Reader, bank: str) -> list[tuple[str, list[str]]] | None:
    """``(id, tags)`` of the bank's private documents, or ``None`` when the
    scan hit its cap."""
    try:
        rows = reader.paged(
            f"{_bank_path(bank)}/documents",
            "items",
            {"tags": [VISIBILITY_PRIVATE_TAG], "tags_match": "all_strict"},
            _PRIVATE_SCAN_PAGES,
        )
    except _TooMany:
        return None
    out: list[tuple[str, list[str]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        tags = row.get("tags")
        out.append(
            (
                str(row.get("id")),
                [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else [],
            )
        )
    return out


def _foreign(docs: list[tuple[str, list[str]]], agent: str) -> list[tuple[str, list[str]]]:
    """The private documents ``agent`` may not read (one with no identified
    owner counts as another agent's)."""
    out = []
    for doc_id, tags in docs:
        owner = _agent_of(tags)
        if not (is_identified(owner) and owner == agent):
            out.append((doc_id, tags))
    return out


def survey(
    request: Request,
    banks: Iterable[str] | None = None,
    *,
    private_banks_only: bool = False,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Survey:
    """Every model that needs the flag, in ``banks`` (default: every bank).

    Reads only. A bank that cannot be read is listed in ``errors`` and the
    rest are still surveyed. With ``private_banks_only`` a bank holding no
    private memory at all is not listed (none of its models can be hidden);
    the doctor row uses it. Past ``deadline`` (a ``clock`` reading) the survey
    stops and says it is ``incomplete``.
    """
    report = Survey()
    reader = _Reader(request, deadline, clock)
    try:
        bank_ids = (
            list(banks)
            if banks is not None
            else [
                str(r["bank_id"])
                for r in reader.paged(_ADMIN, "banks", {}, _LIST_PAGES)
                if isinstance(r, dict) and r.get("bank_id")
            ]
        )
    except _Deadline:
        report.incomplete = True
        return report
    except _TooMany:
        report.errors.append(("*", "too many banks to list"))
        return report
    for bank in bank_ids:
        try:
            docs = _private_docs(reader, bank)
            if private_banks_only and docs == []:
                report.banks += 1
                continue
            listed = reader.paged(f"{_bank_path(bank)}/mental-models", "items", {}, _LIST_PAGES)
            todo: list[tuple[dict[str, Any], str]] = []
            for row in listed:
                if not isinstance(row, dict):
                    continue
                model = reader.with_trigger(bank, row)
                owner = _needs_flag(model)
                if owner is not None:
                    todo.append((model, owner))
        except _Deadline:
            report.incomplete = True
            return report
        except _TooMany:
            report.errors.append((bank, "too many mental models to list"))
            continue
        except Exception as exc:  # one bank's failure must not hide the rest
            report.errors.append((bank, str(exc) or type(exc).__name__))
            continue
        report.banks += 1
        for model, owner in todo:
            foreign = None if docs is None else _foreign(docs, owner)
            finding = Finding(
                bank=bank,
                model_id=str(model.get("id")),
                name=str(model.get("name") or ""),
                owner=owner,
                hidden=foreign is None or bool(foreign),
            )
            if foreign is None:
                report.skipped.append((finding, "too many private documents in the bank to check"))
                continue
            own_tag = f"{AGENT_TAG_PREFIX}{owner}"
            clash = [doc_id for doc_id, tags in foreign if own_tag in tags]
            if clash:
                report.skipped.append(
                    (
                        finding,
                        f"another agent's private document also carries {own_tag} "
                        f"({', '.join(clash[:3])}{', …' if len(clash) > 3 else ''})",
                    )
                )
                continue
            report.findings.append(finding)
    return report


def apply_repairs(
    request: Request, findings: Iterable[Finding], *, refresh: bool = True
) -> list[Result]:
    """Repair each finding: clear it if hidden, set the flag, then (with
    ``refresh``) queue a full refresh. See the module docstring for why in
    that order. A failure is recorded with what was already done, and the
    next model is still repaired.
    """
    results: list[Result] = []
    for finding in findings:
        path = _model_path(finding.bank, finding.model_id)
        cleared = False
        try:
            if finding.hidden:
                request("POST", f"{path}/clear")
                cleared = True
            request("PATCH", path, json={"trigger": {"exclude_mental_models": True}})
        except Exception as exc:
            results.append(Result(finding, error=str(exc) or type(exc).__name__, cleared=cleared))
            continue
        op_id: str | None = None
        if refresh:
            try:
                resp = request("POST", f"{path}/refresh", json={"mode": "full"})
            except Exception as exc:
                results.append(
                    Result(
                        finding,
                        error=f"refresh failed: {str(exc) or type(exc).__name__}",
                        cleared=cleared,
                        flagged=True,
                    )
                )
                continue
            op_id = resp.get("operation_id") if isinstance(resp, dict) else None
        results.append(Result(finding, operation_id=op_id, cleared=cleared, flagged=True))
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
