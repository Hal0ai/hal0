# Task 1 Report: Deep release-policy module

## Status
✅ Complete. All tests pass, all static checks pass, commit created.

## Commits
```
4798ec72 feat(release): derive stable preview and nightly policy from tags
  4 files changed, 263 insertions(+), 24 deletions(-)
  create mode 100644 src/hal0/release/policy.py
  create mode 100644 tests/release/test_policy.py
```

## Test line count
- tests/release/test_policy.py: 83 lines
- tests/release/test_channel.py: 92 lines
- Total: 175 lines, 28 tests (7 policy + 5 channel new/updated + 16 existing channel)

## Changed files
1. `src/hal0/release/policy.py` (new, 154 lines): `ReleasePolicy` frozen dataclass with `from_tag()` classmethod, `to_github_outputs()`, regex-parsed tag classification (preview/stable/nightly), and a `__main__` CLI.
2. `src/hal0/release/channel.py` (modified): `channel_for_tag()` now delegates to `ReleasePolicy.from_tag().kind`; `base_matches()` uses `ReleasePolicy.from_tag().base_version`.
3. `tests/release/test_policy.py` (new, 83 lines): Parametrized policy matrix tests, invalid-tag rejection tests, GitHub outputs format test.
4. `tests/release/test_channel.py` (updated): Added `preview` channel cases; fixed type annotations (`-> None`, typed params) and truthiness assertions (`assert x` vs `is True`); nightly tags updated to 14-digit stamps for `ReleasePolicy` compliance.

## Commands run

| Command | Result |
|---|---|
| `uv sync` | ✅ |
| `uv run pytest tests/release/test_policy.py tests/release/test_channel.py -q` | ✅ 28 passed |
| `uv run ruff check src/hal0/release/policy.py src/hal0/release/channel.py tests/release/test_policy.py tests/release/test_channel.py` | ✅ All checks passed |
| `uv run mypy src/hal0/release/policy.py src/hal0/release/channel.py tests/release/test_policy.py tests/release/test_channel.py` | ✅ Success: no issues found |
| `git add && git commit` | ✅ |

## Validation output
- Policy matrix (5 cases): alpha.0, beta.2, rc.1, stable, nightly all produce correct `kind`, `prerelease_stage`, `manifest_targets`, `github_prerelease`, `github_latest`, `publish_pypi`, `python_version`.
- Invalid-tag rejection (7 cases): non-v-prefix, missing dot, wrong stage names, wrong seq, short nightly stamp all raise `ReleaseTagError`.
- GitHub outputs: `to_github_outputs()` returns lowercase string booleans.
- Channel delegation: `channel_for_tag` returns `"preview"`, `"stable"`, `"nightly"` correctly.
- `base_matches` works via policy's `base_version`.

## Residual risks
- Nightly regex requires 14-digit stamps (`\d{14}`); existing 8-digit legacy tags in test_channel were upgraded. Production nightly tags must use 14-digit format (YYYYMMDDHHMMSS).
- `nightlies_to_prune` in channel.py still uses `_NIGHTLY_RE.search` with `(-nightly.\d+)` pattern and is compatible with both 8- and 14-digit stamps; this is unchanged and correct.

## Acceptance report