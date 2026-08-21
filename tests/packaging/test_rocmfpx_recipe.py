"""#1970 — the pinned runner image must be reproducible from tracked source.

`ade07ba` was a hand-build with no tracked recipe, so isolating #1888 meant
reconstructing its lineage from scratch. A signed default pin must never be in
that position again, and "there is a recipe" is only true while the recipe
still describes the thing actually pinned — which is what these tests hold.
"""

from __future__ import annotations

import subprocess
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
    assert base.startswith("ghcr.io/") or base.startswith("docker.io/"), base
    # The ROCm version is load-bearing: the runtime layer must match the
    # toolchain the binaries were linked against, or the runner resolves
    # libraries out of two different ROCm trees.
    assert MANIFEST["base"]["rocm_version"] in base


@pytest.mark.parametrize(
    "flag", ["-DGGML_HIP=ON", "-DGGML_VULKAN=ON", "-DCMAKE_HIP_ARCHITECTURES=gfx1151"]
)
def test_the_build_is_combined_hip_plus_vulkan(flag: str) -> None:
    """hal0 resolves BOTH the rocmfpx and vulkanfpx runners to this one tag, so
    dropping either backend silently breaks a lane."""
    assert flag in MANIFEST["build"]["cmake_flags"]
