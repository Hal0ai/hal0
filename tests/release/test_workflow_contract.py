"""Static contracts for immutable release publication.

These tests inspect every executable YAML scalar without publishing anything.
They cannot establish that GitHub, Sigstore, or PyPI behave as expected remotely;
the first real release run remains required before relying on authorization.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(".github/workflows/release.yml")


def _workflow_text() -> str:
    return WORKFLOW.read_text()


def _yaml(text: str | None = None) -> Any:
    return yaml.safe_load(_workflow_text() if text is None else text)


def _executable_scalars(node: Any) -> Iterator[tuple[str, str]]:
    """Yield every ``run`` and ``uses`` string via recursive YAML traversal."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"run", "uses"} and isinstance(value, str):
                yield key, value
            yield from _executable_scalars(value)
    elif isinstance(node, list):
        for value in node:
            yield from _executable_scalars(value)


def _job(job_id: str) -> dict[str, Any]:
    return _yaml()["jobs"][job_id]


def _step(job_id: str, name: str) -> dict[str, Any]:
    return next(step for step in _job(job_id)["steps"] if step.get("name") == name)


def _shell_function(script: str, name: str) -> str:
    match = re.search(rf"(?ms)^\s*{re.escape(name)}\(\) \{{\n(?P<body>.*?)^\s*\}}$", script)
    assert match is not None, f"missing shell function: {name}"
    return match.group("body")


def test_executable_scalar_traversal_covers_every_yaml_run_form_and_uses() -> None:
    fixture = """
name: traversal-regression
jobs:
  synthetic:
    steps:
      - run: echo plain
      - run: "echo quoted"
      - run: |
          echo literal
      - run: |-
          echo literal-strip
      - run: |+
          echo literal-keep
      - run: >
          echo folded
      - run: >-
          echo folded-strip
      - run: >+
          echo folded-keep
      - uses: owner/action@sha
"""
    surface = list(_executable_scalars(_yaml(fixture)))
    assert len(surface) == 9
    assert {value.strip() for _, value in surface} == {
        "echo plain",
        "echo quoted",
        "echo literal",
        "echo literal-strip",
        "echo literal-keep",
        "echo folded",
        "echo folded-strip",
        "echo folded-keep",
        "owner/action@sha",
    }


def test_workflow_yaml_parses_and_declares_live_run_limit() -> None:
    assert isinstance(_yaml(), dict)
    text = _workflow_text().lower()
    assert "static contract" in text
    assert "first real release run" in text


def test_release_resolves_policy_before_building() -> None:
    text = _workflow_text()
    assert 'PYTHONPATH=src python3 -m hal0.release.policy "${TAG}" --format github' in text
    for output in (
        "kind",
        "prerelease_stage",
        "manifest_targets",
        "github_prerelease",
        "github_latest",
        "publish_pypi",
        "signer_identity",
        "target_sha",
    ):
        assert output in _job("resolve")["outputs"]


def test_resolve_first_rejects_every_noncanonical_repository() -> None:
    first_step = _job("resolve")["steps"][0]
    run = first_step["run"]
    assert '"${GITHUB_REPOSITORY,,}" != "hal0ai/hal0"' in run
    assert "GITHUB_EVENT_NAME" not in run
    assert "refusing noncanonical repository" in run


