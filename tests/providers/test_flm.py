"""Unit tests for FLMProvider.

Critical: TIER1 — the haloai FLM health probe accepted an empty
/v1/models and an unstuck-but-non-functional NPU as "ready". hal0
requires a real inference round-trip. These tests are the contract.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from hal0.providers.flm import (
    _DEFAULT_FLM_IMAGE,
    FLMInferError,
    FLMProvider,
)

_HAL0_FLM_IMAGE = _DEFAULT_FLM_IMAGE


@pytest.fixture
def provider() -> FLMProvider:
    return FLMProvider()


@pytest.fixture
def slot_cfg() -> dict[str, Any]:
    return {"port": 8086, "ctx_size": 65536, "_paths": {}}


@pytest.fixture
def model_info() -> dict[str, Any]:
    return {"flm_tag": "qwen3.5:0.8b", "path": "/var/lib/hal0/models/flm-qwen3.5"}


# ─── build_env ────────────────────────────────────────────────────────────────


def test_build_env_renames_to_hal0_namespace(
    provider: FLMProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    env = provider.build_env(slot_cfg, model_info)
    assert all(k.startswith("HAL0_") for k in env)
    assert env["HAL0_FLM_TAG"] == "qwen3.5:0.8b"
    assert env["HAL0_PORT"] == "8086"
    assert env["HAL0_FLM_CTX"] == "65536"


def test_build_env_multiplex_flags(provider: FLMProvider, model_info: dict[str, Any]) -> None:
    """FLM multiplexes ASR + embed on the same NPU via defaults flags."""
    slot_cfg = {
        "port": 8086,
        "defaults": {"load_asr": True, "load_embed": True},
        "_paths": {},
    }
    env = provider.build_env(slot_cfg, model_info)
    assert env["HAL0_FLM_LOAD_ASR"] == "1"
    assert env["HAL0_FLM_LOAD_EMBED"] == "1"


def test_build_env_defaults_to_no_multiplex(
    provider: FLMProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    env = provider.build_env(slot_cfg, model_info)
    assert env["HAL0_FLM_LOAD_ASR"] == "0"
    assert env["HAL0_FLM_LOAD_EMBED"] == "0"


# ─── start_cmd ────────────────────────────────────────────────────────────────


def test_start_cmd_uses_flm_serve(
    provider: FLMProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    env = provider.build_env(slot_cfg, model_info)
    cmd = provider.start_cmd(env)
    assert "serve" in cmd
    assert env["HAL0_FLM_TAG"] in cmd
    assert "--port" in cmd
    assert "--ctx-len" in cmd


# ─── image_ref / container_spec ───────────────────────────────────────────────


def test_image_ref_is_hal0ai_flm(provider: FLMProvider) -> None:
    assert provider.image_ref({}) == _HAL0_FLM_IMAGE
    assert _HAL0_FLM_IMAGE.startswith("ghcr.io/hal0ai/hal0-toolbox-flm")


def test_container_spec_passes_through_accel_device(
    provider: FLMProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    spec = provider.container_spec(slot_cfg, model_info)
    # /dev/accel/accel0 is the XDNA2 NPU device node.
    assert "/dev/accel/accel0" in spec.devices
    assert spec.port == 8086


def test_container_spec_does_not_bind_mount_host_flm_tree(
    provider: FLMProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    """The image bundles FLM; do not host-mount a binary tree on top of it.

    Earlier provider versions bind-mounted /opt/hal0/flm-ubuntu on top of
    the same path inside the container, so internal absolute symlinks
    (bin/xclbins → share/flm/xclbins) resolved against the host tree.
    The toolbox image now bundles FLM at /opt/fastflowlm with the same
    internal layout, so the host mount is unnecessary — and the host
    directory is empty in production, which masked the redundancy.
    """
    spec = provider.container_spec(slot_cfg, model_info)
    mount_pairs = list(spec.mounts)
    host_dirs = [host for host, _ in mount_pairs]
    assert "/opt/hal0/flm-ubuntu" not in host_dirs, (
        "host /opt/hal0/flm-ubuntu must NOT be bind-mounted — the image "
        f"is self-contained. Mounts: {mount_pairs!r}"
    )


def test_container_spec_command_does_not_prefix_binary_path(
    provider: FLMProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    """First command arg must be the flm subcommand, not a binary path.

    Regression: the image's ENTRYPOINT is ``tini -- /usr/local/bin/flm``,
    so whatever we pass becomes flm's argv. Prepending an absolute path
    (the old behaviour) makes flm reject the args with::

        Error parsing arguments: too many positional options have been
        specified on the command line

    Diagnosed live on hal0 on 2026-05-20 once the LD_LIBRARY_PATH fix
    let flm reach main(). First command arg must be 'serve' (or another
    flm subcommand), never a path.
    """
    spec = provider.container_spec(slot_cfg, model_info)
    assert spec.command, "container_spec returned an empty command"
    first = spec.command[0]
    assert not first.startswith("/"), (
        f"first command arg must be an flm subcommand, not an absolute "
        f"path; got {first!r}. Full command: {spec.command!r}"
    )
    assert first == "serve", f"expected first arg to be 'serve' subcommand; got {first!r}"


def test_container_spec_passes_multiplex_flags_in_command(
    provider: FLMProvider, model_info: dict[str, Any]
) -> None:
    cfg = {
        "port": 8086,
        "defaults": {"load_embed": True, "load_asr": True, "context_size": 2048},
        "_paths": {},
    }
    spec = provider.container_spec(cfg, model_info)
    cmd = spec.command
    assert "--embed" in cmd and "1" in cmd
    assert "--asr" in cmd


def test_container_spec_ld_library_path_includes_xrt(
    provider: FLMProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    """LD_LIBRARY_PATH must include /opt/xilinx/xrt/lib.

    Regression: docker `--env LD_LIBRARY_PATH=...` overwrites the image's
    own ENV from the Dockerfile. If this spec omits /opt/xilinx/xrt/lib,
    /usr/local/bin/flm fails to load libxrt_coreutil.so.2 at startup
    (status 127, "error while loading shared libraries") before main()
    runs. Diagnosed live on hal0 on 2026-05-20 — the FLM toolbox image
    was correct, the env override broke it.
    """
    spec = provider.container_spec(slot_cfg, model_info)
    ld = spec.env.get("LD_LIBRARY_PATH", "")
    assert "/opt/xilinx/xrt/lib" in ld, f"LD_LIBRARY_PATH missing XRT runtime path: {ld!r}"


# ─── health (TIER1 inference round-trip) ──────────────────────────────────────


def _mock_response(
    *, status_code: int = 200, json_payload: Any = None, text: str = ""
) -> MagicMock:
    """httpx.Response stub with SYNC raise_for_status (matches the real API)."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = lambda: json_payload
    resp.text = text
    if status_code < 400:
        resp.raise_for_status = MagicMock(return_value=None)
    else:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                f"http {status_code}", request=MagicMock(), response=resp
            )
        )
    return resp


