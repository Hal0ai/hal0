# hal0 Nightly Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish an automated, signed nightly hal0 build from green `main` each night that beta testers follow with a one-time `hal0 update --channel nightly`.

**Architecture:** A thin scheduler workflow (`nightly.yml`) gates on green `main` + new commits, computes a dated `v<base>-nightly.<YYYYMMDD>` tag, pushes it, and invokes the existing signed-build pipeline (`release.yml`) via `workflow_call` with `channel=nightly`. Pure version/channel logic lives in a unit-tested `hal0.release.channel` module shared by both workflows. No updater/CLI changes — the `nightly` channel is already fully supported client-side.

**Tech Stack:** GitHub Actions (workflow_call reusable workflows), Python 3.11+ (stdlib only for the helper), pytest, cosign keyless OIDC, `gh` CLI.

**Spec:** `docs/superpowers/specs/2026-06-14-nightly-channel-design.md`

**Working branch/worktree:** `feat/nightly-channel` at `/home/halo/dev/hal0-nightly` (based on `origin/main`).

---

## File Structure

- **Create** `src/hal0/release/__init__.py` — new subpackage marker.
- **Create** `src/hal0/release/channel.py` — pure helpers: channel derivation, base-version stripping, nightly version/tag composition, prune selection. No I/O, stdlib only, so both workflows can call it with `PYTHONPATH=src python3 -c …` without an editable install, and it stays unit-testable next to the updater's `_version_tuple`.
- **Create** `tests/release/__init__.py` and `tests/release/test_channel.py` — unit tests for the helper (mirrors `tests/updater/` layout).
- **Modify** `.github/workflows/release.yml` — add `workflow_call`, make channel auto-derive, relax the version gate for nightly, derive cosign identity from `github.workflow_ref`, and make publish flags channel-conditional. The stable path stays behavior-identical.
- **Create** `.github/workflows/nightly.yml` — the scheduler/guard/tagger + the `workflow_call` into `release.yml`.
- **Modify** `docs/operate/updates.mdx` — document the nightly channel for testers.

---

## Task 1: `hal0.release.channel` helper module

**Files:**
- Create: `src/hal0/release/__init__.py`
- Create: `src/hal0/release/channel.py`
- Create: `tests/release/__init__.py`
- Create: `tests/release/test_channel.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/release/__init__.py` as an empty file. Then create `tests/release/test_channel.py`:

```python
"""Unit tests for hal0.release.channel — the version/channel helpers shared
by the release + nightly GitHub Actions workflows."""

from __future__ import annotations

import pytest

from hal0.release.channel import (
    base_matches,
    base_version,
    channel_for_tag,
    nightlies_to_prune,
    nightly_tag,
    nightly_version,
)


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v0.5.0-nightly.20260614", "nightly"),
        ("0.5.0-nightly.20260614", "nightly"),
        ("v0.5.0", "stable"),
        ("v0.5.0-alpha.1", "stable"),
        ("v1.0.0-rc1", "stable"),
        ("", "stable"),
    ],
)
def test_channel_for_tag(tag, expected):
    assert channel_for_tag(tag) == expected


@pytest.mark.parametrize(
    "version,expected",
    [
        ("v0.5.0-nightly.20260614", "0.5.0"),
        ("0.5.0-alpha.1", "0.5.0"),
        ("0.5.0", "0.5.0"),
        ("v1.2.3", "1.2.3"),
    ],
)
def test_base_version(version, expected):
    assert base_version(version) == expected


def test_nightly_version_and_tag():
    assert nightly_version("0.5.0", "20260614") == "0.5.0-nightly.20260614"
    assert nightly_tag("0.5.0", "20260614") == "v0.5.0-nightly.20260614"


def test_base_matches_relaxed_gate():
    # pyproject stays on its dev version; the nightly tag carries the date.
    assert base_matches("0.5.0-alpha.1", "v0.5.0-nightly.20260614") is True
    assert base_matches("0.5.0", "v0.5.0-nightly.20260614") is True
    # base mismatch (someone bumped pyproject to 0.6.0) must fail the gate.
    assert base_matches("0.6.0-alpha.1", "v0.5.0-nightly.20260614") is False


def test_nightlies_to_prune_keeps_newest():
    tags = [
        "v0.5.0-nightly.20260610",
        "v0.5.0-nightly.20260611",
        "v0.5.0-nightly.20260612",
        "v0.5.0-nightly.20260613",
        "v0.5.0",  # stable — never pruned
        "v0.5.0-alpha.1",  # not a nightly — never pruned
    ]
    # keep=2 → the two oldest nightlies are pruned; stable/alpha untouched.
    assert sorted(nightlies_to_prune(tags, keep=2)) == [
        "v0.5.0-nightly.20260610",
        "v0.5.0-nightly.20260611",
    ]


def test_nightlies_to_prune_nothing_when_under_keep():
    tags = ["v0.5.0-nightly.20260613", "v0.5.0-nightly.20260614"]
    assert nightlies_to_prune(tags, keep=7) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/halo/dev/hal0-nightly && PYTHONPATH=src python3 -m pytest tests/release/test_channel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hal0.release'`.

