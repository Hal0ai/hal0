"""hal0.registry.fileset — file-SET pulling (ML-2).

Covers: role_of classification, plan_fileset shard grouping + deterministic
mmproj tiebreak, and enumerate_repo's recursive+paginated HF tree walk
(mocked Link header pagination via httpx.MockTransport, per the existing
tests/registry/test_pull.py convention).
"""

from __future__ import annotations

import json

import httpx
import pytest

from hal0.registry.fileset import (
    SHARD_RE,
    FilesetEmpty,
    FilesetVariantNotFound,
    RawTreeEntry,
    enumerate_repo,
    plan_fileset,
    resolve_revision,
    role_of,
)


class TestShardRe:
    def test_matches_full_filename_with_extension(self) -> None:
        assert SHARD_RE.match("model-00001-of-00003.gguf")
        assert SHARD_RE.match("model-00002-of-00003.safetensors")

    def test_no_match_without_extension_or_wrong_shape(self) -> None:
        assert SHARD_RE.match("model-00001-of-00003") is None  # discover's OLD stem-only shape
        assert SHARD_RE.match("model.gguf") is None
        assert SHARD_RE.match("model-1-of-3.gguf") is None  # needs 5-digit zero-pad


class TestRoleOf:
    def test_model_role(self) -> None:
        assert role_of("model.gguf") == "model"
        assert role_of("model.safetensors") == "model"

    def test_shard_role(self) -> None:
        assert role_of("model-00001-of-00003.gguf") == "shard"

    def test_mmproj_role(self) -> None:
        assert role_of("mmproj-F16.gguf") == "mmproj"
        assert role_of("subdir/mmproj-model-f32.gguf") == "mmproj"

    def test_tokenizer_role(self) -> None:
        assert role_of("tokenizer.json") == "tokenizer"
        assert role_of("tokenizer.model") == "tokenizer"

    def test_config_role(self) -> None:
        assert role_of("config.json") == "config"
        assert role_of("chat_template.jinja") == "config"
        assert role_of("README.md") == "config"  # low-priority carried, per spec


def _entry(rel: str, size: int = 0, sha: str | None = None) -> RawTreeEntry:
    return RawTreeEntry(path=rel, size=size, lfs_oid=sha, lfs_size=size)


class TestPlanFilesetShardGrouping:
    def test_single_file_repo(self) -> None:
        entries = [_entry("model.gguf", 1000, "sha-a")]
        plan = plan_fileset(entries, repo="org/repo", revision="deadbeef")
        assert plan.entry_rel == "model.gguf"
        assert [f.rel for f in plan.files] == ["model.gguf"]
        assert plan.files[0].role == "model"
        assert plan.files[0].shard_index is None
        assert plan.total_bytes == 1000

    def test_multi_shard_grouped_in_order(self) -> None:
        entries = [
            _entry("model-00002-of-00003.gguf", 200),
            _entry("model-00001-of-00003.gguf", 100),
            _entry("model-00003-of-00003.gguf", 300),
        ]
        plan = plan_fileset(entries, repo="org/repo", revision="deadbeef")
        assert plan.entry_rel == "model-00001-of-00003.gguf"
        rels = [f.rel for f in plan.files]
        assert rels == [
            "model-00001-of-00003.gguf",
            "model-00002-of-00003.gguf",
            "model-00003-of-00003.gguf",
        ]
        assert [f.shard_index for f in plan.files] == [1, 2, 3]
        assert all(f.role == "shard" for f in plan.files)
        assert plan.total_bytes == 600

    def test_largest_unit_wins_without_requested_variant(self) -> None:
        entries = [_entry("small.gguf", 100), _entry("big.gguf", 900)]
        plan = plan_fileset(entries, repo="org/repo", revision="rev")
        assert plan.entry_rel == "big.gguf"

    def test_requested_variant_restricts_choice(self) -> None:
        entries = [_entry("small.gguf", 100), _entry("big.gguf", 900)]
        plan = plan_fileset(
            entries, repo="org/repo", revision="rev", requested_variant="small.gguf"
        )
        assert plan.entry_rel == "small.gguf"

    def test_requested_variant_not_found_raises(self) -> None:
        entries = [_entry("model.gguf", 100)]
        with pytest.raises(FilesetVariantNotFound):
            plan_fileset(entries, repo="org/repo", revision="rev", requested_variant="ghost.gguf")

    def test_empty_entries_raises_fileset_empty(self) -> None:
        with pytest.raises(FilesetEmpty):
            plan_fileset(
                [_entry("tokenizer.json"), _entry("config.json")], repo="org/repo", revision="rev"
            )

    def test_tokenizer_and_config_carried_from_same_dir(self) -> None:
        entries = [
            _entry("variant-a/model.safetensors", 900),
            _entry("variant-a/tokenizer.json", 5),
            _entry("variant-a/config.json", 2),
            _entry("variant-b/other.json", 5),  # different dir, must not be carried
        ]
        plan = plan_fileset(entries, repo="org/repo", revision="rev")
        rels = {f.rel for f in plan.files}
        assert "variant-a/tokenizer.json" in rels
        assert "variant-a/config.json" in rels
        assert "variant-b/other.json" not in rels