@pytest.mark.asyncio
async def test_health_requires_inference_round_trip(provider: FLMProvider) -> None:
    """TIER1: health MUST exercise /v1/chat/completions, not just /v1/models.

    This is the explicit contract from PLAN.md §5 Tier 1 (haloai bug
    at lib/slots.py:899-920).
    """
    models_payload = {"data": [{"id": "qwen3.5:0.8b"}]}
    chat_payload = {"choices": [{"message": {"content": "x"}}]}

    sentinel_was_called = {"value": False}

    async def _fake_get(url: str) -> httpx.Response:
        assert url.endswith("/v1/models")
        return _mock_response(status_code=200, json_payload=models_payload)

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        # TIER1: sentinel POST is required, with max_tokens=1.
        assert url.endswith("/v1/chat/completions")
        assert json["max_tokens"] == 1
        assert json["model"] == "qwen3.5:0.8b"
        sentinel_was_called["value"] = True
        return _mock_response(status_code=200, json_payload=chat_payload)

    with patch("hal0.providers.flm.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        client.post = _fake_post
        result = await provider.health(8086)

    assert sentinel_was_called["value"], (
        "TIER1: FLM health probe MUST issue a /v1/chat/completions sentinel "
        "(haloai bug was reporting ready without it)."
    )
    assert result["ok"] is True
    assert result["status"] == "ready"
    assert result["model"] == "qwen3.5:0.8b"


@pytest.mark.asyncio
async def test_health_rejects_empty_models(provider: FLMProvider) -> None:
    """TIER1: empty /v1/models → not ready (the original haloai bug)."""

    async def _fake_get(url: str) -> httpx.Response:
        return _mock_response(status_code=200, json_payload={"data": []})

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        raise AssertionError("must not POST when /v1/models is empty")

    with patch("hal0.providers.flm.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        client.post = _fake_post
        result = await provider.health(8086)

    assert result["ok"] is False
    assert result["status"] == "models_endpoint_empty"


@pytest.mark.asyncio
async def test_health_rejects_models_ok_but_inference_failing(
    provider: FLMProvider,
) -> None:
    """TIER1: populated /v1/models but failing inference → not ready.

    This is the precise failure mode the haloai code missed.
    """
    models_payload = {"data": [{"id": "qwen3.5:0.8b"}]}

    async def _fake_get(url: str) -> httpx.Response:
        return _mock_response(status_code=200, json_payload=models_payload)

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        # Inference fails — NPU loaded the model metadata but the runtime is stuck.
        return _mock_response(status_code=500, text="kernel not ready")

    with patch("hal0.providers.flm.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        client.post = _fake_post
        result = await provider.health(8086)

    assert result["ok"] is False, (
        "TIER1: failed sentinel must drop ok=False even though /v1/models was good."
    )
    assert "sentinel_completion_http_500" in result["status"]


@pytest.mark.asyncio
async def test_health_rejects_response_with_no_choices(provider: FLMProvider) -> None:
    """TIER1: 200 but malformed (no choices) → not ready."""
    models_payload = {"data": [{"id": "qwen3.5:0.8b"}]}

    async def _fake_get(url: str) -> httpx.Response:
        return _mock_response(status_code=200, json_payload=models_payload)

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        return _mock_response(status_code=200, json_payload={"id": "x"})  # no choices

    with patch("hal0.providers.flm.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        client.post = _fake_post
        result = await provider.health(8086)

    assert result["ok"] is False
    assert result["status"] == "sentinel_completion_no_choices"


@pytest.mark.asyncio
async def test_health_transport_failure_surfaces_typed_status(
    provider: FLMProvider,
) -> None:
    async def _fake_get(url: str) -> httpx.Response:
        raise httpx.ConnectError("ECONNREFUSED")

    with patch("hal0.providers.flm.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        result = await provider.health(8086)

    assert result["ok"] is False
    assert result["status"] == "http_error"


# ─── infer ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_infer_passthrough(provider: FLMProvider) -> None:
    expected = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        return _mock_response(status_code=200, json_payload=expected)

    with patch("hal0.providers.flm.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.post = _fake_post
        out = await provider.infer(8086, {"model": "x", "messages": []})

    assert out == expected


@pytest.mark.asyncio
async def test_infer_raises_typed_error_on_upstream_failure(
    provider: FLMProvider,
) -> None:
    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        return _mock_response(status_code=502)

    with patch("hal0.providers.flm.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.post = _fake_post
        with pytest.raises(FLMInferError) as exc:
            await provider.infer(8086, {})
    assert exc.value.code == "dispatch.upstream_failed"


# ─── host-flm catalog probe + pull (no docker toolbox) ──────────────────────────
# Regression: the probe used to run the docker toolbox (FLM v0.9.42) against an
# empty mount, reporting installed=False for every model so the dashboard hid
# them. It now runs the host flm binary as the hal0 user against the real cache.


def test_probe_uses_host_flm_binary_not_docker() -> None:
    """_probe_flm_catalog shells the host flm binary, never `docker run`."""
    import hal0.providers.flm as flm

    captured: dict[str, Any] = {}

    def _fake_run(argv: list[str], **kwargs: Any) -> Any:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return MagicMock(returncode=0, stdout=b'{"models": []}')

    with patch("subprocess.run", _fake_run):
        flm._probe_flm_catalog()

    assert captured["argv"][0] == flm._HOST_FLM_BIN
    assert captured["argv"][1:] == ["list", "-j"]
    assert "docker" not in captured["argv"]
    # HOME is set so flm resolves ~/.config/flm/models to the real cache.
    assert captured["kwargs"]["env"]["HOME"] == flm._HOST_FLM_HOME


def test_probe_strips_warning_preamble_and_reads_installed() -> None:
    """A leading [WARNING] line must not null the catalog; installed flags survive."""
    import hal0.providers.flm as flm

    noisy = (
        b"[WARNING]  Local model version: 0.9.43 > 0.9.42\n"
        b'{"models": [{"model": "gemma4-it:e4b", "installed": true},'
        b' {"model": "qwen3:0.6b", "installed": false}]}\n'
    )
    with patch("subprocess.run", lambda *a, **k: MagicMock(returncode=0, stdout=noisy)):
        flm.reset_flm_catalog_cache()
        out = flm.flm_served_models()
        flm.reset_flm_catalog_cache()

    by_tag = {m["tag"]: m["installed"] for m in out}
    assert by_tag == {"gemma4-it:e4b": True, "qwen3:0.6b": False}


def test_probe_returns_none_when_binary_missing() -> None:
    import hal0.providers.flm as flm

    def _boom(*a: Any, **k: Any) -> Any:
        raise FileNotFoundError

    with patch("subprocess.run", _boom):
        assert flm._probe_flm_catalog() is None


def test_pull_command_is_host_flm_and_real_cache_dir() -> None:
    import hal0.providers.flm as flm

    argv, host_dir = flm.flm_pull_command("gemma4-it:e4b")
    assert argv == [flm._HOST_FLM_BIN, "pull", "gemma4-it:e4b"]
    assert "docker" not in argv
    assert host_dir == flm._HOST_FLM_MODELS_DIR
    assert host_dir.endswith("/.config/flm/models")


def test_spawn_kwargs_sets_home_and_skips_user_when_not_root() -> None:
    """As a non-root test runner, HOME is set but user= is omitted (would EPERM)."""
    import hal0.providers.flm as flm

    with patch("os.geteuid", lambda: 1000):
        kw = flm.flm_host_spawn_kwargs()
    assert kw["env"]["HOME"] == flm._HOST_FLM_HOME
    assert "user" not in kw


# ─── is_installed_flm_id (slot-apply provider-resolvability) ─────────────────


def test_is_installed_flm_id_matches_installed_tag() -> None:
    """The <tag>-FLM id of an INSTALLED probe model resolves true."""
    import hal0.providers.flm as flm

    fake = [
        {"tag": "gemma4-it:e4b", "installed": True, "capabilities": ["chat"]},
        {"tag": "qwen3:0.6b", "installed": False, "capabilities": ["chat"]},
    ]
    with patch("hal0.providers.flm.flm_served_models", lambda: fake):
        assert flm.is_installed_flm_id("gemma4-it-e4b-FLM") is True
        # not installed → false
        assert flm.is_installed_flm_id("qwen3-0.6b-FLM") is False
        # unknown tag → false
        assert flm.is_installed_flm_id("nope-7b-FLM") is False
        # missing -FLM suffix (a colon tag or GGUF id) → false (fast path)
        assert flm.is_installed_flm_id("gemma4-it:e4b") is False
        assert flm.is_installed_flm_id("chadrock-35b-ace-saber") is False


# ─── flm_id_to_tag (hal0 <tag>-FLM id → FLM native colon tag) ────────────────


def test_flm_id_to_tag_resolves_colon_tag() -> None:
    """A hal0 ``<tag>-FLM`` id resolves to FLM's native ``family:size`` tag.

    Regression for the slot crash-loop where the FLM provider was handed the
    raw ``-FLM`` id and FLM answered "Model not found" (manager._resolve_model_info
    stamped ``flm_tag = model_id`` without translating).
    """
    import hal0.providers.flm as flm

    fake = [
        {"tag": "gemma4-it:e2b", "installed": True, "capabilities": ["chat"]},
        {"tag": "embed-gemma:300m", "installed": True, "capabilities": ["embed"]},
        {"tag": "whisper-v3:turbo", "installed": False, "capabilities": ["stt"]},
    ]
    with patch("hal0.providers.flm.flm_served_models", lambda: fake):
        # family with embedded dashes still maps off the LAST segment
        assert flm.flm_id_to_tag("gemma4-it-e2b-FLM") == "gemma4-it:e2b"
        assert flm.flm_id_to_tag("embed-gemma-300m-FLM") == "embed-gemma:300m"
        # resolves regardless of installed flag (pure naming transform)
        assert flm.flm_id_to_tag("whisper-v3-turbo-FLM") == "whisper-v3:turbo"
        # unknown id → None (caller falls back to the raw id)
        assert flm.flm_id_to_tag("nope-7b-FLM") is None
        # already a colon tag / not an -FLM id → None (fast path)
        assert flm.flm_id_to_tag("gemma4-it:e2b") is None
        assert flm.flm_id_to_tag("chadrock-35b-ace-saber") is None


def test_flm_id_to_tag_empty_catalog_returns_none() -> None:
    """When the catalog probe is empty, resolution yields None (no crash)."""
    import hal0.providers.flm as flm

    with patch("hal0.providers.flm.flm_served_models", list):
        assert flm.flm_id_to_tag("gemma4-it-e2b-FLM") is None
