"""Registry of specialty model distributions — spec 2026-08-29."""

from hal0.registry.specialty import (
    SPECIALTY_KINDS,
    companion_role_of,
    detect_specialty,
    kind_for_role,
)


class TestPromptforgeKind:
    def test_promptforge_registered(self):
        kind = SPECIALTY_KINDS["promptforge"]
        assert kind.key == "promptforge"
        assert kind.quant_marker == "ActiveFPX"
        assert kind.degraded_ok is True
        assert kind.default_ctx == 262144

    def test_promptforge_companion_envs(self):
        kind = SPECIALTY_KINDS["promptforge"]
        env_by_role = {c.role: c.env for c in kind.companions}
        assert env_by_role["promptforge_ffn"] == "PROMPTFORGE_SIDECAR"
        assert env_by_role["promptforge_gdn"] == "PROMPTFORGE_GDN_SIDECAR"
        assert env_by_role["promptforge_output_k8"] == "PROMPTFORGE_MTP_OUTPUT_K8_PROXY"
        # runtime patch installs but exports no env
        assert env_by_role["runtime_patch"] is None

    def test_kind_key_is_the_capability_token(self):
        # The guard compares SpecialtyKind.key against
        # RunnerSupports.specialties — there is no separate token field.
        kind = SPECIALTY_KINDS["promptforge"]
        assert not hasattr(kind, "runner_capability")


class TestCompanionRoleOf:
    def test_pfs_files_classify(self):
        assert companion_role_of("Qwen3.8-27B-v3-FFN.pfs") == "promptforge_ffn"
        assert companion_role_of("Qwen3.8-27B-v3-GDN.pfs") == "promptforge_gdn"
        assert companion_role_of("Qwen3.8-v3-Output-K8.pfs") == "promptforge_output_k8"

    def test_runtime_patch_classifies(self):
        assert companion_role_of("qwen38-v3-output-k8-runtime.patch") == "runtime_patch"

    def test_near_miss_returns_none(self):
        assert companion_role_of("model-Q8.gguf") is None
        assert companion_role_of("notes-about-pfs.md") is None
        assert companion_role_of("some.patch.txt") is None


class TestDetectSpecialty:
    def test_quant_marker_hit(self):
        paths = ["Qwen3.8-27B-CIRU-ActiveFPX-v3-Q8.gguf"]
        assert detect_specialty(paths) == "promptforge"

    def test_quant_param_hit(self):
        assert detect_specialty(["model.gguf"], quant="ActiveFPX") == "promptforge"

    def test_companion_presence_hit(self):
        paths = ["model-Q8.gguf", "model-FFN.pfs"]
        assert detect_specialty(paths) == "promptforge"

    def test_plain_repo_no_hit(self):
        paths = ["model-Q4_K_M.gguf", "mmproj-F16.gguf", "config.json"]
        assert detect_specialty(paths) is None

    def test_ambiguous_match_returns_none(self):
        """Spec 2026-08-29: ambiguity → ``None`` (M4, fix wave).

        With one kind registered this can't happen in production; the rule
        exists so the day a second kind lands, a listing that lights up both
        stops stamping the dict-order winner. Registers a throwaway second
        kind to prove the rule instead of asserting it can't be reached.
        """
        import re

        from hal0.registry.specialty import CompanionSpec, SpecialtyKind

        other = SpecialtyKind(
            key="_test_other",
            quant_marker="ActiveFPX",  # deliberately the same marker
            companions=(
                CompanionSpec(
                    role="_test_other_blob",
                    pattern=re.compile(r"other[^/]*\.blob$", re.IGNORECASE),
                    env="OTHER_SIDECAR",
                ),
            ),
        )
        SPECIALTY_KINDS["_test_other"] = other
        try:
            assert detect_specialty(["Qwen-CIRU-ActiveFPX-v3-Q8.gguf"]) is None
            # one signal each, two different kinds — still ambiguous
            assert detect_specialty(["model-FFN.pfs", "model-other.blob"]) is None
        finally:
            del SPECIALTY_KINDS["_test_other"]
        # and the rule doesn't disturb the single-kind answer
        assert detect_specialty(["Qwen-CIRU-ActiveFPX-v3-Q8.gguf"]) == "promptforge"


class TestKindForRole:
    def test_maps_role_back_to_kind(self):
        assert kind_for_role("promptforge_ffn").key == "promptforge"
        assert kind_for_role("mmproj") is None