def test_nightly_gate_pins_entrypoint_and_never_tests_for_workflow_call_event() -> None:
    """The nightly trigger-shape gate must be satisfiable by nightly.yml.

    Under ``workflow_call`` the ``github`` context — and therefore
    ``GITHUB_EVENT_NAME`` — belongs to the caller, so it can never equal
    ``workflow_call``. Comparing against that literal fails closed on every
    nightly (run 30254411300). The entry-point ref is what actually
    distinguishes reusable invocation from a direct tag push.
    """
    run = _step("resolve", "Verify requested tag and export policy")["run"]
    executable = "\n".join(line for line in run.splitlines() if not line.lstrip().startswith("#"))
    assert '"workflow_call"' not in executable
    assert "GITHUB_WORKFLOW_REF" in executable
    assert (
        r"NIGHTLY_ENTRYPOINT='^(Hal0ai|hal0ai)/hal0/\.github/workflows/nightly\.yml"
        r"@refs/heads/main$'"
    ) in executable
    assert '[[ ! "${GITHUB_WORKFLOW_REF}" =~ ${NIGHTLY_ENTRYPOINT} ]]' in executable
    # The surviving event-shape rule must match nightly.yml's declared triggers.
    nightly_on = yaml.safe_load(Path(".github/workflows/nightly.yml").read_text())[True]
    assert set(nightly_on) == {"schedule", "workflow_dispatch"}
    for event in nightly_on:
        assert f'"${{GITHUB_EVENT_NAME}}" != "{event}"' in executable
    # Ref and signer-identity binding are unchanged.
    assert '[[ "${GITHUB_REF}" != "refs/heads/main" ]]' in executable
    assert "signer_identity=${IDENT_PREFIX}refs/heads/main$" in executable


def test_nightly_caller_invokes_release_workflow_from_main() -> None:
    nightly = yaml.safe_load(Path(".github/workflows/nightly.yml").read_text())
    release_job = nightly["jobs"]["release"]
    assert release_job["uses"] == "./.github/workflows/release.yml"
    assert release_job["with"]["channel"] == "nightly"
    checkout = nightly["jobs"]["tag"]["steps"][0]
    assert checkout["with"]["ref"] == "main"


def test_resolve_uses_only_tag_namespace_and_exports_target_sha() -> None:
    text = _workflow_text()
    assert "ref: refs/tags/${{ steps.requested.outputs.tag }}" in text
    assert 'git show-ref --verify --quiet "refs/tags/${TAG}"' in text
    assert 'git rev-parse "refs/tags/${TAG}^{commit}"' in text
    assert 'echo "target_sha=${TARGET_SHA}" >> "$GITHUB_OUTPUT"' in text


