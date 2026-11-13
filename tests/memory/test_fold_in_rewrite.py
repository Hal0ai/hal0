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
from hal0.memory.knowledge_pages import folded_node_id, placeholder_folder_id
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
        ("shared.switch-0123abcd", fi.CLASS_SCRATCH, {}),
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
        "dropped_private_tag": 0,
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
    new_kf = folded_node_id("coding-agent::web", "kf-1", "folder")
    new_kp = folded_node_id("coding-agent::web", "kp-1", "page")
    folder = placeholder_folder_id("web")
    assert {p["id"] for p in out["knowledge_pages"]} == {folder, new_kf, new_kp}
    page = next(p for p in out["knowledge_pages"] if p["id"] == new_kp)
    assert page["mental_model_id"] == "web-page-model"
    assert page["parent_id"] == new_kf
    assert result.report["mental_models"] == {
        "carried": 2,
        "renamed": 2,
        "private": 0,
        "dropped_private": 1,
        "content_cleared": 0,
    }
    assert result.report["knowledge_pages"] == {
        "carried": 2,
        "dropped": 2,
        "placed": 1,
        "unplaced": 0,
        # No --owner for the harness: the page is filed under its pseudo-owner, and named.
        "owners": {"hal0-legacy-coding-agent": 1},
        "pseudo_owned": ["P"],
    }
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


def test_a_private_banks_models_go_private_confined_to_their_owner() -> None:
    from hal0.memory.hindsight_provider import HindsightProvider
    from hal0.memory.read_scope import model_scope_confined_to

    result = fi.rewrite_archive(_models_archive("private__hermes"), plan("private__hermes"))
    assert result.shared is None
    models = read(result.private)["mental_models"]
    # The models behind pages are dropped with their pages; the other is carried.
    assert {m["id"] for m in models} == {"hermes-conventions"}
    assert read(result.private)["knowledge_pages"] == []
    assert result.report["mental_models"]["private"] == 1
    assert result.report["mental_models"]["dropped_private"] == 2
    assert result.report["knowledge_pages"]["dropped"] == 4
    assert result.expected["private"]["mental_models"] == 1
    assert result.ids["private"]["mental_models"] == ["hermes-conventions"]
    for model in models:
        assert {"agent:hermes", "visibility:private"} <= set(model["tags"])
        assert len(model["id"]) <= fi.MODEL_ID_MAX
        # The provider's read rule: visible to hermes alone, scope confined to hermes.
        assert HindsightProvider._is_visible_to(model, "hermes")
        assert not HindsightProvider._is_visible_to(model, "brain")
        assert model_scope_confined_to(model, "hermes")
        assert not model_scope_confined_to(model, "brain")
        assert model["trigger"]["exclude_mental_models"] is True
    # Built from hermes's own bank, so its content is kept.
    assert next(m for m in models if m["id"] == "hermes-conventions")["content"] == "use ruff"


def test_a_private_models_own_scope_is_kept_and_anded_with_its_owner() -> None:
    data = archive(
        "private__hermes",
        [doc("s1", [fact("mine")])],
        models=[
            {
                "id": "topic",
                "name": "T",
                "tags": ["topic"],
                "trigger": json.dumps({"tags_match": "any"}),
            },
            {
                "id": "grouped",
                "name": "G",
                "tags": [],
                "trigger": {"tag_groups": [{"tags": ["x"], "match": "any"}]},
            },
        ],
    )
    models = {
        m["id"]: m
        for m in read(fi.rewrite_archive(data, plan("private__hermes")).private)["mental_models"]
    }
    confine = {"tags": ["agent:hermes", "visibility:private"], "match": "all_strict"}
    assert models["hermes-topic"]["trigger"]["tag_groups"] == [
        {"tags": ["topic"], "match": "any"},
        confine,
    ]
    assert models["hermes-grouped"]["trigger"]["tag_groups"] == [
        {"tags": ["x"], "match": "any"},
        confine,
    ]


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
    for key in ("model_digests", "model_spec_digests", "model_content_digests"):
        assert set(result.ids["shared"].pop(key)) == {"web-conventions", "web-page-model"}
    assert set(result.ids["shared"].pop("page_nodes")) == {"kf-1", "kp-1"}
    assert result.ids["shared"].pop("project_folders") == {placeholder_folder_id("web"): "web"}
    assert result.ids["shared"].pop("page_names") == [
        ["web", "f", folded_node_id("coding-agent::web", "kf-1", "folder")]
    ]
    assert result.ids["shared"] == {
        "documents": ["web::hal0-legacy-coding-agent::n"],
        "mental_models": ["web-conventions", "web-page-model"],
        "pages": ["web-page-model"],
        "observations": [],
    }
    assert set(result.digests["shared"]) == {"web::hal0-legacy-coding-agent::n"}


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