- [ ] **Step 3: Write the module**

Create `src/hal0/release/__init__.py`:

```python
"""Release-pipeline helpers (version/channel logic shared by CI workflows)."""
```

Create `src/hal0/release/channel.py`:

```python
"""Release-channel helpers shared by the release + nightly workflows.

Pure functions — no I/O, stdlib only — so both .github/workflows/release.yml
and .github/workflows/nightly.yml can call them via a bare
``PYTHONPATH=src python3 -c …`` (no editable install needed), and so the
nightly version ordering stays in lock-step with the updater's
``_version_tuple`` (src/hal0/updater/updater.py).
"""

from __future__ import annotations

import re

_NIGHTLY_RE = re.compile(r"-nightly\.(\d+)")


def channel_for_tag(tag: str) -> str:
    """Return the release channel implied by a git tag.

    A version carrying a ``-nightly.<date>`` segment is on the ``nightly``
    channel; everything else (stable, alpha, beta, rc) is ``stable``.
    """
    return "nightly" if _NIGHTLY_RE.search(tag or "") else "stable"


def base_version(version: str) -> str:
    """Strip a leading ``v`` and any pre-release suffix → the base ``X.Y.Z``.

    ``v0.5.0-nightly.20260614`` → ``0.5.0``; ``0.5.0-alpha.1`` → ``0.5.0``.
    """
    v = (version or "").lstrip("v")
    return v.split("-", 1)[0]


def nightly_version(base: str, date: str) -> str:
    """Compose a nightly version from a base ``X.Y.Z`` and a ``YYYYMMDD`` date."""
    return f"{base}-nightly.{date}"


def nightly_tag(base: str, date: str) -> str:
    """Compose the nightly git tag (``v`` + nightly version)."""
    return f"v{nightly_version(base, date)}"


def base_matches(pyproject_version: str, tag: str) -> bool:
    """True when ``tag`` and ``pyproject_version`` share the same base X.Y.Z.

    The relaxed gate for nightly: pyproject stays on its dev version
    (e.g. ``0.5.0-alpha.1``) while the nightly tag is
    ``v0.5.0-nightly.20260614`` — both reduce to base ``0.5.0``.
    """
    return base_version(pyproject_version) == base_version(tag)


def nightlies_to_prune(tags: list[str], keep: int = 7) -> list[str]:
    """Return the nightly tags to delete, keeping the ``keep`` most recent.

    Only ``*-nightly.<date>`` tags are eligible; stable/alpha/rc tags are
    never returned. Ordering is by the numeric date segment, newest first.
    """
    dated: list[tuple[int, str]] = []
    for t in tags:
        m = _NIGHTLY_RE.search(t or "")
        if m:
            dated.append((int(m.group(1)), t))
    dated.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in dated[keep:]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/halo/dev/hal0-nightly && PYTHONPATH=src python3 -m pytest tests/release/test_channel.py -v`