def test_release_checkouts_pin_canonical_repository_tag_and_sha() -> None:
    checkouts = [
        step
        for job in _yaml()["jobs"].values()
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert len(checkouts) == 4
    assert all(step["with"]["repository"] == "Hal0ai/hal0" for step in checkouts)
    assert checkouts[0]["with"]["ref"] == "refs/tags/${{ steps.requested.outputs.tag }}"
    assert all(
        step["with"]["ref"] == "refs/tags/${{ needs.resolve.outputs.tag }}"
        for step in checkouts[1:]
    )
    text = _workflow_text()
    assert text.count("EXPECTED_SHA: ${{ needs.resolve.outputs.target_sha }}") == 3
    assert text.count('if [[ "${HEAD_SHA}" != "${EXPECTED_SHA}" ]]') == 3


def test_release_publication_remains_immutable_and_intended_publishers_allowed() -> None:
    text = _workflow_text()
    publish = _step("release", "Publish GitHub Release")["run"]
    normalized = " ".join(publish.replace("\\\n", " ").split())
    assert (
        'gh release create "${TAG}" --verify-tag --title "hal0 ${TAG}" '
        '--notes-file "${NOTES_FILE}" --draft=false '
        '--prerelease="${GITHUB_PRERELEASE}" --latest="${GITHUB_LATEST}"'
    ) in normalized
    assert 'gh release upload "${TAG}" "${ASSETS[@]}"' in publish
    assert "--clobber" not in text
    assert _step("pypi-publish", "Publish to PyPI")["uses"] == (
        "pypa/gh-action-pypi-publish@release/v1"
    )
    assert "Record separately verified channel pointer gate" not in text


def test_global_executable_surface_has_no_external_pointer_or_mutation_markers() -> None:
    # YAML parsing discards comments, so documentation may name forbidden systems.
    surface = "\n".join(value for _, value in _executable_scalars(_yaml())).lower()
    markers = (
        r"releases\.hal0\.dev",
        r"\bhal0-web\b",
        r"\bcloudflare\b",
        r"\bwrangler\b",
        r"\bapi\.cloudflare\.com\b",
        r"\b(?:aws\s+s3|gsutil|rclone)\b",
        r"\bkv\b",
        r"\br2\b",
        r"\bpointer[-_ ]service\b",
        r"\b(?:advance|update|publish|promote|write|sync|mutate)[-_ ](?:channel[-_ ]?)?pointer\b",
        r"\bchannel[-_ ]pointer[-_ ](?:advance|update|publish|promote|write|sync|mutate)\b",
    )
    for marker in markers:
        assert re.search(marker, surface) is None, marker


def test_authorize_job_truth_table_permissions_and_dependencies() -> None:
    job = _job("authorize-pointer")
    assert job["needs"] == ["resolve", "release", "pypi-publish"]
    condition = job["if"]
    assert "always()" in condition
    assert "needs.resolve.result == 'success'" in condition
    assert "needs.release.result == 'success'" in condition
    assert "needs.resolve.outputs.publish_pypi == 'true'" in condition
    assert "needs.pypi-publish.result == 'success'" in condition
    assert "needs.resolve.outputs.publish_pypi == 'false'" in condition
    assert "needs.pypi-publish.result == 'skipped'" in condition
    assert job["permissions"] == {"contents": "read"}
    serialized = yaml.safe_dump(job).lower()
    for forbidden in (
        "id-token",
        "environment:",
        "secrets.",
        "password",
        "cloudflare_api",
        "wrangler",
        "pointer_token",
    ):
        assert forbidden not in serialized
    assert job["outputs"]["authorized"] == "${{ steps.evidence.outputs.authorized }}"


def test_release_exposes_digest_and_authorizer_redownloads_exact_remote_asset_set() -> None:
    release = _job("release")
    assert release["outputs"]["tarball_digest"] == "${{ steps.tarball.outputs.digest }}"
    run = _step("authorize-pointer", "Download and verify exact remote publication")["run"]
    assert "EXPECTED_DIGEST: ${{ needs.release.outputs.tarball_digest }}" in yaml.safe_dump(
        _step("authorize-pointer", "Download and verify exact remote publication")
    )
    assert "repos/${GITHUB_REPOSITORY}/releases/tags/${TAG}" in run
    assert "repos/${GITHUB_REPOSITORY}/releases/latest" in run
    assert "repos/${GITHUB_REPOSITORY}/releases/assets/${ASSET_ID}" in run
    assert "Accept: application/octet-stream" in run
    assert "mktemp -d" in run
    assert "EXPECTED_ASSETS" in run
    assert "duplicate" in run.lower()
    assert "uploaded" in run
    assert "empty" in run.lower()
    assert "gh release download" not in run
    assert "--pattern" not in run
    assert "--clobber" not in run
    assert run.count("repos/${GITHUB_REPOSITORY}/releases/latest") == 1


def test_authorizer_retries_every_github_api_read_and_fails_closed() -> None:
    run = _step("authorize-pointer", "Download and verify exact remote publication")["run"]
    api_read = _shell_function(run, "github_api_read").replace("\\\n", " ")
    asset_download = _shell_function(run, "download_asset").replace("\\\n", " ")
    run_without_continuations = run.replace("\\\n", " ")

    assert re.search(r"(?m)^GH_API_MAX_ATTEMPTS=5$", run)
    assert re.search(r"(?m)^GH_API_BACKOFF_SECONDS=2$", run)
    retry_loop = r"for \(\(ATTEMPT = 1; ATTEMPT <= GH_API_MAX_ATTEMPTS; ATTEMPT\+\+\)\); do"
    for body in (api_read, asset_download):
        assert re.search(retry_loop, body)
        assert re.search(
            r"if \(\(ATTEMPT < GH_API_MAX_ATTEMPTS\)\); then\s+"
            r'(?:echo "::warning::[^\n]+"\s+)?'
            r'sleep "\$\(\(GH_API_BACKOFF_SECONDS \* ATTEMPT\)\)"',
            body,
        )
        assert re.search(r'done\s+echo "::error::[^\n]+"\s+return 1\s*$', body)

    assert re.search(
        r'if gh api "\$\{ENDPOINT\}" > "\$\{ATTEMPT_PATH\}"; then\s+'
        r'mv -- "\$\{ATTEMPT_PATH\}" "\$\{DESTINATION\}"\s+'
        r"return 0",
        api_read,
    )
    assert 'rm -f -- "${ATTEMPT_PATH}"' in api_read
    assert re.search(
        r"github_api_read\s+"
        r'"repos/\$\{GITHUB_REPOSITORY\}/releases/tags/\$\{TAG\}"\s+'
        r'"\$\{RELEASE_JSON\}"',
        run_without_continuations,
    )
    assert re.search(
        r"github_api_read\s+"
        r'"repos/\$\{GITHUB_REPOSITORY\}/releases/latest"\s+'
        r'"\$\{LATEST_JSON\}"',
        run_without_continuations,
    )
    assert "|| true" not in "\n".join(
        line for line in run.splitlines() if "github_api_read" in line
    )
    # The only gh invocations are inside the two retry functions, so no
    # evidence endpoint or octet-stream download can bypass bounded retries.
    assert run.count("gh api ") == 2


def test_authorizer_retries_size_mismatches_and_atomically_accepts_assets() -> None:
    run = _step("authorize-pointer", "Download and verify exact remote publication")["run"]
    download = _shell_function(run, "download_asset").replace("\\\n", " ")

    assert 'rows.append((name, asset_id, asset["size"]))' in run
    assert 'f"{name}\\t{asset_id}\\t{size}\\n"' in run
    assert "while IFS=$'\\t' read -r ASSET_NAME ASSET_ID DECLARED_SIZE; do" in run
    assert re.search(
        r'download_asset "\$\{ASSET_NAME\}" "\$\{ASSET_ID\}" '
        r'"\$\{DECLARED_SIZE\}" "\$\{DESTINATION\}"',
        run,
    )
    assert re.search(
        r'if gh api -H "Accept: application/octet-stream"\s+'
        r'"repos/\$\{GITHUB_REPOSITORY\}/releases/assets/\$\{ASSET_ID\}"\s+'
        r'> "\$\{ATTEMPT_PATH\}"; then\s+'
        r'OBSERVED_SIZE="\$\(wc -c < "\$\{ATTEMPT_PATH\}"\)"\s+'
        r'if \[\[ "\$\{OBSERVED_SIZE\}" == "\$\{DECLARED_SIZE\}" \]\]; then\s+'
        r'mv -- "\$\{ATTEMPT_PATH\}" "\$\{DESTINATION\}"\s+'
        r"return 0",
        download,
    )
    assert "remote asset size mismatch for ${ASSET_NAME}" in download
    assert 'rm -f -- "${ATTEMPT_PATH}"' in download
    assert 'ATTEMPT_PATH="${ATTEMPT_DIR}/asset-${ASSET_ID}.attempt-${ATTEMPT}"' in download
    assert 'DESTINATION="${DOWNLOAD_DIR}/${ASSET_NAME}"' in run
    assert 'mv -- "${ATTEMPT_PATH}" "${DESTINATION}"' in download
    assert "gh release download" not in run


def test_authorizer_verifies_flags_digests_policy_and_all_signatures() -> None:
    run = _step("authorize-pointer", "Download and verify exact remote publication")["run"]
    assert "ReleaseManifest.model_validate" in run
    assert "ReleasePolicy.from_tag" in run
    assert "github_prerelease" in run
    assert "github_latest" in run
    assert "digest_sha256" in run
    assert "EXPECTED_DIGEST" in run
    assert "signer_identity" in run
    assert "signer_issuer" in run
    assert "stable and preview manifests differ in artifact identity" in run
    assert 'cosign verify-blob --bundle "${TARBALL}.bundle"' in run
    normalized = " ".join(run.replace("\\\n", " ").split())
    assert (
        'cosign verify-blob --signature "${TARBALL}.sig" --certificate "${TARBALL}.crt"'
    ) in normalized
    assert 'cosign verify-blob --bundle "${MANIFEST}.bundle"' in run
    installers = [
        step["uses"]
        for job in ("release", "authorize-pointer")
        for step in _job(job)["steps"]
        if step.get("name") == "Install cosign"
    ]
    assert installers == ["sigstore/cosign-installer@v3"] * 2


def test_authorizer_conditionally_checks_exact_pypi_version_with_bounded_retries() -> None:
    run = _step("authorize-pointer", "Download and verify exact remote publication")["run"]
    assert 'if [[ "${PUBLISH_PYPI}" == "true" ]]' in run
    assert "pypi/hal0ai/${PYPI_VERSION}/json" in run
    assert "--retry 5" in run
    assert "--max-time 20" in run
    assert 'payload["info"]["name"] != "hal0ai"' in run
    assert 'payload["info"]["version"] != expected' in run
    # The skip branch names the actual policy kind — both nightly and preview
    # skip PyPI now, so hardcoding "nightly" would misreport a preview release.
    assert "PyPI publication | not required (${POLICY_KIND} policy)" in run
    assert "POLICY_KIND: ${{ needs.resolve.outputs.kind }}" in _workflow_text()


def test_evidence_is_appended_only_after_all_remote_checks() -> None:
    steps = _job("authorize-pointer")["steps"]
    assert steps[-1]["name"] == "Record non-mutating authorization evidence"
    evidence = steps[-1]["run"]
    assert 'echo "authorized=true" >> "$GITHUB_OUTPUT"' in evidence
    assert "No external channel pointer was mutated" in evidence
    assert "$GITHUB_STEP_SUMMARY" in evidence
    assert "Download and verify exact remote publication" in steps[-2]["name"]


def test_release_tree_pins_installer_and_updater_runtime_inputs() -> None:
    stage = _step("release", "Stage release tree")["run"]
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
    assert "if [[ -s ui/dist/index.html ]]" in stage
    assert 'cp -a ui/dist            "${STAGE}/ui/dist"' in stage
    assert '"${STAGE}/ui/package.json"' in stage
    assert '"${STAGE}/ui/node_modules"' in stage


# ── Delivery gates: the installer mirror and the stable channel pointer ──
#
# Two failure modes stayed invisible for weeks (#2057, #2101 gates 2 and 4):
#
#   1. `mirror-bootstrap` refuses to publish while the stable channel has no
#      sibling Sigstore bundle. That refusal was a `::warning::` on an
#      otherwise-green run, so the workflow list looked healthy while the live
#      one-liner rotted. The gate is correct; only its reporting was not.
#   2. Tagging a GA release does not advance the stable channel pointer, and
#      nothing ever noticed that it had not. `release.yml` deliberately cannot
#      look — its `authorize-pointer` job is read-only, and its executable
#      surface may not name an external pointer system at all (see
#      `test_global_executable_surface_has_no_external_pointer_or_mutation_markers`).
#      A post-tag deadline is also not something a job in the tagging run can
#      observe: it completes in minutes and the deadline is hours out. The
#      watch therefore lives in its own scheduled workflow.

MIRROR = Path(".github/workflows/mirror-bootstrap.yml")
PARITY = Path(".github/workflows/bootstrap-parity.yml")
POINTER = Path(".github/workflows/stable-pointer-watch.yml")

MIRROR_INPUTS = {"force", "gate_channel", "dry_run"}

CANONICAL_LABELS = {
    "bug",
    "needs-triage",
    "needs-info",
    "ready-for-agent",
    "ready-for-human",
    "wontfix",
}


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def _on(path: Path) -> Any:
    # PyYAML resolves a bare `on:` key to the boolean True.
    return _load(path)[True]


def _named_step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("name") == name)


