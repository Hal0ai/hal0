"""BridgeService teach methods: scoped memory, gated skills, capabilities (ADR-0043 decision 10).

The ``hal0-agents`` tools and any REST projection are thin over these methods, so
the rules are pinned here:

* **Memory** is written and searched in the agent's own namespace, forced by
  ``MemoryDispatcher.scoped`` (private mode on, identity fixed): ``private:hermes``
  for the default profile, ``private:hermes__<profile>`` for ``hermes/<profile>``;
  ``agent="shared"`` uses the shared bucket with private mode forced *off*. Every
  write is tagged ``source:bridge:<client>``. A memory refusal is raised, never
  returned as a success payload.
* **Skill writes and deletes never touch disk directly.** They are validated, then
  queued in the approval inbox as ``agent_skill_put`` / ``agent_skill_delete`` and
  executed only when the operator approves. An invalid or shipped-name request is
  refused before anything is queued; a repeated push of one skill is one row that
  always carries the newest content.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from hal0.api.auth import AuthPrincipal
from hal0.bridge.errors import BRIDGE_ERROR_STATUS, BridgeError
from hal0.bridge.hermes_client import HermesGateway
from hal0.bridge.service import BridgeService, Caller
from hal0.bridge.skills import SkillLibrary
from hal0.bridge.store import BridgeStore
from hal0.config.schema import BridgeConfig
from hal0.dispatcher.memory_dispatcher import MemoryDispatcher
from hal0.mcp.approval_queue import ApprovalQueue
from tests.bridge._fake_hermes import FakeHermes


def skill_md(name: str = "forum-style", description: str = "How to write for the forum.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nBe brief.\n"


VALID_MD = skill_md()


# -- a recording memory dispatcher ---------------------------------------------------------


@dataclass
class Call:
    tool: str
    args: dict[str, Any]
    client_id: str | None
    private: bool | None


@dataclass
class FakeDispatcher:
    """Records every dispatch with the identity and private mode it was pinned to.

    ``scoped(cid)`` pins ``private=True`` and ``shared(cid)`` pins ``private=False``,
    like the real :class:`MemoryDispatcher`; calling the root directly records
    ``client_id=None`` (identity resolved off a live request, which a bridge call
    must never rely on).
    """

    calls: list[Call] = field(default_factory=list)
    answer: dict[str, Any] | None = None

    def scoped(self, client_id: str) -> _Pinned:
        return _Pinned(self, client_id, True)

    def shared(self, client_id: str) -> _Pinned:
        return _Pinned(self, client_id, False)

    async def dispatch(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        return await self._record(tool, args, None, None)

    async def _record(
        self, tool: str, args: dict[str, Any], client_id: str | None, private: bool | None
    ) -> dict[str, Any]:
        self.calls.append(Call(tool, dict(args), client_id, private))
        if self.answer is not None:
            return self.answer
        if tool == "memory_search":
            return {"status": "ok", "results": [{"id": "m1", "text": "a fact"}]}
        return {"status": "ok", "id": "m1", "timestamp": "2026-10-29T00:00:00Z"}


@dataclass
class _Pinned:
    root: FakeDispatcher
    client_id: str
    private: bool

    async def dispatch(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        return await self.root._record(tool, args, self.client_id, self.private)


# -- fixtures --------------------------------------------------------------------------------


@pytest.fixture
def cc() -> Caller:
    return Caller.from_principal(
        AuthPrincipal(tier="bridge", name="cc", scopes=frozenset({"agents"}))
    )


@pytest.fixture
def fake_dispatcher() -> FakeDispatcher:
    return FakeDispatcher()


@pytest.fixture
def tmp_roots(tmp_path: Path) -> SimpleNamespace:
    shipped = tmp_path / "shipped"
    dropin = tmp_path / "dropin"
    shipped.mkdir()
    dropin.mkdir()
    return SimpleNamespace(shipped=shipped, dropin=dropin)


@pytest.fixture
def queue() -> ApprovalQueue:
    return ApprovalQueue()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "hermes"
    (path / "profiles" / "coder").mkdir(parents=True)
    return path


def _service(
    tmp_path: Path,
    home: Path,
    queue: ApprovalQueue,
    roots: SimpleNamespace,
    dispatcher: Any,
    *,
    detail: Any = None,
) -> tuple[BridgeService, BridgeStore]:
    store = BridgeStore(tmp_path / "bridge.db")
    store.open()
    fake = FakeHermes()
    service = BridgeService(
        store=store,
        gateway=HermesGateway(
            home=home, environ=fake.environ(), transport=httpx.MockTransport(fake)
        ),
        config=BridgeConfig(),
        approval_queue=queue,
        events=None,
        memory_dispatcher=dispatcher,
        skills=SkillLibrary(shipped_root=roots.shipped, dropin_root=roots.dropin),
        detail_fetch=detail if detail is not None else (lambda _url: None),
    )
    return service, store


@pytest.fixture
async def svc(
    tmp_path: Path,
    home: Path,
    queue: ApprovalQueue,
    tmp_roots: SimpleNamespace,
    fake_dispatcher: FakeDispatcher,
) -> AsyncIterator[BridgeService]:
    service, _store = _service(tmp_path, home, queue, tmp_roots, fake_dispatcher)
    yield service
    await service.close()


# -- memory ----------------------------------------------------------------------------------


async def test_memory_add_forced_namespace_and_tag(
    svc: BridgeService, fake_dispatcher: FakeDispatcher, cc: Caller
) -> None:
    out = await svc.memory_add(cc, agent="hermes/coder", text="fact", tags=["x"])
    call = fake_dispatcher.calls[-1]
    assert call.tool == "memory_add" and call.args["text"] == "fact"
    assert call.client_id == "hermes__coder" and call.private is True
    assert set(call.args["tags"]) == {"x", "source:bridge:cc"}
    assert "dataset" not in call.args  # the namespace is the dispatcher's, not the caller's
    assert out["id"] == "m1"

    await svc.memory_add(cc, agent="shared", text="f2", tags=None)
    call = fake_dispatcher.calls[-1]
    assert call.args["dataset"] == "shared" and call.private is False
    assert call.client_id == "cc"  # the writer is the client; no request-derived identity
    assert call.args["tags"] == ["source:bridge:cc"]


async def test_memory_add_default_profile_uses_the_hermes_namespace(
    svc: BridgeService, fake_dispatcher: FakeDispatcher, cc: Caller
) -> None:
    await svc.memory_add(cc, agent="hermes", text="fact", tags=[])
    call = fake_dispatcher.calls[-1]
    assert call.client_id == "hermes" and call.private is True


async def test_memory_add_tags_keep_order_and_repeat_nothing(
    svc: BridgeService, fake_dispatcher: FakeDispatcher, cc: Caller
) -> None:
    await svc.memory_add(cc, agent="hermes", text="t", tags=["b", "a", "b", "source:bridge:cc"])
    assert fake_dispatcher.calls[-1].args["tags"] == ["b", "a", "source:bridge:cc"]


async def test_memory_add_cannot_forge_the_source_tag_of_another_client(
    svc: BridgeService, fake_dispatcher: FakeDispatcher, cc: Caller
) -> None:
    await svc.memory_add(cc, agent="hermes", text="t", tags=["source:bridge:other"])
    assert fake_dispatcher.calls[-1].args["tags"] == ["source:bridge:cc"]


@pytest.mark.parametrize("agent", ["pi", "claude", "hermes/", "hermes/nope", "hermes/../x", ""])
async def test_memory_unknown_agent(
    svc: BridgeService, fake_dispatcher: FakeDispatcher, cc: Caller, agent: str
) -> None:
    with pytest.raises(BridgeError) as added:
        await svc.memory_add(cc, agent=agent, text="t", tags=None)
    assert added.value.code == "bridge.unknown_agent"
    with pytest.raises(BridgeError) as searched:
        await svc.memory_search(cc, agent=agent, query="q")
    assert searched.value.code == "bridge.unknown_agent"
    assert fake_dispatcher.calls == []


async def test_memory_disabled_is_503(
    tmp_path: Path, home: Path, queue: ApprovalQueue, tmp_roots: SimpleNamespace, cc: Caller
) -> None:
    service, _store = _service(tmp_path, home, queue, tmp_roots, None)
    try:
        for coro in (
            service.memory_add(cc, agent="hermes", text="t", tags=None),
            service.memory_search(cc, agent="hermes", query="q"),
        ):
            with pytest.raises(BridgeError) as exc:
                await coro
            assert exc.value.code == "bridge.memory_disabled" and exc.value.status == 503
    finally:
        await service.close()
    assert BRIDGE_ERROR_STATUS["bridge.memory_disabled"] == 503


async def test_memory_search_forced_namespace(
    svc: BridgeService, fake_dispatcher: FakeDispatcher, cc: Caller
) -> None:
    out = await svc.memory_search(cc, agent="hermes/coder", query="what", limit=5)
    call = fake_dispatcher.calls[-1]
    assert call.tool == "memory_search"
    assert call.client_id == "hermes__coder" and call.private is True
    assert call.args == {"query": "what", "limit": 5}
    assert out["results"][0]["id"] == "m1"

    await svc.memory_search(cc, agent="shared", query="what")
    call = fake_dispatcher.calls[-1]
    assert call.private is False and call.args == {
        "query": "what",
        "limit": 10,
        "dataset": "shared",
    }


@pytest.mark.parametrize(("limit", "sent"), [(0, 1), (-3, 1), (500, 50), (7, 7)])
async def test_memory_search_limit_is_clamped(
    svc: BridgeService, fake_dispatcher: FakeDispatcher, cc: Caller, limit: int, sent: int
) -> None:
    await svc.memory_search(cc, agent="hermes", query="q", limit=limit)
    assert fake_dispatcher.calls[-1].args["limit"] == sent


@pytest.mark.parametrize(
    ("code", "expected"),
    [("mcp.memory_schema", "bridge.memory_invalid"), ("mcp.memory_failed", "bridge.memory_failed")],
)
async def test_memory_refusal_is_raised_not_returned(
    svc: BridgeService, fake_dispatcher: FakeDispatcher, cc: Caller, code: str, expected: str
) -> None:
    fake_dispatcher.answer = {
        "status": "error",
        "error": {"code": code, "detail": "text must be non-empty"},
    }
    with pytest.raises(BridgeError) as exc:
        await svc.memory_add(cc, agent="hermes", text=" ", tags=None)
    assert exc.value.code == expected and "text must be non-empty" in exc.value.message
    assert BRIDGE_ERROR_STATUS[expected] in (422, 502)


class RecordingProvider:
    """A memory provider that remembers the dataset, source and client of each call."""

    def __init__(self) -> None:
        self.added: list[dict[str, Any]] = []
        self.searched: list[dict[str, Any]] = []

    async def add(self, **kw: Any) -> dict[str, Any]:
        self.added.append(kw)
        return {"id": f"doc-{len(self.added)}", "timestamp": "2026-10-29T00:00:00Z"}

    async def search(self, **kw: Any) -> list[dict[str, Any]]:
        self.searched.append(kw)
        return [{"id": "doc-1", "text": "a fact", "dataset": "shared"}]


async def test_real_dispatcher_namespaces_survive_a_hostile_request_context(
    tmp_path: Path, home: Path, queue: ApprovalQueue, tmp_roots: SimpleNamespace, cc: Caller
) -> None:
    """With the app-level dispatcher resolving identity and private mode from the live request
    (here: a client that sent ``X-hal0-Private: 1`` as someone else), the bridge's own writes
    still land where ADR-0043 decision 10 says."""
    provider = RecordingProvider()
    hostile = MemoryDispatcher(
        provider, client_id_resolver=lambda: "mallory", private_resolver=lambda: True
    )
    service, _store = _service(tmp_path, home, queue, tmp_roots, hostile)
    try:
        await service.memory_add(cc, agent="hermes/coder", text="private fact", tags=["x"])
        await service.memory_add(cc, agent="shared", text="shared fact", tags=None)
        await service.memory_search(cc, agent="hermes/coder", query="fact")
        await service.memory_search(cc, agent="shared", query="fact")
    finally:
        await service.close()
    private_add, shared_add = provider.added
    assert (
        private_add["dataset"] == "private:hermes__coder"
        and private_add["client_id"] == "hermes__coder"
    )
    assert private_add["source"] == "hermes__coder"
    assert set(private_add["tags"]) == {"x", "source:bridge:cc"}
    assert shared_add["dataset"] == "shared" and shared_add["client_id"] == "cc"
    private_search, shared_search = provider.searched
    assert private_search["dataset"] == ["shared", "private:hermes__coder"]
    assert shared_search["dataset"] == "shared"


# -- skills: reads ---------------------------------------------------------------------------


async def test_skills_list_and_get_delegate_to_the_library(
    svc: BridgeService, tmp_roots: SimpleNamespace
) -> None:
    (tmp_roots.shipped / "hal0-bench").mkdir()
    (tmp_roots.shipped / "hal0-bench" / "SKILL.md").write_text(skill_md("hal0-bench", "Bench."))
    rows = svc.skills_list()
    assert [(r["name"], r["source"]) for r in rows] == [("hal0-bench", "shipped")]
    got = svc.skill_get("hal0-bench")
    assert got["skill_md"].startswith("---") and got["source"] == "shipped"
    with pytest.raises(BridgeError) as exc:
        svc.skill_get("missing")
    assert exc.value.code == "bridge.skill_not_found"


# -- skills: gated writes --------------------------------------------------------------------


async def test_skill_put_is_gated_and_executes_on_approve(
    svc: BridgeService, queue: ApprovalQueue, cc: Caller, tmp_roots: SimpleNamespace
) -> None:
    out = await svc.skill_put(cc, name="forum-style", skill_md=VALID_MD, files=None)
    assert out["status"] == "pending_approval" and out["name"] == "forum-style"
    [entry] = queue.list_pending()
    assert out["approval_id"] == entry["id"]
    assert entry["tool"] == "agent_skill_put" and entry["args"]["name"] == "forum-style"
    assert entry["client_id"] == "bridge:cc" and "name" in entry["target_args"]
    assert not (tmp_roots.dropin / "forum-style").exists()  # nothing on disk before approval

    done = await queue.approve(entry["id"])
    assert done["state"] == "executed"
    assert (tmp_roots.dropin / "forum-style" / "SKILL.md").read_text() == VALID_MD


async def test_skill_put_deny_writes_nothing(
    svc: BridgeService, queue: ApprovalQueue, cc: Caller, tmp_roots: SimpleNamespace
) -> None:
    out = await svc.skill_put(cc, name="forum-style", skill_md=VALID_MD, files=None)
    await queue.deny(out["approval_id"])
    assert not (tmp_roots.dropin / "forum-style").exists()


async def test_skill_put_invalid_never_enqueues(
    svc: BridgeService, queue: ApprovalQueue, cc: Caller
) -> None:
    with pytest.raises(BridgeError) as e:
        await svc.skill_put(cc, name="x", skill_md="no frontmatter", files=None)
    assert e.value.code == "bridge.skill_invalid" and queue.list_pending() == []
    with pytest.raises(BridgeError) as e:
        await svc.skill_put(cc, name="Bad Name", skill_md=VALID_MD, files=None)
    assert e.value.code == "bridge.skill_invalid" and queue.list_pending() == []
    with pytest.raises(BridgeError) as e:
        await svc.skill_put(cc, name="forum-style", skill_md=VALID_MD, files={"../x": "y"})
    assert e.value.code == "bridge.skill_invalid" and queue.list_pending() == []


async def test_skill_put_over_a_shipped_name_is_refused_before_queueing(
    svc: BridgeService, queue: ApprovalQueue, cc: Caller, tmp_roots: SimpleNamespace
) -> None:
    (tmp_roots.shipped / "hal0-bench").mkdir()
    (tmp_roots.shipped / "hal0-bench" / "SKILL.md").write_text(skill_md("hal0-bench", "Bench."))
    with pytest.raises(BridgeError) as e:
        await svc.skill_put(cc, name="hal0-bench", skill_md=skill_md("hal0-bench"), files=None)
    assert e.value.code == "bridge.skill_readonly" and queue.list_pending() == []


async def test_skill_put_args_count_bytes_and_name_files(
    svc: BridgeService, queue: ApprovalQueue, cc: Caller
) -> None:
    files = {"b.sh": "echo é\n", "a.md": "notes\n"}
    await svc.skill_put(cc, name="forum-style", skill_md=VALID_MD, files=files)
    [entry] = queue.list_pending()
    expected = len(VALID_MD.encode()) + sum(len(c.encode()) for c in files.values())
    assert entry["args"]["bytes"] == expected  # UTF-8 bytes, not characters
    assert entry["args"]["files"] == ["a.md", "b.sh"]
    assert re.fullmatch(r"[0-9a-f]{16}", entry["args"]["sha256"])
    assert entry["args"]["preview"].startswith("---\nname: forum-style")
    assert "echo" not in str(entry["args"])  # supporting file contents are not in the card


async def test_repeated_identical_pushes_are_one_row(
    svc: BridgeService, queue: ApprovalQueue, cc: Caller
) -> None:
    first = await svc.skill_put(cc, name="forum-style", skill_md=VALID_MD, files=None)
    second = await svc.skill_put(cc, name="forum-style", skill_md=VALID_MD, files=None)
    assert first["approval_id"] == second["approval_id"]
    [entry] = queue.list_pending()
    assert entry["hit_count"] == 2


async def test_an_edited_push_replaces_the_pending_row_so_approval_writes_what_the_card_shows(
    svc: BridgeService, queue: ApprovalQueue, cc: Caller, tmp_roots: SimpleNamespace
) -> None:
    v1 = skill_md(description="First draft.")
    v2 = skill_md(description="Second draft, much better.")
    first = await svc.skill_put(cc, name="forum-style", skill_md=v1, files=None)
    second = await svc.skill_put(cc, name="forum-style", skill_md=v2, files=None)
    assert first["approval_id"] != second["approval_id"]
    [entry] = queue.list_pending()  # one row per skill
    assert entry["id"] == second["approval_id"]
    assert entry["args"]["sha256"] != _pending_digest(queue, first["approval_id"])
    assert "Second draft" in entry["args"]["preview"]
    old = queue.get(first["approval_id"])
    assert old is not None and old.state == "failed" and "newer push" in (old.error or "")

    await queue.approve(entry["id"])
    assert (tmp_roots.dropin / "forum-style" / "SKILL.md").read_text() == v2


def _pending_digest(queue: ApprovalQueue, approval_id: str) -> str:
    entry = queue.get(approval_id)
    assert entry is not None
    return str(entry.args["sha256"])


async def test_pushes_of_different_skills_do_not_collapse(
    svc: BridgeService, queue: ApprovalQueue, cc: Caller
) -> None:
    await svc.skill_put(cc, name="forum-style", skill_md=VALID_MD, files=None)
    await svc.skill_put(cc, name="other-skill", skill_md=skill_md("other-skill"), files=None)
    assert sorted(e["args"]["name"] for e in queue.list_pending()) == ["forum-style", "other-skill"]


async def test_skill_delete_is_gated_and_executes_on_approve(
    svc: BridgeService, queue: ApprovalQueue, cc: Caller, tmp_roots: SimpleNamespace
) -> None:
    put = await svc.skill_put(cc, name="forum-style", skill_md=VALID_MD, files=None)
    await queue.approve(put["approval_id"])
    assert (tmp_roots.dropin / "forum-style" / "SKILL.md").is_file()

    out = await svc.skill_delete(cc, name="forum-style")
    assert out["status"] == "pending_approval" and out["name"] == "forum-style"
    [entry] = queue.list_pending()
    assert entry["tool"] == "agent_skill_delete" and entry["args"] == {"name": "forum-style"}
    assert entry["client_id"] == "bridge:cc"
    assert (tmp_roots.dropin / "forum-style").exists()  # still there until approved

    await queue.approve(entry["id"])
    assert not (tmp_roots.dropin / "forum-style").exists()


async def test_skill_delete_of_a_shipped_name_is_refused_before_queueing(
    svc: BridgeService, queue: ApprovalQueue, cc: Caller, tmp_roots: SimpleNamespace
) -> None:
    (tmp_roots.shipped / "hal0-bench").mkdir()
    (tmp_roots.shipped / "hal0-bench" / "SKILL.md").write_text(skill_md("hal0-bench", "Bench."))
    with pytest.raises(BridgeError) as e:
        await svc.skill_delete(cc, name="hal0-bench")
    assert e.value.code == "bridge.skill_readonly" and e.value.status == 403
    assert queue.list_pending() == []


async def test_skill_delete_of_a_missing_or_badly_named_skill_never_enqueues(
    svc: BridgeService, queue: ApprovalQueue, cc: Caller
) -> None:
    with pytest.raises(BridgeError) as e:
        await svc.skill_delete(cc, name="ghost")
    assert e.value.code == "bridge.skill_not_found"
    with pytest.raises(BridgeError) as e:
        await svc.skill_delete(cc, name="../etc")
    assert e.value.code == "bridge.skill_invalid"
    assert queue.list_pending() == []


async def test_skill_writes_need_an_approval_queue(
    tmp_path: Path, home: Path, tmp_roots: SimpleNamespace, cc: Caller
) -> None:
    """With no inbox there is nobody to approve, so a skill write is refused, not applied."""
    service, _store = _service(tmp_path, home, None, tmp_roots, None)  # type: ignore[arg-type]
    try:
        with pytest.raises(BridgeError) as e:
            await service.skill_put(cc, name="forum-style", skill_md=VALID_MD, files=None)
        assert e.value.code == "bridge.unavailable"
        assert not (tmp_roots.dropin / "forum-style").exists()
    finally:
        await service.close()


async def test_teach_writes_after_close_are_unavailable(
    svc: BridgeService, cc: Caller, queue: ApprovalQueue
) -> None:
    await svc.close()
    with pytest.raises(BridgeError) as e:
        await svc.skill_put(cc, name="forum-style", skill_md=VALID_MD, files=None)
    assert e.value.code == "bridge.unavailable" and queue.list_pending() == []
    with pytest.raises(BridgeError) as e:
        await svc.memory_add(cc, agent="hermes", text="t", tags=None)
    assert e.value.code == "bridge.unavailable"


# -- capabilities ----------------------------------------------------------------------------


async def test_capabilities_reads_the_profiles_config(
    tmp_path: Path, home: Path, queue: ApprovalQueue, tmp_roots: SimpleNamespace
) -> None:
    (home / "config.yaml").write_text(
        "toolsets: [hermes-cli]\nagent:\n  disabled_toolsets: [a2a]\n"
        "mcp_servers:\n  hal0-memory:\n    headers:\n      Authorization: Bearer nlQ_secret\n",
        encoding="utf-8",
    )
    service, _store = _service(
        tmp_path, home, queue, tmp_roots, None, detail=lambda _url: {"version": "0.22.4"}
    )
    try:
        view = await service.capabilities("hermes")
        coder = await service.capabilities("hermes/coder")
        with pytest.raises(BridgeError) as e:
            await service.capabilities("hermes/nope")
    finally:
        await service.close()
    assert view["agent"] == "hermes" and view["api_server_version"] == "0.22.4"
    assert view["mcp_servers"] == ["hal0-memory"] and view["toolsets_disabled"] == ["a2a"]
    assert "nlQ_" not in str(view) and "Bearer" not in str(view)
    assert coder["agent"] == "hermes/coder" and coder["toolsets_enabled"] == ["hermes-cli"]
    assert e.value.code == "bridge.unknown_agent"
