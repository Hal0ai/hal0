"""#2993: ``installer/wrappers/hal0-flmctl`` — FLM list/pull in the runner image.

These run the REAL bash wrapper as the (non-root) test user against a fake
podman (``HAL0_FLMCTL_PODMAN``, honoured only when not root) that records its
argv. They pin three things:

* the exact podman argv each verb execs, and that its container-side literals
  (store mount target, in-image flm path, env) are the ones
  :class:`hal0.providers.flm.FLMProvider` gives the serving slot;
* the exit-code contract the Python side branches on (69 not running, 70 image
  missing, 64 rejected, 65 no podman);
* the root-side validators, and that the Python tag mirror agrees with them.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from hal0.providers import flm as flm_mod
from hal0.providers import flm_cli

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "installer" / "wrappers" / "hal0-flmctl"
SUDOERS = REPO / "packaging" / "sudoers" / "hal0-flmctl"

IMAGE = "ghcr.io/hal0ai/hal0-runner-flm:1.0.10-r0@sha256:" + "a" * 64
# tini -> the in-image flm, the runner image's own ENTRYPOINT with the
# symlink resolved; pinned so a caller-named image cannot pick the program.
ENTRYPOINT = f'["/usr/bin/tini","--","{flm_mod._CONTAINER_FLM_BIN}"]'

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="the podman override is ignored as root")

_FAKE_PODMAN = """#!/bin/bash
printf '%s\\n' "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "image exists") exit "${FAKE_IMAGE_RC:-0}" ;;
  "container inspect") echo "${FAKE_RUNNING:-true}"; exit "${FAKE_INSPECT_RC:-0}" ;;
