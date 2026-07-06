from hal0.cli.setup_install import _apply_in_process, choose_apply_mode
from hal0.install.orchestrate import SetupResult


def test_mode_in_process_when_api_down(monkeypatch):
    monkeypatch.setattr("hal0.cli.setup_install._api_reachable", lambda **k: False)
    assert choose_apply_mode() == "in_process"


def test_mode_api_when_up(monkeypatch):
    monkeypatch.setattr("hal0.cli.setup_install._api_reachable", lambda **k: True)
    assert choose_apply_mode() == "api"


def _stub_offline_deps(monkeypatch):
    """Patch setup_command._build_offline_deps so _apply_in_process never
    touches a real SlotManager/ModelRegistry."""
    monkeypatch.setattr(
        "hal0.cli.setup_command._build_offline_deps",
        lambda: (object(), object()),
    )


def _capture_apply_setup(monkeypatch):
    """Patch hal0.install.orchestrate.apply_setup (imported lazily inside
    _apply_in_process) and return a dict that captures the kwargs it was
    called with."""
    captured: dict = {}

    async def fake_apply_setup(
        sel, *, hardware, slot_manager, registry, jobs, hf_token=None, write_sentinel=True
    ):
        captured["hf_token"] = hf_token
        return SetupResult(slots=[], extensions=[], model_ids=[], pulls=[])

    monkeypatch.setattr("hal0.install.orchestrate.apply_setup", fake_apply_setup)
    return captured


async def test_apply_in_process_threads_hf_token(monkeypatch):
    """HF_TOKEN in the environment must reach apply_setup (issue #1094)."""
    _stub_offline_deps(monkeypatch)
    captured = _capture_apply_setup(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)

    await _apply_in_process(sel=object(), hw=object(), no_pull=True)

    assert captured["hf_token"] == "hf_test_token"


async def test_apply_in_process_falls_back_to_hugging_face_hub_token(monkeypatch):
    """HUGGING_FACE_HUB_TOKEN is used when HF_TOKEN is unset, matching the API
    route's precedence in hal0/api/routes/installer.py."""
    _stub_offline_deps(monkeypatch)
    captured = _capture_apply_setup(monkeypatch)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hf_fallback_token")

    await _apply_in_process(sel=object(), hw=object(), no_pull=True)

    assert captured["hf_token"] == "hf_fallback_token"


async def test_apply_in_process_no_token_passes_none(monkeypatch):
    """Neither var set: apply_setup receives hf_token=None (its own default),
    not a hardcoded skip of the kwarg."""
    _stub_offline_deps(monkeypatch)
    captured = _capture_apply_setup(monkeypatch)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)

    await _apply_in_process(sel=object(), hw=object(), no_pull=True)

    assert captured["hf_token"] is None