def _alert_script(path: Path, job: str, step: str) -> str:
    return _named_step(_load(path)["jobs"][job], step)["with"]["script"]


def _script_labels(script: str) -> set[str]:
    match = re.search(r"const labels = \[([^\]]*)\]", script)
    assert match is not None, "alert script must declare `const labels = [...]`"
    return {label.strip().strip("'\"") for label in match.group(1).split(",") if label.strip()}


# ── mirror-bootstrap: the skip must be a distinguishable outcome ─────────


def test_mirror_gate_and_publish_are_separate_jobs_so_a_skip_is_visible() -> None:
    """A closed gate must not read as a green publish.

    Splitting the probe from the publish makes the refusal show up as a
    `skipped` job conclusion in the run's job list and in the Actions API,
    instead of a `::warning::` annotation buried inside a successful run.
    """
    jobs = _load(MIRROR)["jobs"]
    assert set(jobs) == {"gate", "mirror"}
    assert jobs["mirror"]["needs"] == ["gate"]
    assert "needs.gate.outputs.publish == 'true'" in jobs["mirror"]["if"]
    for output in ("publish", "outcome", "channel", "status_code"):
        assert output in jobs["gate"]["outputs"], output


def test_mirror_gate_records_every_outcome_in_the_job_summary() -> None:
    gate = _load(MIRROR)["jobs"]["gate"]
    summary = _named_step(gate, "Record the gate outcome")["run"]
    assert "$GITHUB_STEP_SUMMARY" in summary
    assert "## mirror-bootstrap" in summary
    probe = _named_step(gate, "Gate on a signed channel manifest")["run"]
    for outcome in ("outcome=published", "outcome=forced", "outcome=skipped"):
        assert outcome in probe, outcome
    skip_branch = probe.split("outcome=skipped")[1]
    assert "::warning::" not in skip_branch
    assert "::notice::" in skip_branch


