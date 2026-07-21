import httpx

from hal0.cli.setup_install import _apply_in_process, _apply_via_api, choose_apply_mode
from hal0.install.orchestrate import Selections, SetupResult


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


# ── _apply_via_api: graceful 409 handling (issue #1158) ──────────────────────


def _empty_selections() -> Selections:
    return Selections(storage_dir="", slots=[], extensions={})


class _FakeAsyncClient:
    """Stand-in for ``httpx.AsyncClient`` that returns a canned POST response.

    ``apply-selections`` is POSTed; ``_dashboard_url`` issues a GET we don't
    care about here, so GET raises and the URL resolver falls back to the API
    base (its documented degradation)."""

    _post_response: httpx.Response

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        return type(self)._post_response

    async def get(self, url, **kwargs):
        raise httpx.ConnectError("dashboard url probe not stubbed")


def _install_fake_client(monkeypatch, response: httpx.Response) -> None:
    _FakeAsyncClient._post_response = response
    monkeypatch.setattr("hal0.cli.setup_install.httpx.AsyncClient", _FakeAsyncClient)


async def test_apply_via_api_409_is_recoverable(monkeypatch, capsys):
    """A 409 from apply-selections must NOT abort setup with an HTTPStatusError.

    The endpoint is idempotent/re-runnable, so a conflict means "already applied
    or apply in progress" — we surface the message and continue (issue #1158)."""
    resp = httpx.Response(
        status_code=409,
        json={
            "error": {
                "code": "install.apply_in_progress",
                "message": "an apply is already in progress",
                "details": {},
            }
        },
        request=httpx.Request("POST", "http://127.0.0.1:8080/api/install/apply-selections"),
    )
    _install_fake_client(monkeypatch, resp)

    # Must not raise (previously raise_for_status() blew up here).
    await _apply_via_api(_empty_selections())

    out = capsys.readouterr().out
    assert "already" in out.lower()
    assert "an apply is already in progress" in out


async def test_apply_via_api_non_conflict_error_still_raises(monkeypatch):
    """A genuine server failure (500) still surfaces — we only soften 409."""
    resp = httpx.Response(
        status_code=500,
        json={"error": {"code": "system.internal", "message": "boom"}},
        request=httpx.Request("POST", "http://127.0.0.1:8080/api/install/apply-selections"),
    )
    _install_fake_client(monkeypatch, resp)

    try:
        await _apply_via_api(_empty_selections())
    except httpx.HTTPStatusError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected HTTPStatusError for a 500 response")


# ── _apply_via_api: timeout configuration (issue: 150lxc installer) ───────


def test_setup_http_timeout_default_is_300():
    """Default timeout is 300s (was 30s, too tight for cold-start apply)."""
    import hal0.cli.setup_install as m

    assert m.SETUP_HTTP_TIMEOUT_SECS == 300.0


def test_setup_http_timeout_env_var_override(monkeypatch):
    """Operators on slow hardware can raise (or lower) the timeout via env."""
    import importlib

    import hal0.cli.setup_install as m

    monkeypatch.setenv("HAL0_SETUP_TIMEOUT_SECS", "45.5")
    importlib.reload(m)
    try:
        assert m.SETUP_HTTP_TIMEOUT_SECS == 45.5
    finally:
        # Restore default for other tests in this module.
        monkeypatch.delenv("HAL0_SETUP_TIMEOUT_SECS", raising=False)
        importlib.reload(m)


def test_setup_http_timeout_bad_env_falls_back_to_default(monkeypatch):
    """A non-numeric HAL0_SETUP_TIMEOUT_SECS falls back to 300s, not a crash."""
    import importlib

    import hal0.cli.setup_install as m

    monkeypatch.setenv("HAL0_SETUP_TIMEOUT_SECS", "not-a-number")
    importlib.reload(m)
    try:
        assert m.SETUP_HTTP_TIMEOUT_SECS == 300.0
    finally:
        monkeypatch.delenv("HAL0_SETUP_TIMEOUT_SECS", raising=False)
        importlib.reload(m)


async def test_apply_via_api_uses_configured_timeout(monkeypatch):
    """The HTTP client must use SETUP_HTTP_TIMEOUT_SECS, not a hardcoded value."""
    import hal0.cli.setup_install as m

    monkeypatch.setattr(m, "SETUP_HTTP_TIMEOUT_SECS", 123.0)

    captured_timeout: dict = {}

    class _CapturingClient(_FakeAsyncClient):
        def __init__(self, *args, **kwargs):
            # ``_dashboard_url`` also instantiates a client; key the capture to
            # the POST path only by inspecting the caller's stack frame via the
            # last kwarg passed to ``post`` upstream is fragile, so just take
            # the FIRST client (the POST) and reuse the same fake for the GET.
            if "value" not in captured_timeout:
                captured_timeout["value"] = kwargs.get("timeout")

    resp = httpx.Response(
        200,
        json={"model_ids": [], "slots": []},
        request=httpx.Request("POST", "http://127.0.0.1:8080/api/install/apply-selections"),
    )
    _FakeAsyncClient._post_response = resp
    monkeypatch.setattr("hal0.cli.setup_install.httpx.AsyncClient", _CapturingClient)

    await _apply_via_api(_empty_selections())

    assert captured_timeout["value"] is not None
    assert captured_timeout["value"].connect == 123.0
