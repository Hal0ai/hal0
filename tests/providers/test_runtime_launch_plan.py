"""RuntimeLaunchPlan + the single Quadlet renderer (P3-quadlet — one renderer,
first-class read-only mounts and health).

Covers what the refactor newly guarantees:
  * Mount expresses read-only as a flag (not a ":ro" target string), and the
    renderer emits the right ``Volume=`` value either way (incl. legacy tuples);
  * HealthCheck renders the ``Health*=`` Quadlet keys;
  * the llama scalar shim and building the equivalent RuntimeLaunchPlan agree on
    the ``Exec=`` argv — the legacy llama path folds into the spec path;
  * _spec_provider_for routes on the profile's runtime_family.
"""

from __future__ import annotations

import shlex

import pytest

from hal0.config.schema import ProfileConfig
from hal0.providers._gpu import resolve_gpu_device_paths, resolve_gpu_group_ids
from hal0.providers.base import HealthCheck, Mount, RuntimeLaunchPlan
from hal0.providers.comfyui import ComfyUIProvider
from hal0.providers.container import (
    _MODEL_STORE_MOUNT,
    _llama_launch_plan,
    _render_quadlet_from_plan,
    _spec_provider_for,
)
from hal0.providers.flm import FLMProvider
from hal0.providers.kokoro import KokoroProvider


# Thin shims so call sites change minimally. ``runtime_bin`` is accepted and
# IGNORED — Quadlet is podman-only, there is no runtime to select.
def _render_from_plan(token, plan, *, runtime_bin=None, publish_host="127.0.0.1"):
    return _render_quadlet_from_plan(token, plan, publish_host=publish_host)


def _render_llama(
    token,
    image,
    port,
    model_path,
    flags_str,
    *,
    runtime_bin=None,
    device_paths=None,
    context_size=None,
    extra_args=None,
    model_alias=None,
    publish_host="127.0.0.1",
):
    devices = device_paths if device_paths is not None else resolve_gpu_device_paths()
    # ADR-0029: the profile is the whole tune — the flags ride the profile layer.
    merged_tune = " ".join(p for p in ((flags_str or "").strip(), (extra_args or "").strip()) if p)
    plan = _llama_launch_plan(
        image=image,
        port=port,
        model_path=model_path,
        devices=list(devices),
        group_ids=[str(g) for g in resolve_gpu_group_ids()],
        context_size=context_size if context_size is not None else 8192,
        model_alias=model_alias,
        profile_flags=merged_tune,
    )
    return _render_quadlet_from_plan(token, plan, publish_host=publish_host)


def _exec(unit_text: str) -> str:
    """The in-container argv from the Quadlet ``Exec=`` line (no podman preamble)."""
    for line in unit_text.splitlines():
        if line.startswith("Exec="):
            return line[len("Exec=") :]
    raise AssertionError("Exec not found")


# ── Mount ──────────────────────────────────────────────────────────────────────


class TestMount:
    def test_read_write_renders_src_dst(self) -> None:
        assert Mount("/a", "/b").render_quadlet() == "/a:/b"

    def test_read_only_appends_ro(self) -> None:
        assert Mount("/a", "/b", read_only=True).render_quadlet() == "/a:/b:ro"

    def test_selinux_relabel_appended(self, monkeypatch) -> None:
        # SELinux relabel is a first-class flag (Fedora/enforcing hosts).
        monkeypatch.setattr("hal0.config.paths.is_nfs", lambda path: False)
        assert Mount("/a", "/b", read_only=True, selinux="z").render_quadlet() == "/a:/b:ro,z"
        assert Mount("/a", "/b", selinux="z").render_quadlet() == "/a:/b:z"


# ── HealthCheck ────────────────────────────────────────────────────────────────


class TestHealthCheck:
    def test_render_flags_order_and_defaults(self) -> None:
        flags = HealthCheck(cmd="curl -fsS http://127.0.0.1:9/health || exit 1").render_flags()
        assert flags[0] == "--health-cmd=curl -fsS http://127.0.0.1:9/health || exit 1"
        assert "--health-start-period=180s" in flags
        assert "--health-interval=30s" in flags
        assert "--health-retries=3" in flags
        assert "--health-timeout=5s" in flags


# ── single renderer ─────────────────────────────────────────────────────────────