def test_mirror_publish_gate_still_refuses_an_unsigned_stable_channel() -> None:
    """The gate itself is correct and has to survive this change."""
    probe = _named_step(_load(MIRROR)["jobs"]["gate"], "Gate on a signed channel manifest")["run"]
    assert "https://releases.hal0.dev/${CHANNEL}.json.bundle" in probe
    assert 'echo "publish=false"' in probe
    assert 'echo "publish=true"' in probe


def test_mirror_force_and_rehearsal_inputs_are_wired_to_both_entrypoints() -> None:
    """GA day must not be the first execution of the publish path.

    `gate_channel` lets an operator point the probe at an already-signed
    channel (`preview`), and `dry_run` runs the whole publish path — checkout,
    sync, `bash -n`, diff — while stopping short of the push.
    """
    triggers = _on(MIRROR)
    for entrypoint in ("workflow_dispatch", "workflow_call"):
        assert set(triggers[entrypoint]["inputs"]) == MIRROR_INPUTS, entrypoint
        assert triggers[entrypoint]["inputs"]["gate_channel"]["default"] == "stable"
        assert triggers[entrypoint]["inputs"]["force"]["default"] is False
        assert triggers[entrypoint]["inputs"]["dry_run"]["default"] is False


def test_mirror_rehearsal_channel_cannot_publish_for_real() -> None:
    """`gate_channel: preview` proves the path works; it must never push.

    bootstrap.sh installs from `stable` by default, so mirroring the canonical
    fail-closed script on the strength of a signed *preview* manifest would
    break the one-line install exactly as publishing against an unsigned
    stable channel would.
    """
    gate = _load(MIRROR)["jobs"]["gate"]
    guard = _named_step(gate, "Reject a rehearsal channel outside a dry run")["run"]
    assert '"${CHANNEL}" != "stable"' in guard
    assert '"${DRY_RUN}" != "true"' in guard
    assert "::error::" in guard
    assert "exit 1" in guard


