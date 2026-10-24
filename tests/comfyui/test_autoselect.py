"""The first renderable image model that lands becomes the ``img`` capability's model.

Fresh installs have no image selection, so Open WebUI and ``/v1/images`` would stay unwired
after the first install until the operator picked a model by hand. The pick is made once:
an existing selection (enabled, with a model) is the operator's and is never replaced, a
model the API cannot render (video) never gets picked, and a failure never fails the install.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.capabilities import catalog
from hal0.comfyui.autoselect import AutoSelectStatus, auto_select_first_image_model
from hal0.comfyui.catalog import CATALOG
from hal0.errors import BadRequest

_SDXL = CATALOG["sdxl-lightning"]
_VIDEO = next(m for m in CATALOG.values() if not m.api_renderable)


class _Orchestrator:
    """Just the two calls auto-select makes: ``get_state()`` and ``apply()``."""

    def __init__(
        self,
        *,
        model: str = "",
        enabled: bool = False,
        state_error: Exception | None = None,
        apply_error: Exception | None = None,
    ) -> None:
        self._selection = {"model": model, "enabled": enabled}
        self._state_error = state_error
        self._apply_error = apply_error
        self.applied: list[tuple[str, str, dict[str, Any]]] = []
        self.starts: list[bool] = []

    async def get_state(self) -> dict[str, Any]:
        if self._state_error is not None:
            raise self._state_error
        return {"selections": {"img": {"img": self._selection}}}

    async def apply(
        self, slot: str, child: str, partial: dict[str, Any], *, start: bool = True
    ) -> dict[str, Any]:
        if self._apply_error is not None:
            raise self._apply_error
        self.applied.append((slot, child, partial))
        self.starts.append(start)
        return {}


@pytest.fixture(autouse=True)
def rocm_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog, "comfyui_backends", lambda: ["gpu-vulkan"])


def test_video_model_fixture_is_not_renderable() -> None:
    """The premise of the video case: the catalog has a model the API cannot render."""
    assert _VIDEO.api_renderable is False
    assert _SDXL.api_renderable is True


async def test_auto_select_on_first_renderable_install() -> None:
    orchestrator = _Orchestrator()

    await auto_select_first_image_model(_SDXL, orchestrator=orchestrator)

    assert orchestrator.applied == [
        (
            "img",
            "img",
            {
                "enabled": True,
                "model": "sdxl-lightning",
                "device": "gpu-rocm",
                "provider": "comfyui",
            },
        )
    ]


async def test_auto_select_passes_the_seeded_rocm_device_not_the_picker_row() -> None:
    """``comfyui_backends()`` says ``gpu-vulkan`` (the picker row); the seeded slot says ``gpu-rocm``."""
    orchestrator = _Orchestrator()

    await auto_select_first_image_model(_SDXL, orchestrator=orchestrator)

    assert orchestrator.applied[0][2]["device"] == "gpu-rocm"


async def test_auto_select_replaces_a_disabled_selection_that_names_a_model() -> None:
    orchestrator = _Orchestrator(model="old-model", enabled=False)

    await auto_select_first_image_model(_SDXL, orchestrator=orchestrator)

    assert len(orchestrator.applied) == 1


async def test_no_auto_select_when_selection_exists() -> None:
    orchestrator = _Orchestrator(model="sd-1.5-pruned-emaonly", enabled=True)

    await auto_select_first_image_model(_SDXL, orchestrator=orchestrator)

    assert orchestrator.applied == []


async def test_no_auto_select_for_video_model() -> None:
    orchestrator = _Orchestrator()

    await auto_select_first_image_model(_VIDEO, orchestrator=orchestrator)

    assert orchestrator.applied == []


async def test_no_auto_select_without_an_orchestrator() -> None:
    await auto_select_first_image_model(_SDXL, orchestrator=None)  # does not raise


async def test_no_auto_select_without_a_comfyui_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog, "comfyui_backends", lambda: [])
    orchestrator = _Orchestrator()

    await auto_select_first_image_model(_SDXL, orchestrator=orchestrator)

    assert orchestrator.applied == []


async def test_auto_select_swallows_an_apply_failure() -> None:
    """The install already succeeded; a refused apply must not turn it into a failure."""
    orchestrator = _Orchestrator(
        apply_error=BadRequest("nope", code="capability.invalid_selection")
    )

    await auto_select_first_image_model(_SDXL, orchestrator=orchestrator)  # does not raise

    assert orchestrator.applied == []


async def test_no_auto_select_when_the_selection_cannot_be_read() -> None:
    """Not knowing whether the operator picked a model is not licence to replace it."""
    orchestrator = _Orchestrator(state_error=RuntimeError("slot manager is down"))

    await auto_select_first_image_model(_SDXL, orchestrator=orchestrator)  # does not raise

    assert orchestrator.applied == []


# ── start: the install path persists the pick without starting the slot ─────


async def test_auto_select_starts_the_slot_by_default() -> None:
    orchestrator = _Orchestrator()

    await auto_select_first_image_model(_SDXL, orchestrator=orchestrator)

    assert orchestrator.starts == [True]


async def test_auto_select_passes_start_false_through() -> None:
    orchestrator = _Orchestrator()

    await auto_select_first_image_model(_SDXL, orchestrator=orchestrator, start=False)

    assert len(orchestrator.applied) == 1
    assert orchestrator.starts == [False]


# ── the outcome: what happened, for a caller that reports it ────────────────


async def test_outcome_selected() -> None:
    outcome = await auto_select_first_image_model(_SDXL, orchestrator=_Orchestrator())

    assert outcome.status is AutoSelectStatus.selected


async def test_outcome_kept_names_the_existing_selection() -> None:
    orchestrator = _Orchestrator(model="sd-1.5-pruned-emaonly", enabled=True)

    outcome = await auto_select_first_image_model(_SDXL, orchestrator=orchestrator)

    assert outcome.status is AutoSelectStatus.kept
    assert outcome.detail == "sd-1.5-pruned-emaonly"


async def test_outcome_failed_carries_the_refusal_message() -> None:
    orchestrator = _Orchestrator(
        apply_error=BadRequest("device busy", code="capability.invalid_selection")
    )

    outcome = await auto_select_first_image_model(_SDXL, orchestrator=orchestrator)

    assert outcome.status is AutoSelectStatus.failed
    assert outcome.detail == "device busy"


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("video", "cannot render"),
        ("no-orchestrator", "orchestrator"),
        ("no-lane", "ROCm lane"),
        ("unreadable", "read"),
    ],
)
async def test_outcome_skipped_says_why(
    case: str, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    orchestrator: _Orchestrator | None = _Orchestrator()
    model = _SDXL
    if case == "video":
        model = _VIDEO
    elif case == "no-orchestrator":
        orchestrator = None
    elif case == "no-lane":
        monkeypatch.setattr(catalog, "comfyui_backends", lambda: [])
    else:
        orchestrator = _Orchestrator(state_error=RuntimeError("slot manager is down"))

    outcome = await auto_select_first_image_model(model, orchestrator=orchestrator)

    assert outcome.status is AutoSelectStatus.skipped
    assert reason in outcome.detail
