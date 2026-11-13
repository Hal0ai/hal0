"""The fold-in's pure half: classification, owners and :func:`rewrite_archive` (ADR-0058 7a).

Archives are built in the layout hindsight-api 1.2.0 exports
(``engine/transfer/schema.py``): ``manifest.json``, ``documents/NNNNNN.json``
with each document's facts, ``observations.json`` whose sources name a
``(document_id, fact_index)``, ``mental_models.json``, ``knowledge_pages.json``
and ``directives.json``.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest

from hal0.memory import fold_in as fi
from hal0.memory.document_namespace import CLIENT_DOCUMENT_ID_KEY, stored_document_id
from hal0.memory.projects import ProjectRegistry

TARGETS = ("hal0:shared", "hal0:private")


def fact(text: str, *tags: str, **metadata: str) -> dict[str, Any]:
    return {"text": text, "fact_type": "world", "tags": list(tags), "metadata": dict(metadata)}


def doc(doc_id: str, facts: list[dict[str, Any]], *tags: str) -> dict[str, Any]:
    return {
        "id": doc_id,
        "original_text": f"text of {doc_id}",
        "tags": list(tags),
        "chunks": [{"chunk_index": 0, "chunk_text": "c"}],
        "facts": facts,
    }


def archive(
    bank: str,
    documents: list[dict[str, Any]],
    *,
    observations: list[dict[str, Any]] | None = None,
    models: list[dict[str, Any]] | None = None,
    pages: list[dict[str, Any]] | None = None,
    directives: list[dict[str, Any]] | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "archive_type": "bank",
                    "source_bank_id": bank,
                    "document_count": len(documents),
                    "fact_count": sum(len(d["facts"]) for d in documents),
                    "observation_count": len(observations or []),
                    "directive_count": len(directives or []),
                }
            ),
        )
        for i, d in enumerate(documents):
            zf.writestr(f"documents/{i:06d}.json", json.dumps(d))
        zf.writestr("observations.json", json.dumps(observations or []))
        zf.writestr("mental_models.json", json.dumps(models or []))
        zf.writestr("knowledge_pages.json", json.dumps(pages or []))
        zf.writestr("directives.json", json.dumps(directives or []))
    return buf.getvalue()


def read(data: bytes | None) -> dict[str, Any]:
    assert data is not None
    return fi._read_archive(data)


def plan(bank: str, *, owners: dict[str, str] | None = None, known: set[str] | None = None):
    cls = fi.classify_bank(bank, platform_bank="shared", targets=TARGETS)
    return fi.RewritePlan(
        bank=cls,
        owners={**fi.DEFAULT_OWNERS, **(owners or {})},
        known_clients=frozenset(known or ()),
        registry=ProjectRegistry(aliases={"hal0-mono": "hal0"}),
        shared_prefixes=("git:",),
    )


# ── classification ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("bank", "cls", "extra"),
    [
        ("hal0:shared", fi.CLASS_TARGET, {}),
        ("hal0:private", fi.CLASS_TARGET, {}),
        ("agents", fi.CLASS_EXCLUDED, {}),
        ("shared.switch-0123abcd", fi.CLASS_EXCLUDED, {}),
        ("shared", fi.CLASS_PLATFORM, {}),
        ("private__hermes", fi.CLASS_PRIVATE, {"agent": "hermes"}),
        ("coding-agent::hal0", fi.CLASS_CODING, {"harness": "coding-agent", "project": "hal0"}),
        ("claude-code::myrepo", fi.CLASS_CODING, {"harness": "claude-code", "project": "myrepo"}),
        ("project__docs", fi.CLASS_PROJECT, {"project": "docs"}),
        ("pi-coder", fi.CLASS_CODING, {"harness": "pi", "tag_project": True, "project": None}),
        (
            "Hal0::Agents",
            fi.CLASS_CODING,
            {"harness": "hal0-agents", "tag_project": True, "project": None},
        ),
        ("hal0-mono", fi.CLASS_UNCLASSIFIED, {}),
        ("Team::Agents", fi.CLASS_UNCLASSIFIED, {}),
    ],
)
def test_classifies_every_kind_of_bank(bank: str, cls: str, extra: dict[str, Any]) -> None:
    got = fi.classify_bank(bank, platform_bank="shared", targets=TARGETS)
    assert got.cls == cls
    for key, value in extra.items():
        assert getattr(got, key) == value


def test_a_mapped_bank_is_a_coding_bank_whose_project_comes_from_its_tags() -> None:
    got = fi.classify_bank(
        "hal0-mono", platform_bank="shared", targets=TARGETS, maps={"hal0-mono": "Claude Code"}
    )
    assert (got.cls, got.harness, got.tag_project, got.project) == (
        fi.CLASS_CODING,
        "claude-code",
        True,
        None,
    )


def test_the_platform_bank_is_whatever_the_old_layout_recorded() -> None:
    assert fi.classify_bank("memory", platform_bank="memory", targets=TARGETS).cls == (
        fi.CLASS_PLATFORM
    )
    assert fi.classify_bank("shared", platform_bank="memory", targets=TARGETS).cls == (
        fi.CLASS_UNCLASSIFIED
    )


def test_owners_come_from_the_map_then_a_client_then_the_pseudo_owner() -> None:
    owners = {"claude-code": "laptop", **fi.DEFAULT_OWNERS}
    assert fi.resolve_owner("claude-code", owners=owners, known={"codex"}) == ("laptop", "map")
    assert fi.resolve_owner("Codex", owners=owners, known={"codex"}) == ("codex", "client")
    assert fi.resolve_owner("pi", owners=owners, known=()) == ("pi-coder", "map")
    assert fi.resolve_owner("cursor", owners=owners, known=()) == ("hal0-legacy-cursor", "pseudo")
    assert fi.resolve_owner(None, owners=owners, known=()) == ("hal0-legacy-unowned", "pseudo")
    assert fi.resolve_owner("unknown", owners=owners, known=()) == ("hal0-legacy-unowned", "pseudo")


def test_a_pseudo_owner_is_reserved_so_no_client_can_take_it() -> None:
    from hal0.bridge.keys import is_reserved_name, valid_name

    name = fi.pseudo_owner("Claude Code!")
    assert name == "hal0-legacy-claude-code"
    assert valid_name(name) and is_reserved_name(name)
    assert len(fi.pseudo_owner("x" * 200)) <= 64


# ── the private split ────────────────────────────────────────────────────


def _unified_platform_archive() -> bytes:
    return archive(
        "shared",
        [
            doc("note-1", [fact("the sky is blue", "agent:hermes", "topic")]),
            doc(
                "private:hermes:s1",
                [fact("my secret", "agent:hermes", "visibility:private")],
                "visibility:private",
            ),
            # A private fact in a document whose id and tags say nothing.
            doc("odd", [fact("public", "agent:brain"), fact("hidden", "visibility:private")]),
        ],
    )


def test_private_documents_go_to_the_private_archive_and_nothing_private_stays_shared() -> None:
    result = fi.rewrite_archive(_unified_platform_archive(), plan("shared"))
    shared, private = read(result.shared), read(result.private)
    assert [d["id"] for d in shared["documents"]] == ["note-1"]
    assert sorted(d["id"] for d in private["documents"]) == [
        "private:brain:odd",
        "private:hermes:s1",
    ]
    assert fi.shared_archive_problems(result.shared) == []
    assert fi.private_archive_problems(result.private) == []
    flat = json.dumps(shared)
    assert "visibility:private" not in flat
    assert '"private:' not in flat
    assert result.report["private_facts"] == 3
    assert result.expected["shared"]["facts"] == 1
    assert result.expected["private"] == {
        "facts": 3,
        "observations": 0,
        "mental_models": 0,
        "directives": 0,
        "documents": 2,
    }


def test_every_private_fact_carries_its_owner_and_the_private_stamp() -> None:
    result = fi.rewrite_archive(_unified_platform_archive(), plan("shared"))
    for d in read(result.private)["documents"]:
        owner = d["id"].split(":")[1]
        for f in d["facts"]:
            assert f"agent:{owner}" in f["tags"]
            assert sum(t.startswith("agent:") for t in f["tags"]) == 1
            assert "visibility:private" in f["tags"]
            assert f["observation_scopes"][0][:2] == [f"agent:{owner}", "visibility:private"]


def test_a_private_bank_goes_to_the_private_side_under_its_agent() -> None:
    data = archive("private__hermes", [doc("s1", [fact("mine", "topic")])])
    result = fi.rewrite_archive(data, plan("private__hermes"))
    assert result.shared is None
    (d,) = read(result.private)["documents"]
    assert d["id"] == "private:hermes:s1"
    assert set(d["facts"][0]["tags"]) >= {"agent:hermes", "visibility:private", "topic"}


def test_a_private_fact_with_no_owner_at_all_is_filed_under_the_unowned_pseudo_owner() -> None:
    data = archive("shared", [doc("x", [fact("orphan", "visibility:private")])])
    result = fi.rewrite_archive(data, plan("shared"))
    (d,) = read(result.private)["documents"]
    assert d["id"] == "private:hal0-legacy-unowned:x"
    assert result.report["ownerless_facts"] == 1


def test_the_safety_check_refuses_a_shared_archive_with_anything_private() -> None:
    bad = archive(
        "shared",
        [doc("private:x:1", [fact("a")]), doc("y", [fact("b", "visibility:private")])],
        observations=[{"text": "o", "tags": ["visibility:private"], "sources": []}],
        models=[{"id": "m", "tags": ["Visibility:Private"]}],
    )
    problems = fi.shared_archive_problems(bad)
    assert len(problems) == 4


def test_rewrite_refuses_rather_than_build_an_unsafe_archive(monkeypatch) -> None:
    # Break both guards the split has (routing and the tag strip): the check still holds.
    monkeypatch.setattr(fi, "_document_private", lambda _plan, _doc: False)
    monkeypatch.setattr(fi, "_caller_tags", lambda tags: [str(t) for t in tags or ()])
    data = archive("shared", [doc("y", [fact("b", "visibility:private")])])
    with pytest.raises(fi.BankLeft, match="safety check"):
        fi.rewrite_archive(data, plan("shared"))


# ── owners and projects in coding banks ──────────────────────────────────


def test_a_coding_bank_files_its_facts_under_the_mapped_owner_and_its_project() -> None:
    data = archive(
        "claude-code::hal0-mono",
        [doc("conversation:abc", [fact("we use ruff", "agent:claude-code", "session:abc")])],
    )
    result = fi.rewrite_archive(data, plan("claude-code::hal0-mono", owners={"claude-code": "lap"}))
    (d,) = read(result.shared)["documents"]
    # hal0-mono is an alias of hal0: the id and the tag use the canonical slug.
    assert d["id"] == "hal0::lap::conversation:abc"
    (f,) = d["facts"]
    assert "agent:lap" in f["tags"]
    assert "project:hal0" in f["tags"]
    assert "kind:durable" in f["tags"]
    assert not any(t.startswith("session") for t in f["tags"])
    assert f["metadata"]["session_id"] == "abc"
    assert f["observation_scopes"] == [["project:hal0"]]
    assert result.report["owners"]["claude-code"] == {
        "owner": "lap",
        "source": "map",
        "facts": 1,
        "part": "shared",
    }
    assert result.report["projects"] == {"hal0": 1}


def test_a_coding_bank_with_no_known_owner_folds_into_the_pseudo_owner() -> None:
    data = archive("coding-agent::web", [doc("note", [fact("x")])])
    result = fi.rewrite_archive(data, plan("coding-agent::web"))
    (d,) = read(result.shared)["documents"]
    assert d["id"] == "web::hal0-legacy-coding-agent::note"
    assert "agent:hal0-legacy-coding-agent" in d["facts"][0]["tags"]


def test_a_known_bridge_client_of_the_harness_name_is_the_owner() -> None:
    data = archive("codex::web", [doc("n", [fact("x", "agent:codex")])])
    result = fi.rewrite_archive(data, plan("codex::web", known={"codex"}))
    assert read(result.shared)["documents"][0]["id"] == "web::codex::n"


def test_shared_prefix_ids_go_to_the_projects_shared_namespace() -> None:
    data = archive("coding-agent::web", [doc("git:abc123", [fact("commit")])])
    result = fi.rewrite_archive(data, plan("coding-agent::web"))
    assert read(result.shared)["documents"][0]["id"] == "web::shared::git:abc123"


def test_an_id_too_long_to_address_is_digested_and_the_clients_id_kept_in_metadata() -> None:
    long_id = "conversation:" + "x" * 300
    data = archive("coding-agent::web", [doc(long_id, [fact("x")])])
    result = fi.rewrite_archive(data, plan("coding-agent::web"))
    (d,) = read(result.shared)["documents"]
    assert d["id"] == stored_document_id("web", "hal0-legacy-coding-agent", long_id, ("git:",))
    assert d["facts"][0]["metadata"][CLIENT_DOCUMENT_ID_KEY] == long_id
    assert result.report["document_ids_digested"] == 1


def test_pi_coder_takes_each_facts_own_project_and_pi_is_filed_under_pi_coder() -> None:
    data = archive(
        "pi-coder",
        [
            doc("a", [fact("one", "project:hal0-mono"), fact("two", "project:web")]),
            doc("b", [fact("three")]),
        ],
    )
    result = fi.rewrite_archive(data, plan("pi-coder"))
    docs = {d["facts"][0]["text"]: d for d in read(result.shared)["documents"]}
    first = docs["one"]
    # Two projects in one document: the id has none, each fact keeps its own.
    assert first["id"] == "unresolved::pi-coder::a"
    assert "project:hal0" in first["facts"][0]["tags"]
    assert "project:web" in first["facts"][1]["tags"]
    assert "agent:pi-coder" in first["facts"][0]["tags"]
    # A coding fact with no project is unresolved, never global.
    assert "project:unresolved" in docs["three"]["facts"][0]["tags"]
    assert "scope:global" not in docs["three"]["facts"][0]["tags"]
    assert result.report["projects"] == {"hal0": 1, "web": 1, "unresolved": 1}


def test_a_denied_project_name_is_no_project() -> None:
    data = archive("Hal0::Agents", [doc("a", [fact("x", "project:tmp")])])
    result = fi.rewrite_archive(data, plan("Hal0::Agents"))
    tags = read(result.shared)["documents"][0]["facts"][0]["tags"]
    assert "project:tmp" not in tags and "project:unresolved" in tags


def test_platform_facts_keep_their_agent_and_get_scope_global_without_a_project() -> None:
    data = archive("shared", [doc("n", [fact("x", "agent:brain"), fact("y", "project:web")])])
    result = fi.rewrite_archive(data, plan("shared"))
    (d,) = read(result.shared)["documents"]
    assert d["id"] == "n"
    first, second = d["facts"]
    assert "agent:brain" in first["tags"] and "scope:global" in first["tags"]
    assert "project:web" in second["tags"] and "scope:global" not in second["tags"]


def test_a_guid_document_is_carried_as_it_is() -> None:
    guid = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    data = archive("shared", [doc(guid, [fact("x", "agent:hermes")])])
    result = fi.rewrite_archive(data, plan("shared"))
    assert read(result.shared)["documents"][0]["id"] == guid
    assert result.report["document_ids_rewritten"] == 0


# ── observations ─────────────────────────────────────────────────────────


def test_observations_follow_their_source_facts_and_a_split_one_is_dropped() -> None:
    data = archive(
        "shared",
        [
            doc("pub", [fact("a", "agent:brain", "project:web")]),
            doc("private:hermes:s", [fact("b", "agent:hermes", "visibility:private")]),
            doc("private:pi-coder:t", [fact("c", "agent:pi-coder", "visibility:private")]),
        ],
        observations=[
            {
                "text": "shared obs",
                "tags": ["project:old"],
                "sources": [{"document_id": "pub", "fact_index": 0}],
            },
            {
                "text": "private obs",
                "tags": [],
                "sources": [{"document_id": "private:hermes:s", "fact_index": 0}],
            },
            {
                "text": "split",
                "tags": [],
                "sources": [
                    {"document_id": "pub", "fact_index": 0},
                    {"document_id": "private:hermes:s", "fact_index": 0},
                ],
            },
            {
                "text": "two owners",
                "tags": [],
                "sources": [
                    {"document_id": "private:hermes:s", "fact_index": 0},
                    {"document_id": "private:pi-coder:t", "fact_index": 0},
                ],
            },
            {"text": "nowhere", "tags": [], "sources": [{"document_id": "gone", "fact_index": 0}]},
        ],
    )
    result = fi.rewrite_archive(data, plan("shared"))
    (shared_obs,) = read(result.shared)["observations"]
    assert shared_obs["tags"] == ["project:web"]
    assert shared_obs["observation_scopes"] == [["project:web"]]
    (private_obs,) = read(result.private)["observations"]
    assert private_obs["sources"] == [{"document_id": "private:hermes:s", "fact_index": 0}]
    assert {"agent:hermes", "visibility:private"} <= set(private_obs["tags"])
    assert result.report["observations"] == {
        "shared": 1,
        "private": 1,
        "dropped_split": 2,
        "dropped_unrouted": 1,
    }
    assert result.expected["shared"]["observations"] == 1


def test_an_observation_points_at_the_rewritten_document_id() -> None:
    data = archive(
        "coding-agent::web",
        [doc("n", [fact("x")])],
        observations=[
            {"text": "o", "tags": [], "sources": [{"document_id": "n", "fact_index": 0}]}
        ],
    )
    result = fi.rewrite_archive(data, plan("coding-agent::web"))
    (obs,) = read(result.shared)["observations"]
    assert obs["sources"][0]["document_id"] == "web::hal0-legacy-coding-agent::n"


# ── mental models, knowledge pages, directives ──────────────────────────


def _models_archive(bank: str, *documents: dict[str, Any]) -> bytes:
    return archive(
        bank,
        list(documents) or [doc("n", [fact("x")])],
        models=[
            {"id": "conventions", "name": "Conventions", "tags": [], "content": "use ruff"},
            {"id": "page-model", "name": "Page", "tags": [], "content": "page text"},
            {"id": "mine", "name": "Mine", "tags": ["visibility:private"], "content": "secret"},
        ],
        pages=[
            {"id": "kf-1", "kind": "folder", "name": "F", "parent_id": None},
            {
                "id": "kp-1",
                "kind": "page",
                "name": "P",
                "parent_id": "kf-1",
                "mental_model_id": "page-model",
            },
            {
                "id": "kp-2",
                "kind": "page",
                "name": "Q",
                "parent_id": "kf-1",
                "mental_model_id": "mine",
            },
            {"id": "kf-2", "kind": "folder", "name": "Empty", "parent_id": None},
        ],
        directives=[{"id": "d1", "name": "Be terse", "content": "be terse"}],
    )


def test_a_coding_banks_models_are_rekeyed_and_tagged_with_its_project() -> None:
    result = fi.rewrite_archive(_models_archive("coding-agent::web"), plan("coding-agent::web"))
    out = read(result.shared)
    assert {m["id"] for m in out["mental_models"]} == {"web-conventions", "web-page-model"}
    assert all("project:web" in m["tags"] for m in out["mental_models"])
    assert {p["id"] for p in out["knowledge_pages"]} == {"kf-1", "kp-1"}
    page = next(p for p in out["knowledge_pages"] if p["id"] == "kp-1")
    assert page["mental_model_id"] == "web-page-model"
    assert result.report["mental_models"] == {
        "carried": 2,
        "renamed": 2,
        "dropped_private": 1,
        "content_cleared": 0,
    }
    assert result.report["knowledge_pages"] == {"carried": 2, "dropped": 2}
    assert result.expected["shared"]["mental_models"] == 2
    assert out["mental_models"][0]["content"] == "use ruff"


def test_the_platform_banks_models_keep_their_ids() -> None:
    result = fi.rewrite_archive(_models_archive("shared"), plan("shared"))
    assert {m["id"] for m in read(result.shared)["mental_models"]} == {"conventions", "page-model"}


def test_models_of_a_bank_that_held_private_facts_lose_what_they_generated() -> None:
    data = _models_archive(
        "shared",
        doc("n", [fact("x", "agent:brain")]),
        doc("private:hermes:s", [fact("secret", "agent:hermes", "visibility:private")]),
    )
    result = fi.rewrite_archive(data, plan("shared"))
    models = read(result.shared)["mental_models"]
    assert all(m["content"] == "" for m in models)
    assert "secret" not in json.dumps(read(result.shared))
    assert result.report["mental_models"]["content_cleared"] == 2


def test_a_private_bank_gives_no_mental_model() -> None:
    result = fi.rewrite_archive(_models_archive("private__hermes"), plan("private__hermes"))
    assert result.shared is None
    assert result.report["mental_models"]["dropped_private"] == 3


def test_directives_are_listed_and_never_carried() -> None:
    result = fi.rewrite_archive(_models_archive("coding-agent::web"), plan("coding-agent::web"))
    assert result.report["directives_not_carried"] == ["Be terse"]
    with zipfile.ZipFile(io.BytesIO(result.shared or b"")) as zf:
        assert "directives.json" not in zf.namelist()
    assert result.expected["shared"]["directives"] == 0


def test_a_model_id_always_fits_the_engines_column() -> None:
    assert fi.model_id_for("web", "Conventions") == "web-conventions"
    long = fi.model_id_for("a-very-long-project-name-indeed", "m" * 80)
    assert len(long) <= fi.MODEL_ID_MAX
    assert long == fi.model_id_for("a-very-long-project-name-indeed", "m" * 80)


def test_the_rewritten_archive_is_one_a_merge_import_reads() -> None:
    result = fi.rewrite_archive(_models_archive("coding-agent::web"), plan("coding-agent::web"))
    with zipfile.ZipFile(io.BytesIO(result.shared or b"")) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        names = zf.namelist()
    assert manifest["schema_version"] == 1
    assert manifest["source_bank_id"] == "coding-agent::web"
    assert manifest["document_count"] == 1
    assert "mental_models.json" in names and "knowledge_pages.json" in names
    assert result.ids["shared"] == {
        "documents": ["web::hal0-legacy-coding-agent::n"],
        "mental_models": ["web-conventions", "web-page-model"],
    }


# ── the dry run's analysis ───────────────────────────────────────────────


def test_the_dry_run_analysis_agrees_with_the_rewrite() -> None:
    p = plan("shared")
    result = fi.rewrite_archive(_unified_platform_archive(), p)
    documents = [
        {"id": "note-1"},
        {"id": "private:hermes:s1", "tags": ["visibility:private"]},
        {"id": "odd"},
    ]
    facts = [
        {"document_id": "note-1", "tags": ["agent:hermes", "topic"]},
        {"document_id": "private:hermes:s1", "tags": ["agent:hermes", "visibility:private"]},
        {"document_id": "odd", "tags": ["agent:brain"]},
        {"document_id": "odd", "tags": ["visibility:private"]},
    ]
    report = fi.analyse_listing(p, documents, facts)
    for key in ("documents", "facts", "private_facts", "ownerless_facts", "owners", "projects"):
        assert report[key] == result.report[key], key


# ── direct clients ───────────────────────────────────────────────────────


def test_direct_engine_clients_are_found_by_port_in_each_section() -> None:
    configs = [
        (
            "/home/a/.hindsight/coding-agent.json",
            {
                "apiUrl": "http://127.0.0.1:9177",
                "harnesses": {
                    "pi": {"apiUrl": "http://127.0.0.1:8080/api/memory/compat"},
                    "claude-code": {},
                    "codex": {"apiUrl": "http://localhost:9177/"},
                },
            },
        )
    ]
    found = fi.direct_engine_clients(configs, 9177)
    assert [f["harness"] for f in found] == ["(default)", "claude-code", "codex"]
    assert fi.direct_engine_clients(configs, None) == []