def test_mirror_dry_run_stops_before_the_push() -> None:
    mirror = _load(MIRROR)["jobs"]["mirror"]
    push = _named_step(mirror, "Commit + push")["run"]
    assert '"${DRY_RUN}" == "true"' in push
    assert "git push" in push
    assert push.index('"${DRY_RUN}" == "true"') < push.index("git push")


# ── bootstrap-parity: N consecutive reds must page someone ──────────────


def test_parity_exports_its_exit_code_for_the_alerting_job() -> None:
    parity = _load(PARITY)["jobs"]["parity"]
    assert parity["outputs"]["rc"] == "${{ steps.parity.outputs.rc }}"
    run = _named_step(parity, "Run bootstrap-parity check")["run"]
    assert 'echo "rc=${rc}" >> "$GITHUB_OUTPUT"' in run


def test_parity_alert_job_files_an_owned_issue_after_n_consecutive_reds() -> None:
    """Watching `mirror-bootstrap` for red never fires — it goes green and
    skips. The daily parity red is the signal that actually tracks the live
    installer rotting, so that is the one that has to grow an owner.
    """
    alert = _load(PARITY)["jobs"]["alert"]
    assert alert["needs"] == ["parity"]
    assert "always()" in alert["if"]
    assert alert["permissions"]["issues"] == "write"
    assert alert["permissions"]["contents"] == "read"
    step = _named_step(alert, "Open, update, or close the parity tracking issue")
    assert step["uses"] == "actions/github-script@v9"
    assert step["env"]["ALERT_STREAK"] == "3"
    script = step["with"]["script"]
    assert "MARKER" in script
    assert "issues.createComment" in script
    assert "issues.create" in script
    assert "state: 'closed'" in script