Expected: PASS — all 6 test functions green.

- [ ] **Step 5: Lint**

Run: `cd /home/halo/dev/hal0-nightly && ruff check src/hal0/release/ tests/release/ && ruff format --check src/hal0/release/ tests/release/`
Expected: no errors. (CI runs `ruff format --check` as a fatal step — see memory `feedback_hal0_ci_ruff_format_check`; run `ruff format` first if it complains.)

- [ ] **Step 6: Commit**

```bash
cd /home/halo/dev/hal0-nightly
git add src/hal0/release/ tests/release/
git commit -m "feat(release): hal0.release.channel helpers for nightly version/tag/prune logic

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Make `release.yml` channel-aware + reusable

**Files:**
- Modify: `.github/workflows/release.yml`

No new behavior on the stable (tag-push / dispatch) path — every change is conditional on channel or is an identity-derivation refactor that produces the identical string for the stable case.

- [ ] **Step 1: Add `workflow_call` + make the channel input optional**

Replace the `on:` block (lines 29–42) with:

```yaml
on:
  push:
    tags:
      - "v*"
  workflow_dispatch:
    inputs:
      tag:
        description: "Tag to (re)build a release for, e.g. v0.1.0-rc1"
        required: true
        default: ""
      channel:
        description: "Release channel (blank = derive from tag)"
        required: false
        default: ""
  workflow_call:
    inputs:
      tag:
        description: "Tag to build a release for"
        required: true
        type: string
      channel:
        description: "Release channel (blank = derive from tag)"
        required: false
        type: string
        default: ""
```

- [ ] **Step 2: Auto-derive the channel from the tag**

In the "Resolve tag + version" step, replace this line (was line 79):

```bash
          CHANNEL="${{ inputs.channel || 'stable' }}"
```

with:

```bash
          # Channel: an explicit input wins; otherwise derive from the tag
          # (a `-nightly.<date>` segment ⇒ nightly, else stable).
          CHANNEL="${{ inputs.channel }}"
          if [[ -z "${CHANNEL}" ]]; then
            CHANNEL="$(PYTHONPATH=src python3 -c "from hal0.release.channel import channel_for_tag; print(channel_for_tag('${TAG}'))")"
          fi
```

- [ ] **Step 3: Relax the version gate for nightly**

Replace the comparison block in the "Confirm pyproject.toml version matches the tag" step (was lines 92–96):

```bash
          if [[ "${PYV}" != "${{ steps.ver.outputs.version }}" ]]; then
            echo "::error::pyproject.toml version (${PYV}) ≠ tag (${{ steps.ver.outputs.version }})"
            echo "::error::bump pyproject.toml + retag, or use scripts/release-check.sh --tag ${{ steps.ver.outputs.tag }} first"
            exit 1
          fi
```

with:

```bash
          CHANNEL="${{ steps.ver.outputs.channel }}"
          if [[ "${CHANNEL}" == "nightly" ]]; then
            # Nightly: pyproject stays on its dev version (e.g. 0.5.0-alpha.1)
            # while the tag is v<base>-nightly.<date>; require only the base
            # X.Y.Z to match so we never ship a tarball off the wrong line.
            if ! PYTHONPATH=src python3 -c "import sys; from hal0.release.channel import base_matches; sys.exit(0 if base_matches('${PYV}', '${{ steps.ver.outputs.tag }}') else 1)"; then
              echo "::error::nightly tag base ≠ pyproject base version (${PYV})"
              exit 1
            fi
          elif [[ "${PYV}" != "${{ steps.ver.outputs.version }}" ]]; then
            echo "::error::pyproject.toml version (${PYV}) ≠ tag (${{ steps.ver.outputs.version }})"
            echo "::error::bump pyproject.toml + retag, or use scripts/release-check.sh --tag ${{ steps.ver.outputs.tag }} first"
            exit 1
          fi
