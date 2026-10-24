"""Install-time starter image models: the offer, the answer grammar and the provisioning.

The properties under test are the ones that would turn a multi-gigabyte download into an
incident, or leave an operator with models installed and nothing wired to them:

* the offer is the API-renderable catalog models in catalog order, one marked recommended
  for this box's memory pool, and it is empty on a host without a ROCm lane;
* ``--plan`` and ``--resolve`` never raise (install.sh runs under ``set -euo pipefail``);
* an answer is validated before anything downloads, and every invalid one says what is valid;
* the first model that lands and that the API can render becomes the ``img`` selection,
  exactly once, and Open WebUI is re-wired afterwards without that ever failing the install.

Nothing here reaches huggingface.co: ``run_install`` is stubbed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities import catalog as capability_catalog
from hal0.comfyui import install as comfyui_install
from hal0.comfyui import paths as comfyui_paths
from hal0.comfyui import picker
from hal0.comfyui.catalog import CATALOG, component_path
from hal0.config.schema import HardwareInfo
from hal0.install import image_models
from hal0.install.image_models import (
    ImageOffer,
    image_offers,
    main,
    no_offer_reason,
    provision_image_models,
    resolve_selection,
)

_NO_LANE_REASON = "no ROCm lane on this host (ComfyUI needs /dev/kfd and a render node)"
# The real lane gate, captured before the autouse fixture stubs it.
_REAL_COMFYUI_BACKENDS = capability_catalog.comfyui_backends


def _hw(pool_gb: int) -> HardwareInfo:
    return HardwareInfo(
        platform="strix-halo", ram_mb=pool_gb * 1024, unified_memory_mb=pool_gb * 1024
    )


@pytest.fixture(autouse=True)
def _tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The ComfyUI tree, hal0's state dir and the lane answer, all hermetic."""
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(capability_catalog, "comfyui_backends", lambda: ["gpu-vulkan"])
    yield


@pytest.fixture
def no_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capability_catalog, "comfyui_backends", lambda: [])


