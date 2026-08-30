from hal0.runners import RUNNER_IMAGES


def test_promptforge_runner_registered():
    r = RUNNER_IMAGES["promptforge"]
    assert r.runtime_family == "llama-server"
    assert r.device_class == "gpu"
    assert r.backend == "rocm"
    assert r.supported_backends == ("rocm",)  # HIP-only: no vulkan
    assert r.format_arch == "gguf"
    # None until the CANDIDATE image passes the #1891 gate and ships in
    # manifest.json's toolbox_images (test_registry enforces that a set key
    # actually exists there).
    assert r.manifest_key is None
    assert r.supports.specialties == ("promptforge",)
    assert r.supports.mtp is True


def test_existing_runners_have_empty_specialties():
    for key in ("rocmfpx", "vulkanfpx", "cuda", "cpu"):
        assert RUNNER_IMAGES[key].supports.specialties == ()
