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

import grp
import os
import pwd
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

# The account the wrapper runs containers as. Under test it is the current
# (non-root) user, through the HAL0_FLMCTL_USER override that root ignores.
ME = pwd.getpwuid(os.getuid()).pw_name
MY_GROUPS = os.getgrouplist(ME, os.getgid())
ONESHOT_HOME = "/flm-home"
ONESHOT_STORE = f"{ONESHOT_HOME}/.config/flm/models"

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
            "HAL0_FLMCTL_USER": ME,
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


def _oneshot(store: Path, *flm_args: str, name: str | None = None) -> str:
    env = flm_mod._CONTAINER_FLM_ENV
    named = [f"--name={name}", "--replace"] if name else []
    groups = [f"--group-add={g}" for g in _id_groups() if g != os.getgid()]
    return " ".join(
        [
            "run",
            "--rm",
            "--pull=never",
            *named,
            f"--entrypoint={ENTRYPOINT}",
            f"--user={os.getuid()}:{os.getgid()}",
            *groups,
            "--cap-drop=all",
            "--security-opt=no-new-privileges",
            f"--tmpfs={ONESHOT_HOME}:rw,nosuid,nodev,mode=1777",
            f"--volume={os.path.realpath(store)}:{ONESHOT_STORE}",
            f"--env=FLM_CONFIG_PATH={env['FLM_CONFIG_PATH']}",
            f"--env=LD_LIBRARY_PATH={env['LD_LIBRARY_PATH']}",
            f"--env=HOME={ONESHOT_HOME}",
            "--",
            IMAGE,
            *flm_args,
        ]
    )


def _id_groups() -> list[int]:
    """``id -G`` for the test user, in its order (what the wrapper iterates)."""
    out = subprocess.run(["id", "-G", ME], capture_output=True, text=True, check=True)
    return [int(g) for g in out.stdout.split()]


def test_run_pull_execs_the_one_shot_with_the_slot_mount(podman, store: Path) -> None:
    run, log = podman
    proc = run("run-pull", IMAGE, str(store), "llama3.2:1b")
    assert proc.returncode == 0, proc.stderr
    assert "fake-podman-ran" in proc.stdout
    assert _calls(log) == [
        f"image exists -- {IMAGE}",
        _oneshot(store, "pull", "llama3.2:1b", name="hal0-flm-pull-llama3.2_1b"),
    ]


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
    # The one-shot mounts the store at the same HOME-relative path as the
    # slot; only HOME differs (the image's own HOME is 0700 for uid 1000).
    assert flm_mod._CONTAINER_FLM_MODELS == "/var/lib/hal0/.config/flm/models"
    assert f"CTR_HOME={ONESHOT_HOME}\n" in text
    assert 'CTR_STORE="${CTR_HOME}/.config/flm/models"\n' in text
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
        env={
            "PATH": "/usr/bin:/bin",
            "HAL0_FLMCTL_PODMAN": str(tmp_path / "nope"),
            "HAL0_FLMCTL_USER": ME,
        },
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
    assert verbs == {"exec-list", "run-list", "run-pull", "stop-pull", "check-tag"}


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


def test_stop_pull_removes_the_named_pull_container(podman) -> None:
    run, log = podman
    proc = run("stop-pull", "qwen3:0.6b")
    assert proc.returncode == 0, proc.stderr
    assert _calls(log) == ["rm --force --ignore --time=10 -- hal0-flm-pull-qwen3_0.6b"]


@pytest.mark.parametrize("tag", ["", "qwen3", "-a:b", "a:b;id", "x" * 70 + ":1b"])
def test_stop_pull_rejects_a_bad_tag(podman, tag: str) -> None:
    run, log = podman
    assert run("stop-pull", tag).returncode == 64
    assert _calls(log) == []


def test_pull_container_name_matches_stop_pull(podman, store: Path) -> None:
    run, log = podman
    run("run-pull", IMAGE, str(store), "hy-mt2-flash:1.8b")
    run("stop-pull", "hy-mt2-flash:1.8b")
    calls = _calls(log)
    assert "--name=hal0-flm-pull-hy-mt2-flash_1.8b" in calls[1]
    assert calls[2].endswith("-- hal0-flm-pull-hy-mt2-flash_1.8b")


def test_one_shot_runs_as_the_service_account_not_the_image_user(podman, store: Path) -> None:
    run, log = podman
    assert run("run-list", IMAGE, str(store)).returncode == 0
    call = _calls(log)[-1]
    assert f"--user={os.getuid()}:{os.getgid()}" in call
    assert "--user=1000:1000" not in call or os.getuid() == 1000
    for g in MY_GROUPS:
        if g != os.getgid():
            assert f"--group-add={g}" in call


def test_missing_service_account_is_rejected_before_podman(podman, store: Path) -> None:
    run, log = podman
    proc = run("run-list", IMAGE, str(store), HAL0_FLMCTL_USER="hal0-no-such-user")
    assert proc.returncode == 64
    assert "no 'hal0-no-such-user' account" in proc.stderr
    assert _calls(log) == []


@pytest.mark.parametrize("parent", ["/home", "/var", "/var/lib", "/tmp", "/srv", "/mnt", "/opt"])
def test_broad_parent_directories_are_rejected(podman, parent: str) -> None:
    run, log = podman
    assert run("run-list", IMAGE, parent).returncode == 64
    assert _calls(log) == []


@pytest.mark.skipif(0 in MY_GROUPS, reason="the test user is in group 0")
def test_store_not_handed_to_the_account_is_rejected(podman, tmp_path: Path) -> None:
    """A directory owned by another account and group (here root's /var/tmp)."""
    run, log = podman
    st = os.stat("/var/tmp")
    if st.st_uid == os.getuid() or st.st_gid in MY_GROUPS:
        pytest.skip("/var/tmp is handed to the test user on this host")
    proc = run("run-list", IMAGE, "/var/tmp")
    assert proc.returncode == 64
    assert "is not owned by" in proc.stderr
    assert _calls(log) == []


def test_store_group_owned_by_the_account_is_accepted(podman, store: Path) -> None:
    """The 1000:<hal0 group> 2775 shape: handed over through the group."""
    run, _log = podman
    gid = os.stat(store).st_gid
    assert gid in MY_GROUPS
    assert grp.getgrgid(gid)
    assert run("run-list", IMAGE, str(store)).returncode == 0


def test_wrapper_runs_from_root_directory() -> None:
    """sudo keeps the caller's cwd; the wrapper must not resolve against it (#3007)."""
    lines = WRAPPER.read_text().splitlines()
    assert "cd /" in lines
    assert lines.index("cd /") < next(i for i, ln in enumerate(lines) if ln.startswith("case "))


def test_pull_names_never_collide(podman, store: Path) -> None:
    """`_` is legal on both sides of a tag, so ':' -> '_' alone would collide."""
    run, log = podman
    for tag in ("a_b:c", "a:b_c", "a_:b", "a__b:c"):
        assert run("run-pull", IMAGE, str(store), tag).returncode == 0
        assert run("stop-pull", tag).returncode == 0
    calls = _calls(log)
    names = [c.split("--name=")[1].split()[0] for c in calls if "--name=" in c]
    assert names == [
        "hal0-flm-pull-a__b_c",
        "hal0-flm-pull-a_b__c",
        "hal0-flm-pull-a___b",
        "hal0-flm-pull-a____b_c",
    ]
    assert len(set(names)) == len(names)
    # stop-pull targets exactly the container its own tag's pull created.
    stops = [c.rsplit(" ", 1)[1] for c in calls if c.startswith("rm ")]
    assert stops == names
