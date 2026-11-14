"""Config-write guard: a per-op xdna slot and the FLM anchor may not both autoload (#3666).

Autoload slots start straight from systemd at boot and never pass the load-time
guard, so the config write that would make both autoload is refused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.slots.config_write import check_npu_autoload_conflict
from hal0.slots.state import SlotNpuConflict


def _write(root: Path, name: str, lines: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _flm_lines(name: str = "flm", autoload: bool = True) -> list[str]:
    return [
        f'name = "{name}"',
        'device = "npu"',
        'type = "llm"',
        f"autoload = {str(autoload).lower()}",
        "[model]",
        'default = "gemma3-1b"',
    ]


def _xdna_lines(name: str = "x", model: str = "m-perop", autoload: bool = True) -> list[str]:
    return [
        f'name = "{name}"',
        'device = "npu"',
        'type = "llm"',
        'binary = "xdna"',
        f"autoload = {str(autoload).lower()}",
        "[model]",
        f'default = "{model}"',
    ]


def _cfg(lines_fn: Any, **kw: Any) -> dict[str, Any]:
    import tomllib

    return tomllib.loads("\n".join(lines_fn(**kw)))


def _resolver(model_id: str) -> str | None:
    return f"/models/{model_id}.gguf"


@pytest.fixture(autouse=True)
def _fit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Models named ``*-fused`` fit the fused path; everything else is per-op."""
    from types import SimpleNamespace

    def fake(path: str | Path) -> Any:
        return SimpleNamespace(xdna_path="fused" if str(path).endswith("-fused.gguf") else "per-op")

    monkeypatch.setattr("hal0.slots.model_fit.xdna_model_fit_cached", fake)


def test_autoload_perop_while_flm_autoload_refused(tmp_path: Path) -> None:
    _write(tmp_path, "flm", _flm_lines())
    with pytest.raises(SlotNpuConflict) as ei:
        check_npu_autoload_conflict(
            "x", _cfg(_xdna_lines), slots_dir=tmp_path, model_path_for=_resolver
        )
    d = ei.value.details
    assert ei.value.code == "slot.npu_conflict" and ei.value.status == 409
    assert d["reason"] == "autoload" and d["pending"] is False
    assert d["holders"] == ["flm"] and d["holder_engine"] == "flm"
    assert d["slot"] == "x"


def test_autoload_flm_while_perop_autoload_refused(tmp_path: Path) -> None:
    _write(tmp_path, "x", _xdna_lines())
    with pytest.raises(SlotNpuConflict) as ei:
        check_npu_autoload_conflict(
            "flm", _cfg(_flm_lines), slots_dir=tmp_path, model_path_for=_resolver
        )
    assert ei.value.details["holders"] == ["x"]
    assert ei.value.details["holder_engine"] == "xdna"
    assert ei.value.details["unloads"] == ["x"]


def test_model_change_to_perop_on_autoload_xdna_refused(tmp_path: Path) -> None:
    _write(tmp_path, "flm", _flm_lines())
    # Fused model: fine.
    check_npu_autoload_conflict(
        "x",
        _cfg(_xdna_lines, model="m-fused"),
        slots_dir=tmp_path,
        model_path_for=_resolver,
    )
    # Switch to a per-op model: refused.
    with pytest.raises(SlotNpuConflict):
        check_npu_autoload_conflict(
            "x",
            _cfg(_xdna_lines, model="m-perop"),
            slots_dir=tmp_path,
            model_path_for=_resolver,
        )


def test_autoload_fused_with_flm_allowed(tmp_path: Path) -> None:
    _write(tmp_path, "flm", _flm_lines())
    check_npu_autoload_conflict(
        "x", _cfg(_xdna_lines, model="m-fused"), slots_dir=tmp_path, model_path_for=_resolver
    )


def test_autoload_false_never_checked(tmp_path: Path) -> None:
    _write(tmp_path, "flm", _flm_lines())
    check_npu_autoload_conflict(
        "x", _cfg(_xdna_lines, autoload=False), slots_dir=tmp_path, model_path_for=_resolver
    )
    # A non-autoload peer never counts either.
    _write(tmp_path, "y", _xdna_lines("y", autoload=False))
    check_npu_autoload_conflict(
        "flm2", _cfg(_flm_lines, name="flm2"), slots_dir=tmp_path, model_path_for=_resolver
    )


def test_unloads_include_flm_shadows(tmp_path: Path) -> None:
    _write(tmp_path, "flm", _flm_lines())
    _write(
        tmp_path,
        "flm-stt",
        ['name = "flm-stt"', 'device = "npu"', 'type = "transcription"', 'served_by = "flm"'],
    )
    with pytest.raises(SlotNpuConflict) as ei:
        check_npu_autoload_conflict(
            "x", _cfg(_xdna_lines), slots_dir=tmp_path, model_path_for=_resolver
        )
    assert ei.value.details["unloads"] == ["flm", "flm-stt"]


def test_fused_layer_env_zero_makes_fused_model_conflict(tmp_path: Path) -> None:
    _write(tmp_path, "flm", _flm_lines())
    cfg = _cfg(_xdna_lines, model="m-fused")
    cfg["xdna"] = {"env": {"GGML_XDNA_FUSED_LAYER": "0"}}
    with pytest.raises(SlotNpuConflict):
        check_npu_autoload_conflict("x", cfg, slots_dir=tmp_path, model_path_for=_resolver)


def test_unregistered_xdna_model_refused_with_note(tmp_path: Path) -> None:
    _write(tmp_path, "flm", _flm_lines())
    with pytest.raises(SlotNpuConflict) as ei:
        check_npu_autoload_conflict(
            "x", _cfg(_xdna_lines, model="ghost"), slots_dir=tmp_path, model_path_for=lambda m: None
        )
    assert "model ghost is not in the registry, so it is treated as per-op" in str(ei.value)
    assert set(ei.value.details) == {
        "slot",
        "holders",
        "holder_engine",
        "unloads",
        "pinned",
        "pending",
        "reason",
    }


def test_registry_lookup_failure_logged_at_debug(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import hal0.registry.store as store
    from hal0.slots.config_write import registry_model_path

    class Boom:
        def get(self, _id: str) -> Any:
            raise RuntimeError("down")

    monkeypatch.setattr(store, "ModelRegistry", Boom)
    with caplog.at_level("DEBUG", logger="hal0.slots.config_write"):
        assert registry_model_path("m") is None
    assert any(
        getattr(r, "error_type", "") == "RuntimeError" and r.levelname == "DEBUG"
        for r in caplog.records
    )