def _listing_of(documents: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """The document and fact listings the engine would give for ``documents``."""
    docs = [{"id": d["id"], "tags": d["tags"]} for d in documents]
    facts = [{"document_id": d["id"], "tags": f["tags"]} for d in documents for f in d["facts"]]
    return docs, facts


_MODELS = [
    {"id": "conventions", "name": "Conventions", "tags": [], "content": "use ruff"},
    {"id": "page-model", "name": "Page", "tags": [], "content": "page text"},
    {"id": "mine", "name": "Mine", "tags": ["visibility:private"], "content": "secret"},
]


@pytest.mark.parametrize(
    ("bank", "documents"),
    [
        ("shared", [doc("n", [fact("x", "agent:brain")])]),
        (
            "shared",
            [
                doc("n", [fact("x", "agent:brain")]),
                doc("private:hermes:s", [fact("secret", "agent:hermes", "visibility:private")]),
            ],
        ),
        (
            "coding-agent::web",
            [doc("n", [fact("x"), fact("y", "visibility:private")])],
        ),
        ("private__hermes", [doc("n", [fact("x")])]),
    ],
)
def test_the_dry_run_counts_the_models_the_rewrite_clears(
    bank: str, documents: list[dict[str, Any]]
) -> None:
    # CT143: the dry run said 0 models would be cleared and the apply cleared 3.
    p = plan(bank)
    result = fi.rewrite_archive(_models_archive(bank, *documents), p)
    docs, facts = _listing_of(documents)
    # The models behind the archive's knowledge pages (``_models_archive``).
    report = fi.analyse_listing(p, docs, facts, _MODELS, ["page-model", "mine"])
    assert report["mental_models"] == {
        **result.report["mental_models"],
        # The dry run does not re-key; only the rewrite counts renames.
        "renamed": report["mental_models"]["renamed"],
    }
    assert report["documents"] == result.report["documents"]


def test_the_dry_run_says_a_bank_with_private_facts_clears_its_shared_models() -> None:
    documents = [
        doc("n", [fact("x", "agent:brain")]),
        doc("private:hermes:s", [fact("secret", "agent:hermes", "visibility:private")]),
    ]
    docs, facts = _listing_of(documents)
    report = fi.analyse_listing(plan("shared"), docs, facts, _MODELS, ["page-model"])
    assert report["mental_models"]["content_cleared"] == 2


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


# ── the routing check (review item 1) ─────────────────────────────────────


def _leaky_archive() -> bytes:
    # A private fact in a document whose id and tags say nothing: only the
    # routing (``_document_private``) sends it private. The tag strip removes
    # ``visibility:`` from whatever goes shared, so the tag check cannot see it.
    return archive(
        "shared",
        [
            doc("note", [fact("public", "agent:brain")]),
            doc("odd", [fact("public too", "agent:brain"), fact("hidden", "visibility:private")]),
        ],
    )


def test_the_routing_check_refuses_when_only_the_routing_is_broken(monkeypatch) -> None:
    monkeypatch.setattr(fi, "_document_private", lambda _plan, _doc: False)
    with pytest.raises(fi.BankLeft) as caught:
        fi.rewrite_archive(_leaky_archive(), plan("shared"))
    problems = caught.value.details["problems"]
    assert any("document odd is private but is in the shared archive" in p for p in problems)


def test_without_the_routing_check_the_broken_routing_would_leak(monkeypatch) -> None:
    # The mutation check of the guard: with routing_problems neutralised, the
    # same broken routing builds a shared archive that the tag check passes,
    # with the private fact in it. The routing check is what stops it.
    monkeypatch.setattr(fi, "_document_private", lambda _plan, _doc: False)
    monkeypatch.setattr(fi, "routing_problems", lambda *_a, **_k: [])
    result = fi.rewrite_archive(_leaky_archive(), plan("shared"))
    assert fi.shared_archive_problems(result.shared) == []
    assert "hidden" in json.dumps(read(result.shared))


def test_the_routing_check_passes_a_correct_rewrite() -> None:
    data = _leaky_archive()
    result = fi.rewrite_archive(data, plan("shared"))
    assert (
        fi.routing_problems(
            data, result.shared, result.private, result.provenance, bank_cls="platform"
        )
        == []
    )
    assert [d["id"] for d in read(result.private)["documents"]] == ["private:brain:odd"]


def test_the_routing_check_finds_a_private_observation_model_and_page_on_the_shared_side() -> None:
    source = archive(
        "shared",
        [doc("n", [fact("x")]), doc("private:hermes:s", [fact("s", "visibility:private")])],
        observations=[
            {"text": "pub", "tags": [], "sources": [{"document_id": "n", "fact_index": 0}]},
            {
                "text": "priv",
                "tags": [],
                "sources": [{"document_id": "private:hermes:s", "fact_index": 0}],
            },
        ],
        models=[{"id": "m", "tags": ["visibility:private"]}, {"id": "ok", "tags": []}],
        pages=[{"id": "kp", "kind": "page", "name": "P", "mental_model_id": "m"}],
    )
    # A hand-built shared archive that carries the private observation, model
    # and page, all stripped of their stamps, as a broken rewrite would.
    leaked = fi._write_archive(
        "shared",
        [{**doc("n", [fact("x")]), "tags": []}],
        [
            {"text": "pub", "tags": [], "sources": [{"document_id": "n", "fact_index": 0}]},
            {"text": "priv", "tags": [], "sources": [{"document_id": "n", "fact_index": 0}]},
        ],
        [{"id": "m", "tags": []}, {"id": "ok", "tags": []}],
        [{"id": "kp", "kind": "page", "name": "P", "mental_model_id": "m"}],
    )
    private = fi._write_archive(
        "shared",
        [doc("private:hermes:s", [fact("s", "agent:hermes", "visibility:private")])],
        [],
        [],
        [],
    )
    provenance = {
        "shared": {
            "documents": {"n": "n"},
            "observations": [0, 1],
            "mental_models": {"m": "m", "ok": "ok"},
            "pages": ["kp"],
        },
        "private": {
            "documents": {"private:hermes:s": "private:hermes:s"},
            "observations": [],
            "mental_models": {},
            "pages": [],
        },
        "dropped": {"observations": [], "mental_models": [], "pages": []},
    }
    assert fi.shared_archive_problems(leaked) == []
    problems = fi.routing_problems(source, leaked, private, provenance, bank_cls="platform")
    joined = "; ".join(problems)
    assert "observation 1 is private but is in the shared archive" in joined
    assert "mental model m is private but is in the shared archive" in joined
    assert "knowledge page kp is private but is in the shared archive" in joined


def test_the_routing_check_refuses_a_document_missing_from_both_archives() -> None:
    source = archive("shared", [doc("n", [fact("x")]), doc("p", [fact("y", "visibility:private")])])
    shared = fi._write_archive("shared", [doc("n", [fact("x")])], [], [], [])
    provenance = {
        "shared": {"documents": {"n": "n"}, "observations": [], "mental_models": {}, "pages": []},
        "private": {"documents": {}, "observations": [], "mental_models": {}, "pages": []},
        "dropped": {},
    }
    problems = fi.routing_problems(source, shared, None, provenance, bank_cls="platform")
    assert problems == ["source document p is in neither archive"]


def _one_shared_doc_provenance(observations: list[int], models: dict[str, str]) -> dict:
    return {
        "shared": {
            "documents": {"n": "n"},
            "observations": observations,
            "mental_models": models,
            "pages": [],
        },
        "private": {"documents": {}, "observations": [], "mental_models": {}, "pages": []},
        "dropped": {"observations": [], "mental_models": [], "pages": []},
    }


def test_the_routing_check_finds_an_observation_private_by_its_own_tag_on_the_shared_side() -> None:
    # Its source is shared; only its own visibility:private says it is private.
    # A broken rewrite stripped the stamp and sent it shared.
    obs = {
        "text": "says private",
        "tags": ["visibility:private"],
        "sources": [{"document_id": "n", "fact_index": 0}],
    }
    source = archive("shared", [doc("n", [fact("x")])], observations=[obs])
    leaked = fi._write_archive("shared", [doc("n", [fact("x")])], [{**obs, "tags": []}], [], [])
    assert fi.shared_archive_problems(leaked) == []
    problems = fi.routing_problems(
        source, leaked, None, _one_shared_doc_provenance([0], {}), bank_cls="platform"
    )
    assert "observation 0 is private but is in the shared archive" in problems
    assert "observation 0 is private but is not on the private side" in problems


def test_the_routing_check_finds_a_shared_model_that_kept_what_private_facts_generated() -> None:
    source = archive(
        "shared",
        [doc("n", [fact("x")]), doc("p", [fact("y", "visibility:private")])],
        models=[{"id": "m", "tags": [], "content": "built from p"}],
    )
    private = fi._write_archive(
        "shared", [doc("private:unknown:p", [fact("y", "visibility:private")])], [], [], []
    )
    provenance = _one_shared_doc_provenance([], {"m": "m"})
    provenance["private"]["documents"] = {"private:unknown:p": "p"}
    kept = fi._write_archive(
        "shared",
        [doc("n", [fact("x")])],
        [],
        [{"id": "m", "tags": [], "content": "built from p"}],
        [],
    )
    problems = fi.routing_problems(source, kept, private, provenance, bank_cls="platform")
    assert any("shared mental model m keeps content" in p for p in problems)
    cleared = fi._write_archive(
        "shared", [doc("n", [fact("x")])], [], [{"id": "m", "tags": [], "content": ""}], []
    )
    assert fi.routing_problems(source, cleared, private, provenance, bank_cls="platform") == []


# ── review items 4, 7, 8, 9 ──────────────────────────────────────────────


def test_a_shared_observation_tagged_private_is_dropped_not_published() -> None:
    data = archive(
        "shared",
        [doc("pub", [fact("a", "agent:brain")])],
        observations=[
            {
                "text": "says private",
                "tags": ["visibility:private", "agent:brain"],
                "sources": [{"document_id": "pub", "fact_index": 0}],
            }
        ],
    )
    result = fi.rewrite_archive(data, plan("shared"))
    assert read(result.shared)["observations"] == []
    assert result.report["observations"]["dropped_private_tag"] == 1
    assert result.expected["shared"]["observations"] == 0
    assert result.provenance["dropped"]["observations"] == [0]


def test_the_report_counts_shared_facts_no_agent_can_read() -> None:
    data = archive(
        "Hal0::Agents",
        [doc("n", [fact("no project", "agent:claude-code")]), doc("m", [fact("p", "project:web")])],
    )
    p = plan("Hal0::Agents")
    result = fi.rewrite_archive(data, p)
    # project:unresolved under hal0-legacy-claude-code: readable by nobody.
    assert result.report["unreadable_shared_facts"] == 1
    listing = fi.analyse_listing(
        p,
        [{"id": "n"}, {"id": "m"}],
        [
            {"document_id": "n", "tags": ["agent:claude-code"]},
            {"document_id": "m", "tags": ["project:web"]},
        ],
    )
    assert listing["unreadable_shared_facts"] == 1


def test_a_private_owner_in_the_platform_bank_is_lowercased_and_follows_the_map() -> None:
    data = archive(
        "shared",
        [
            doc("a", [fact("x", "agent:Hermes", "visibility:private")]),
            doc("b", [fact("y", "agent:claude-code", "visibility:private")]),
        ],
    )
    result = fi.rewrite_archive(data, plan("shared", owners={"claude-code": "laptop"}))
    ids = sorted(d["id"] for d in read(result.private)["documents"])
    assert ids == ["private:hermes:a", "private:laptop:b"]
    assert result.report["owners"]["Hermes"]["source"] == "agent"
    assert result.report["owners"]["claude-code"]["source"] == "map"


def test_a_private_document_with_two_owners_refuses_the_bank() -> None:
    data = archive(
        "shared",
        [
            doc(
                "mixed",
                [
                    fact("a", "agent:hermes", "visibility:private"),
                    fact("b", "agent:brain", "visibility:private"),
                ],
            )
        ],
    )
    with pytest.raises(fi.BankLeft, match="more than one owner") as caught:
        fi.rewrite_archive(data, plan("shared"))
    assert caught.value.details["mixed_owner_documents"] == [
        {"document": "mixed", "owners": ["brain", "hermes"]}
    ]
    listing = fi.analyse_listing(
        plan("shared"),
        [{"id": "mixed"}],
        [
            {"document_id": "mixed", "tags": ["agent:hermes", "visibility:private"]},
            {"document_id": "mixed", "tags": ["agent:brain", "visibility:private"]},
        ],
    )
    assert listing["mixed_owner_documents"] == [
        {"document": "mixed", "owners": ["brain", "hermes"]}
    ]


def test_a_document_digest_ignores_what_the_engine_moves_by_itself() -> None:
    base = doc("d", [fact("x", "t")])
    moved = json.loads(json.dumps(base))
    moved["facts"][0].update(
        {"consolidated_at": "2026-11-12", "source_id": "u1", "entities": ["E"], "created_at": "z"}
    )
    assert fi.document_digest(base) == fi.document_digest(moved)
    appended = json.loads(json.dumps(base))
    appended["facts"].append(fact("y"))
    assert fi.document_digest(base) != fi.document_digest(appended)


def test_a_model_row_with_a_null_in_a_not_null_column_is_refused() -> None:
    """The engine's insert fails on it after the documents landed; refuse before sending."""
    ok = {"id": "m", "name": "M", "content": "", "last_refreshed_at": fi.NEVER_REFRESHED_AT}
    assert fi.model_null_problems([ok]) == []
    assert fi.model_null_problems([{"id": "m", "content": "x"}]) == []
    (problem,) = fi.model_null_problems([{**ok, "last_refreshed_at": None, "content": None}])
    assert "content, last_refreshed_at would be NULL" in problem


# ── a coding bank's knowledge pages (#3635) ─────────────────────────────

_PLUGIN_TRIGGER = {
    "fact_types": ["world", "experience", "observation"],
    "tags_match": "all",
    "refresh_after_consolidation": True,
}


def _pages_archive(bank: str, *documents: dict[str, Any], tags: list[str] | None = None) -> bytes:
    return archive(
        bank,
        list(documents) or [doc("n", [fact("x")])],
        models=[
            {
                "id": "mm-map",
                "name": "Component map",
                "source_query": "How is it put together?",
                "tags": ["knowledge:component", *(tags or [])],
                "trigger": json.dumps({**_PLUGIN_TRIGGER, "tag_groups": [{"tags": ["x"]}]}),
                "content": "map",
            },
            {
                "id": "mm-init",
                "name": "Bank layout",
                "source_query": "Summarize the initiative",
                "tags": ["knowledge:feature-work", *(tags or [])],
                "trigger": _PLUGIN_TRIGGER,
                "content": "See [[page:kp-map]], [[page:kp-gone]].",
            },
        ],
        pages=[
            {"id": "kp-map", "kind": "page", "name": "Component map", "mental_model_id": "mm-map"},
            {"id": "kf-init", "kind": "folder", "name": "Initiatives", "parent_id": None},
            {
                "id": "kp-init",
                "kind": "page",
                "name": "Bank layout",
                "parent_id": "kf-init",
                "mental_model_id": "mm-init",
            },
        ],
    )


def _shared_pages(result: fi.RewriteResult) -> tuple[dict[str, Any], dict[str, Any]]:
    out = read(result.shared)
    return (
        {p["id"]: p for p in out["knowledge_pages"]},
        {m["id"]: m for m in out["mental_models"]},
    )


def test_a_coding_banks_pages_are_hung_in_its_project_folder_under_new_ids() -> None:
    result = fi.rewrite_archive(_pages_archive("coding-agent::web"), plan("coding-agent::web"))
    nodes, _ = _shared_pages(result)
    folder = placeholder_folder_id("web")
    new = {
        old: folded_node_id("coding-agent::web", old, k)
        for old, k in (("kp-map", "page"), ("kf-init", "folder"), ("kp-init", "page"))
    }
    assert nodes[folder] == {
        "id": folder,
        "kind": "folder",
        "name": "project:web",
        "parent_id": None,
        "mental_model_id": None,
        "sort_order": 0,
        "managed": False,
    }
    assert nodes[new["kp-map"]]["parent_id"] == folder
    assert nodes[new["kf-init"]]["parent_id"] == folder
    assert nodes[new["kp-init"]]["parent_id"] == new["kf-init"]
    assert nodes[new["kp-init"]]["name"] == "Bank layout"
    assert result.provenance["shared"]["pages"] == {v: k for k, v in new.items()}
    assert result.ids["shared"]["page_nodes"]["kp-init"] == {
        "id": new["kp-init"],
        "kind": "page",
        "name": "Bank layout",
        "project": "web",
        "refreshed_at": None,
    }
    # Stable across runs: a resend sends the same nodes under the same ids.
    again = fi.rewrite_archive(_pages_archive("coding-agent::web"), plan("coding-agent::web"))
    assert again.ids["shared"]["page_nodes"] == result.ids["shared"]["page_nodes"]


def test_a_placed_page_is_stamped_as_compat_stamps_a_page_it_writes() -> None:
    from hal0.memory import knowledge_pages as kp
    from hal0.memory.projects import resolve_project
    from hal0.memory.read_scope import project_scope_groups, reflect_visibility_scope

    p = plan("coding-agent::web", owners={"coding-agent": "cc"}, known={"cc"})
    result = fi.rewrite_archive(_pages_archive("coding-agent::web"), p)
    _, models = _shared_pages(result)
    model = models["web-mm-map"]
    resolution = resolve_project(bank_segment="web", registry=p.registry, coding=True)
    labels, leaf = kp.page_tags(["knowledge:component"], agent="cc", resolution=resolution)
    assert model["tags"] == labels
    # The plugin's refresh settings, hal0's scope; the old tag_groups are gone.
    assert model["trigger"] == kp.page_trigger(_PLUGIN_TRIGGER, leaf, "web")
    assert {"tags": ["x"]} not in model["trigger"]["tag_groups"]
    assert (model["name"], model["source_query"], model["content"]) == (
        "Component map",
        "How is it put together?",
        "map",
    )
    groups = [*reflect_visibility_scope("cc"), *project_scope_groups("web")]
    node = {"tags": model["tags"], "trigger": model["trigger"]}
    assert kp.page_scope_held(node, read_groups=groups, slug="web")
    other = [*reflect_visibility_scope("cc"), *project_scope_groups("atlas")]
    assert not kp.page_scope_held(node, read_groups=other, slug="atlas")


def test_page_links_in_content_name_the_new_ids() -> None:
    result = fi.rewrite_archive(_pages_archive("coding-agent::web"), plan("coding-agent::web"))
    _, models = _shared_pages(result)
    new_map = folded_node_id("coding-agent::web", "kp-map", "page")
    assert models["web-mm-init"]["content"] == f"See [[page:{new_map}]], [[page:kp-gone]]."


def test_a_tag_project_banks_pages_go_by_their_own_project_or_stay_at_the_root() -> None:
    data = archive(
        "pi-coder",
        [doc("n", [fact("x", "project:web")])],
        models=[
            {"id": "m-web", "name": "W", "tags": ["project:web"], "content": "w"},
            {"id": "m-none", "name": "N", "tags": [], "content": "n", "trigger": {"x": 1}},
        ],
        pages=[
            {"id": "kp-web", "kind": "page", "name": "W", "mental_model_id": "m-web"},
            {"id": "kp-none", "kind": "page", "name": "N", "mental_model_id": "m-none"},
        ],
    )
    result = fi.rewrite_archive(data, plan("pi-coder"))
    nodes, models = _shared_pages(result)
    placed = nodes[folded_node_id("pi-coder", "kp-web", "page")]
    unplaced = nodes[folded_node_id("pi-coder", "kp-none", "page")]
    assert placed["parent_id"] == placeholder_folder_id("web")
    assert unplaced["parent_id"] is None
    # Pi's pages are pi-coder's (DEFAULT_OWNERS), like its documents.
    assert "agent:pi-coder" in models[placed["mental_model_id"]]["tags"]
    assert models[unplaced["mental_model_id"]]["trigger"] == {"x": 1}
    assert result.report["knowledge_pages"]["placed"] == 1
    assert result.report["knowledge_pages"]["unplaced"] == 1
    assert result.ids["shared"]["page_nodes"]["kp-none"]["project"] is None


def test_a_placed_page_of_a_bank_with_private_facts_keeps_nothing_generated() -> None:
    data = _pages_archive(
        "coding-agent::web",
        doc("n", [fact("x")]),
        doc("s", [fact("secret", "visibility:private", "agent:cc")]),
    )
    result = fi.rewrite_archive(data, plan("coding-agent::web"))
    _, models = _shared_pages(result)
    assert models["web-mm-init"]["content"] == ""
    assert "secret" not in json.dumps(read(result.shared))


def test_a_private_page_is_never_placed() -> None:
    data = _pages_archive("coding-agent::web", tags=["visibility:private"])
    result = fi.rewrite_archive(data, plan("coding-agent::web"))
    assert read(result.shared)["knowledge_pages"] == []
    assert result.report["knowledge_pages"]["dropped"] == 3


def _placed(bank: str = "coding-agent::web") -> tuple[bytes, fi.RewriteResult]:
    data = _pages_archive(bank)
    return data, fi.rewrite_archive(data, plan(bank))


def _tampered(result: fi.RewriteResult, change) -> bytes:
    out = read(result.shared)
    change(out)
    return fi._write_archive(
        "coding-agent::web",
        out["documents"],
        out["observations"],
        out["mental_models"],
        out["knowledge_pages"],
    )


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            lambda a: next(m for m in a["mental_models"] if m["id"] == "web-mm-map")["trigger"][
                "tag_groups"
            ].pop(),
            "lacks the project scope and the private guard",
        ),
        (
            lambda a: next(m for m in a["mental_models"] if m["id"] == "web-mm-map")[
                "trigger"
            ].update(exclude_mental_models=False),
            "lacks the project scope and the private guard",
        ),
        (
            lambda a: next(m for m in a["mental_models"] if m["id"] == "web-mm-init")[
                "trigger"
            ].update(tag_groups=[]),
            "lacks the project scope and the private guard",
        ),
        (
            lambda a: a["knowledge_pages"].append(
                {"id": "kp-stray", "kind": "page", "name": "S", "mental_model_id": "web-mm-map"}
            ),
            "shared knowledge page kp-stray comes from no source page",
        ),
        (
            lambda a: next(p for p in a["knowledge_pages"] if p["kind"] == "folder").update(
                kind="page", mental_model_id="web-mm-map"
            ),
            "is not a folder",
        ),
    ],
)
def test_the_routing_check_refuses_a_shown_page_without_compats_scope(change, expected) -> None:
    """Mutation check of the page guard: each way of breaking the stamp is caught."""
    source, result = _placed()
    assert (
        fi.routing_problems(
            source, result.shared, result.private, result.provenance, bank_cls="coding"
        )
        == []
    )
    bad = _tampered(result, change)
    problems = fi.routing_problems(
        source, bad, result.private, result.provenance, bank_cls="coding"
    )
    assert any(expected in p for p in problems), problems