```

- [ ] **Step 4: Derive the cosign identity from `github.workflow_ref` (self-verify step)**

In the "Self-verify (cosign verify-blob …)" step, replace the `IDENT` line (was line 209):

```bash
          IDENT="^(?i)https://github\\.com/${{ github.repository }}/\\.github/workflows/release\\.yml@${{ github.ref }}$"
```

with:

```bash
          # The Fulcio cert SAN is the ENTRY-POINT workflow ref: release.yml@<tag>
          # on a direct tag push, or nightly.yml@refs/heads/main when invoked as
          # a reusable workflow. github.workflow_ref gives exactly that, so the
          # verify identity is correct in both modes (and matches the manifest).
          IDENT="^(?i)https://github\\.com/${{ github.workflow_ref }}$"
```

- [ ] **Step 5: Derive the same identity in the manifest step**

In the "Generate release manifest" step, replace the `IDENT` line (was line 231):

```bash
          IDENT="^(?i)https://github\\.com/${{ github.repository }}/\\.github/workflows/release\\.yml@refs/tags/${TAG}$"
```

with:

```bash
          # Must match the Self-verify identity above (and the cert SAN) so the
          # updater's cosign verify-blob succeeds. Derived from the entry-point
          # workflow ref to stay correct under workflow_call (nightly).
          IDENT="^(?i)https://github\\.com/${{ github.workflow_ref }}$"
```

- [ ] **Step 6: Make publish flags channel-conditional**

Replace the `gh release create` block inside the "Publish GitHub Release" step (was lines 296–318):

```bash
          if ! gh release view "${TAG}" >/dev/null 2>&1; then
            # NOTE: every pre-v1.0 release is a SemVer prerelease by tag
            # convention (vX.Y.Z-alpha.N / -beta.N / -rcN), but we
            # publish them with --prerelease=false on purpose: GitHub's
            # /releases/latest endpoint and "Latest" badge filter out
            # prereleases, and the install + update path relies on a
            # working /latest while we're still pre-stable. Flip back
            # to a real prerelease check on the v1.0.0 cut. See the
            # hal0_v0.1.0-alpha-launch + hal0_release_prerelease_flag
            # memories.
            # `--latest` is also explicit. The "automatic based on date
            # and version" default did NOT flip the Latest badge from
            # v0.3.0-alpha.1 → v0.3.1-alpha.1 on the 2026-05-28 cut,
            # so /releases/latest stayed stuck at the prior tag until
            # a manual `gh release edit --latest`. Hard-code it for
            # the same v1.0.0 horizon as --prerelease=false above.
            gh release create "${TAG}" \
              --title "hal0 ${TAG}" \
              --notes "Automated release. See docs/internal/release-manifest.md for the verify path." \
              --draft=false \
              --prerelease=false \
              --latest
          fi
```

with:

```bash
          if ! gh release view "${TAG}" >/dev/null 2>&1; then
            if [[ "${{ steps.ver.outputs.channel }}" == "nightly" ]]; then
              # Nightly: a real prerelease, and deliberately NOT --latest —
              # /releases/latest and the "Latest" badge must keep pointing at
              # the newest STABLE release that install.sh + stable `hal0 update`
              # depend on. The nightly channel is followed via releases.hal0.dev
              # /nightly.json, not /releases/latest.
              gh release create "${TAG}" \
                --title "hal0 ${TAG}" \
                --notes "Automated nightly build from green main. Follow with: hal0 update --channel nightly. See docs/operate/updates.mdx." \
                --draft=false \
                --prerelease=true
            else
              # Stable: see hal0_v0.1.0-alpha-launch + hal0_release_prerelease_flag
              # memories. Pre-v1.0 tags are published --prerelease=false --latest
              # on purpose so /releases/latest works while we're pre-stable.
              gh release create "${TAG}" \
                --title "hal0 ${TAG}" \
                --notes "Automated release. See docs/internal/release-manifest.md for the verify path." \
                --draft=false \
                --prerelease=false \
                --latest
            fi
          fi
