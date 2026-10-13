"""A slot's ``[server].env`` may not set a llama-server option.

llama.cpp reads every option from an environment variable as well as from
argv (``common_arg::set_env`` in ``common/arg.cpp`` at the runner pin,
b11574): ``LLAMA_ARG_*`` (and ``LLAMA_ARG_NO_*``) plus ``LLAMA_API_KEY``,
``HF_TOKEN`` and ``MTMD_BACKEND_DEVICE``. The slot env reaches the
llama-server container, so it could otherwise set what the profile screen
refuses — ``LLAMA_ARG_MODEL`` redirects the model, ``LLAMA_ARG_AGENT`` turns
on every built-in tool. Tuning belongs on the profile, so every such name is
refused on each write path and again at launch.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.errors import BadRequest
from hal0.slots.argv import LLAMA_ENV_NAMES, deny_llama_env, llama_env_offenders

#: A sample of the names arg.cpp b11574 declares, across every kind of flag
#: the profile screen refuses, plus an ordinary tuning one and a negation.
DENIED = [
    "LLAMA_ARG_MODEL",
    "LLAMA_ARG_ALIAS",
    "LLAMA_ARG_HOST",
    "LLAMA_ARG_PORT",
    "LLAMA_ARG_CTX_SIZE",
    "LLAMA_ARG_N_GPU_LAYERS",
    "LLAMA_ARG_MMPROJ",
    "LLAMA_ARG_JINJA",
    "LLAMA_ARG_HF_REPO",
    "LLAMA_ARG_API_KEY_FILE",
    "LLAMA_ARG_AGENT",
    "LLAMA_ARG_TOOLS",
    "LLAMA_ARG_UI_MCP_PROXY",
    "LLAMA_ARG_FLASH_ATTN",
    "LLAMA_ARG_NO_WEBUI",
    "LLAMA_API_KEY",
    "HF_TOKEN",
    "MTMD_BACKEND_DEVICE",
]

#: Runtime env a slot legitimately carries.
ALLOWED = {
    "HSA_OVERRIDE_GFX_VERSION": "11.5.1",
    "ROCR_VISIBLE_DEVICES": "0",
    "GGML_VK_VISIBLE_DEVICES": "0",
    "LLAMA_CACHE": "/var/cache/llama",
}


def test_the_unprefixed_names_are_pinned() -> None:
    assert frozenset({"LLAMA_API_KEY", "HF_TOKEN", "MTMD_BACKEND_DEVICE"}) == LLAMA_ENV_NAMES


@pytest.mark.parametrize("name", DENIED)
def test_each_llama_option_name_is_refused(name: str) -> None:
    assert llama_env_offenders({name: "x", **ALLOWED}) == [name]
    with pytest.raises(BadRequest) as exc:
        deny_llama_env({name: "x"}, segment="slot [server].env")
    assert exc.value.code == "slot.llama_env_denied"
    assert exc.value.details["keys"] == [name]


def test_ordinary_runtime_env_passes() -> None:
    assert llama_env_offenders(ALLOWED) == []
    deny_llama_env(ALLOWED, segment="slot [server].env")
    deny_llama_env(None, segment="slot [server].env")


# ── write paths ──────────────────────────────────────────────────────────────


def _payload(env: dict[str, str]) -> dict[str, Any]:
    return {"device": "gpu-rocm", "server": {"env": env}}


@pytest.mark.parametrize("name", ["LLAMA_ARG_MODEL", "LLAMA_ARG_AGENT", "HF_TOKEN"])
def test_the_write_guards_refuse_it(name: str) -> None:
    from hal0.slot_config import reject_slot_llama_env, reject_slot_tuning_keys
    from hal0.slots.config_write import guard_slot_write_payload, reconcile_slot_updates

    for check in (
        reject_slot_llama_env,
        reject_slot_tuning_keys,
        guard_slot_write_payload,
        lambda p: reconcile_slot_updates({"name": "s", "port": 8081}, p),
    ):
        with pytest.raises(BadRequest) as exc:
            check(_payload({name: "x"}))
        assert exc.value.code == "slot.llama_env_denied"


def test_the_write_guards_pass_ordinary_env() -> None:
    from hal0.slot_config import reject_slot_tuning_keys

    reject_slot_tuning_keys(_payload(ALLOWED))


def _cfg(name: str = "chat", env: dict[str, str] | None = None) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "name": name,
        "port": 18081,
        "type": "llm",
        "device": "gpu-rocm",
        "profile": "chat",
        "model": {"default": "m"},
    }
    if env is not None:
        cfg["server"] = {"env": env}
    return cfg


async def test_slot_manager_create_refuses_it(tmp_hal0_home: str) -> None:
    from hal0.config import paths
    from hal0.slots.manager import SlotManager

    sm = SlotManager()
    with pytest.raises(BadRequest) as exc:
        await sm.create("chat", _cfg(env={"LLAMA_ARG_MODEL": "/etc/shadow"}))
    assert exc.value.code == "slot.llama_env_denied"
    assert not (paths.slots_config_dir() / "chat.toml").exists()


async def test_slot_manager_update_config_refuses_it(tmp_hal0_home: str) -> None:
    from hal0.slots.manager import SlotManager

    sm = SlotManager()
    await sm.create("chat", _cfg(env=ALLOWED))
    with pytest.raises(BadRequest) as exc:
        await sm.update_config("chat", {"server": {"env": {"LLAMA_ARG_TOOLS": "all"}}})
    assert exc.value.code == "slot.llama_env_denied"
    assert (await sm.get_config("chat"))["server"]["env"] == ALLOWED


# ── launch ───────────────────────────────────────────────────────────────────


def test_the_launch_refuses_a_hand_edited_env(tmp_hal0_home: str) -> None:
    """A slot file edited on disk is refused when its launch is resolved."""
    from hal0.providers.container import _resolve_llama_scalars, resolve_slot_profile

    cfg = _cfg(env={"LLAMA_ARG_AGENT": "1"})
    with pytest.raises(BadRequest) as exc:
        _resolve_llama_scalars(cfg, {"path": "/models/m.gguf"}, resolve_slot_profile(cfg))
    assert exc.value.code == "slot.llama_env_denied"


def test_the_launch_keeps_ordinary_env(tmp_hal0_home: str) -> None:
    from hal0.providers.container import _resolve_llama_scalars, resolve_slot_profile

    cfg = _cfg(env=ALLOWED)
    scalars = _resolve_llama_scalars(cfg, {"path": "/models/m.gguf"}, resolve_slot_profile(cfg))
    assert scalars["server_env"] == ALLOWED
