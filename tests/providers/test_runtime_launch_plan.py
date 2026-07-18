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

from hal0.config.schema import ProfileConfig, resolve_profile_flags
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
    plan = _llama_launch_plan(
        image=image,
        port=port,
        model_path=model_path,
        flags_str=flags_str,
        devices=list(devices),
        group_ids=[str(g) for g in resolve_gpu_group_ids()],
        context_size=context_size,
        extra_args=extra_args,
        model_alias=model_alias,
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
        assert Mount("/a", "/b").render() == "/a:/b"

    def test_read_only_appends_ro(self) -> None:
        assert Mount("/a", "/b", read_only=True).render() == "/a:/b:ro"

    def test_read_only_does_not_double_ro_on_legacy_target(self) -> None:
        # A target that already carries ":ro" must not become ":ro:ro".
        assert Mount("/a", "/b:ro", read_only=True).render() == "/a:/b:ro"

    def test_selinux_relabel_appended(self) -> None:
        # SELinux relabel is a first-class flag (Fedora/enforcing hosts).
        assert Mount("/a", "/b", read_only=True, selinux="z").render() == "/a:/b:ro,z"
        assert Mount("/a", "/b", selinux="z").render() == "/a:/b:z"
        assert Mount("/a", "/b:ro", read_only=True, selinux="z").render() == "/a:/b:ro,z"

    def test_coerce_passes_mount_through(self) -> None:
        m = Mount("/a", "/b", read_only=True)
        assert Mount.coerce(m) is m

    def test_coerce_plain_tuple_is_read_write(self) -> None:
        m = Mount.coerce(("/a", "/b"))
        assert (m.source, m.target, m.read_only) == ("/a", "/b", False)

    def test_coerce_ro_suffixed_tuple_becomes_read_only(self) -> None:
        # Legacy callers smuggled ":ro" into the target — coercion recovers it.
        m = Mount.coerce(("/a", "/b:ro"))
        assert (m.source, m.target, m.read_only) == ("/a", "/b", True)


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

    def test_mount_and_legacy_tuple_render_identically(self) -> None:
        # The renderer coerces a legacy (src, dst:ro) tuple to the same Volume
        # value a first-class read-only Mount produces.
        as_mount = RuntimeLaunchPlan(
            image="img", command=["x"], mounts=[Mount("/m", "/m", read_only=True)]
        )
        as_tuple = RuntimeLaunchPlan(image="img", command=["x"], mounts=[("/m", "/m:ro")])
        m_unit = _render_from_plan("s", as_mount)
        t_unit = _render_from_plan("s", as_tuple)
        assert "Volume=/m:/m:ro" in m_unit.splitlines()
        assert m_unit == t_unit


# ── legacy shim folds into the single builder ──────────────────────────────────


def test_render_llama_shim_matches_equivalent_plan(monkeypatch) -> None:
    """The llama scalar shim must produce the same ``Exec=`` argv as building the
    equivalent RuntimeLaunchPlan and rendering it — proving the legacy llama
    path is now just the spec path with a thin adapter."""
    monkeypatch.setenv("HAL0_MODEL_STORE", _MODEL_STORE_MOUNT)  # pin the default
    profile = ProfileConfig(image="ghcr.io/x:server", flags="-fa on --no-mmap", mtp=False)
    flags = resolve_profile_flags(profile)

    shim_unit = _render_llama(
        "chat",
        profile.image,
        8095,
        "/mnt/ai-models/m.gguf",
        flags,
        device_paths=["/dev/kfd", "/dev/dri/renderD128"],
        context_size=131072,
        model_alias="my-model",
    )

    equivalent = RuntimeLaunchPlan(
        image=profile.image,
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
        assert isinstance(_spec_provider_for({"profile": "tts"}), KokoroProvider)

    def test_comfyui_profile_routes_by_family(self) -> None:
        assert isinstance(_spec_provider_for({"profile": "comfyui"}), ComfyUIProvider)

    def test_flm_profile_routes_by_family(self) -> None:
        assert isinstance(_spec_provider_for({"profile": "flm"}), FLMProvider)

    def test_gpu_profile_routes_to_llama_default(self) -> None:
        assert _spec_provider_for({"profile": "rocm"}) is None

    def test_unknown_profile_falls_back_to_device_hint(self) -> None:
        # Unresolvable profile → family None → device fallback still routes NPU.
        assert isinstance(_spec_provider_for({"profile": "nope", "device": "npu"}), FLMProvider)
