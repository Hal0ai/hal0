"""hal0-api converts an old-runner install before it reads config (ADR-0028)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.config import paths


def test_boot_converts_removed_runner_keys_before_loading_config(tmp_hal0_home: str) -> None:
    hal0_toml = paths.hal0_toml()
    hal0_toml.parent.mkdir(parents=True, exist_ok=True)
    hal0_toml.write_text(
        "[models]\nroots = []\nauto_scan_on_start = false\n\n"
        '[slots.default_images]\nvulkanfpx = "ghcr.io/hal0ai/hal0-combined:0826"\n',
        encoding="utf-8",
    )

    with TestClient(create_app()) as client:
        # Config loaded the converted file: the old key would have failed
        # validation and fallen back to defaults, dropping
        # [models].auto_scan_on_start = false.
        assert client.app.state.hal0_config.models.auto_scan_on_start is False

    data = tomllib.loads(hal0_toml.read_text(encoding="utf-8"))
    assert data["slots"]["default_images"] == {}
    assert any(Path(p).name.startswith("runners-") for p in (paths.var_lib() / "backups").iterdir())