class TestRenderUnitFromPlan:
    def test_host_network_skips_publish(self) -> None:
        plan = RuntimeLaunchPlan(image="img", command=["x"], port=8080, network_mode="host")
        unit = _render_from_plan("s", plan)
        assert "PublishPort" not in unit
        assert "Network=host" in unit

    def test_empty_network_publishes_loopback_from_port(self) -> None:
        plan = RuntimeLaunchPlan(image="img", command=["x"], port=8080, network_mode="")
        unit = _render_from_plan("s", plan)
        assert "PublishPort=127.0.0.1:8080:8080" in unit
        assert "Network=" not in unit

    def test_no_health_emits_no_health_flags(self) -> None:
        plan = RuntimeLaunchPlan(image="img", command=["x"], port=8080, network_mode="")
        assert "HealthCmd=" not in _render_from_plan("s", plan)

    def test_read_only_mount_renders_volume_ro(self) -> None:
        plan = RuntimeLaunchPlan(
            image="img", command=["x"], mounts=[Mount("/m", "/m", read_only=True)]
        )
        assert "Volume=/m:/m:ro" in _render_from_plan("s", plan).splitlines()


# ── stop grace + restart-prevent codes (typed launch-plan fields) ──────────────


def test_plan_defaults_render_no_stop_or_extra_restart_codes() -> None:
    """Both fields default to rendering nothing: no ``--stop-timeout``, no
    ``TimeoutStopSec``, and ``RestartPreventExitStatus`` stays exactly ``64 132``."""
    unit = _render_from_plan("s", RuntimeLaunchPlan(image="img", command=["x"]))
    assert "--stop-timeout" not in unit
    assert "TimeoutStopSec" not in unit
    assert "RestartPreventExitStatus=64 132" in unit.splitlines()


def test_stop_timeout_renders_podman_arg_and_service_key() -> None:
    plan = RuntimeLaunchPlan(
        image="x:1",
        command=["all"],
        stop_timeout_s=120,
        restart_prevent_exit_status=(1,),
    )
    unit = _render_from_plan("flash", plan)
    assert "PodmanArgs=--stop-timeout=120" in unit
    assert "TimeoutStopSec=150" in unit
    assert "RestartPreventExitStatus=64 132 1" in unit


def test_stop_timeout_follows_ipc_and_ulimits_in_podman_args() -> None:
    plan = RuntimeLaunchPlan(
        image="img",
        command=["x"],
        ipc="host",
        ulimits=["memlock=-1"],
        stop_timeout_s=60,
    )
    assert "PodmanArgs=--ipc=host --ulimit memlock=-1 --stop-timeout=60" in (
        _render_from_plan("s", plan).splitlines()
    )


def test_timeout_stop_sec_sits_right_after_restart_max_delay() -> None:
    plan = RuntimeLaunchPlan(image="img", command=["x"], stop_timeout_s=120)
    lines = _render_from_plan("s", plan).splitlines()
    assert lines[lines.index("RestartMaxDelaySec=300") + 1] == "TimeoutStopSec=150"


# ── Environment= quoting ───────────────────────────────────────────────────────


def _env_assignments(unit: str) -> list[str]:
    """The assignments systemd reads from the unit's ``Environment=`` lines, in order.

    systemd splits each line into words on unquoted spaces and strips ``"``
    quoting with ``\\"`` and ``\\\\`` escapes inside it, which is what POSIX
    ``shlex`` does for the shapes the renderer emits.
    """
    out: list[str] = []
    for line in unit.splitlines():
        if line.startswith("Environment="):
            out.extend(shlex.split(line[len("Environment=") :]))
    return out


def test_environment_values_without_special_characters_render_bare() -> None:
    plan = RuntimeLaunchPlan(
        image="img",
        command=["x"],
        env={
            "HSA_OVERRIDE_GFX_VERSION": "11.5.1",
            "LD_LIBRARY_PATH": "/opt/a/lib:/opt/b/lib",
            "URL": "http://127.0.0.1:8080/x?a=b",
        },
    )
    lines = _render_from_plan("s", plan).splitlines()
    assert "Environment=HSA_OVERRIDE_GFX_VERSION=11.5.1" in lines
    assert "Environment=LD_LIBRARY_PATH=/opt/a/lib:/opt/b/lib" in lines
    assert "Environment=URL=http://127.0.0.1:8080/x?a=b" in lines


