"""#1970 — the pinned runner image must be reproducible from tracked source.

`ade07ba` was a hand-build with no tracked recipe, so isolating #1888 meant
reconstructing its lineage from scratch. A signed default pin must never be in
that position again, and "there is a recipe" is only true while the recipe
still describes the thing actually pinned — which is what these tests hold.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

import pytest

from hal0.config.schema import DEFAULT_ROCMFPX_IMAGE

RECIPE = Path(__file__).resolve().parents[2] / "packaging" / "runner" / "rocmfpx"
MANIFEST = tomllib.loads((RECIPE / "manifest.toml").read_text(encoding="utf-8"))


def test_the_manifest_tag_matches_the_shipped_pin() -> None:
    """The whole guarantee: this recipe builds the image hal0 actually runs.

    A manifest describing some other tag is worse than none — it reads as
    provenance while pointing somewhere else.
    """
    assert MANIFEST["image"]["tag"] == DEFAULT_ROCMFPX_IMAGE


def test_the_source_ref_is_pinned_not_a_branch() -> None:
    """A branch tip moves; the image would stop matching this manifest with no
    diff to show for it."""
    ref = MANIFEST["source"]["ref"]
    assert ref not in ("main", "master", "HEAD"), ref
    # FULL sha, not abbreviated: a short sha is ambiguous as the upstream grows,
    # and `git checkout` of an ambiguous prefix fails years later on the exact
    # bisect this recipe exists to make possible.
    assert len(ref) == 40, f"pin the full 40-char sha, got {ref!r}"
    assert all(c in "0123456789abcdef" for c in ref.lower()), ref


def test_every_declared_patch_exists_and_is_non_empty() -> None:
    for entry in MANIFEST["patches"]:
        p = RECIPE / "patches" / entry["file"]
        assert p.is_file(), f"missing patch: {entry['file']}"
        assert p.stat().st_size > 0, f"empty patch: {entry['file']}"


def test_no_untracked_patch_files() -> None:
    """A patch on disk that the manifest does not list would be applied by
    nobody and silently rot — or worse, imply coverage it does not have."""
    declared = {e["file"] for e in MANIFEST["patches"]}
    on_disk = {p.name for p in (RECIPE / "patches").glob("*.patch")}
    assert on_disk == declared


def test_every_patch_explains_itself() -> None:
    """Each patch carries WHY. This series exists because a defect was hard to
    isolate; an unexplained patch re-creates that cost."""
    for entry in MANIFEST["patches"]:
        why = entry.get("why", "").strip()
        assert len(why) > 80, f"{entry['file']} needs a real rationale"


def test_the_patches_are_well_formed_diffs() -> None:
    """Parseable without a checkout: `git apply --check` needs the tree, but a
    malformed diff can be caught here and in CI."""
    for entry in MANIFEST["patches"]:
        body = (RECIPE / "patches" / entry["file"]).read_text(encoding="utf-8")
        assert body.startswith("diff --git "), entry["file"]
        assert "\n@@ " in body or body.count("@@") >= 2, entry["file"]


def test_the_build_script_is_executable_and_valid_shell() -> None:
    script = RECIPE / "build.sh"
    assert script.stat().st_mode & 0o111, "build.sh must be executable"
    proc = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_the_recipe_records_its_base_image() -> None:
    base = MANIFEST["base"]["image"]
    # Exact host match, not a prefix check: startswith("ghcr.io/") admits
    # "ghcr.io.evil.com/x"-shaped hosts and tripped CodeQL's
    # incomplete-url-substring-sanitization (2x high) — and the digest test
    # owns digest-shape now, so this assertion's only job is the registry
    # host.
    assert base.split("/")[0] in {"ghcr.io", "docker.io"}, base
    # The ROCm version is load-bearing: the runtime layer must match the
    # toolchain the binaries were linked against, or the runner resolves
    # libraries out of two different ROCm trees.
    assert MANIFEST["base"]["rocm_version"] in base


def test_the_base_image_is_pinned_by_digest_not_only_by_tag() -> None:
    """A tag is mutable; re-push it and this recipe yields a different image
    from byte-identical tracked inputs, with the same --check output.

    The previous version of this test asserted a registry prefix and a version
    substring, both of which a floating tag satisfies happily. Pinning the
    patch series while floating the base pins the interesting 5% and leaves the
    other 95% — the whole ROCm toolchain — free to move under it.
    """
    digest = MANIFEST["base"].get("digest", "")
    assert digest.startswith("sha256:"), f"base must carry a digest, got {digest!r}"
    hexpart = digest.removeprefix("sha256:")
    assert len(hexpart) == 64, f"sha256 digest is 64 hex chars, got {len(hexpart)}"
    assert all(c in "0123456789abcdef" for c in hexpart.lower()), digest


def test_build_sh_actually_pulls_the_base_by_digest() -> None:
    """Recording the digest in the manifest is worthless if the build still
    resolves the tag. Both stages must consume `${BASE}@${DIGEST}`.
    """
    script = (RECIPE / "build.sh").read_text(encoding="utf-8")
    assert 'BASE_DIGEST="$(read_manifest base.digest)"' in script
    assert 'BASE_REF="${BASE}@${BASE_DIGEST}"' in script

    from_lines = [ln.strip() for ln in script.splitlines() if ln.startswith("FROM ")]
    assert len(from_lines) == 2, f"expected two build stages, found {from_lines}"
    for line in from_lines:
        assert line == "FROM ${BASE_REF}", (
            f"stage pulls a mutable ref: {line!r} — must be FROM ${{BASE_REF}}"
        )


def test_the_image_labels_name_immutable_things() -> None:
    """Labels are how a box answers "where did this come from" without the
    repo. Filenames are mutable in-tree under stable names, so a label naming
    only filenames cannot identify the recipe revision that produced an image.
    """
    script = (RECIPE / "build.sh").read_text(encoding="utf-8")
    for label in (
        'dev.hal0.recipe.revision="${RECIPE_REV}"',
        'dev.hal0.runner.patches.sha256="${PATCH_SERIES_SHA}"',
        'org.opencontainers.image.base.digest="${BASE_DIGEST}"',
    ):
        assert label in script, f"missing provenance label: {label}"


def test_check_mode_does_not_require_a_container_runtime() -> None:
    """`--check` builds nothing, so demanding docker/podman made the one mode
    worth running in CI the one mode CI could not run.

    The failure was worse than a bad error message: under `set -e`,
    ``RUNTIME="${...:-$(command -v docker || command -v podman)}"`` takes the
    substitution's exit status, so on a runtime-less host the script died on
    that line before reaching its own readable error — and before printing
    anything at all.

    That is what this test pins, and it pins it offline: with a PATH that has
    no container runtime, the script must get far enough to print its banner.
    On the old ordering stdout was empty. Whether the subsequent clone reaches
    the network is not this test's business, so a network failure downstream is
    tolerated — but the runtime gate firing is not.
    """
    # A PATH containing only what --check legitimately needs. Shadowing
    # docker/podman is not possible (`command -v` would still find them), so
    # the sandbox is built by inclusion rather than exclusion.
    with tempfile.TemporaryDirectory() as td:
        binroot = Path(td) / "bin"
        binroot.mkdir()
        tools = (
            # `bash` first: subprocess resolves the executable through this
            # PATH too, so the interpreter has to live inside the sandbox.
            "bash",
            "sh",
            "env",
            "git",
            "python3",
            "rm",
            "mkdir",
            "dirname",
            "cat",
            "cut",
            "sha256sum",
        )
        for tool in tools:
            found = shutil.which(tool)
            if found is None:  # pragma: no cover - every supported host has these
                pytest.skip(f"{tool} not on PATH")
            (binroot / tool).symlink_to(found)
        assert shutil.which("docker", path=str(binroot)) is None
        assert shutil.which("podman", path=str(binroot)) is None

        try:
            proc = subprocess.run(
                ["bash", str(RECIPE / "build.sh"), "--check"],
                capture_output=True,
                text=True,
                timeout=600,
                env={
                    "PATH": str(binroot),
                    "HOME": td,
                    "HAL0_RUNNER_BUILD_DIR": str(Path(td) / "work"),
                },
            )
        except subprocess.TimeoutExpired:  # pragma: no cover - slow network
            # The clone is the only slow part and it is not what this test is
            # about. Skipping beats a flaky red on a congested runner.
            pytest.skip("cloning the pinned source exceeded the timeout")

    combined = proc.stdout + proc.stderr
    assert "no docker/podman on PATH" not in combined, combined
    assert proc.returncode != 65, combined
    # The banner is the proof: it sits after the old RUNTIME line, so under the
    # previous ordering none of it was ever reached.
    assert "==> tag" in proc.stdout, f"died before the banner:\n{combined}"
    assert "==> base@  sha256:" in proc.stdout, proc.stdout

    if proc.returncode == 0:
        # Network was available, so the full contract held end to end.
        assert "--check: patch series applies cleanly" in proc.stdout
    else:
        # Offline runner: tolerate only a source-fetch failure, never a
        # regression in the parts this test exists to cover.
        assert (
            "clone" in combined
            or "Could not resolve" in combined
            or ("unable to access" in combined)
        ), f"--check failed for a reason other than network:\n{combined}"


@pytest.mark.parametrize(
    "flag", ["-DGGML_HIP=ON", "-DGGML_VULKAN=ON", "-DCMAKE_HIP_ARCHITECTURES=gfx1151"]
)
def test_the_build_is_combined_hip_plus_vulkan(flag: str) -> None:
    """hal0 resolves BOTH the rocmfpx and vulkanfpx runners to this one tag, so
    dropping either backend silently breaks a lane."""
    assert flag in MANIFEST["build"]["cmake_flags"]