def test_parity_alert_never_counts_an_operational_error_as_drift() -> None:
    """exit 2 is a fetch failure, not drift (scripts/check-bootstrap-parity.sh
    exit-code contract). Counting it would page the installer team for a
    transient Cloudflare blip."""
    script = _alert_script(PARITY, "alert", "Open, update, or close the parity tracking issue")
    assert "rc === 2" in script
    assert "conclusion === 'failure'" in script


def test_parity_alert_does_not_read_a_missing_exit_code_as_green() -> None:
    """If the parity job dies before running the check, `outputs.rc` is the
    empty string — and `Number('')` is 0, the *success* code. Parsing it
    naively would close a live drift issue on the strength of a bad checkout,
    which is the exact class of silent failure this whole change is about."""
    script = _alert_script(PARITY, "alert", "Open, update, or close the parity tracking issue")
    assert "rawRc === '' ? NaN" in script
    assert "Number.isNaN(rc)" in script
    # ...and the naive parse must not survive anywhere in the script.
    assert "Number(process.env.PARITY_RC)" not in script


def test_parity_alert_uses_only_canonical_labels() -> None:
    """CLAUDE.md: use the canonical labels unmodified rather than inventing new
    ones. The tracking issue is located by a body marker, not a bespoke label."""
    script = _alert_script(PARITY, "alert", "Open, update, or close the parity tracking issue")
    assert _script_labels(script) <= CANONICAL_LABELS


# ── stable-pointer-watch: a GA tag with no pointer must not stay silent ──


def test_pointer_watch_runs_on_a_schedule_and_on_demand() -> None:
    triggers = _on(POINTER)
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"][0]["cron"]


def test_pointer_watch_derives_the_expected_channel_from_release_policy() -> None:
    """`ReleasePolicy.manifest_targets` is the authority on which tags emit
    stable.json. The watch reads it; it does not restate it."""
    resolve = _named_step(_load(POINTER)["jobs"]["watch"], "Resolve the newest GA tag")["run"]
    assert "hal0.release.policy" in resolve
    assert "manifest_targets" in resolve
    assert '"stable" in' in resolve


def test_pointer_watch_probes_both_the_manifest_and_its_sibling_bundle() -> None:
    """`stable.json` alone is not delivery: bootstrap.sh is fail-closed and
    authenticates the manifest against `stable.json.bundle` before parsing it,
    so a manifest without a bundle still breaks every one-line install."""
    probe = _named_step(_load(POINTER)["jobs"]["watch"], "Probe the live stable pointer")["run"]
    assert "https://releases.hal0.dev/stable.json" in probe
    assert "https://releases.hal0.dev/stable.json.bundle" in probe
    assert "EXPECTED_VERSION" in probe
    assert "::error::" in probe


