"""Task 3.5 TDD: selection.py — auto_selections + variant_for."""

from __future__ import annotations

import pytest

from hal0.comfyui.capabilities import CAPABILITIES, ModelVariant
from hal0.comfyui.selection import auto_selections, variant_for


def test_auto_selections_count():
    """One variant per capability (5 total)."""
    result = auto_selections()
    assert len(result) == len(CAPABILITIES) == 5


def test_auto_selections_are_model_variants():
    for v in auto_selections():
        assert isinstance(v, ModelVariant)


def test_auto_selections_match_defaults():
    """Each returned variant == the capability's first alternative."""
    result = auto_selections()
    cap_list = list(CAPABILITIES.values())
    for v, cap in zip(result, cap_list, strict=False):
        assert v is cap.alternatives[0], f"{cap.id}: expected first alternative"


def test_variant_for_known():
    """variant_for('txt2video', 'wan22') resolves to the wan22 variant."""
    v = variant_for("txt2video", "wan22")
    assert v.family == "wan22"


def test_variant_for_default_family():
    """variant_for returns the correct variant for the default family."""
    v = variant_for("txt2img", "qwen-image")
    assert v.family == "qwen-image"
    assert v is CAPABILITIES["txt2img"].alternatives[0]


def test_variant_for_unknown_capability_raises():
    with pytest.raises(KeyError):
        variant_for("nonexistent", "foo")


def test_variant_for_unknown_family_raises():
    """Unknown family within a valid capability raises KeyError."""
    with pytest.raises(KeyError):
        variant_for("txt2img", "no-such-model")
