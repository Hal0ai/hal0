"""Task 2.2: ComfyUI capability registry — TDD tests."""

import os

from hal0.comfyui.capabilities import CAPABILITIES, default_variant

SCRIPTS_DIR = os.path.join(
    os.path.dirname(__file__),
    "../../installer/comfyui/scripts",
)


def test_all_capability_ids_present():
    assert set(CAPABILITIES.keys()) == {
        "txt2img",
        "img2img",
        "txt2video",
        "img2video",
        "image_upscale",
    }


def test_txt2img_default_family():
    assert CAPABILITIES["txt2img"].default_family == "qwen-image"


def test_default_variant_est_seconds():
    v = default_variant("txt2img")
    assert v.est_seconds <= 80


def test_ltx2_default_txt2video():
    assert CAPABILITIES["txt2video"].default_family == "ltx2"
    assert default_variant("txt2video").family == "ltx2"


def test_ltx2_default_img2video():
    assert CAPABILITIES["img2video"].default_family == "ltx2"
    assert default_variant("img2video").family == "ltx2"


def test_every_variant_fetch_script_exists():
    for cap_id, cap in CAPABILITIES.items():
        for v in cap.alternatives:
            script_path = os.path.abspath(os.path.join(SCRIPTS_DIR, v.fetch_script))
            assert os.path.isfile(script_path), (
                f"{cap_id}/{v.family}: fetch_script {v.fetch_script!r} not found at {script_path}"
            )


def test_default_variant_is_first_alternative():
    for cap_id, cap in CAPABILITIES.items():
        v = default_variant(cap_id)
        assert v is cap.alternatives[0], f"{cap_id}: default_variant should be first alternative"


def test_capability_labels():
    assert CAPABILITIES["txt2img"].label == "Text → Image"
    assert CAPABILITIES["img2img"].label == "Image Edit"
    assert CAPABILITIES["txt2video"].label == "Text → Video"
    assert CAPABILITIES["img2video"].label == "Image → Video"
    assert CAPABILITIES["image_upscale"].label == "Upscale"