```

- [ ] **Step 7: Validate the YAML parses**

Run: `cd /home/halo/dev/hal0-nightly && python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml')); print('release.yml OK')"`
Expected: `release.yml OK`.

- [ ] **Step 8: Sanity-check the helper calls the workflow makes**

Run:
```bash
cd /home/halo/dev/hal0-nightly
PYTHONPATH=src python3 -c "from hal0.release.channel import channel_for_tag, base_matches; print(channel_for_tag('v0.5.0-nightly.20260614'), channel_for_tag('v0.5.0'), base_matches('0.5.0-alpha.1','v0.5.0-nightly.20260614'))"
```
Expected: `nightly stable True`.

- [ ] **Step 9: Commit**

```bash
cd /home/halo/dev/hal0-nightly
git add .github/workflows/release.yml
git commit -m "ci(release): channel-aware + workflow_call reusable; derive cosign identity from workflow_ref

Stable path unchanged. Adds workflow_call so nightly.yml can invoke the signed
build directly (GITHUB_TOKEN can't trigger release.yml via tag push). Nightly
relaxes the version gate to base-match and publishes prerelease without --latest.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `nightly.yml` scheduler workflow

**Files:**
- Create: `.github/workflows/nightly.yml`

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/nightly.yml`:

```yaml
name: Nightly

# Cut a nightly hal0 build from green `main` and publish it on the `nightly`
# release channel. This workflow is only the scheduler/guard/tagger — it does
# NOT build or sign. It computes a dated v<base>-nightly.<YYYYMMDD> tag at
# main HEAD, pushes it, then invokes release.yml (workflow_call) which builds,
# cosign-signs, and publishes nightly.json (channel auto-derived from the tag).
#
# Testers follow it with a one-time `hal0 update --channel nightly`, then plain
# `hal0 update`. See docs/operate/updates.mdx and
# docs/superpowers/specs/2026-06-14-nightly-channel-design.md.

on:
  schedule:
    # 06:00 UTC = 02:00 America/New_York during EDT (drifts to 01:00 EST in
    # winter; GitHub cron is UTC and does not observe DST). The exact minute
    # is not load-bearing for a nightly, and GH delays scheduled runs anyway.
    - cron: "0 6 * * *"
  workflow_dispatch:
    inputs:
      force:
        description: "Tag even if main is unchanged since the last nightly"
        type: boolean
        default: false

permissions:
  contents: write   # push the nightly tag (tag job) + create the release (release job)
  id-token: write   # cosign keyless OIDC in the called release.yml
  packages: read

concurrency:
  group: nightly
  cancel-in-progress: false

jobs:
  tag:
    name: Gate + tag nightly
    runs-on: ubuntu-latest
    timeout-minutes: 10
    outputs:
      skip: ${{ steps.green.outputs.skip == 'true' || steps.change.outputs.skip == 'true' }}
      tag: ${{ steps.ver.outputs.tag }}
    steps:
      - name: Checkout main (full history + tags)
        uses: actions/checkout@v6
        with:
          ref: main
          fetch-depth: 0

      - name: Greenness gate — latest CI run on main must be success
        id: green
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          CONCLUSION="$(gh run list --workflow=ci.yml --branch=main \
            --limit=1 --json conclusion --jq '.[0].conclusion // "none"')"
          echo "latest main CI conclusion: ${CONCLUSION}"
          if [[ "${CONCLUSION}" != "success" ]]; then
            echo "::notice::main CI is '${CONCLUSION}', not success — skipping nightly."
            echo "skip=true" >> "$GITHUB_OUTPUT"
          else
            echo "skip=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Change gate — skip if HEAD already nightly-tagged
        id: change
        if: steps.green.outputs.skip == 'false'
        run: |
          HEAD_SHA="$(git rev-parse HEAD)"
          LAST_TAG="$(git tag --list 'v*-nightly.*' --sort=-creatordate | head -n1)"
          echo "head=${HEAD_SHA}  last-nightly-tag=${LAST_TAG:-<none>}"
          SKIP=false
          if [[ -n "${LAST_TAG}" ]]; then
            LAST_SHA="$(git rev-list -n1 "${LAST_TAG}")"
            if [[ "${HEAD_SHA}" == "${LAST_SHA}" && "${{ inputs.force }}" != "true" ]]; then
              echo "::notice::main HEAD already tagged ${LAST_TAG} — skipping (use force to override)."
              SKIP=true
            fi
          fi
          echo "skip=${SKIP}" >> "$GITHUB_OUTPUT"

      - name: Compute nightly version + tag
        id: ver
        if: steps.green.outputs.skip == 'false' && steps.change.outputs.skip == 'false'
        run: |
          PYV="$(python3 -c '
          import tomllib
          print(tomllib.loads(open("pyproject.toml","rb").read().decode())["project"]["version"])
          ')"
          DATE="$(date -u +%Y%m%d)"
          TAG="$(PYTHONPATH=src python3 -c "from hal0.release.channel import base_version, nightly_tag; print(nightly_tag(base_version('${PYV}'), '${DATE}'))")"
          echo "tag=${TAG}" >> "$GITHUB_OUTPUT"
          echo "::notice::nightly tag ${TAG}"

      - name: Create + push nightly tag
        if: steps.green.outputs.skip == 'false' && steps.change.outputs.skip == 'false'
        env:
          TAG: ${{ steps.ver.outputs.tag }}
        run: |
          git config user.name  "hal0-nightly[bot]"
          git config user.email "hal0-nightly@users.noreply.github.com"
          # Re-runnable within a day (manual force): move the tag if it exists.
          git tag -fa "${TAG}" -m "hal0 nightly ${TAG}"
          git push -f origin "refs/tags/${TAG}"

      - name: Prune old nightly releases + tags (keep last 7)
        if: steps.green.outputs.skip == 'false' && steps.change.outputs.skip == 'false'
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          mapfile -t TAGS < <(git tag --list 'v*-nightly.*')
          TO_PRUNE="$(PYTHONPATH=src python3 -c "
          import sys
          from hal0.release.channel import nightlies_to_prune
          print('\n'.join(nightlies_to_prune(sys.argv[1:], keep=7)))
          " "${TAGS[@]}")"
          if [[ -z "${TO_PRUNE}" ]]; then
            echo "nothing to prune"; exit 0
          fi
          while IFS= read -r t; do
            [[ -z "${t}" ]] && continue
            echo "pruning ${t}"
            gh release delete "${t}" --yes --cleanup-tag || true
          done <<< "${TO_PRUNE}"

  release:
    name: Build + sign + publish (nightly)
    needs: tag
    if: needs.tag.outputs.skip == 'false'
    permissions:
      contents: write
      id-token: write
      packages: read
    uses: ./.github/workflows/release.yml
    with:
      tag: ${{ needs.tag.outputs.tag }}
      channel: nightly
```

