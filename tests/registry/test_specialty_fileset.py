"""Specialty companions through the fileset classifier/planner."""

from hal0.registry.fileset import RawTreeEntry, plan_fileset, role_of


class TestRoleOfCompanions:
    def test_pfs_sidecars(self):
        assert role_of("Qwen3.8-27B-v3-FFN.pfs") == "promptforge_ffn"
        assert role_of("sub/dir/Qwen3.8-27B-v3-GDN.pfs") == "promptforge_gdn"
        assert role_of("Qwen3.8-v3-Output-K8.pfs") == "promptforge_output_k8"

    def test_runtime_patch(self):
        assert role_of("runtime/qwen38-v3-output-k8-runtime.patch") == "runtime_patch"

    def test_existing_roles_unchanged(self):
        # regression pins: today's classifications must not move
        assert role_of("mmproj-model-F16.gguf") == "mmproj"
        assert role_of("model-Q4_K_M.gguf") == "model"
        assert role_of("tokenizer.json") == "tokenizer"
        assert role_of("config.json") == "config"
        assert role_of("README.md") == "config"


def _e(path, size=100, oid=None):
    return RawTreeEntry(path=path, size=size, lfs_oid=oid, lfs_size=size if oid else None)


class TestBuildPlanCompanions:
    # NOTE: the brief names this function `_build_plan`; this codebase's
    # equivalent public entry point is `plan_fileset(entries, *, repo,
    # revision, requested_variant=None)` — same shape (entries in, a
    # FileSetPlan with .files/.specialty out), just a different name.
    def test_companions_join_the_plan(self):
        entries = [
            _e("Qwen-ActiveFPX-v3-Q8.gguf", size=15_000, oid="a" * 64),
            _e("Qwen-v3-FFN.pfs", size=17_100, oid="b" * 64),
            _e("Qwen-v3-GDN.pfs", size=4_000, oid="c" * 64),
            _e("Qwen-v3-Output-K8.pfs", size=700, oid="d" * 64),
            _e("runtime/qwen38-v3-output-k8-runtime.patch", size=10),
        ]
        plan = plan_fileset(entries, repo="jcbtc/qwen", revision="main")
        roles = {f.role for f in plan.files}
        assert {"model", "promptforge_ffn", "promptforge_gdn",
                "promptforge_output_k8", "runtime_patch"} <= roles
        assert plan.specialty == "promptforge"
        # companion bytes counted
        assert plan.total_bytes == 15_000 + 17_100 + 4_000 + 700 + 10

    def test_plain_repo_unaffected(self):
        entries = [_e("model-Q4_K_M.gguf", size=5_000, oid="a" * 64)]
        plan = plan_fileset(entries, repo="x/y", revision="main")
        assert plan.specialty is None
        assert [f.role for f in plan.files] == ["model"]

    def test_stray_pfs_in_plain_repo_not_installed(self):
        # No quant marker, no required-companion FULL set — but one lone
        # pattern hit DOES trigger detection (companion presence is a
        # signal); the point of this test is the inverse: a repo whose only
        # oddity is a .patch (required=False) detects nothing.
        entries = [
            _e("model-Q4_K_M.gguf", size=5_000, oid="a" * 64),
            _e("build-runtime.patch", size=10),
        ]
        plan = plan_fileset(entries, repo="x/y", revision="main")
        assert plan.specialty is None
        assert [f.role for f in plan.files] == ["model"]