def _land(model_id: str) -> None:
    """Put every component of ``model_id`` on disk, as a finished install would."""
    root = comfyui_paths.models_root()
    for component in CATALOG[model_id].components:
        path = component_path(component, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


# ── who gets offered what ───────────────────────────────────────────────────


def test_offers_empty_without_rocm_lane(no_lane: None) -> None:
    assert image_offers(_hw(128)) == []
    assert no_offer_reason(_hw(128)) == _NO_LANE_REASON


def test_offers_empty_on_a_vulkan_host_without_rocm_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real lane gate, not a stub: NVIDIA or Intel with a Vulkan node is offered nothing."""
    monkeypatch.setattr(capability_catalog, "comfyui_backends", _REAL_COMFYUI_BACKENDS)
    monkeypatch.setattr(
        capability_catalog, "available_backends", lambda: [{"id": "gpu-vulkan"}, {"id": "cpu"}]
    )
    monkeypatch.setattr(capability_catalog, "host_is_amd_gpu", lambda: False)
    monkeypatch.setattr(capability_catalog, "rocm_lane_present", lambda: False)

    assert image_offers(_hw(128)) == []
    assert no_offer_reason(_hw(128)) == _NO_LANE_REASON


def test_a_box_with_offers_has_no_reason_to_give() -> None:
    assert image_offers(_hw(128))
    assert no_offer_reason(_hw(128)) is None


def test_offers_are_the_api_renderable_catalog_models_in_catalog_order() -> None:
    expected = [m.id for m in CATALOG.values() if m.api_renderable]
    offers = image_offers(_hw(128))
    assert [o.id for o in offers] == expected
    assert "esrgan-4x" not in expected  # an upscaler the API never renders is not a starter
    first = offers[0]
    assert isinstance(first, ImageOffer)
    assert first.label == CATALOG[first.id].label
    assert first.approx_gb == CATALOG[first.id].approx_gb
    assert first.est_seconds == CATALOG[first.id].est_seconds
    assert first.license == CATALOG[first.id].license


@pytest.mark.parametrize("pool_gb", [0, 8, 15, 16, 64, 96, 128])
def test_the_installer_and_the_picker_recommend_the_same_model(
    monkeypatch: pytest.MonkeyPatch, pool_gb: int
) -> None:
    """One owner (``picker.recommended_for``): an unprobed 0 GB box gets the same pick from the
    install-time offer as from ``GET /api/comfyui/models``, not SD 1.5 in one place only."""
    hw = _hw(pool_gb)
    monkeypatch.setattr(picker, "load_hardware_info", lambda: hw)

    marked = [o.id for o in image_offers(hw) if o.recommended]

    assert marked == [picker.recommended_id()]


@pytest.mark.parametrize(
    ("pool_gb", "recommended"),
    [(128, "qwen-image-lightning"), (64, "sdxl-lightning"), (8, "sd-1.5-pruned-emaonly")],
)
def test_offers_mark_recommended_by_pool(pool_gb: int, recommended: str) -> None:
    marked = [o.id for o in image_offers(_hw(pool_gb)) if o.recommended]
    assert marked == [recommended]


def test_missing_gb_counts_only_what_is_not_on_disk() -> None:
    _land("sdxl-lightning")
    by_id = {o.id: o for o in image_offers(_hw(64))}
    assert by_id["sdxl-lightning"].missing_gb == 0.0
    assert by_id["sdxl-lightning"].approx_gb == CATALOG["sdxl-lightning"].approx_gb
    assert by_id["sdxl-turbo"].missing_gb == CATALOG["sdxl-turbo"].approx_gb


# ── --plan / --resolve ──────────────────────────────────────────────────────


@pytest.fixture
def hw(monkeypatch: pytest.MonkeyPatch) -> HardwareInfo:
    box = _hw(64)
    monkeypatch.setattr(image_models, "_load_hardware", lambda: box)
    return box


def _plan(capsys: pytest.CaptureFixture[str]) -> list[list[str]]:
    assert main(["--plan"]) == 0
    return [line.split("\t") for line in capsys.readouterr().out.splitlines()]


def test_plan_prints_one_tab_separated_line_per_offer(
    hw: HardwareInfo, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = _plan(capsys)
    assert [r[0] for r in rows] == [o.id for o in image_offers(hw)]
    assert all(len(r) == 3 for r in rows)
    sdxl = next(r for r in rows if r[0] == "sdxl-lightning")
    model = CATALOG["sdxl-lightning"]
    assert sdxl[1] == (
        f"{model.label} — {model.approx_gb:.1f} GB download, ~{model.est_seconds}s per image"
    )
    assert sdxl[2] == "1"  # 64 GB pool
    assert {r[2] for r in rows if r[0] != "sdxl-lightning"} == {"0"}


def test_offer_line_says_already_downloaded(
    hw: HardwareInfo, capsys: pytest.CaptureFixture[str]
) -> None:
    _land("sdxl-lightning")
    rows = {r[0]: r[1] for r in _plan(capsys)}
    assert rows["sdxl-lightning"].endswith(" (already downloaded)")
    assert " 0.0 GB download" in rows["sdxl-lightning"]
    assert "already downloaded" not in rows["sdxl-turbo"]


def test_plan_without_a_lane_prints_the_reason_alone(
    hw: HardwareInfo, no_lane: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _plan(capsys) == [["none", _NO_LANE_REASON]]


def test_main_plan_never_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> HardwareInfo:
        raise RuntimeError("hardware.json is unreadable")

    monkeypatch.setattr(image_models, "_load_hardware", boom)

    assert main(["--plan"]) == 0

    (line,) = capsys.readouterr().out.splitlines()
    marker, reason = line.split("\t")
    assert marker == "none"
    assert "hardware.json is unreadable" in reason


def test_main_resolve_prints_ids_space_separated(
    hw: HardwareInfo, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = [o.id for o in image_offers(hw)]
    assert main(["--resolve", "1,2"]) == 0
    assert capsys.readouterr().out.split() == ids[:2]
    assert main(["--resolve", "recommended"]) == 0
    assert capsys.readouterr().out.split() == ["sdxl-lightning"]
    assert main(["--resolve", "none"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_main_resolve_rejects_a_bad_answer_with_exit_2(
    hw: HardwareInfo, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--resolve", "bogus"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "bogus" in captured.err
    assert "recommended" in captured.err  # the message lists what is valid


def test_main_resolve_never_raises_when_hardware_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> HardwareInfo:
        raise RuntimeError("no hardware")

    monkeypatch.setattr(image_models, "_load_hardware", boom)

    assert main(["--resolve", "recommended"]) == 1  # not an invalid answer: 2 is reserved for that
    assert "no hardware" in capsys.readouterr().err


# ── the answer grammar ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("", []),
        ("   ", []),
        ("none", []),
        ("None", []),
        ("recommended", ["sdxl-lightning"]),
        ("1,3", ["sdxl-lightning", "sd-1.5-pruned-emaonly"]),
        ("1 1 3", ["sdxl-lightning", "sd-1.5-pruned-emaonly"]),
        ("3, 1", ["sd-1.5-pruned-emaonly", "sdxl-lightning"]),
        ("sdxl-lightning", ["sdxl-lightning"]),
        ("2 sdxl-lightning", ["sdxl-turbo", "sdxl-lightning"]),
    ],
)
def test_resolve_selection_grammar(answer: str, expected: list[str]) -> None:
    assert resolve_selection(answer, image_offers(_hw(64))) == expected


@pytest.mark.parametrize(
    ("answer", "offender"),
    [
        ("9", "9"),
        ("0", "0"),
        ("-1", "-1"),
        ("bogus", "bogus"),
        ("1,bogus", "bogus"),
        ("esrgan-4x", "esrgan-4x"),  # in the catalog, but not something the API renders
        ("recommended 1", "recommended"),  # `recommended` is a whole answer, not a list item
    ],
)
def test_resolve_selection_rejects_what_it_cannot_resolve(answer: str, offender: str) -> None:
    offers = image_offers(_hw(64))
    with pytest.raises(ValueError) as exc:
        resolve_selection(answer, offers)
    message = str(exc.value)
    assert repr(offender) in message  # names the token it could not resolve
    assert "recommended" in message
    assert "none" in message
    assert f"1-{len(offers)}" in message
    assert "sdxl-lightning" in message


def test_resolve_selection_with_no_offers_only_accepts_a_skip() -> None:
    assert resolve_selection("", []) == []
    assert resolve_selection("none", []) == []
    for answer in ("recommended", "1", "sdxl-lightning"):
        with pytest.raises(ValueError):
            resolve_selection(answer, [])


# ── provisioning ────────────────────────────────────────────────────────────


class _FakeOrchestrator:
    """Enough of ``CapabilityOrchestrator`` for the auto-select: state in, apply recorded."""

    def __init__(self, selected: str | None = None) -> None:
        self.applied: list[tuple[str, str, dict[str, Any]]] = []
        self._model = selected

    async def get_state(self) -> dict[str, Any]:
        selection = {"enabled": True, "model": self._model} if self._model else {}
        return {"selections": {"img": {"img": selection}}}

    async def apply(self, slot: str, child: str, partial: dict[str, Any]) -> dict[str, Any]:
        self.applied.append((slot, child, partial))
        self._model = partial["model"]
        return {**partial, "status": "ready"}


@pytest.fixture
def installs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``run_install`` stubbed: every id ends in ``outcomes[id]`` (default ``completed``)."""
    seen: dict[str, Any] = {"order": [], "outcomes": {}}

    async def fake_run_install(job, model, *, hf_token, client=None, on_complete=None):
        seen["order"].append((model.id, hf_token))
        job.state = seen["outcomes"].get(model.id, "completed")

    monkeypatch.setattr(comfyui_install, "run_install", fake_run_install)
    monkeypatch.setattr(comfyui_install, "preflight_disk", lambda model: None)
    return seen


@pytest.fixture
def owui(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    from hal0.components import openwebui_arm

    monkeypatch.setattr(
        openwebui_arm, "reconcile_openwebui_env", lambda **kw: calls.append("reconciled") or {}
    )
    return calls


def _provision(ids: list[str], orchestrator: Any, lines: list[str] | None = None):
    import asyncio

    return asyncio.run(
        provision_image_models(
            ids,
            orchestrator=orchestrator,
            hf_token="hf_token",
            on_line=(lines if lines is not None else []).append,
        )
    )


def test_provision_installs_in_order_and_returns_each_final_state(
    installs: dict[str, Any], owui: list[str]
) -> None:
    installs["outcomes"]["sdxl-turbo"] = "failed"

    results = _provision(
        ["sdxl-lightning", "sdxl-turbo", "sd-1.5-pruned-emaonly"], _FakeOrchestrator()
    )

    assert results == [
        ("sdxl-lightning", "completed"),
        ("sdxl-turbo", "failed"),
        ("sd-1.5-pruned-emaonly", "completed"),
    ]
    assert installs["order"] == [
        ("sdxl-lightning", "hf_token"),
        ("sdxl-turbo", "hf_token"),
        ("sd-1.5-pruned-emaonly", "hf_token"),
    ]


def test_provision_auto_selects_first_renderable_once(
    installs: dict[str, Any], owui: list[str]
) -> None:
    orchestrator = _FakeOrchestrator()

    # esrgan-4x lands first but the API never renders it; the next two are both renderable.
    _provision(["esrgan-4x", "sdxl-lightning", "sd-1.5-pruned-emaonly"], orchestrator)

    ((slot, child, partial),) = orchestrator.applied
    assert (slot, child) == ("img", "img")
    assert partial["model"] == "sdxl-lightning"
    assert partial["enabled"] is True


def test_provision_selects_the_first_renderable_that_completed(
    installs: dict[str, Any], owui: list[str]
) -> None:
    installs["outcomes"]["sdxl-lightning"] = "failed"
    orchestrator = _FakeOrchestrator()

    _provision(["sdxl-lightning", "sdxl-turbo"], orchestrator)

    assert [p["model"] for _, _, p in orchestrator.applied] == ["sdxl-turbo"]


def test_provision_leaves_an_existing_selection_alone(
    installs: dict[str, Any], owui: list[str]
) -> None:
    orchestrator = _FakeOrchestrator(selected="qwen-image")

    _provision(["sdxl-lightning"], orchestrator)

    assert orchestrator.applied == []


def test_provision_selects_nothing_when_nothing_landed(
    installs: dict[str, Any], owui: list[str]
) -> None:
    installs["outcomes"] = {"sdxl-lightning": "failed", "esrgan-4x": "completed"}
    orchestrator = _FakeOrchestrator()

    _provision(["sdxl-lightning", "esrgan-4x"], orchestrator)

    assert orchestrator.applied == []
    assert owui == []  # nothing the API can render landed: nothing for Open WebUI to learn


def test_provision_rewires_open_webui_once_after_a_renderable_model_landed(
    installs: dict[str, Any], owui: list[str]
) -> None:
    _provision(["sdxl-lightning", "sdxl-turbo"], _FakeOrchestrator())

    assert owui == ["reconciled"]


def test_a_failing_open_webui_reconcile_changes_nothing(
    installs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.components import openwebui_arm

    def boom(**kw: Any) -> dict[str, Any]:
        raise RuntimeError("systemctl is gone")

    monkeypatch.setattr(openwebui_arm, "reconcile_openwebui_env", boom)

    assert _provision(["sdxl-lightning"], _FakeOrchestrator()) == [("sdxl-lightning", "completed")]


def test_a_refused_apply_does_not_fail_the_install(
    installs: dict[str, Any], owui: list[str]
) -> None:
    from hal0.errors import BadRequest

    class Refusing(_FakeOrchestrator):
        async def apply(self, slot, child, partial):
            raise BadRequest("no", code="capability.illegal_backend_model_pair")

    assert _provision(["sdxl-lightning"], Refusing()) == [("sdxl-lightning", "completed")]


def test_an_explicit_install_clears_the_operators_earlier_removal(
    installs: dict[str, Any], owui: list[str]
) -> None:
    comfyui_install.mark_removed("sdxl-lightning")
    assert comfyui_install.is_removed("sdxl-lightning")

    _provision(["sdxl-lightning"], _FakeOrchestrator())

    assert not comfyui_install.is_removed("sdxl-lightning")


def test_provision_reports_progress_through_on_line(
    installs: dict[str, Any], owui: list[str]
) -> None:
    lines: list[str] = []

    _provision(["sdxl-lightning"], _FakeOrchestrator(), lines)

    assert any("sdxl-lightning" in line for line in lines)


def test_provision_reports_an_unknown_id_as_failed_and_carries_on(
    installs: dict[str, Any], owui: list[str]
) -> None:
    results = _provision(["not-a-model", "sdxl-lightning"], _FakeOrchestrator())

    assert results == [("not-a-model", "failed"), ("sdxl-lightning", "completed")]


def test_provision_says_what_it_is_doing_and_what_came_of_it(
    installs: dict[str, Any], owui: list[str]
) -> None:
    label = CATALOG["sdxl-lightning"].label
    lines: list[str] = []

    _provision(["sdxl-lightning"], _FakeOrchestrator(), lines)

    before, after = lines[-2:]
    assert before == (
        f"making {label} the default image model: this starts the image engine, "
        "and its first start downloads the ComfyUI runtime, which can take several minutes"
    )
    assert after == f"{label} is the default image model; the image engine is ready"


def test_provision_does_not_claim_more_than_the_apply_reported(
    installs: dict[str, Any], owui: list[str]
) -> None:
    class Warming(_FakeOrchestrator):
        async def apply(self, slot, child, partial):
            await super().apply(slot, child, partial)
            return {"status": "warming"}

    lines: list[str] = []
    _provision(["sdxl-lightning"], Warming(), lines)
    assert lines[-1].endswith("the image engine is warming")

    class Silent(_FakeOrchestrator):
        async def apply(self, slot, child, partial):
            await super().apply(slot, child, partial)
            return {}

    lines = []
    _provision(["sdxl-lightning"], Silent(), lines)
    assert lines[-1].endswith("the image engine has been started")


def test_provision_says_when_it_kept_the_operators_selection(
    installs: dict[str, Any], owui: list[str]
) -> None:
    label = CATALOG["sdxl-lightning"].label
    lines: list[str] = []

    _provision(["sdxl-lightning"], _FakeOrchestrator(selected="qwen-image"), lines)

    assert lines[-1] == (
        f"keeping qwen-image as the default image model; {label} is installed "
        "and can be picked in the dashboard"
    )


def test_provision_prints_a_refusal_as_one_line_with_its_message(
    installs: dict[str, Any], owui: list[str]
) -> None:
    from hal0.errors import BadRequest

    class Refusing(_FakeOrchestrator):
        async def apply(self, slot, child, partial):
            raise BadRequest("device is busy", code="capability.invalid_selection")

    lines: list[str] = []

    _provision(["sdxl-lightning"], Refusing(), lines)

    label = CATALOG["sdxl-lightning"].label
    # Refused before the selection was saved: there is nothing to load yet.
    assert lines[-1] == (
        f"could not make {label} the default image model (device is busy); pick it in the dashboard"
    )


def test_provision_points_at_slot_load_when_the_selection_was_saved_but_the_start_failed(
    installs: dict[str, Any], owui: list[str]
) -> None:
    from hal0.errors import Hal0Error

    class StartFailed(_FakeOrchestrator):
        async def apply(self, slot, child, partial):
            await super().apply(slot, child, partial)  # the commit happens before the lifecycle
            raise Hal0Error("the unit did not come up", code="capability.apply_failed")

    lines: list[str] = []

    results = _provision(["sdxl-lightning"], StartFailed(), lines)

    assert results == [("sdxl-lightning", "completed")]  # the install itself is untouched
    label = CATALOG["sdxl-lightning"].label
    assert lines[-1] == (
        f"could not make {label} the default image model (the unit did not come up); "
        "load it later with: hal0 slot load img"
    )
    assert owui == ["reconciled"]


def test_the_failure_line_is_there_whatever_the_selection_state_reads(
    installs: dict[str, Any], owui: list[str]
) -> None:
    """An unreadable selection after a failure falls back to the dashboard hint."""
    from hal0.errors import Hal0Error

    class Unreadable(_FakeOrchestrator):
        async def get_state(self):
            if self.applied:
                raise RuntimeError("state is gone")
            return await super().get_state()

        async def apply(self, slot, child, partial):
            await super().apply(slot, child, partial)
            raise Hal0Error("nope", code="capability.apply_failed")

    lines: list[str] = []

    _provision(["sdxl-lightning"], Unreadable(), lines)

    assert lines[-1].endswith("(nope); pick it in the dashboard")


def test_provision_turns_an_unexpected_error_into_a_line_not_a_traceback(
    installs: dict[str, Any], owui: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    class Broken(_FakeOrchestrator):
        async def get_state(self):
            return {"selections": {}}

        async def apply(self, slot, child, partial):
            raise RuntimeError("boom")

    lines: list[str] = []
    with caplog.at_level("DEBUG"):
        results = _provision(["sdxl-lightning"], Broken(), lines)

    assert results == [("sdxl-lightning", "completed")]
    assert "RuntimeError: boom" in lines[-1]
    assert "Traceback" not in "\n".join(lines)
    assert owui == ["reconciled"]  # the rewire still runs


def test_provision_says_why_a_pick_was_skipped(installs: dict[str, Any], owui: list[str]) -> None:
    lines: list[str] = []

    _provision(["sdxl-lightning"], None, lines)

    assert "was not made the default image model (no capability orchestrator)" in lines[-1]


def test_provision_prints_no_selection_lines_when_nothing_selectable_landed(
    installs: dict[str, Any], owui: list[str]
) -> None:
    installs["outcomes"]["sdxl-lightning"] = "failed"
    lines: list[str] = []

    _provision(["sdxl-lightning"], _FakeOrchestrator(), lines)

    assert not any("default image model" in line for line in lines)


def test_provision_offline_still_downloads_when_the_orchestrator_cannot_be_built(
    installs: dict[str, Any], owui: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken() -> Any:
        raise RuntimeError("slot manager would not build")

    monkeypatch.setattr(image_models, "_build_offline_orchestrator", broken)
    lines: list[str] = []

    results = image_models.provision_offline(
        ["sdxl-lightning"], hf_token="hf_token", on_line=lines.append
    )

    assert results == [("sdxl-lightning", "completed")]
    assert any(
        "could not build the capability orchestrator (RuntimeError: slot manager would not build)"
        in line
        for line in lines
    )
    assert "no capability orchestrator" in lines[-1]


def test_installer_log_lines_carry_no_traceback() -> None:
    """A WARNING that logs an exception reaches the operator as one line."""
    import logging
    import sys

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord(
            "hal0.test", logging.WARNING, __file__, 1, "something failed", None, sys.exc_info()
        )

    text = image_models._OneLineFormatter("  %(message)s").format(record)

    assert text == "  something failed\nRuntimeError: boom"
    assert "Traceback" not in text


# ── main() installing ids ───────────────────────────────────────────────────


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``provision_offline`` stubbed: what ``main`` handed it, and what it answers.

    Hardware is made unreadable: installing ids has no use for it, so ``main`` must not ask.
    """
    seen: dict[str, Any] = {"results": None}

    def no_hardware() -> HardwareInfo:
        raise AssertionError("installing ids must not read the hardware")

    monkeypatch.setattr(image_models, "_load_hardware", no_hardware)

    def fake(model_ids, *, hf_token, on_line):
        seen["call"] = (tuple(model_ids), hf_token)
        on_line("sdxl-lightning: installing")
        if isinstance(seen["results"], Exception):
            raise seen["results"]
        return seen["results"]

    monkeypatch.setattr(image_models, "provision_offline", fake)
    monkeypatch.setenv("HF_TOKEN", "hf_from_env")
    return seen


def test_main_installs_the_ids_it_is_given(
    offline: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    offline["results"] = [("sdxl-lightning", "completed"), ("sdxl-turbo", "completed")]

    assert main(["sdxl-lightning", "sdxl-turbo"]) == 0

    assert offline["call"] == (("sdxl-lightning", "sdxl-turbo"), "hf_from_env")
    assert "sdxl-lightning: installing" in capsys.readouterr().out


def test_main_exits_1_when_any_model_failed(offline: dict[str, Any]) -> None:
    offline["results"] = [("sdxl-lightning", "completed"), ("sdxl-turbo", "failed")]
    assert main(["sdxl-lightning", "sdxl-turbo"]) == 1


def test_main_exits_0_for_a_cancelled_model(offline: dict[str, Any]) -> None:
    offline["results"] = [("sdxl-lightning", "cancelled")]
    assert main(["sdxl-lightning"]) == 0


def test_main_never_raises_from_the_install(
    offline: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    offline["results"] = RuntimeError("slot manager would not build")

    assert main(["sdxl-lightning"]) == 1

    assert "slot manager would not build" in capsys.readouterr().err


def test_main_with_no_ids_installs_nothing(offline: dict[str, Any]) -> None:
    assert main([]) == 0
    assert "call" not in offline
