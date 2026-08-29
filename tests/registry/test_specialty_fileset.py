"""Specialty companions through the fileset classifier/planner."""

from hal0.registry.fileset import role_of


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
