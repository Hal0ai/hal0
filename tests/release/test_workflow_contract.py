"""Static contracts for immutable release publication.

These tests deliberately inspect the workflow text: they protect security-critical
GitHub Actions wiring without publishing a release during the test suite.
"""

import re
from pathlib import Path

WORKFLOW = Path(".github/workflows/release.yml")


def _workflow_text() -> str:
    return WORKFLOW.read_text()


def _workflow_command_lines(text: str) -> list[str]:
    """Return action invocations and non-comment shell lines from every run block."""
    command_lines: list[str] = []
    run_indent: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if run_indent is not None:
            if stripped and indent <= run_indent:
                run_indent = None
            elif stripped and not stripped.startswith("#"):
                command_lines.append(stripped)
                continue
            else:
                continue
        if re.match(r"^\s*run:\s*\|\s*$", line):
            run_indent = indent
        elif re.match(r"^\s*uses:\s*", line):
            command_lines.append(stripped)
    return command_lines


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


def test_resolve_first_rejects_every_noncanonical_repository() -> None:
    text = _workflow_text()
    resolve_steps = text.split("  resolve:", 1)[1].split("    steps:\n", 1)[1]
    first_step = resolve_steps.split("\n      - name:", 1)[0]

    assert '"${GITHUB_REPOSITORY,,}" != "hal0ai/hal0"' in first_step
    assert "GITHUB_EVENT_NAME" not in first_step
    assert "workflow_call" not in first_step
    assert "refusing noncanonical repository" in first_step


def test_resolve_uses_only_the_tag_namespace_and_exports_target_sha() -> None:
    text = _workflow_text()
    assert "target_sha: ${{ steps.policy.outputs.target_sha }}" in text
    assert "ref: refs/tags/${{ steps.requested.outputs.tag }}" in text
    assert 'git show-ref --verify --quiet "refs/tags/${TAG}"' in text
    assert 'git rev-parse "refs/tags/${TAG}^{commit}"' in text
    assert 'echo "target_sha=${TARGET_SHA}" >> "$GITHUB_OUTPUT"' in text


def test_release_checkouts_pin_canonical_repository_and_immutable_tag() -> None:
    text = _workflow_text()
    checkout_with_blocks = re.findall(
        r"(?m)^        uses: actions/checkout@[^\n]+\n"
        r"        with:\n((?:          [^\n]+\n)+)",
        text,
    )

    assert text.count("uses: actions/checkout@") == 3
    assert len(checkout_with_blocks) == 3
    assert all(block.count("repository: Hal0ai/hal0") == 1 for block in checkout_with_blocks)
    assert sum(block.count("repository: Hal0ai/hal0") for block in checkout_with_blocks) == 3
    assert text.count("ref: refs/tags/${{ needs.resolve.outputs.tag }}") == 2
    assert text.count("EXPECTED_SHA: ${{ needs.resolve.outputs.target_sha }}") == 2
    assert text.count('if [[ "${HEAD_SHA}" != "${EXPECTED_SHA}" ]]') == 2


def test_preview_release_is_not_latest_and_upload_is_immutable() -> None:
    text = _workflow_text()
    publish = text.split("      - name: Publish GitHub Release", 1)[1].split(
        "      - name: Record separately verified channel pointer gate", 1
    )[0]
    normalized = " ".join(publish.replace("\\\n", " ").split())

    assert "GITHUB_PRERELEASE: ${{ needs.resolve.outputs.github_prerelease }}" in publish
    assert "GITHUB_LATEST: ${{ needs.resolve.outputs.github_latest }}" in publish
    assert (
        'gh release create "${TAG}" --verify-tag --title "hal0 ${TAG}" '
        '--notes-file "${NOTES_FILE}" --draft=false '
        '--prerelease="${GITHUB_PRERELEASE}" --latest="${GITHUB_LATEST}"'
    ) in normalized
    assert 'gh release upload "${TAG}" "${ASSETS[@]}"' in publish
    assert "--clobber" not in text
    assert 'gh release view "${TAG}"' in text
    assert "asset collision:" in text


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
    gate = text.split("      - name: Record separately verified channel pointer gate", 1)[1].split(
        "      - name: Summary", 1
    )[0]

    assert "channel pointer" in text.lower()
    assert "separately verified" in text.lower()
    assert 'echo "Channel pointer advancement remains external' in gate
    for publishing_command in ("curl ", "gh ", "git push", "upload", "release create"):
        assert publishing_command not in gate

    # The external pointer endpoint may be documented, but never executed by
    # this workflow. This scans every job rather than trusting the named gate.
    pointer_endpoint_mentions = [
        line for line in text.splitlines() if "releases.hal0.dev" in line.lower()
    ]
    assert pointer_endpoint_mentions
    assert all(line.lstrip().startswith("#") for line in pointer_endpoint_mentions)

    command_surface = "\n".join(_workflow_command_lines(text)).lower()
    external_pointer_markers = (
        r"releases\.hal0\.dev",
        r"\bhal0-web\b",
        r"\bcloudflare\b",
        r"\bwrangler\b",
        r"\bapi\.cloudflare\.com\b",
        r"\b(?:aws\s+s3|gsutil|rclone)\b",
        r"\br2(?:://|\s+(?:bucket|object|put|copy))\b",
        r"\b(?:advance|update|publish|promote|write|sync)[-_ ](?:channel[-_ ]?)?pointer\b",
        r"\bchannel[-_ ]pointer[-_ ](?:advance|update|publish|promote|write|sync)\b",
    )
    for marker in external_pointer_markers:
        assert re.search(marker, command_surface) is None, marker


def test_release_tree_pins_installer_and_updater_runtime_inputs() -> None:
    text = _workflow_text()
    stage = text.split("      - name: Stage release tree", 1)[1].split(
        "      - name: Stage release notes", 1
    )[0]

    staged_roots = re.findall(r'(?m)^\s*cp -a\s+(\S+)\s+"\$\{STAGE\}/"\s*$', stage)
    assert staged_roots == [
        "src",
        "manifest.json",
        "LICENSE",
        "README.md",
        "pyproject.toml",
        "installer",
        "packaging",
        "docs",
    ]

    for required in (
        "src/hal0/updater/updater.py",
        "manifest.json",
        "pyproject.toml",
        "installer/install.sh",
        "installer/lib/ui.sh",
        "packaging/systemd/hal0-openwebui.service",
        "ui/dist/index.html",
        "VERSION",
    ):
        assert f'"${{STAGE}}/{required}"' in stage


def test_release_tree_is_explicitly_prebuilt_ui_only() -> None:
    text = _workflow_text()
    stage = text.split("      - name: Stage release tree", 1)[1].split(
        "      - name: Stage release notes", 1
    )[0]

    assert "if [[ -s ui/dist/index.html ]]" in stage
    assert 'cp -a ui/dist            "${STAGE}/ui/dist"' in stage
    assert '"${STAGE}/ui/package.json"' in stage
    assert '"${STAGE}/ui/node_modules"' in stage
    assert "ui-dist/" not in stage
    assert "npm fallback" not in stage
