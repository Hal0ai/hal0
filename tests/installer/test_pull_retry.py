"""installer/lib/pull-retry.sh — backoff table + non-retryable classifier.

Same technique as test_seam_verification.py: source the file and invoke a
function directly, no root/sudo/provisioned box needed. Mirrors
tests/registry/test_runner_pull_retry.py's Python-side classifier so the
bash and Python pull paths fail for the same reasons.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PULL_RETRY = REPO / "installer" / "lib" / "pull-retry.sh"


def _call(func: str, *args: str) -> subprocess.CompletedProcess[str]:
    script = f"""
set -uo pipefail
source "{PULL_RETRY}"
{func} {" ".join(f'"{a}"' for a in args)}
echo "rc=$?"
"""
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
    )


class TestClassifier:
    @pytest.mark.parametrize(
        "message",
        [
            "unauthorized: authentication required",
            "Error: denied: requested access to the resource is denied",
            "manifest unknown",
            "Error: reading manifest latest: manifest unknown",
            "Error: initializing source docker://ghcr.io/x:y: 404 Not Found",
            "write /var/lib/containers/storage/...: no space left on device",
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
        ],
    )
    def test_non_retryable_signatures_return_1(self, message: str) -> None:
        proc = _call("hal0_pull_is_retryable", message)
        assert "rc=1" in proc.stdout, proc.stdout

    @pytest.mark.parametrize(
        "message",
        [
            "connection reset by peer",
            "context deadline exceeded",
            "TLS handshake timeout",
            "unexpected EOF",
            "",
        ],
    )
    def test_transient_or_unclassified_messages_return_0(self, message: str) -> None:
        proc = _call("hal0_pull_is_retryable", message)
        assert "rc=0" in proc.stdout, proc.stdout


class TestBackoffTable:
    def test_default_table_first_entries(self) -> None:
        for attempt, expected in ((1, "5"), (2, "15"), (3, "30"), (4, "60")):
            proc = _call("hal0_pull_backoff_delay", str(attempt))
            assert proc.stdout.splitlines()[0] == expected, proc.stdout

    def test_past_the_table_end_the_last_value_doubles(self) -> None:
        proc = _call("hal0_pull_backoff_delay", "5")
        assert proc.stdout.splitlines()[0] == "120"
        proc = _call("hal0_pull_backoff_delay", "6")
        assert proc.stdout.splitlines()[0] == "240"

    def test_custom_table_is_honoured_via_env(self) -> None:
        script = f"""
set -uo pipefail
source "{PULL_RETRY}"
HAL0_PULL_RETRY_DELAYS="1 2 3"
hal0_pull_backoff_delay 1
hal0_pull_backoff_delay 3
hal0_pull_backoff_delay 4
"""
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
        )
        assert proc.stdout.splitlines() == ["1", "3", "6"], proc.stdout


class TestPullWithRetry:
    def _fake_runtime(self, tmp_path: Path, script_body: str) -> Path:
        """A fake `podman`/`docker`-shaped executable driven by a counter file."""
        runtime = tmp_path / "fake-runtime"
        runtime.write_text(f"#!/usr/bin/env bash\n{script_body}\n")
        runtime.chmod(0o755)
        return runtime

    def test_a_retryable_failure_is_retried_then_succeeds(self, tmp_path: Path) -> None:
        counter = tmp_path / "count"
        counter.write_text("0")
        runtime = self._fake_runtime(
            tmp_path,
            f"""
case "$1" in
    pull)
        n=$(($(cat "{counter}") + 1)); echo "$n" > "{counter}"
        if [[ "$n" -lt 2 ]]; then echo "connection reset by peer" >&2; exit 1; fi
        exit 0
        ;;
    image) [[ "$2" == "exists" ]] && exit 0 ;;
    inspect) exit 0 ;;
esac
""",
        )
        script = f"""
set -uo pipefail
export HAL0_PULL_RETRY_DELAYS="0"
source "{PULL_RETRY}"
hal0_pull_with_retry "{runtime}" myimage:latest 4
echo "rc=$?"
echo "attempts=$(cat "{counter}")"
"""
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
        )
        assert "rc=0" in proc.stdout, proc.stderr
        assert "attempts=2" in proc.stdout

    def test_a_non_retryable_failure_stops_after_the_first_attempt(self, tmp_path: Path) -> None:
        counter = tmp_path / "count"
        counter.write_text("0")
        runtime = self._fake_runtime(
            tmp_path,
            f"""
n=$(($(cat "{counter}") + 1)); echo "$n" > "{counter}"
echo "unauthorized: authentication required" >&2
exit 1
""",
        )
        script = f"""
set -uo pipefail
export HAL0_PULL_RETRY_DELAYS="0"
source "{PULL_RETRY}"
hal0_pull_with_retry "{runtime}" myimage:latest 4
echo "rc=$?"
echo "attempts=$(cat "{counter}")"
"""
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
        )
        assert "rc=1" in proc.stdout, proc.stderr
        assert "attempts=1" in proc.stdout
        assert "non-retryable" in proc.stderr

    def test_a_pull_that_exits_0_without_the_image_present_is_retried(self, tmp_path: Path) -> None:
        counter = tmp_path / "count"
        counter.write_text("0")
        runtime = self._fake_runtime(
            tmp_path,
            f"""
case "$1" in
    pull) exit 0 ;;
    image)
        n=$(($(cat "{counter}") + 1)); echo "$n" > "{counter}"
        [[ "$3" == "myimage:latest" && "$n" -ge 2 ]] && exit 0
        exit 1
        ;;
    inspect) exit 1 ;;
esac
""",
        )
        script = f"""
set -uo pipefail
export HAL0_PULL_RETRY_DELAYS="0"
source "{PULL_RETRY}"
hal0_pull_with_retry "{runtime}" myimage:latest 4
echo "rc=$?"
echo "checks=$(cat "{counter}")"
"""
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
        )
        assert "rc=0" in proc.stdout, proc.stderr
        assert "checks=2" in proc.stdout