def test_without_the_restamp_a_placed_page_fails_the_routing_check(monkeypatch) -> None:
    """Mutation check of the re-stamp itself: with the trigger left as the plugin
    wrote it, the rewrite refuses its own archive."""
    from hal0.memory import knowledge_pages as kp

    monkeypatch.setattr(kp, "page_trigger", lambda chosen, leaf, slug: dict(chosen))
    with pytest.raises(fi.BankLeft) as caught:
        fi.rewrite_archive(_pages_archive("coding-agent::web"), plan("coding-agent::web"))
    assert any("lacks the project scope" in p for p in caught.value.details["problems"])


def test_rehome_swaps_the_placeholder_for_the_targets_folder() -> None:
    _, result = _placed()
    folder = placeholder_folder_id("web")
    out = read(fi.rehome_folders(result.shared, {folder: "kf-real"}))
    ids = {p["id"] for p in out["knowledge_pages"]}
    assert folder not in ids and "kf-real" in ids
    assert folder not in json.dumps(out["knowledge_pages"])
    tops = [p for p in out["knowledge_pages"] if p.get("parent_id") == "kf-real"]
    assert len(tops) == 2
    assert out["mental_models"] == read(result.shared)["mental_models"]


def test_the_platform_banks_pages_keep_their_ids_and_scope() -> None:
    data = archive(
        "shared",
        [doc("n", [fact("x")])],
        models=[{"id": "m", "name": "M", "tags": [], "content": "c", "trigger": {"a": 1}}],
        pages=[
            {"id": "kf-p", "kind": "folder", "name": "project:web", "parent_id": None},
            {"id": "kp", "kind": "page", "name": "P", "parent_id": "kf-p", "mental_model_id": "m"},
        ],
    )
    result = fi.rewrite_archive(data, plan("shared"))
    nodes, models = _shared_pages(result)
    assert set(nodes) == {"kf-p", "kp"}
    assert models["m"]["trigger"] == {"a": 1}
    assert "page_nodes" not in result.ids["shared"]
    assert result.ids["shared"]["page_names"] == [["web", "p", "kp"]]
