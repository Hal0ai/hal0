"""The query half of the artefact seam must key on the SAME token as the
lifecycle half (#1417).

``load_sync`` / ``unload_sync`` / ``rerender_unit_sync`` resolve
``slot_instance_token(cfg)`` first, so on an id-keyed (post-migration) box they
create and target ``hal0-slot@<id>.service`` / ``hal0-slot-<id>``. The probe
half — ``is_active`` / ``running_image`` / ``running_argv``, plus slot_view's
raw crashed-vs-stopped ``systemctl is-active`` — used to pass the slot NAME
into the same pure formatters, so it asked systemd/podman about a
pre-migration artefact that does not exist: every container slot reported
``offline`` / ``stopped`` while its container was ``Up (healthy)`` and serving.

Every test here pins one probe path onto the id token for a slot whose name
and id differ.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hal0.providers.container import ContainerProvider
from hal0.slot_view import container_enrichment
from hal0.slots.drift import compute_config_drift
from hal0.slots.naming import slot_container_name, slot_instance_token, slot_unit_name
from hal0.slots.watchdog import SlotWatchdog

# The live lxc105 shape: display name "brain", durable id 2. The unit the load
# path created is hal0-slot@2.service; hal0-slot@brain.service never existed.
_SLOT_ID = 2
_SLOT_NAME = "brain"
_UNIT = slot_unit_name(str(_SLOT_ID))  # hal0-slot@2.service
_CONTAINER = slot_container_name(str(_SLOT_ID))  # hal0-slot-2


def _cfg(**over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "id": _SLOT_ID,
        "name": _SLOT_NAME,
        "port": 8087,
        "type": "llm",
        "device": "gpu-rocm",
        "provider": "llama-server",
        "model": {"default": "hal0-brain-sft-fpx8"},
    }
    cfg.update(over)
    return cfg


def _seen_token(arg: Any) -> str:
    """The instance token a probe argument resolves to.

    Mirrors the provider: a slot config resolves through the seam, a bare
    string is already a token. Lets a test assert on the token regardless of
    which shape the caller handed down.
    """
    if isinstance(arg, Mapping):
        return slot_instance_token(arg)
    return str(arg)


# ── provider: the three probe entry points ───────────────────────────────────


class TestProviderProbesKeyOnTheInstanceToken:
    def test_is_active_probes_the_id_keyed_unit(self) -> None:
        provider = ContainerProvider()
        calls: list[list[str]] = []

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            calls.append(list(args))
            m = MagicMock()
            m.returncode = 0
            return m

        with patch.object(provider, "_run", side_effect=fake_run):
            assert provider.is_active(_cfg()) is True

        assert calls == [["systemctl", "is-active", _UNIT]], calls

    def test_running_image_inspects_the_id_keyed_container(self) -> None:
        provider = ContainerProvider()
        result = MagicMock()
        result.returncode = 0
        result.stdout = "ghcr.io/hal0ai/hal0-rocmfpx:c077206\n"

        with (
            patch("hal0.providers.container._container_runtime", return_value="/usr/bin/podman"),
            patch("hal0.providers.container.subprocess.run", return_value=result) as run,
        ):
            ref = provider.running_image(_cfg())

        assert ref == "ghcr.io/hal0ai/hal0-rocmfpx:c077206"
        assert _CONTAINER in run.call_args.args[0], run.call_args.args[0]

    def test_running_argv_inspects_the_id_keyed_container(self) -> None:
        provider = ContainerProvider()
        result = MagicMock()
        result.returncode = 0
        result.stdout = '["--ctx-size","4096"]\n'

        with (
            patch("hal0.providers.container._container_runtime", return_value="/usr/bin/podman"),
            patch("hal0.providers.container.subprocess.run", return_value=result) as run,
        ):
            argv = provider.running_argv(_cfg())

        assert argv == ["--ctx-size", "4096"]
        assert _CONTAINER in run.call_args.args[0], run.call_args.args[0]

    def test_name_keyed_slot_still_probes_the_name_unit(self) -> None:
        """A pre-migration slot (no id) is unchanged — the token IS the name."""
        provider = ContainerProvider()
        calls: list[list[str]] = []

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            calls.append(list(args))
            m = MagicMock()
            m.returncode = 0
            return m

        with patch.object(provider, "_run", side_effect=fake_run):
            provider.is_active({"name": "chat"})

        assert calls == [["systemctl", "is-active", "hal0-slot@chat.service"]], calls

    def test_bare_token_string_is_still_accepted(self) -> None:
        """Back-compat: an already-resolved token passes straight through."""
        provider = ContainerProvider()
        calls: list[list[str]] = []

        def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
            calls.append(list(args))
            m = MagicMock()
            m.returncode = 3
            return m

        with patch.object(provider, "_run", side_effect=fake_run):
            assert provider.is_active("2") is False

        assert calls == [["systemctl", "is-active", _UNIT]], calls


# ── callers: every probe path must hand the config down ──────────────────────


class _RecordingProvider:
    """Records what each probe path was keyed on."""

    def __init__(self, *, active: bool = True, healthy: bool = True) -> None:
        self._active = active
        self._healthy = healthy
        self.is_active_args: list[Any] = []
        self.running_image_args: list[Any] = []
        self.running_argv_args: list[Any] = []

    def is_active(self, slot: Any) -> bool:
        self.is_active_args.append(slot)
        return self._active

    async def health(self, port: int, slot_cfg: Any = None) -> dict[str, Any]:
        return {"ok": self._healthy}

    def running_image(self, slot: Any) -> str | None:
        self.running_image_args.append(slot)
        return None

    def running_argv(self, slot: Any) -> list[str] | None:
        self.running_argv_args.append(slot)
        return ["--ctx-size", "4096"]

    def expected_argv(self, slot_cfg: Any, model_info: Any) -> list[str] | None:
        return ["--ctx-size", "4096"]

    def image_present(self, image: str) -> bool:
        return True


class _WatchdogHost:
    """Narrow WatchdogHost stub — only ``_maybe_load_config`` is exercised."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self._cfg = cfg

    async def _maybe_load_config(self, name: str) -> dict[str, Any] | None:
        return self._cfg