- [ ] **Step 2: Validate the YAML parses**

Run: `cd /home/halo/dev/hal0-nightly && python3 -c "import yaml; yaml.safe_load(open('.github/workflows/nightly.yml')); print('nightly.yml OK')"`
Expected: `nightly.yml OK`.

- [ ] **Step 3: Verify the reusable-workflow call target resolves**

Run: `cd /home/halo/dev/hal0-nightly && python3 -c "import yaml; d=yaml.safe_load(open('.github/workflows/nightly.yml')); assert d['jobs']['release']['uses']=='./.github/workflows/release.yml'; print('uses OK')"`
Expected: `uses OK`. (The `./` path means the `release.yml` on the same ref is used — so both files travel together to `main`.)

- [ ] **Step 4: Commit**

```bash
cd /home/halo/dev/hal0-nightly
git add .github/workflows/nightly.yml
git commit -m "ci(nightly): scheduled nightly channel build (gate green main, dated tag, prune, call release.yml)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Document the nightly channel for testers

**Files:**
- Modify: `docs/operate/updates.mdx`

- [ ] **Step 1: Read the current doc to find the channels section**

Run: `cd /home/halo/dev/hal0-nightly && cat docs/operate/updates.mdx`
Identify where channels / `hal0 update` are described (look for an existing "channel" mention or the section listing update commands).

- [ ] **Step 2: Insert the nightly-channel section**

Add the following section after the existing channel/update-command discussion (or near the top of the body if there's no channel section yet). Keep the surrounding `.mdx` heading style consistent with the file:

```mdx
## Nightly channel (beta testers)

