# Task 2: Atomic version synchronization and release preflight — Report

## Status: Complete ✅

## Commits

```
4d77304d feat(release): synchronize and preflight official versions
```

## Files Changed

| File | Action |
|---|---|
| `scripts/set-version.py` | **Created** — atomic version synchronizer (stdlib-only) |
| `tests/scripts/test_set_version.py` | **Created** — hermetic TDD tests |
| `scripts/release-check.sh` | **Modified** — added gate 8 (release preflight) |

## Test Results

```
4 passed in 0.20s
```

- `test_set_version_updates_all_public_files` — asserts pyproject.toml, ui/package.json, ui/package-lock.json, manifest.json semver updates + uv.lock PEP 440 + channel derivation
- `test_nightly_is_rejected` — nightly raises ValueError with "nightly" in message
- `test_raises_on_missing_version_field` — corrupt pyproject.toml causes raise without file replacement
- `test_duplicate_version_field_raises` — duplicate JSON keys via `object_pairs_hook` cause raise

## Verification

```
python scripts/set-version.py --check 1.0.0-alpha.0
```
Output:
```
version:      1.0.0-alpha.0
pep440:       1.0.0a0
channel:      preview
current:      1.0.0-alpha.0
result:       VALID (dry-run, no files modified)
```

```
bash scripts/release-check.sh --tag v1.0.0-alpha.0 --dry-run
```
Gate 8 preflight output:
```
── 8. Release preflight
✔  policy: 1.0.0-alpha.0 (preview)
✔  policy version agrees with tag
✔  DRY-RUN: policy printed above; no tag or release mutation performed
```

## Implementation Details

### `scripts/set-version.py`
- **stdlib-only**: `json`, `tomllib`, `tempfile`, `os.replace`, `re`, `subprocess`, `shutil`
- Parses version through `ReleasePolicy.from_tag(f"v{version}")` via `sys.path` insert to `src/`
- Rejects nightly with `ValueError("nightly version ... not eligible for source-version rewriting")`
- Updates exact top-level JSON version fields in: pyproject.toml, ui/package.json, ui/package-lock.json, manifest.json, and the hal0ai editable package in uv.lock
- Atomic write: all candidates written to tmpdir tempfiles, validated, then `os.replace` originals
- Re-runs `uv lock` and re-validates the lock version
- Duplicate JSON key detection via `json.loads(object_pairs_hook=...)`

### `scripts/release-check.sh` (gate 8)
- Invokes `PYTHONPATH=src python3 -m hal0.release.policy "${TAG}" --format json`
- Extracts `POLICY_VERSION` and `POLICY_KIND` from JSON output
- Compares normalized source versions (strips `v` prefix)
- For non-nightly: requires tag target = `origin/main`, queries GitHub checks, rejects existing local/remote tags, GitHub Releases, and PyPI versions
- Nightly keeps existing base-match and collision behavior (gate 6)
- `--dry-run` flag prints policy and performs no tag/release mutation

## Residual Risks
- `_resolve_channel()` modifies `sys.path` in a function body (acceptable for CLI usage, not re-entrant)
- GitHub check query uses `:owner/:repo` shorthand which requires `gh` to be authenticated to a fork context; may produce false-negative on CI
- PyPI check via unauthenticated `curl` may be rate-limited in CI environments

## Concerns
- No concerns