"""Platform-name listing for the agent overview (the reader behind the extras)."""

from __future__ import annotations

from pathlib import Path

from hal0.agents.hermes_platforms import configured_platform_extras, configured_platform_names


def test_configured_platform_names_reads_env_and_always_includes_api_server(
    tmp_path: Path,
) -> None:
    home = tmp_path
    (home / "config.yaml").write_text("gateway: {}\n")
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=x\n")
    assert configured_platform_names(home, None) == ["api_server", "telegram"]


def test_configured_platform_extras_is_the_extras_of_the_configured_names(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=x\n")
    assert configured_platform_names(tmp_path, None) == ["api_server", "telegram"]
    assert configured_platform_extras(tmp_path, None) == ("sms", "telegram")