esac
echo "fake-podman-ran"
exit "${FAKE_EXEC_RC:-0}"
"""


@pytest.fixture
def podman(tmp_path: Path):
    """A recording fake podman; returns ``(run, log_path)``."""
    fake = tmp_path / "podman"
    fake.write_text(_FAKE_PODMAN)
    fake.chmod(0o755)
    log = tmp_path / "podman.log"

    def run(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
        full_env = {
            "PATH": "/usr/bin:/bin",
            "HAL0_FLMCTL_PODMAN": str(fake),
            "FAKE_LOG": str(log),
            **env,
        }
        return subprocess.run(
            [str(WRAPPER), *args], capture_output=True, text=True, env=full_env, timeout=20
        )

    return run, log


@pytest.fixture
def store(tmp_path: Path) -> Path:
    d = tmp_path / "flm" / "models"
    d.mkdir(parents=True)
    return d


def _calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def _oneshot(store: Path, *flm_args: str) -> str:
    env = flm_mod._CONTAINER_FLM_ENV
    return " ".join(
        [
            "run",
            "--rm",
            "--pull=never",
            f"--entrypoint={ENTRYPOINT}",
            "--user=1000:1000",
            "--cap-drop=all",
            "--security-opt=no-new-privileges",
            f"--volume={os.path.realpath(store)}:{flm_mod._CONTAINER_FLM_MODELS}",
            f"--env=FLM_CONFIG_PATH={env['FLM_CONFIG_PATH']}",
            f"--env=LD_LIBRARY_PATH={env['LD_LIBRARY_PATH']}",
            "--",
            IMAGE,
            *flm_args,
        ]
    )


def test_run_pull_execs_the_one_shot_with_the_slot_mount(podman, store: Path) -> None:
    run, log = podman
    proc = run("run-pull", IMAGE, str(store), "llama3.2:1b")
    assert proc.returncode == 0, proc.stderr
    assert "fake-podman-ran" in proc.stdout
    assert _calls(log) == [f"image exists -- {IMAGE}", _oneshot(store, "pull", "llama3.2:1b")]


def test_run_list_execs_the_one_shot(podman, store: Path) -> None:
    run, log = podman
    proc = run("run-list", IMAGE, str(store))
    assert proc.returncode == 0, proc.stderr
    assert _calls(log)[-1] == _oneshot(store, "list", "-j")


def test_one_shot_mount_matches_the_serving_slot(store: Path) -> None:
    """The wrapper's mount target and env are the serving container's."""
    plan = flm_mod.FLMProvider().container_spec({"_paths": {"flm_models": str(store)}}, {})
    assert [(m.source, m.target) for m in plan.mounts] == [
        (str(store), flm_mod._CONTAINER_FLM_MODELS)
    ]
    assert plan.env == flm_mod._CONTAINER_FLM_ENV
    text = WRAPPER.read_text()
    assert f"CTR_STORE={flm_mod._CONTAINER_FLM_MODELS}\n" in text
    assert f"CTR_FLM={flm_mod._CONTAINER_FLM_BIN}\n" in text
    for key, value in flm_mod._CONTAINER_FLM_ENV.items():
        assert f"={key}={value}\n" in text


def test_missing_image_is_rc_70_and_never_runs(podman, store: Path) -> None:
    run, log = podman
    proc = run("run-pull", IMAGE, str(store), "llama3.2:1b", FAKE_IMAGE_RC="1")
    assert proc.returncode == flm_cli.RC_IMAGE_MISSING
    assert _calls(log) == [f"image exists -- {IMAGE}"]


def test_image_probe_failure_is_rc_66(podman, store: Path) -> None:
    run, _log = podman
    proc = run("run-list", IMAGE, str(store), FAKE_IMAGE_RC="125")
    assert proc.returncode == 66


def test_exec_list_runs_in_the_slot_container(podman) -> None:
    run, log = podman
    proc = run("exec-list", "8")
    assert proc.returncode == 0, proc.stderr
    assert _calls(log) == [
        "container inspect --format {{.State.Running}} -- hal0-slot-8",
        f"exec -- hal0-slot-8 {flm_mod._CONTAINER_FLM_BIN} list -j",
    ]


@pytest.mark.parametrize(("running", "inspect_rc"), [("false", "0"), ("", "125")])
def test_exec_list_not_running_is_rc_69(podman, running: str, inspect_rc: str) -> None:
    run, log = podman
    proc = run("exec-list", "flm", FAKE_RUNNING=running, FAKE_INSPECT_RC=inspect_rc)
    assert proc.returncode == flm_cli.RC_NOT_RUNNING
    assert not any(c.startswith("exec ") for c in _calls(log))


def test_flm_exit_code_passes_through(podman, store: Path) -> None:
    run, _log = podman
    assert run("run-pull", IMAGE, str(store), "qwen3:0.6b", FAKE_EXEC_RC="3").returncode == 3


def test_missing_podman_is_rc_65(store: Path, tmp_path: Path) -> None:
    proc = subprocess.run(
        [str(WRAPPER), "run-list", IMAGE, str(store)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HAL0_FLMCTL_PODMAN": str(tmp_path / "nope")},
        timeout=20,
    )
    assert proc.returncode == 65


GOOD_TAGS = ["qwen3:0.6b", "hy-mt2-flash:1.8b", "embed-gemma:300m", "whisper-v3:turbo"]
BAD_TAGS = [
    "",
    "qwen3",
    "-rm:x",
    "qwen3:-x",
    "a:b:c",
    "qwen3:0.6b;id",
    "qwen3 :0.6b",
    "$(id):1b",
    "qwen3:0.6b\nid",
    "../x:1b",
    "qwené:1b",
]


@pytest.mark.parametrize("tag", GOOD_TAGS)
def test_good_tags_pass_both_sides(podman, tag: str) -> None:
    run, _log = podman
    assert run("check-tag", tag).returncode == 0
    assert flm_cli.is_valid_flm_tag(tag)


@pytest.mark.parametrize("tag", BAD_TAGS)
def test_bad_tags_fail_both_sides(podman, store: Path, tag: str) -> None:
    run, log = podman
    assert run("check-tag", tag).returncode == 64
    assert run("run-pull", IMAGE, str(store), tag).returncode == 64
    assert not flm_cli.is_valid_flm_tag(tag)
    assert _calls(log) == []


@pytest.mark.parametrize(
    "bad_store",
    ["relative/dir", "/does/not/exist", "/etc", "/usr/lib", "/", "/proc/self", "/tmp/a:b"],
)
def test_bad_store_is_rejected_before_podman(podman, bad_store: str) -> None:
    run, log = podman
    assert run("run-list", IMAGE, bad_store).returncode == 64
    assert _calls(log) == []


def test_symlinked_store_mounts_the_resolved_directory(podman, store: Path, tmp_path: Path) -> None:
    run, log = podman
    link = tmp_path / "store-link"
    link.symlink_to(store)
    proc = run("run-list", IMAGE, str(link))
    assert proc.returncode == 0, proc.stderr
    assert _calls(log)[-1] == _oneshot(store, "list", "-j")


def test_symlink_to_a_system_directory_is_rejected(podman, tmp_path: Path) -> None:
    run, log = podman
    link = tmp_path / "etc-link"
    link.symlink_to("/etc")
    assert run("run-list", IMAGE, str(link)).returncode == 64
    assert _calls(log) == []


def test_store_traversal_is_rejected(podman, store: Path) -> None:
    run, log = podman
    assert run("run-list", IMAGE, f"{store}/../models").returncode == 64
    assert _calls(log) == []


@pytest.mark.parametrize("image", ["-v/:/host", "alpine;id", "--rm", "", "../x"])
def test_bad_image_is_rejected_before_podman(podman, store: Path, image: str) -> None:
    run, log = podman
    assert run("run-list", image, str(store)).returncode == 64
    assert _calls(log) == []


@pytest.mark.parametrize("token", ["", "a/b", "a b", "hal0-slot-x;id", "x" * 65])
def test_bad_token_is_rejected_before_podman(podman, token: str) -> None:
    run, log = podman
    assert run("exec-list", token).returncode == 64
    assert _calls(log) == []


@pytest.mark.parametrize(
    "argv",
    [
        ["run-list", IMAGE],
        ["run-pull", IMAGE, "/tmp"],
        ["exec-list"],
        ["exec-list", "a", "b"],
        ["rm", "x"],
        ["run", IMAGE],
    ],
)
def test_wrong_arity_or_verb_is_rejected(podman, argv: list[str]) -> None:
    run, log = podman
    assert run(*argv).returncode == 64
    assert _calls(log) == []


def test_help_lists_exactly_the_verbs() -> None:
    proc = subprocess.run(
        [str(WRAPPER), "help"], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}
    )
    assert proc.returncode == 0
    verbs = {
        line.split()[0]
        for line in proc.stdout.splitlines()
        if line.startswith("  ") and not line.strip()[0].isdigit()
    }
    assert verbs == {"exec-list", "run-list", "run-pull", "check-tag"}


def test_sudoers_grant_is_pinned_to_the_wrapper() -> None:
    grants = [
        ln for ln in SUDOERS.read_text().splitlines() if ln.strip() and not ln.startswith("#")
    ]
    assert grants == ["hal0 ALL=(root) NOPASSWD: /usr/lib/hal0/bin/hal0-flmctl"]
    assert flm_cli.SEAM_BIN == "/usr/lib/hal0/bin/hal0-flmctl"


def test_wrapper_is_executable_in_git() -> None:
    proc = subprocess.run(
        ["git", "ls-files", "-s", "installer/wrappers/hal0-flmctl"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.stdout.startswith("100755"), proc.stdout
