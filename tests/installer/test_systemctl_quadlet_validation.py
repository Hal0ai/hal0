"""#1740 — root-side content allow-list for the ``write-quadlet`` verb.

``installer/wrappers/hal0-systemctl`` is reachable as root by the unprivileged
``hal0`` service account (packaging/sudoers/hal0-systemctl), so *stdin* is
attacker-controlled at a privilege boundary. ``write-quadlet`` used to
``install /dev/stdin`` verbatim into
``/etc/containers/systemd/hal0-slot@<id>.container``.

A ``.container`` file is not merely a container spec: podman's quadlet
generator copies its ``[Unit]``, ``[Service]`` and ``[Install]`` sections
through VERBATIM into the generated system unit. So a ``[Service]`` carrying
``ExecStartPre=/bin/sh -c 'id>/root/pwned'``, followed by this same wrapper's
``daemon-reload`` + ``start`` verbs, was an unconditional root exec — no
container escape required. #1718 closed exactly this hole for the two drop-in
verbs and left this one open.

These tests exercise the *real* bash wrapper through its side-effect-free
``check-quadlet`` verb (the same validator the write arm calls), so they need
no root, no sudo and no provisioned box — the #1718 suite's posture.

The legitimate bodies come from the actual renderer
(:func:`hal0.providers.container._render_quadlet_from_plan`, the ONE producer
of slot-unit text) rather than being retyped, so a renderer change the
allow-list would reject fails here instead of on a production host.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hal0.providers.base import HealthCheck, Mount, RuntimeLaunchPlan
from hal0.providers.container import ContainerProvider, _render_quadlet_from_plan

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "installer" / "wrappers" / "hal0-systemctl"


def _check(body: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the real wrapper's validator over ``body``. rc 0 = accepted."""
    return subprocess.run(
        [str(WRAPPER), "check-quadlet", *args],
        input=body,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )


def _accepts(body: str, *args: str) -> None:
    proc = _check(body, *args)
    assert proc.returncode == 0, f"rejected: {proc.stderr}"
    # The write arm persists the VALIDATED reconstruction, not the raw stdin,
    # so what is echoed here is byte-for-byte what lands in /etc.
    assert proc.stdout == body


def _rejects(body: str, *args: str) -> str:
    proc = _check(body, *args)
    assert proc.returncode == 64, f"accepted (rc={proc.returncode}): {body!r}"
    return proc.stderr


# ── the wrapper still parses, and the write arm actually validates ─────────


