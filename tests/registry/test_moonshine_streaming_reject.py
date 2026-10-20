"""#2556: a streaming-layout Moonshine bundle is refused at add and scan."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.errors import UnprocessableEntity
from hal0.registry.detect import unsupported_moonshine_layout
from hal0.registry.discover import find_candidates


def _stage(root: Path, *names: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for n in names:
        (root / n).write_bytes(b"\0")
    return root


def test_layout_detection(tmp_path: Path) -> None:
    streaming = _stage(tmp_path / "s", "encoder.ort", "frontend.ort")
    loadable = _stage(tmp_path / "l", "encoder_model.ort", "decoder_model_merged.ort")
    mixed = _stage(tmp_path / "m", "encoder.ort", "encoder_model.ort")
    assert unsupported_moonshine_layout(streaming)
    assert unsupported_moonshine_layout(streaming / "encoder.ort")
    assert not unsupported_moonshine_layout(loadable)
    assert not unsupported_moonshine_layout(loadable / "encoder_model.ort")
    assert not unsupported_moonshine_layout(mixed)
    assert not unsupported_moonshine_layout(mixed / "encoder.ort")


def test_scan_skips_streaming_ort(tmp_path: Path) -> None:
    _stage(tmp_path / "s", "encoder.ort", "frontend.ort")
    skipped: list[dict] = []
    cands = find_candidates([tmp_path], [".ort"], set(), skipped=skipped)
    assert all(c.path.name != "encoder.ort" for c in cands)
    reasons = {Path(s["path"]).name: s["reason"] for s in skipped}
    assert reasons["encoder.ort"] == "unsupported_moonshine_layout"


@pytest.mark.asyncio
async def test_add_from_path_refuses_streaming(tmp_path: Path, tmp_hal0_home: str) -> None:
    from hal0.services.models_service import add_from_path

    target = _stage(tmp_path / "s", "encoder.ort", "frontend.ort") / "encoder.ort"
    with pytest.raises(UnprocessableEntity) as exc_info:
        await add_from_path({"path": str(target)}, registry=object(), event_bus=object())
    assert exc_info.value.code == "models.unsupported_moonshine_layout"
    assert "decoder_model_merged.ort" in str(exc_info.value)