The `nightly` channel publishes an automated, cosign-signed build from the
latest green `main`, cut every night (~02:00 US-Eastern). It is the easiest way
for co-developers and beta testers to track hal0 development.

Switch to it once, then update normally:

```bash
hal0 update --channel nightly   # persists the channel, then checks
hal0 update                     # every day after — pulls the newest nightly
```

Nightly builds are GitHub **prereleases** and never become the "Latest"
release, so they don't affect a `stable`-channel install. To go back:

```bash
hal0 update --channel stable
```

Nightlies are versioned `X.Y.Z-nightly.<YYYYMMDD>` and the seven most recent are
retained. If `main` is red or unchanged on a given night, no nightly is cut.
```

(Note: the inner fenced code blocks above use triple backticks — when editing
the `.mdx`, ensure they render as code blocks within the page.)

- [ ] **Step 3: Commit**

```bash
cd /home/halo/dev/hal0-nightly
git add docs/operate/updates.mdx
git commit -m "docs(updates): document the nightly update channel for beta testers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Verify the `releases.hal0.dev` CF middleware serves `nightly.json` (cross-repo)

This touches the **private `hal0-web`** repo — a separate checkout/PR, not the `hal0` worktree. The hal0-side fallback (`HAL0_RELEASES_URL=<asset URL>`) works regardless, so this is a "confirm + fix if needed" task, not a blocker for merging Tasks 1–4.

- [ ] **Step 1: Locate and read the middleware**

```bash
# Find an existing hal0-web checkout, or clone it.
ls /home/halo/dev/hal0-web 2>/dev/null || gh repo clone Hal0ai/hal0-web /home/halo/dev/hal0-web
cd /home/halo/dev/hal0-web && cat functions/_middleware.ts
```

- [ ] **Step 2: Determine whether arbitrary channels resolve**

Inspect how the middleware maps `/<channel>.json` to a GitHub release asset.
- If it resolves the channel generically (e.g. parses `<channel>` from the path and fetches that asset from the appropriate release), **no change needed** — record that and stop.
- If it is hardcoded to `stable` (e.g. only ever fetches `stable.json` or only reads `/releases/latest`), it needs to additionally resolve `nightly.json` from the newest **prerelease** (nightly releases are prereleases, so `/releases/latest` will not surface them).

- [ ] **Step 3: If a fix is needed, implement it in hal0-web and open a PR there**

Make the minimal change so `GET /nightly.json` returns the `nightly.json` asset from the most recent `*-nightly.*` prerelease. Follow hal0-web's existing test/lint conventions. Open a PR in `Hal0ai/hal0-web` and link it back to this plan. (Do not merge hal0-web changes from the hal0 worktree.)

- [ ] **Step 4: Confirm delivery once published**

After a nightly has been published (Task 6) and any hal0-web fix is deployed:
Run: `curl -fsSL https://releases.hal0.dev/nightly.json | python3 -m json.tool | head -20`
Expected: a `hal0.releases.v1` manifest with `"channel": "nightly"` and a `X.Y.Z-nightly.<date>` version.

---

## Task 6: End-to-end smoke (post-merge)

`workflow_dispatch` for a workflow is only invokable once the workflow exists on the **default branch**, and the `schedule` trigger only runs from `main`. So the true end-to-end test happens after Tasks 1–4 are merged. Do not skip this — it's the only place the cosign-under-workflow_call path is exercised for real.