class TestMmprojTiebreak:
    def test_quant_affinity_wins_when_present(self) -> None:
        entries = [
            _entry("model-Q4_K_M.gguf", 900),
            _entry("mmproj-Q4_K_M.gguf", 50),
            _entry("mmproj-F32.gguf", 100),
        ]
        plan = plan_fileset(entries, repo="org/repo", revision="rev")
        assert plan.mmproj_rel == "mmproj-Q4_K_M.gguf"
        assert plan.mmproj_tiebreak_reason == "quant_affinity"

    def test_largest_precision_wins_without_affinity(self) -> None:
        entries = [
            _entry("model-Q4_K_M.gguf", 900),
            _entry("mmproj-F16.gguf", 80),
            _entry("mmproj-F32.gguf", 100),
            _entry("mmproj-Q8_0.gguf", 60),
        ]
        plan = plan_fileset(entries, repo="org/repo", revision="rev")
        assert plan.mmproj_rel == "mmproj-F32.gguf"
        assert plan.mmproj_tiebreak_reason == "largest_precision"

    def test_lexicographic_tiebreak_is_deterministic(self) -> None:
        """Two mmproj candidates at the SAME precision rank (no quant token
        on either) must pick the same one every time — lexicographic, not
        insertion-order roulette."""
        entries = [
            _entry("model.gguf", 900),
            _entry("mmproj-b.gguf", 50),
            _entry("mmproj-a.gguf", 50),
        ]
        plan1 = plan_fileset(entries, repo="org/repo", revision="rev")
        plan2 = plan_fileset(list(reversed(entries)), repo="org/repo", revision="rev")
        assert plan1.mmproj_rel == plan2.mmproj_rel == "mmproj-a.gguf"

    def test_no_mmproj_in_repo_is_none(self) -> None:
        plan = plan_fileset([_entry("model.gguf", 900)], repo="org/repo", revision="rev")
        assert plan.mmproj_rel is None
        assert plan.mmproj_tiebreak_reason is None


class TestRunnerHint:
    def test_gguf_hints_llama_server(self) -> None:
        plan = plan_fileset([_entry("model.gguf", 900)], repo="org/repo", revision="rev")
        assert plan.runner_hint == "llama-server"

    def test_safetensors_with_tokenizer_config_hints_flm(self) -> None:
        entries = [
            _entry("model.safetensors", 900),
            _entry("tokenizer.json", 5),
            _entry("config.json", 2),
        ]
        plan = plan_fileset(entries, repo="org/repo", revision="rev")
        assert plan.runner_hint == "flm"

    def test_ambiguous_shape_is_none(self) -> None:
        plan = plan_fileset([_entry("model.safetensors", 900)], repo="org/repo", revision="rev")
        assert plan.runner_hint is None


def _tree_response(entries: list[dict], next_url: str | None = None) -> httpx.Response:
    headers = {}
    if next_url:
        headers["Link"] = f'<{next_url}>; rel="next"'
    return httpx.Response(200, content=json.dumps(entries).encode(), headers=headers)


class TestEnumerateRepoPagination:
    @pytest.mark.asyncio
    async def test_follows_link_next_header(self) -> None:
        page1 = [{"path": "model-00001-of-00002.gguf", "size": 100}]
        page2 = [{"path": "model-00002-of-00002.gguf", "size": 200}]
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if len(calls) == 1:
                return _tree_response(
                    page1, next_url="https://huggingface.co/api/models/org/repo/tree/main?cursor=2"
                )
            return _tree_response(page2)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        entries = await enumerate_repo("org/repo", client=client)
        await client.aclose()
        assert len(calls) == 2
        assert {e.path for e in entries} == {
            "model-00001-of-00002.gguf",
            "model-00002-of-00002.gguf",
        }

    @pytest.mark.asyncio
    async def test_single_page_no_pagination(self) -> None:
        entries_json = [
            {"path": "model.gguf", "size": 900, "lfs": {"oid": "sha256:abc", "size": 900}}
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return _tree_response(entries_json)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        entries = await enumerate_repo("org/repo", client=client)
        await client.aclose()
        assert len(entries) == 1
        assert entries[0].lfs_oid == "abc"
        assert entries[0].lfs_size == 900

    @pytest.mark.asyncio
    async def test_upstream_error_raises_hfupstreamerror(self) -> None:
        from hal0.registry.fileset import HFUpstreamError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b"boom")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(HFUpstreamError):
            await enumerate_repo("org/repo", client=client)
        await client.aclose()


class TestResolveRevision:
    @pytest.mark.asyncio
    async def test_resolves_via_revision_endpoint(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "/revision/" in str(request.url):
                return httpx.Response(200, content=json.dumps({"sha": "deadbeef123"}).encode())
            return httpx.Response(200, content=b"[]")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sha = await resolve_revision("org/repo", client=client)
        await client.aclose()
        assert sha == "deadbeef123"

    @pytest.mark.asyncio
    async def test_falls_back_to_x_repo_commit_header(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "/revision/" in str(request.url):
                return httpx.Response(404, content=b"")
            return httpx.Response(200, content=b"[]", headers={"X-Repo-Commit": "fallbacksha"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sha = await resolve_revision("org/repo", client=client)
        await client.aclose()
        assert sha == "fallbacksha"