def test_environment_value_with_spaces_is_one_quoted_assignment() -> None:
    plan = RuntimeLaunchPlan(image="img", command=["x"], env={"MSG": "Time is up."})
    unit = _render_from_plan("s", plan)
    assert 'Environment="MSG=Time is up."' in unit.splitlines()
    assert _env_assignments(unit) == ["MSG=Time is up."]


def test_environment_value_cannot_open_a_second_assignment() -> None:
    plan = RuntimeLaunchPlan(
        image="img", command=["x"], env={"A": "y HALOGEN_DOWNLOAD=1", "B": "2"}
    )
    assert _env_assignments(_render_from_plan("s", plan)) == ["A=y HALOGEN_DOWNLOAD=1", "B=2"]


@pytest.mark.parametrize(
    "value",
    ['say "hi"', "back\\slash", "it's", "tab\there", "mix \"q\" and \\ and 's' x"],
)
def test_environment_value_with_quotes_or_backslashes_round_trips(value: str) -> None:
    plan = RuntimeLaunchPlan(image="img", command=["x"], env={"K": value, "OTHER": "2"})
    assert _env_assignments(_render_from_plan("s", plan)) == [f"K={value}", "OTHER=2"]


# ── legacy shim folds into the single builder ──────────────────────────────────


def test_render_llama_shim_matches_equivalent_plan(monkeypatch) -> None:
    """The llama scalar shim must produce the same ``Exec=`` argv as building the
    equivalent RuntimeLaunchPlan and rendering it — proving the legacy llama
    path is now just the spec path with a thin adapter."""
    monkeypatch.setenv("HAL0_MODEL_STORE", _MODEL_STORE_MOUNT)  # pin the default
    profile = ProfileConfig(flags="-fa on --no-mmap", speculation="off")
    flags = profile.flags

    shim_unit = _render_llama(
        "chat",
        "ghcr.io/hal0ai/hal0-toolbox:v1",
        8095,
        "/mnt/ai-models/m.gguf",
        flags,
        device_paths=["/dev/kfd", "/dev/dri/renderD128"],
        context_size=131072,
        model_alias="my-model",
    )

    equivalent = RuntimeLaunchPlan(
        image="ghcr.io/hal0ai/hal0-toolbox:v1",
        command=[
            "--host",
            "0.0.0.0",
            "--port",
            "8095",
            "--model",
            "/mnt/ai-models/m.gguf",
            "--alias",
            "my-model",
            "--ctx-size",
            "131072",
            *shlex.split(flags),
            # #1810: _llama_launch_plan's slot_metrics defaults True, so the
            # shim (which doesn't pass it) emits --metrics unconditionally.
            "--metrics",
        ],
        mounts=[Mount(_MODEL_STORE_MOUNT, _MODEL_STORE_MOUNT, read_only=True, selinux="z")],
        devices=["/dev/kfd", "/dev/dri/renderD128"],
        security_opt=["apparmor=unconfined", "seccomp=unconfined"],
        port=8095,
        network_mode="",
        health=HealthCheck(cmd="curl -fsS http://127.0.0.1:8095/health || exit 1"),
    )

    # group_add is resolved from the host in the shim, but it renders as a
    # ``GroupAdd=`` [Container] key, not into ``Exec=`` — so the in-container
    # argv is directly comparable.
    assert _exec(shim_unit) == _exec(_render_from_plan("chat", equivalent))


# ── runtime-family dispatch (#1 provider-half: stop string-matching) ───────────


class TestSpecProviderRuntimeFamily:
    def test_kokoro_profile_routes_by_family(self) -> None:
        # Profile present, no type/device hint — family alone must decide.
        assert isinstance(_spec_provider_for({"profile": "kokoro"}), KokoroProvider)

    def test_comfyui_profile_routes_by_family(self) -> None:
        assert isinstance(_spec_provider_for({"profile": "comfyui"}), ComfyUIProvider)

    def test_flm_profile_routes_by_family(self) -> None:
        assert isinstance(_spec_provider_for({"profile": "flm"}), FLMProvider)

    def test_gpu_profile_routes_to_llama_default(self) -> None:
        assert _spec_provider_for({"profile": "rocm"}) is None

    def test_unknown_profile_falls_back_to_device_hint(self) -> None:
        # Unresolvable profile → family None → device fallback still routes NPU.
        assert isinstance(_spec_provider_for({"profile": "nope", "device": "npu"}), FLMProvider)