def test_pointer_watch_grace_window_is_explicit_and_bounded() -> None:
    """'Silent and indefinite' is the defect. A named grace window makes the
    wait finite, rather than nobody ever looking."""
    resolve = _named_step(_load(POINTER)["jobs"]["watch"], "Resolve the newest GA tag")
    assert resolve["env"]["GRACE_HOURS"] == "6"
    assert "GRACE_HOURS" in resolve["run"]


def test_pointer_watch_failure_has_an_owner_not_just_a_red_run() -> None:
    alert = _load(POINTER)["jobs"]["alert"]
    assert alert["needs"] == ["watch"]
    assert "always()" in alert["if"]
    assert alert["permissions"]["issues"] == "write"
    step = _named_step(alert, "Open, update, or close the stable-pointer issue")
    assert step["uses"] == "actions/github-script@v9"
    script = step["with"]["script"]
    assert "MARKER" in script
    assert "issues.create" in script
    assert "state: 'closed'" in script
    assert _script_labels(script) <= CANONICAL_LABELS


def test_pointer_watch_observes_the_pointer_and_never_advances_it() -> None:
    """Advancing the stable pointer stays an owned human step in the runbook
    (CONTRIBUTING.md, "Release delivery"). This workflow only observes it."""
    workflow = _load(POINTER)
    assert workflow["permissions"] == {"contents": "read"}
    surface = "\n".join(value for _, value in _executable_scalars(workflow)).lower()
    for marker in (
        r"\bcloudflare\b",
        r"\bwrangler\b",
        r"\b(?:aws\s+s3|gsutil|rclone)\b",
        r"\bgh release (?:create|upload|edit|delete)\b",
        r"\bgit push\b",
        r"\bcurl -x\b",
    ):
        assert re.search(marker, surface) is None, marker


# ── the runbook step itself (#2101 gate 2) ──────────────────────────────


def test_release_delivery_runbook_names_an_owner_and_both_probes() -> None:
    """#2101 gate 2: "Runbook states who performs the pointer advance and how
    it is verified." A runbook that names neither is decoration, so the two
    load-bearing facts are pinned here.

    The runbook lives in CONTRIBUTING.md rather than docs/operate/: everything
    under the five published `docs/` sections is auto-synced to the public
    forum by sync-docs-discourse.yml, and #2116 deliberately moved
    operator-internal material out of the published tree.
    """
    runbook = Path("CONTRIBUTING.md").read_text()
    assert "## Release delivery" in runbook
    section = runbook.split("## Release delivery", 1)[1].split("\n## ", 1)[0]

    # Who.
    assert "Owner: whoever cut the tag" in section
    assert "authorize-pointer" in section and "read-only" in section

    # How it is verified — both halves, because a manifest without its
    # sibling bundle still breaks every fail-closed one-line install.
    assert "https://releases.hal0.dev/stable.json" in section
    assert "https://releases.hal0.dev/stable.json.bundle" in section
    assert "scripts/check-bootstrap-parity.sh" in section

    # The automated backstops point back at the owned step, not away from it.
    assert "stable-pointer-watch.yml" in section
    assert "bootstrap-parity.yml" in section

    # And the pre-GA rehearsal of the publish path (#2101 gate 4).
    assert "gate_channel: preview" in section
    assert "dry_run: true" in section


def test_runbook_does_not_widen_which_tags_emit_a_stable_manifest() -> None:
    """`ReleasePolicy.manifest_targets` stays the one authority. A runbook that
    told an operator to hand-publish `stable.json` for an rc would route around
    it, so the section says the opposite in as many words."""
    from hal0.release.policy import ReleasePolicy

    assert ReleasePolicy.from_tag("v1.0.0").manifest_targets == ("stable", "preview")
    assert ReleasePolicy.from_tag("v1.0.0-rc.1").manifest_targets == ("preview",)

    section = (
        Path("CONTRIBUTING.md").read_text().split("## Release delivery", 1)[1].split("\n## ", 1)[0]
    )
    assert "do not widen it" in section
