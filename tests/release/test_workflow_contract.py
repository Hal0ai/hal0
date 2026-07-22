"""Static contracts for immutable release publication.

These tests deliberately inspect the workflow text: they protect security-critical
GitHub Actions wiring without publishing a release during the test suite.
"""

from pathlib import Path


WORKFLOW = Path(".github/workflows/release.yml")


def _workflow_text() -> str:
    return WORKFLOW.read_text()


def test_release_resolves_policy_before_building() -> None:
    text = _workflow_text()
    assert "resolve:" in text
    assert 'PYTHONPATH=src python3 -m hal0.release.policy "${TAG}" --format github' in text
    for output in (
        "kind",
        "prerelease_stage",
        "manifest_targets",
        "github_prerelease",
        "github_latest",
        "publish_pypi",
    ):
        assert f"{output}: ${{{{ steps.policy.outputs.{output} }}}}" in text


def test_release_checks_out_resolved_tag_in_build_and_pypi_jobs() -> None:
    text = _workflow_text()
    assert text.count("ref: ${{ needs.resolve.outputs.tag }}") >= 2
    assert 'git rev-parse HEAD' in text
    assert 'git rev-list -n1 "${TAG}"' in text


def test_preview_release_is_not_latest_and_upload_is_immutable() -> None:
    text = _workflow_text()
    assert "needs.resolve.outputs.github_prerelease" in text
    assert "needs.resolve.outputs.github_latest" in text
    assert "--clobber" not in text
    assert 'gh release view "${TAG}"' in text
    assert 'asset collision:' in text


def test_release_policy_controls_manifests_and_pypi() -> None:
    text = _workflow_text()
    assert "needs.resolve.outputs.manifest_targets" in text
    assert "needs.resolve.outputs.publish_pypi == 'true'" in text
    assert "pypi.org/pypi/hal0ai/${PYPI_VERSION}/json" in text


def test_release_signs_verifies_and_uploads_every_manifest_bundle() -> None:
    text = _workflow_text()
    assert 'cosign sign-blob --yes --bundle "${MANIFEST}.bundle" "${MANIFEST}"' in text
    assert 'cosign verify-blob \\\n              --bundle "${MANIFEST}.bundle"' in text
    assert 'ASSETS+=("${MANIFEST}" "${MANIFEST}.bundle")' in text
    assert "ReleaseManifest.model_validate(payload)" in text
    assert "refs/heads/main" in text
    assert "refs/tags/v" in text
    assert text.count("https://token.actions.githubusercontent.com") >= 3


def test_channel_pointer_advancement_is_a_separate_final_gate() -> None:
    text = _workflow_text()
    assert "channel pointer" in text.lower()
    assert "separately verified" in text.lower()
