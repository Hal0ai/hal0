from hal0.runners import RUNNER_IMAGES


def test_promptforge_runner_registered():
    r = RUNNER_IMAGES["promptforge"]
    assert r.runtime_family == "llama-server"
    assert r.device_class == "gpu"
    assert r.backend == "rocm"
    assert r.supported_backends == ("rocm",)  # HIP-only: no vulkan
    assert r.format_arch == "gguf"
    assert r.manifest_key == "promptforge"
    assert r.supports.specialties == ("promptforge",)
    assert r.supports.mtp is True


def test_existing_runners_have_empty_specialties():
    for key in ("rocmfpx", "vulkanfpx", "cuda", "cpu"):
        assert RUNNER_IMAGES[key].supports.specialties == ()