def test_wrapper_is_syntactically_valid() -> None:
    proc = subprocess.run(["bash", "-n", str(WRAPPER)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_write_quadlet_arm_validates_before_writing() -> None:
    """The validator must be on the write path, not merely available: a
    ``check-quadlet`` verb nothing calls would be security theatre."""
    text = WRAPPER.read_text()
    arm = text.split("  write-quadlet)", 1)[1].split("\n    ;;", 1)[0]
    # Comments explain the history; only the executable lines are the contract.
    code = "\n".join(ln for ln in arm.splitlines() if not ln.lstrip().startswith("#"))
    assert "validate_quadlet_body" in code
    # …and it writes the validated reconstruction, never raw stdin.
    assert "QUADLET_BODY" in code
    assert "/dev/stdin" not in code
    assert "cat > " not in code


def test_write_unit_verb_is_gone() -> None:
    """``write-unit`` installed an unvalidated body as a root-owned
    ``/etc/systemd/system/*.service``. P3-quadlet left it with no producer, so
    there is no render contract to allow-list against — the verb is deleted."""
    text = WRAPPER.read_text()
    assert "\n  write-unit)" not in text
    from hal0.system.seam import SystemCtlSeam

    assert not hasattr(SystemCtlSeam, "write_unit")


# ── the real renderer's output, byte-for-byte ──────────────────────────────
#
# One plan per shape the fleet actually renders. Every optional key in
# ``_render_quadlet_from_plan`` is covered by at least one entry.

_PLANS: dict[str, tuple[RuntimeLaunchPlan, dict[str, Any]]] = {
    "minimal": (
        RuntimeLaunchPlan(image="ghcr.io/hal0ai/hal0-toolbox-rocm:v1", command=[], network_mode=""),
        {},
    ),
    "gpu-llama-bridge": (
        RuntimeLaunchPlan(
            image="ghcr.io/hal0ai/hal0-toolbox-rocm:v1",
            command=[
                "/opt/llama.cpp/build/bin/llama-server",
                "--host",
                "0.0.0.0",
                "--port",
                "8095",
                "-m",
                "/mnt/ai-models/gguf/model-Q4_K_M.gguf",
                "--chat-template-kwargs",
                '{"enable_thinking":false}',
            ],
            env={"HSA_OVERRIDE_GFX_VERSION": "11.0.0", "HIP_VISIBLE_DEVICES": "0"},
            mounts=[
                Mount("/mnt/ai-models", "/mnt/ai-models", read_only=True),
                Mount("/var/lib/hal0/cache", "/cache", read_only=False, selinux="z"),
            ],
            devices=["/dev/kfd", "/dev/dri/renderD128"],
            cap_add=["SYS_PTRACE"],
            security_opt=["seccomp=unconfined", "label=disable"],
            group_add=["44", "991"],
            port=8095,
            network_mode="",
            health=HealthCheck(cmd="curl -fsS http://127.0.0.1:8095/health || exit 1"),
        ),
        {"publish_host": "0.0.0.0", "network_mode_default": "bridge"},
    ),
    "hostname-publish-host": (
        # #1740 F1 regression: [slots].publish_host permits a bare hostname
        # (SlotsConfig._publish_host_sane), so a bridge-mode slot renders
        # PublishPort=<hostname>:port:port. An IPv4-only allow-list charset
        # false-rejected this documented, default-path config and bricked the
        # slot load.
        RuntimeLaunchPlan(
            image="ghcr.io/hal0ai/hal0-toolbox-rocm:v1",
            command=["llama-server", "--port", "8095"],
            port=8095,
            network_mode="",
        ),
        {"publish_host": "hal0.local", "network_mode_default": "bridge"},
    ),
    "host-net-comfyui": (
        RuntimeLaunchPlan(
            image="ghcr.io/hal0ai/hal0-comfyui:v1",
            command=["python", "main.py", "--listen", "0.0.0.0", "--port", "8188"],
            mounts=[Mount("/var/lib/hal0/comfyui/models", "/root/comfy-models")],
            devices=["nvidia.com/gpu=all"],
            port=8188,
            network_mode="host",
        ),
        {},
    ),
    "npu-flm": (
        RuntimeLaunchPlan(
            image="ghcr.io/hal0ai/hal0-flm:v1",
            command=["flm", "serve", "qwen3-8b", "--port", "8110"],
            env={"FLM_CACHE": "/cache"},
            devices=["/dev/accel/accel0"],
            port=8110,
            network_mode="",
        ),
        {"autoload": False},
    ),
    # #1759: the two shipped providers that emit PodmanArgs= flags beyond the
    # GPU compat pair. Their rendered bodies must still pass the seam, or the
    # slot fails to load on a hal0-service install.
    "comfyui-ipc-host": (
        RuntimeLaunchPlan(
            image="ghcr.io/hal0ai/hal0-comfyui:v1",
            command=["python", "main.py"],
            port=8188,
            network_mode="host",
            extra_args=["--ipc=host"],
        ),
        {},
    ),
    "flm-ulimit-memlock": (
        RuntimeLaunchPlan(
            image="ghcr.io/hal0ai/hal0-flm:v1",
            command=["flm", "serve"],
            port=8110,
            network_mode="",
            extra_args=["--ulimit memlock=-1"],
        ),
        {},
    ),
}


# The deprecated `extra_args` escape hatch renders arbitrary `podman run` flags
# into PodmanArgs=. #1759 allow-lists only the flags hal0's providers emit
# (--group-add/--security-opt/--ipc/--ulimit), so an out-of-list flag is refused
# at the root seam — that was the surviving root-exec vector. Pinned here.
def test_out_of_allowlist_podman_args_are_refused_at_the_seam() -> None:
    """A slot whose extra_args carry a flag outside the provider allow-list
    (e.g. --privileged) is rejected on the root side after #1759, by design."""
    from hal0.providers.base import RuntimeLaunchPlan as _Plan
    from hal0.providers.container import _render_quadlet_from_plan as _render

    plan = _Plan(
        image="ghcr.io/hal0ai/hal0-toolbox-vulkan:v1",
        command=["llama-server"],
        extra_args=["--privileged"],
        port=8101,
        network_mode="",
    )
    body = _render("chat", plan)
    assert "PodmanArgs=" in body  # the renderer still emits it (with a warning)
    err = _rejects(body, "chat")
    assert "PodmanArgs" in err


@pytest.mark.parametrize("shape", sorted(_PLANS))
@pytest.mark.parametrize("token", ["chat", "slot_1", "Mixed-Case", "a" * 64])
def test_rendered_units_are_accepted_byte_for_byte(shape: str, token: str) -> None:
    plan, kwargs = _PLANS[shape]
    _accepts(_render_quadlet_from_plan(token, plan, **kwargs), token)


def test_podman_args_allows_only_the_rendered_compat_flags() -> None:
    """#1759: the two flags the renderer emits into PodmanArgs= are accepted,
    in the multi-pair shape a GPU slot actually produces."""
    body = (
        "[Container]\nImage=ghcr.io/hal0ai/x:v1\nContainerName=hal0-slot-chat\n"
        "PodmanArgs=--group-add 44 --group-add 991 "
        "--security-opt seccomp=unconfined --security-opt label=disable\n"
    )
    _accepts(body, "chat")


def test_live_container_provider_render_is_accepted() -> None:
    """End-to-end through the production path: ``ContainerProvider.container_spec``
    → ``_render_quadlet_from_plan``, not a hand-built plan."""
    provider = ContainerProvider()
    profile = MagicMock()
    profile.image = "ghcr.io/hal0ai/hal0-toolbox-rocm:v1"
    profile.flags = ""
    profile.backend = "rocm"
    profile.device_class = "gpu"
    profile.name = "rocm"
    slot_cfg = {
        "name": "chat",
        "port": 8095,
        "profile": "rocm",
        "model": {"default": "m", "context_size": 131072},
    }
    model_info = {"path": "/mnt/ai-models/model.gguf", "_model_key": "m"}
    with (
        patch("hal0.providers.container._resolve_profile", return_value=profile),
        patch(
            "hal0.providers.container.resolve_gpu_device_paths",
            return_value=["/dev/kfd", "/dev/dri/renderD128"],
        ),
        patch("hal0.providers.container.resolve_gpu_group_ids", return_value=["44", "991"]),
    ):
        text = provider._render_quadlet_text(slot_cfg, model_info)
    _accepts(text, "chat")


# ── the escalation payloads ────────────────────────────────────────────────

_HEAD = "[Container]\nImage=ghcr.io/hal0ai/x:v1\nContainerName=hal0-slot-chat\n"

ESCALATIONS = {
    # The #1740 primitive itself: a host-side [Service] the quadlet generator
    # copies through verbatim.
    "execstartpre-smuggling": f"{_HEAD}\n[Service]\nExecStartPre=/bin/sh -c 'id>/root/pwned'\n",
    "execstart-override": f"{_HEAD}\n[Service]\nExecStart=/bin/sh -c 'id>/root/pwned'\n",
    "execstartpost": f"{_HEAD}\n[Service]\nRestart=always\nExecStartPost=/bin/sh -c ':'\n",
    "execstop": f"{_HEAD}\n[Service]\nExecStop=/bin/sh -c ':'\n",
    "execreload": f"{_HEAD}\n[Service]\nExecReload=/bin/sh -c ':'\n",
    "user-root": f"{_HEAD}\n[Service]\nRestart=always\nUser=root\n",
    "group-root": f"{_HEAD}\n[Service]\nRestart=always\nGroup=root\n",
    "second-service-section": (
        f"{_HEAD}\n[Service]\nRestart=always\n\n[Service]\nExecStartPre=/bin/sh\n"
    ),
    "second-container-section": f"{_HEAD}\n[Container]\nImage=evil\n",
    "service-before-container": f"[Service]\nExecStart=/bin/sh\n\n{_HEAD}",
    "unit-after-container": f"{_HEAD}\n[Unit]\nDescription=x\n",
    "unknown-section": f"{_HEAD}\n[Timer]\nOnCalendar=*-*-* *:*:00\n",
    "unit-onfailure": f"[Unit]\nOnFailure=evil.service\n\n{_HEAD}",
    "unit-condition": f"[Unit]\nConditionPathExists=/tmp/x\n\n{_HEAD}",
    "install-other-target": f"{_HEAD}\n[Install]\nWantedBy=multi-user.target\n",
    "install-also": f"{_HEAD}\n[Install]\nAlso=evil.service\n",
    "line-continuation": f"{_HEAD}\n[Service]\nRestart=always \\\nExecStartPre=/bin/sh\n",
    "crlf-body": f"{_HEAD}\n[Service]\r\nExecStartPre=/bin/sh\n",
    "cr-inside-directive": f"{_HEAD}\n[Service]\nRestart=always\rUser=root\n",
    "tab-indented-directive": f"{_HEAD}\n[Service]\n\tUser=root\n",
    "leading-whitespace-directive": f"{_HEAD}\n[Service]\n  User=root\n",
    "spaced-key": f"{_HEAD}\n[Service]\nUser = root\n",
    "semicolon-comment-then-directive": f"{_HEAD}\n[Service]\n; note\nUser=root\n",
    "directive-before-section": "Image=ghcr.io/hal0ai/x:v1\n[Container]\nImage=x\n",
    "no-section": "ExecStartPre=/bin/sh\n",
    "empty-body": "",
    "section-only": "[Container]\n",
    "no-container-section": "[Unit]\nStartLimitBurst=5\n",
    # [Container] value pinning.
    "container-name-not-a-slot": "[Container]\nImage=x\nContainerName=../../root\n",
    "volume-traversal": f"{_HEAD}Volume=/var/lib/hal0/../../etc:/etc:rw\n",
    "device-traversal": f"{_HEAD}AddDevice=/dev/../root\n",
    "publishport-not-numeric": f"{_HEAD}PublishPort=127.0.0.1:$(id):80\n",
    "logdriver-other": f"{_HEAD}LogDriver=journald\n",
    "environment-bad-name": f"{_HEAD}Environment=9BAD=1\n",
    "health-retries-not-numeric": f"{_HEAD}HealthRetries=$(id)\n",
    "health-interval-not-a-duration": f"{_HEAD}HealthInterval=30 seconds\n",
    "unknown-container-key": f"{_HEAD}Rootfs=/\n",
    "notify-key": f"{_HEAD}Notify=true\n",
    # #1759: PodmanArgs= lands host-side in `podman run` argv — a persistent
    # flag pointing at an attacker binary is a direct root exec, no container.
    "podmanargs-runtime": f"{_HEAD}PodmanArgs=--runtime /tmp/evil.sh\n",
    "podmanargs-hooks-dir": f"{_HEAD}PodmanArgs=--hooks-dir /tmp/hooks\n",
    "podmanargs-privileged": f"{_HEAD}PodmanArgs=--privileged\n",
    "podmanargs-bind-root": f"{_HEAD}PodmanArgs=--volume /:/host\n",
    "podmanargs-group-add-nonnumeric": f"{_HEAD}PodmanArgs=--group-add root\n",
    "podmanargs-security-opt-then-runtime": (
        f"{_HEAD}PodmanArgs=--security-opt label=disable --runtime /tmp/evil.sh\n"
    ),
    "podmanargs-group-add-missing-value": f"{_HEAD}PodmanArgs=--group-add\n",
    # [Service] value pinning.
    "service-standardoutput-file": f"{_HEAD}\n[Service]\nStandardOutput=file:/root/.ssh/authorized_keys\n",
    "service-syslogidentifier-free": f"{_HEAD}\n[Service]\nSyslogIdentifier=../evil\n",
    "service-restart-free": f"{_HEAD}\n[Service]\nRestart=/bin/sh\n",
    "service-workingdirectory": f"{_HEAD}\n[Service]\nWorkingDirectory=/root\n",
    "service-environmentfile": f"{_HEAD}\n[Service]\nEnvironmentFile=/tmp/evil.env\n",
    "service-permissionsstartonly": f"{_HEAD}\n[Service]\nPermissionsStartOnly=true\n",
}


@pytest.mark.parametrize("name", sorted(ESCALATIONS))
def test_rejects_escalation_payloads(name: str) -> None:
    _rejects(ESCALATIONS[name])


# ── the id is validated on the same (root) side ────────────────────────────


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../etc/systemd/system/evil",
        "..",
        "chat/../evil",
        "chat.service",
        "chat evil",
        "chat;id",
        "a" * 65,
        "",
    ],
)
def test_rejects_traversal_and_malformed_slot_ids(bad_id: str) -> None:
    proc = _check(f"{_HEAD}", bad_id)
    assert proc.returncode == 64, f"accepted id {bad_id!r}"
    assert "slot id" in proc.stderr


# ── bounds + inert content ─────────────────────────────────────────────────


def test_absurdly_long_line_is_rejected() -> None:
    _rejects(f"{_HEAD}Environment=X=" + "a" * 8192 + "\n")


def test_absurdly_many_lines_are_rejected() -> None:
    _rejects("# pad\n" * 5000 + _HEAD)


def test_comments_and_blank_lines_are_allowed() -> None:
    _accepts(f"# hal0 container slot — generated by ContainerProvider.\n#\n\n{_HEAD}")