class TestCallersPassTheSlotConfig:
    async def test_watchdog_is_active_keys_on_the_id(self) -> None:
        provider = _RecordingProvider()
        watchdog = SlotWatchdog(_WatchdogHost(_cfg()))  # type: ignore[arg-type]

        with patch("hal0.providers.container.container_provider", return_value=provider):
            assert await watchdog.is_active(_SLOT_NAME) is True

        assert provider.is_active_args, "is_active was never probed"
        assert _seen_token(provider.is_active_args[0]) == str(_SLOT_ID)

    async def test_watchdog_readiness_check_keys_on_the_id(self) -> None:
        provider = _RecordingProvider()
        watchdog = SlotWatchdog(_WatchdogHost(_cfg()))  # type: ignore[arg-type]

        with patch("hal0.providers.container.container_provider", return_value=provider):
            ready, reason = await watchdog.readiness_check(_SLOT_NAME)

        assert (ready, reason) == (True, "ready")
        assert _seen_token(provider.is_active_args[0]) == str(_SLOT_ID)

    async def test_slot_view_enrichment_keys_every_probe_on_the_id(self) -> None:
        provider = _RecordingProvider()
        out = await container_enrichment([_cfg()], pull_jobs={}, provider=provider)

        assert out[_SLOT_NAME]["container_status"] == "running"
        assert _seen_token(provider.is_active_args[0]) == str(_SLOT_ID)
        assert provider.running_image_args, "running_image was never probed"
        assert _seen_token(provider.running_image_args[0]) == str(_SLOT_ID)

    async def test_slot_view_crashed_probe_targets_the_id_keyed_unit(self) -> None:
        """The stopped-vs-crashed discriminator is a raw ``systemctl`` call —
        it bypassed the naming seam entirely with an f-string on the name."""
        provider = _RecordingProvider(active=False)
        result = MagicMock()
        result.returncode = 3
        result.stdout = b"failed"

        with patch("subprocess.run", return_value=result) as run:
            out = await container_enrichment([_cfg()], pull_jobs={}, provider=provider)

        assert out[_SLOT_NAME]["container_status"] == "crashed"
        assert run.call_args is not None, "the unit-state discriminator never ran"
        assert run.call_args.args[0] == ["systemctl", "is-active", _UNIT], run.call_args.args[0]

    async def test_config_drift_reads_the_id_keyed_container_argv(self) -> None:
        provider = _RecordingProvider()
        host = MagicMock()
        host._maybe_load_config = AsyncMock(return_value=_cfg())
        host._resolve_model_info = AsyncMock(return_value={"_model_key": "hal0-brain-sft-fpx8"})
        host._resolve_servable_model = MagicMock(return_value="hal0-brain-sft-fpx8")

        with patch("hal0.providers.container.container_provider", return_value=provider):
            drift = await compute_config_drift(host, _SLOT_NAME, cfg=_cfg(), active=True)

        assert drift is not None
        assert provider.running_argv_args, "running_argv was never probed"
        assert _seen_token(provider.running_argv_args[0]) == str(_SLOT_ID)


# ── the invariant the whole seam exists for ──────────────────────────────────


def test_lifecycle_and_query_halves_agree_on_one_token(tmp_path: Path) -> None:
    """#1417's regression anchor: with ``{"name": "brain", "id": 2}`` every
    artefact resolver agrees on ``2``."""
    cfg = _cfg()
    token = slot_instance_token(cfg)
    assert token == str(_SLOT_ID)
    assert slot_unit_name(token) == _UNIT
    assert slot_container_name(token) == _CONTAINER

    provider = ContainerProvider()
    units: list[str] = []

    def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
        units.extend(a for a in args if a.startswith("hal0-slot@"))
        m = MagicMock()
        m.returncode = 0
        return m

    with (
        patch.object(provider, "_run", side_effect=fake_run),
        patch.object(provider, "_unit_path", return_value=tmp_path / "absent.container"),
    ):
        provider.is_active(cfg)  # query half
        provider.unload_sync(cfg)  # lifecycle half

    # The probe and the teardown target the SAME unit — that is the whole
    # point of routing every artefact name through one token.
    assert units == [_UNIT, _UNIT], units


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