- [ ] **Step 1: Merge the PR for Tasks 1–4 to `main`** (see Finishing, below).

- [ ] **Step 2: Trigger a manual nightly (bypassing the schedule + change gate)**

```bash
gh workflow run nightly.yml -f force=true
sleep 5 && gh run list --workflow=nightly.yml --limit 1
```
Watch it: `gh run watch $(gh run list --workflow=nightly.yml --limit 1 --json databaseId --jq '.[0].databaseId')`
Expected: the `tag` job creates a `v<base>-nightly.<today>` tag, then the `release` job (called release.yml) builds, signs, self-verifies, and publishes.

- [ ] **Step 3: Confirm the GitHub Release shape**

```bash
TAG="$(git ls-remote --tags origin 'v*-nightly.*' | awk -F/ '{print $NF}' | sort | tail -1)"
gh release view "${TAG}" --json isPrerelease,isLatest,assets --jq '{prerelease:.isPrerelease, latest:.isLatest, assets:[.assets[].name]}'
```
Expected: `prerelease: true`, `latest: false`, assets include `hal0-<ver>.tar.gz`, `.sig`, `.crt`, and `nightly.json`.

- [ ] **Step 4: Confirm the stable "Latest" badge is unaffected**

Run: `gh release list --limit 10`
Expected: the `Latest` marker is still on the newest **stable** release, not the nightly.

- [ ] **Step 5: Round-trip the updater against the nightly asset**

```bash
ASSET="$(gh release view "${TAG}" --json assets --jq '.assets[] | select(.name=="nightly.json") | .url')"
HAL0_RELEASES_URL="${ASSET}" hal0 update --check
```
Expected: reports the `X.Y.Z-nightly.<date>` version as available (and the cosign verify path succeeds — this validates the `workflow_ref`-derived signer identity).

- [ ] **Step 6: Confirm retention** (only meaningful once >7 nightlies exist; verify the prune step ran without error in Step 2's logs in the meantime).

---

## Finishing

After Tasks 1–4 pass locally and are committed on `feat/nightly-channel`:

- [ ] Push the branch and open a PR against `main` (`gh pr create`), titled e.g. `ci: nightly channel — scheduled signed build + channel-aware release.yml`. Summarize: new `hal0.release.channel` helper (unit-tested), channel-aware/reusable `release.yml` (stable path unchanged), `nightly.yml` scheduler, tester docs. Call out the hal0-web middleware follow-up (Task 5) and that Task 6 is the post-merge smoke.
- [ ] Use the `superpowers:finishing-a-development-branch` skill to decide merge/PR/cleanup.
- [ ] Run `wip release` for the claimed files when done.
- [ ] After merge, run Task 6 (post-merge smoke) and Task 5 Step 4 (CF delivery confirm).

---

## Self-Review (completed)

- **Spec coverage:** §1 audience path → Task 4 docs + no client change (noted). §2 nightly.yml (cron/greenness/change/version/tag/retention) → Task 3 + helper in Task 1. §3 release.yml channel-aware (derivation/gate/flags) → Task 2. §4 manifest delivery + CF dependency → Task 5. Testing/verification → Task 6. Out-of-scope (branch, toolbox, updater) → respected (no tasks). ✔
- **Placeholder scan:** every code/step block contains real content; no TBD/TODO. Task 4 Step 2 prose is provided verbatim; Task 5 is a verify-then-conditionally-fix in another repo (the only inherently open-ended item, bounded by an explicit decision in Step 2). ✔
- **Type/name consistency:** helper names (`channel_for_tag`, `base_version`, `nightly_version`, `nightly_tag`, `base_matches`, `nightlies_to_prune`) are identical across Task 1 (def), Task 2 (release.yml calls), and Task 3 (nightly.yml calls). ✔
- **Trigger correctness:** workflow_call chosen because GITHUB_TOKEN tag pushes don't trigger `release.yml`; cosign identity unified on `github.workflow_ref` to stay valid under the reusable-workflow SAN. ✔
