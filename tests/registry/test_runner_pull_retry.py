"""Retry/backoff discipline for runner-image pulls (installer-forensics brief).

Mirrors installer/lib/pull-retry.sh's bash classifier so a manual
`install.sh` pull and a dashboard-triggered runner-image pull fail for the
same reasons. ``run_runner_pull``'s own default (``max_attempts=1``) keeps
every pre-existing direct caller and test (test_runner_pull.py) on the
original single-attempt behaviour byte-for-byte — these tests exercise the
opt-in multi-attempt path explicitly, with an injected ``sleep_fn`` so
nothing here waits on a real clock.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.registry.runner_image import RunnerImage
from hal0.registry.runner_image_store import RunnerImageStore
from hal0.registry.runner_pull import (
    is_retryable_pull_error,
    make_job,
    pull_backoff_delay,
    run_runner_pull,
)


@pytest.fixture
def store(tmp_path: Path) -> RunnerImageStore:
    s = RunnerImageStore(db_path=tmp_path / "hal0.db")
    s.upsert(
        RunnerImage(
            id="hal0ai/hal0-toolbox-cpu", image="ghcr.io/hal0ai/hal0-toolbox-cpu", tag="latest"
        )
    )
    return s


@pytest.fixture(autouse=True)
def _isolate_var_lib(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0home"))


def _job():
    return make_job("hal0ai/hal0-toolbox-cpu", "ghcr.io/hal0ai/hal0-toolbox-cpu:latest")


async def _no_sleep(_seconds: float) -> None:
    """Injected in place of asyncio.sleep — retry tests never wait on a real clock."""


class TestClassifier:
    @pytest.mark.parametrize(
        "message",
        [
            "unauthorized: authentication required",
            "denied: requested access to the resource is denied",
            "manifest unknown",
            "pull access denied for ghcr.io/x, repository does not exist or may require 'docker login'",
            "Error: creating build container: writing blob: adding layer: ApplyLayer exit status 1: no space left on device",
            "Error: initializing source docker://ghcr.io/x:y: reading manifest y: manifest unknown",
            'Error: authenticating creds for "ghcr.io": 404',
        ],
    )
    def test_non_retryable_signatures_are_rejected(self, message: str) -> None:
        assert is_retryable_pull_error(message) is False

    @pytest.mark.parametrize(
        "message",
        [
            "connection reset by peer",
            "context deadline exceeded",
            "TLS handshake timeout",
            "unexpected EOF",
            "exit code 1",
            "",
            None,
        ],
    )
    def test_transient_or_unclassified_messages_are_retryable(self, message) -> None:
        assert is_retryable_pull_error(message) is True


class TestBackoffTable:
    def test_default_table_first_entries(self) -> None:
        assert pull_backoff_delay(1) == 5
        assert pull_backoff_delay(2) == 15
        assert pull_backoff_delay(3) == 30
        assert pull_backoff_delay(4) == 60

    def test_past_the_table_end_the_last_value_doubles(self) -> None:
        assert pull_backoff_delay(5) == 120
        assert pull_backoff_delay(6) == 240

    def test_custom_table_is_honoured(self) -> None:
        assert pull_backoff_delay(1, delays=(1, 2, 3)) == 1
        assert pull_backoff_delay(3, delays=(1, 2, 3)) == 3
        assert pull_backoff_delay(4, delays=(1, 2, 3)) == 6


class _ScriptedProvider:
    """Yields one scripted event list per call to pull_image_stream — the
    Nth call (1-indexed) gets the Nth entry of ``attempts``."""

    def __init__(self, attempts: list[list[dict]], *, image_present=None) -> None:
        self._attempts = attempts
        self._calls = 0
        if image_present is not None:
            self.image_present = image_present  # only set when a caller wants it exposed

    async def pull_image_stream(self, image: str):
        events = self._attempts[min(self._calls, len(self._attempts) - 1)]
        self._calls += 1
        for event in events:
            yield event

    @property
    def call_count(self) -> int:
        return self._calls


class TestRetryLoop:
    async def test_default_max_attempts_never_retries(self, store: RunnerImageStore) -> None:
        """Byte-for-byte parity with the pre-retry behaviour for every
        caller that doesn't opt in."""
        job = _job()
        provider = _ScriptedProvider(
            [
                [{"state": "failed", "error": "connection reset by peer"}],
                [{"state": "completed", "layer": 1, "total_layers": 1}],
            ]
        )
        await run_runner_pull(job, store=store, provider=provider)
        assert job.state == "failed"
        assert provider.call_count == 1

    async def test_a_retryable_failure_is_retried_and_then_succeeds(
        self, store: RunnerImageStore
    ) -> None:
        job = _job()
        slept: list[float] = []

        async def _spy_sleep(seconds: float) -> None:
            slept.append(seconds)

        provider = _ScriptedProvider(
            [
                [{"state": "failed", "error": "connection reset by peer"}],
                [{"state": "failed", "error": "TLS handshake timeout"}],
                [{"state": "completed", "layer": 1, "total_layers": 1}],
            ]
        )
        await run_runner_pull(
            job, store=store, provider=provider, max_attempts=4, sleep_fn=_spy_sleep
        )
        assert job.state == "completed"
        assert provider.call_count == 3
        assert slept == [5, 15]  # pull_backoff_delay(1), pull_backoff_delay(2)

    async def test_a_non_retryable_failure_ends_the_job_on_the_first_attempt(
        self, store: RunnerImageStore
    ) -> None:
        job = _job()
        provider = _ScriptedProvider(
            [
                [{"state": "failed", "error": "unauthorized: authentication required"}],
                [{"state": "completed", "layer": 1, "total_layers": 1}],
            ]
        )
        await run_runner_pull(
            job, store=store, provider=provider, max_attempts=4, sleep_fn=_no_sleep
        )
        assert job.state == "failed"
        assert job.error == "unauthorized: authentication required"
        assert provider.call_count == 1

    async def test_exhausting_every_attempt_ends_failed_with_the_last_error(
        self, store: RunnerImageStore
    ) -> None:
        job = _job()
        provider = _ScriptedProvider(
            [[{"state": "failed", "error": "connection reset by peer"}]] * 3
        )
        await run_runner_pull(
            job, store=store, provider=provider, max_attempts=3, sleep_fn=_no_sleep
        )
        assert job.state == "failed"
        assert job.error == "connection reset by peer"
        assert provider.call_count == 3

    async def test_post_pull_verification_false_is_retried(self, store: RunnerImageStore) -> None:
        """podman exit 0 but the image never landed — treated as a retryable
        failure, not a false success."""
        job = _job()
        calls: list[str] = []

        def _image_present(image: str) -> bool:
            calls.append(image)
            return len(calls) > 1  # missing on the first check, present on the second

        provider = _ScriptedProvider(
            [
                [{"state": "completed", "layer": 1, "total_layers": 1}],
                [{"state": "completed", "layer": 1, "total_layers": 1}],
            ],
            image_present=_image_present,
        )
        await run_runner_pull(
            job, store=store, provider=provider, max_attempts=3, sleep_fn=_no_sleep
        )
        assert job.state == "completed"
        assert provider.call_count == 2
        assert calls == ["ghcr.io/hal0ai/hal0-toolbox-cpu:latest"] * 2

    async def test_a_provider_without_image_present_skips_verification(
        self, store: RunnerImageStore
    ) -> None:
        """Every existing test fake lacks image_present — retry must not
        require it."""
        job = _job()
        provider = _ScriptedProvider([[{"state": "completed", "layer": 1, "total_layers": 1}]])
        await run_runner_pull(
            job, store=store, provider=provider, max_attempts=3, sleep_fn=_no_sleep
        )
        assert job.state == "completed"
        assert job.local_path is not None
