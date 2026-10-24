"""hal0.comfyui.install: the image-model installer on the registry downloader (ADR-0037)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from collections import namedtuple
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.comfyui import install, paths
from hal0.comfyui.catalog import (
    CATALOG,
    Component,
    ComponentKind,
    ImageModel,
    component_path,
    get_model,
    install_state,
    render_template,
)
from hal0.registry.pull import (
    _CHUNK_BYTES,
    PullJob,
    _sanitise_id,
    _staging_paths,
    _tmp_dir,
    _write_resume_sidecar,
    hf_download_url,
    make_job,
)

_REPO = "acme/fake-image"
_CKPT = Component(
    ComponentKind.checkpoint,
    "fake-a.safetensors",
    0.001,
    hf_repo=_REPO,
    hf_path="weights/fake-a.safetensors",
)
_VAE = Component(
    ComponentKind.vae,
    "fake-b.safetensors",
    0.001,
    hf_repo=_REPO,
    hf_path="vae/fake-b.safetensors",
)
_FILES = {
    "weights/fake-a.safetensors": b"alpha" * 1000,
    "vae/fake-b.safetensors": b"beta" * 700,
}


def _fake_model(**overrides: Any) -> ImageModel:
    """A two-component model on a real template, so rendering and provisioning run for real."""
    fields: dict[str, Any] = {
        "id": "fake-model",
        "label": "Fake model",
        "task": "txt2img",
        "components": (_CKPT, _VAE),
        "template": "sd15_simple",
        "api_renderable": False,
        "sizes": (),
        "est_seconds": 1,
        "license": "MIT",
        "file_substitutions": {"v1-5-pruned-emaonly.safetensors": "fake-a.safetensors"},
    }
    return ImageModel(**{**fields, **overrides})


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Model store, data root and registry staging dir all under ``tmp_path``."""
    root = tmp_path / "store"
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HAL0_MODEL_STORE", str(root))
    monkeypatch.delenv("HAL0_COMFYUI_DATA_ROOT", raising=False)
    # Guard: a fixture slip must never let a test write a real path.
    assert paths.models_root().is_relative_to(tmp_path)
    assert paths.workflows_dir().is_relative_to(tmp_path)
    assert _tmp_dir().is_relative_to(tmp_path)
    return root


def _serve(request: httpx.Request, body: bytes) -> httpx.Response:
    """What Hugging Face sends for ``body``: the LFS sha256 in ``X-Linked-ETag``, ranges honoured."""
    sha = hashlib.sha256(body).hexdigest()
    headers = {"X-Linked-ETag": f'"{sha}"', "ETag": f'"{sha}"'}
    spec = request.headers.get("range")
    if spec:
        start = int(spec.removeprefix("bytes=").rstrip("-"))
        tail = body[start:]
        headers["Content-Length"] = str(len(tail))
        headers["Content-Range"] = f"bytes {start}-{len(body) - 1}/{len(body)}"
        return httpx.Response(206, content=tail, headers=headers)
    headers["Content-Length"] = str(len(body))
    return httpx.Response(200, content=body, headers=headers)


class _Hub:
    """A ``MockTransport`` that serves fixed bytes per repo file and records every request."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = _FILES if files is None else files
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        prefix = f"/{_REPO}/resolve/main/"
        path = request.url.path
        if not path.startswith(prefix) or path[len(prefix) :] not in self.files:
            return httpx.Response(404, content=b"")
        return _serve(request, self.files[path[len(prefix) :]])

    @property
    def paths(self) -> list[str]:
        return [r.url.path.removeprefix(f"/{_REPO}/resolve/main/") for r in self.requests]


async def _install(
    model: ImageModel,
    transport: httpx.MockTransport,
    *,
    job: PullJob | None = None,
    hf_token: str | None = None,
    on_complete: Callable[[ImageModel], Any] | None = None,
) -> PullJob:
    job = job or make_job(model.id)
    async with httpx.AsyncClient(transport=transport) as client:
        await install.run_install(
            job, model, hf_token=hf_token, client=client, on_complete=on_complete
        )
    return job


def _touch(component: Component) -> None:
    component.dest.parent.mkdir(parents=True, exist_ok=True)
    component.dest.write_bytes(b"x")


# ── run_install ─────────────────────────────────────────────────────────────


async def test_install_writes_under_models_root() -> None:
    model = _fake_model()
    job = await _install(model, _Hub().transport)

    assert job.state == "completed", job.error
    assert job.error is None
    assert job.finished_at is not None
    assert _CKPT.dest == paths.models_root() / "checkpoints" / "fake-a.safetensors"
    assert _CKPT.dest.read_bytes() == _FILES["weights/fake-a.safetensors"]
    assert _VAE.dest.read_bytes() == _FILES["vae/fake-b.safetensors"]
    assert paths.is_under_models_root(_CKPT.dest)
    assert job.bytes_downloaded == job.bytes_total == sum(len(b) for b in _FILES.values())
    assert [f.hf_filename for f in job.files] == [_CKPT.hf_path, _VAE.hf_path]
    assert [f.kind for f in job.files] == ["checkpoint", "vae"]
    assert [f.dest for f in job.files] == [str(_CKPT.dest), str(_VAE.dest)]
    assert install_state(model).state == "complete"


async def test_install_verifies_sha256_against_the_advertised_hash() -> None:
    wrong = "0" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        body = _FILES["weights/fake-a.safetensors"]
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Length": str(len(body)), "X-Linked-ETag": f'"{wrong}"'},
        )

    job = await _install(_fake_model(), httpx.MockTransport(handler))

    assert job.state == "failed"
    assert job.error_code == "pull.checksum_mismatch"
    assert not _CKPT.dest.exists()


async def test_install_sends_the_token_as_a_bearer_header() -> None:
    hub = _Hub()
    await _install(_fake_model(), hub.transport, hf_token="hf_abc")
    assert {r.headers["authorization"] for r in hub.requests} == {"Bearer hf_abc"}

    anonymous = _Hub()
    await _install(_fake_model(id="fake-anon"), anonymous.transport)
    assert all("authorization" not in r.headers for r in anonymous.requests)


async def test_install_ignores_the_endpoint_and_cache_overrides_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Downloads go to huggingface.co however the API process environment is set."""
    monkeypatch.setenv("HF_ENDPOINT", "https://mirror.invalid")
    monkeypatch.setenv("HF_HOME", "/nonexistent/hf-home")
    monkeypatch.setenv("HF_HUB_CACHE", "/nonexistent/hf-cache")
    hub = _Hub()

    job = await _install(_fake_model(), hub.transport)

    assert job.state == "completed", job.error
    assert {r.url.host for r in hub.requests} == {"huggingface.co"}
    assert _CKPT.dest.is_relative_to(paths.models_root())


async def test_install_skips_present_components() -> None:
    model = _fake_model()
    _touch(_CKPT)
    hub = _Hub()

    job = await _install(model, hub.transport)

    assert job.state == "completed", job.error
    assert hub.paths == [_VAE.hf_path]
    assert [f.hf_filename for f in job.files] == [_VAE.hf_path]
    assert _CKPT.dest.read_bytes() == b"x"
    assert _VAE.dest.read_bytes() == _FILES["vae/fake-b.safetensors"]


async def test_install_skips_a_component_that_landed_while_it_ran() -> None:
    """Two installs sharing a component: the later one must not fetch what the first just wrote."""
    model = _fake_model()
    hub = _Hub()
    first = hub._handle

    def handler(request: httpx.Request) -> httpx.Response:
        response = first(request)
        if request.url.path.endswith(_CKPT.hf_path):
            # Another install lands the second component while this one streams the first.
            _touch(_VAE)
        return response

    job = await _install(model, httpx.MockTransport(handler))

    assert job.state == "completed", job.error
    assert hub.paths == [_CKPT.hf_path]
    assert _VAE.dest.read_bytes() == b"x"
    assert job.bytes_downloaded == job.bytes_total


async def test_install_of_a_complete_model_only_provisions_the_workflow() -> None:
    model = _fake_model()
    _touch(_CKPT)
    _touch(_VAE)
    hub = _Hub()

    job = await _install(model, hub.transport)

    assert job.state == "completed"
    assert hub.requests == []
    assert job.files == []
    assert (paths.workflows_dir() / model.workflow_filename).is_file()


async def test_install_cancel() -> None:
    model = _fake_model()
    body = b"z" * (_CHUNK_BYTES * 2)
    job = make_job(model.id)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)

        async def chunks():
            yield body[:_CHUNK_BYTES]
            job.cancel_requested = True  # arrives after the first chunk has been written
            yield body[_CHUNK_BYTES:]

        return httpx.Response(200, content=chunks(), headers={"Content-Length": str(len(body))})

    called: list[str] = []

    async def on_complete(m: ImageModel) -> None:
        called.append(m.id)

    await _install(model, httpx.MockTransport(handler), job=job, on_complete=on_complete)

    assert job.state == "cancelled"
    assert job.finished_at is not None
    assert not _CKPT.dest.exists()
    assert not _VAE.dest.exists()
    assert [p.removeprefix(f"/{_REPO}/resolve/main/") for p in requested] == [_CKPT.hf_path]
    assert list(_tmp_dir().glob("*.part*")) == []  # staging cleaned up
    assert not (paths.workflows_dir() / model.workflow_filename).exists()
    assert called == []


def _cancel_once_served(
    job: PullJob, cancel_after: str
) -> Callable[[httpx.Request], httpx.Response]:
    """A handler that raises ``cancel_requested`` once the whole of ``cancel_after`` is sent.

    The flag goes up after the last chunk of that file, so the file itself completes and
    the cancel is first seen by the installer, not by the downloader. That file is exactly
    one chunk long: the stream re-chunks to ``_CHUNK_BYTES`` and holds a shorter tail back
    until the body ends, which would show the downloader the flag first.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        hf_path = request.url.path.removeprefix(f"/{_REPO}/resolve/main/")
        if hf_path != cancel_after:
            return _serve(request, _FILES[hf_path])
        body = b"c" * _CHUNK_BYTES

        async def chunks():
            yield body
            job.cancel_requested = True

        sha = hashlib.sha256(body).hexdigest()
        return httpx.Response(
            200,
            content=chunks(),
            headers={"Content-Length": str(len(body)), "X-Linked-ETag": f'"{sha}"'},
        )

    return handler


async def test_install_cancel_between_components_never_starts_the_next() -> None:
    model = _fake_model()
    job = make_job(model.id)
    requested: list[str] = []
    inner = _cancel_once_served(job, _CKPT.hf_path)

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path.removeprefix(f"/{_REPO}/resolve/main/"))
        return inner(request)

    await _install(model, httpx.MockTransport(handler), job=job)

    assert job.state == "cancelled"
    assert requested == [_CKPT.hf_path]
    assert _CKPT.dest.is_file()  # the finished file stays, so a retry carries on
    assert not _VAE.dest.exists()
    assert not (paths.workflows_dir() / model.workflow_filename).exists()


async def test_install_cancel_after_the_last_file_skips_the_workflow() -> None:
    model = _fake_model()
    job = make_job(model.id)
    called: list[str] = []

    async def on_complete(m: ImageModel) -> None:
        called.append(m.id)

    handler = _cancel_once_served(job, _VAE.hf_path)
    await _install(model, httpx.MockTransport(handler), job=job, on_complete=on_complete)

    assert job.state == "cancelled"
    assert install_state(model).state == "complete"
    assert not (paths.workflows_dir() / model.workflow_filename).exists()
    assert called == []


async def test_install_resumes_existing_partial() -> None:
    model = _fake_model()
    body = _FILES[_CKPT.hf_path]
    have = 1500
    part, sidecar = _staging_paths(install._staging_key(model), _CKPT.hf_path)
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(body[:have])
    _write_resume_sidecar(
        part,
        sidecar,
        url=hf_download_url(_CKPT.hf_repo, _CKPT.hf_path),
        etag=f'"{hashlib.sha256(body).hexdigest()}"',
        total=len(body),
    )
    hub = _Hub()

    job = await _install(model, hub.transport)

    assert job.state == "completed", job.error
    ranged = [r for r in hub.requests if r.url.path.endswith(_CKPT.hf_path)]
    assert [r.headers.get("range") for r in ranged] == [f"bytes={have}-"]
    assert _CKPT.dest.read_bytes() == body
    assert job.files[0].sha256 == hashlib.sha256(body).hexdigest()
    assert job.bytes_downloaded == job.bytes_total


async def test_install_failure_sets_error_code() -> None:
    model = _fake_model()
    called: list[str] = []

    async def on_complete(m: ImageModel) -> None:
        called.append(m.id)

    job = await _install(
        model,
        httpx.MockTransport(lambda request: httpx.Response(404)),
        on_complete=on_complete,
    )

    assert job.state == "failed"
    assert job.error_code
    assert _CKPT.hf_path in (job.error or "")
    assert job.finished_at is not None
    assert not _CKPT.dest.exists()
    assert not (paths.workflows_dir() / model.workflow_filename).exists()
    assert called == []


async def test_install_failure_keeps_components_that_already_landed() -> None:
    hub = _Hub({_CKPT.hf_path: _FILES[_CKPT.hf_path]})  # the VAE 404s

    job = await _install(_fake_model(), hub.transport)

    assert job.state == "failed"
    assert _CKPT.dest.read_bytes() == _FILES[_CKPT.hf_path]
    assert not _VAE.dest.exists()
    assert install_state(_fake_model()).state == "partial"


async def test_install_transport_error_is_a_failed_job_not_an_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    job = await _install(_fake_model(), httpx.MockTransport(handler))

    assert job.state == "failed"
    assert job.error_code == "comfyui.install_failed"
    assert "ConnectError" in (job.error or "")


async def test_install_workflow_write_failure_is_a_failed_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(model: ImageModel) -> dict[str, Any]:
        raise ValueError("template is broken")

    monkeypatch.setattr(install, "render_template", boom)

    job = await _install(_fake_model(), _Hub().transport)

    assert job.state == "failed"
    assert job.error_code == "comfyui.install_failed"
    # The weights landed, so a retry only has to write the workflow.
    assert install_state(_fake_model()).state == "complete"


async def test_install_provisions_workflow_without_meta() -> None:
    model = _fake_model()
    assert "_meta" in render_template(model)

    await _install(model, _Hub().transport)

    written = paths.workflows_dir() / model.workflow_filename
    assert written.name == "fake-model.json"
    graph = json.loads(written.read_text(encoding="utf-8"))
    assert "_meta" not in graph
    assert graph["4"]["inputs"]["ckpt_name"] == "fake-a.safetensors"  # the substitution
    expected = {k: v for k, v in render_template(model).items() if k != "_meta"}
    assert graph == expected
    assert list(paths.workflows_dir().glob("*.tmp")) == []


async def test_on_complete_called_once_on_success_only() -> None:
    called: list[tuple[str, str]] = []
    job = make_job("fake-model")

    async def on_complete(model: ImageModel) -> None:
        called.append((model.id, job.state))

    failed = await _install(
        _fake_model(),
        httpx.MockTransport(lambda r: httpx.Response(500)),
        job=make_job("fake-model"),
        on_complete=on_complete,
    )
    assert failed.state == "failed"
    assert called == []

    await _install(_fake_model(), _Hub().transport, job=job, on_complete=on_complete)
    assert called == [("fake-model", "completed")]  # once, awaited after the state flipped


async def test_on_complete_error_does_not_undo_a_finished_install() -> None:
    async def on_complete(model: ImageModel) -> None:
        raise RuntimeError("slot activation failed")

    job = await _install(_fake_model(), _Hub().transport, on_complete=on_complete)

    assert job.state == "completed"
    assert job.error is None


async def test_install_owns_and_closes_its_client_when_none_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[httpx.AsyncClient] = []
    seen: dict[str, Any] = {}
    hub = _Hub()
    real = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        seen.update(kwargs)
        client = real(transport=hub.transport, **kwargs)
        made.append(client)
        return client

    monkeypatch.setattr(install.httpx, "AsyncClient", factory)
    job = make_job("fake-model")

    await install.run_install(job, _fake_model(), hf_token=None)

    assert job.state == "completed", job.error
    assert seen["follow_redirects"] is True
    assert seen["timeout"].read is None  # the body stream is unbounded, as in the pull engine
    assert len(made) == 1
    assert made[0].is_closed


async def test_install_leaves_a_caller_supplied_client_open() -> None:
    async with httpx.AsyncClient(transport=_Hub().transport) as client:
        await install.run_install(
            make_job("fake-model"), _fake_model(), hf_token=None, client=client
        )
        assert not client.is_closed


async def test_run_install_signals_each_state_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    job = make_job("fake-model")
    seen: list[str] = []
    original = PullJob._signal

    def spy(self: PullJob) -> None:
        seen.append(self.state)
        original(self)

    monkeypatch.setattr(PullJob, "_signal", spy)
    await _install(_fake_model(), _Hub().transport, job=job)

    assert seen[0] == "running"
    assert seen[-1] == "completed"
    assert job.state == "completed"


# ── same-filesystem guard ───────────────────────────────────────────────────


def _split_devices(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Put the registry staging dir on 'another device'; return it."""
    staging = tmp_path / "other-disk" / ".tmp"
    staging.mkdir(parents=True)  # a separate mount exists before anything is installed
    monkeypatch.setattr(install, "_tmp_dir", lambda: staging)
    monkeypatch.setattr(
        install, "_device_of", lambda p: 2 if Path(p).is_relative_to(staging.parent) else 1
    )
    return staging


async def test_install_fails_fast_on_cross_device_staging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A finished download cannot be renamed across filesystems; refuse before streaming any."""
    staging = _split_devices(monkeypatch, tmp_path)
    model = _fake_model()
    hub = _Hub()
    called: list[str] = []

    async def on_complete(m: ImageModel) -> None:
        called.append(m.id)

    job = await _install(model, hub.transport, on_complete=on_complete)

    assert hub.requests == []  # not one byte was requested
    assert job.state == "failed"
    assert job.error_code == "comfyui.staging_cross_device"
    assert str(paths.models_root()) in (job.error or "")
    assert str(staging) in (job.error or "")
    assert "same filesystem as the model store" in (job.error or "")
    assert "HAL0_COMFYUI_DATA_ROOT" in (job.error or "")
    assert job.finished_at is not None
    assert install_state(model).state == "none"
    assert not (paths.workflows_dir() / model.workflow_filename).exists()
    assert called == []


def test_cross_device_error_names_both_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    staging = _split_devices(monkeypatch, tmp_path)

    with pytest.raises(install.InstallStagingCrossDevice) as excinfo:
        install.require_one_filesystem()

    assert excinfo.value.code == "comfyui.staging_cross_device"
    assert excinfo.value.status == 409
    assert excinfo.value.details == {
        "models_root": str(paths.models_root()),
        "staging_dir": str(staging),
    }


async def test_cross_device_layout_does_not_block_an_install_with_nothing_to_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _split_devices(monkeypatch, tmp_path)
    model = _fake_model()
    _touch(_CKPT)
    _touch(_VAE)

    job = await _install(model, _Hub().transport)

    assert job.state == "completed", job.error
    assert (paths.workflows_dir() / model.workflow_filename).is_file()


async def test_an_unmeasurable_device_does_not_block_the_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreadable(path: Path) -> int:
        raise PermissionError("no stat")

    monkeypatch.setattr(install, "_device_of", unreadable)

    job = await _install(_fake_model(), _Hub().transport)

    assert job.state == "completed", job.error  # the rename itself is the arbiter


# ── progress ────────────────────────────────────────────────────────────────


async def test_install_progress_total_spans_all_components() -> None:
    """The total covers every component from the start, so the bar cannot hit 100% early."""
    a, b = _FILES[_CKPT.hf_path], _FILES[_VAE.hf_path]
    model = _fake_model(
        components=(
            replace(_CKPT, approx_gb=len(a) / 1e9),  # estimates equal to the served sizes
            replace(_VAE, approx_gb=len(b) / 1e9),
        )
    )
    job = make_job(model.id)
    inner = _Hub()._handle
    seen: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((job.bytes_downloaded, job.bytes_total))
        return inner(request)

    await _install(model, httpx.MockTransport(handler), job=job)

    total = len(a) + len(b)
    # Before the first byte, and again once the first component has landed.
    assert seen == [(0, total), (len(a), total)]
    assert job.bytes_downloaded == job.bytes_total == total


async def test_install_total_stays_whole_while_a_component_streams() -> None:
    """Once the server reports a file's length, the job total is that plus the other files."""
    first = b"f" * (_CHUNK_BYTES * 2)
    second = _FILES[_VAE.hf_path]
    model = _fake_model(
        components=(
            replace(_CKPT, approx_gb=len(first) / 1e9),
            replace(_VAE, approx_gb=len(second) / 1e9),
        )
    )
    job = make_job(model.id)
    midway: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith(_CKPT.hf_path):
            return _serve(request, second)

        async def chunks():
            yield first[:_CHUNK_BYTES]
            midway.append((job.bytes_downloaded, job.bytes_total))
            yield first[_CHUNK_BYTES:]

        sha = hashlib.sha256(first).hexdigest()
        return httpx.Response(
            200,
            content=chunks(),
            headers={"Content-Length": str(len(first)), "X-Linked-ETag": f'"{sha}"'},
        )

    await _install(model, httpx.MockTransport(handler), job=job)

    assert midway == [(_CHUNK_BYTES, len(first) + len(second))]
    assert job.bytes_downloaded == job.bytes_total == len(first) + len(second)


async def test_install_total_starts_as_the_catalog_estimate_and_ends_exact() -> None:
    model = _fake_model()  # approx_gb 0.001 per component, far from the served sizes
    job = make_job(model.id)
    inner = _Hub()._handle
    seen: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((job.bytes_downloaded, job.bytes_total))
        return inner(request)

    await _install(model, httpx.MockTransport(handler), job=job)

    estimate = round(0.001 * 1e9)
    assert seen[0] == (0, 2 * estimate)
    # The first file's real size has replaced its estimate; the second is still an estimate.
    assert seen[1] == (len(_FILES[_CKPT.hf_path]), len(_FILES[_CKPT.hf_path]) + estimate)
    assert job.bytes_downloaded == job.bytes_total == sum(len(v) for v in _FILES.values())
    assert [f.bytes_total for f in job.files] == [len(v) for v in _FILES.values()]


# ── staging ─────────────────────────────────────────────────────────────────


def test_every_catalog_model_stages_its_components_apart() -> None:
    for model in CATALOG.values():
        key = install._staging_key(model)
        parts = {_staging_paths(key, c.hf_path)[0] for c in model.components}
        assert len(parts) == len(model.components), model.id


def test_image_install_staging_never_collides_with_registry_pull() -> None:
    """``sdxl-turbo`` is also a curated registry entry. A registry pull stages under the bare
    model id, so an image install of the same id must stage somewhere else or either
    downloader could unlink or replace the other's partial."""
    model = CATALOG["sdxl-turbo"]
    key = install._staging_key(model)
    assert key != model.id
    assert _sanitise_id(key) == key  # the prefix survives the filename sanitiser
    for component in model.components:
        registry_part, registry_sidecar = _staging_paths(model.id, component.hf_path)
        image_part, image_sidecar = _staging_paths(key, component.hf_path)
        assert image_part != registry_part
        assert image_sidecar != registry_sidecar
    for other in CATALOG.values():  # nor does an image key equal any catalog id's bare key
        assert install._staging_key(other) not in CATALOG


async def test_install_leaves_a_registry_pull_partial_alone() -> None:
    """The installer used to discard a partial it could not vouch for, and a registry pull's
    partial of the same model id and file looked exactly like that."""
    model = _fake_model()
    foreign_part, foreign_sidecar = _staging_paths(model.id, _CKPT.hf_path)  # registry's key
    foreign_part.parent.mkdir(parents=True, exist_ok=True)
    foreign_part.write_bytes(b"registry-pull-bytes")
    foreign_sidecar.write_text(
        json.dumps({"url": "https://example.test/other", "etag": None, "bytes": 19, "total": 99})
    )

    job = await _install(model, _Hub().transport)

    assert job.state == "completed", job.error
    assert foreign_part.read_bytes() == b"registry-pull-bytes"
    assert foreign_sidecar.exists()
    assert _CKPT.dest.read_bytes() == _FILES[_CKPT.hf_path]


async def test_interrupted_install_keeps_its_partial_under_the_image_key() -> None:
    model = _fake_model()
    body = b"z" * (_CHUNK_BYTES * 3)

    async def drop(request: httpx.Request) -> httpx.Response:
        async def stream():  # type: ignore[no-untyped-def]
            yield body[:_CHUNK_BYTES]
            raise httpx.ReadError("dropped")

        sha = hashlib.sha256(body).hexdigest()
        return httpx.Response(
            200,
            content=stream(),
            headers={"Content-Length": str(len(body)), "X-Linked-ETag": f'"{sha}"'},
        )

    job = await _install(model, httpx.MockTransport(drop))

    assert job.state == "failed"
    image_part, image_sidecar = _staging_paths(install._staging_key(model), _CKPT.hf_path)
    assert image_part.stat().st_size == _CHUNK_BYTES  # kept for the next run to resume
    assert image_sidecar.exists()
    registry_part, registry_sidecar = _staging_paths(model.id, _CKPT.hf_path)
    assert not registry_part.exists()
    assert not registry_sidecar.exists()


def test_wan_lora_pair_with_one_basename_stages_apart_under_one_model_id() -> None:
    high = [
        c
        for m in (CATALOG["wan22-t2v"], CATALOG["wan22-i2v"])
        for c in m.components
        if c.kind is ComponentKind.lora and c.relpath.endswith("/high_noise_model.safetensors")
    ]
    assert len(high) == 2
    assert high[0].hf_path.rsplit("/", 1)[1] == high[1].hf_path.rsplit("/", 1)[1]
    assert high[0].hf_path != high[1].hf_path
    first, second = (_staging_paths("one-model-id", c.hf_path)[0] for c in high)
    assert first != second


# ── remove_model ────────────────────────────────────────────────────────────


def _complete(model_id: str) -> ImageModel:
    model = get_model(model_id)
    assert model is not None
    for component in model.components:
        _touch(component)
    assert install_state(model).state == "complete"
    return model


def test_remove_keeps_component_shared_with_complete_model() -> None:
    qwen = _complete("qwen-image")
    edit = _complete("qwen-image-edit")

    result = install.remove_model(qwen)

    assert result["removed"] == ["diffusion_models/qwen_image_2512_bf16.safetensors"]
    assert result["kept"] == [
        {"relpath": "text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors", "models": [edit.id]},
        {"relpath": "vae/qwen_image_vae.safetensors", "models": [edit.id]},
    ]
    assert result["affected"] == []
    assert install_state(edit).state == "complete"
    assert install_state(qwen).state == "partial"


def test_remove_deletes_component_shared_only_with_partial_model() -> None:
    qwen = _complete("qwen-image")
    lightning = get_model("qwen-image-lightning")
    assert lightning is not None
    assert install_state(lightning).state == "partial"  # shares all three, lacks its LoRA

    result = install.remove_model(qwen)

    assert sorted(result["removed"]) == sorted(
        f"{c.kind.subdir}/{c.relpath}" for c in qwen.components
    )
    assert result["kept"] == []  # a partial neighbour cannot run, so it holds nothing back
    assert result["affected"] == []
    assert install_state(qwen).state == "none"
    assert install_state(lightning).state == "none"


def test_remove_ignores_missing_files_and_reports_only_what_it_deleted() -> None:
    model = _fake_model()
    _touch(_VAE)

    assert install.remove_model(model) == {
        "removed": ["vae/fake-b.safetensors"],
        "kept": [],
        "affected": [],
    }
    assert install.remove_model(model) == {"removed": [], "kept": [], "affected": []}


def test_remove_reports_nested_relpaths_from_the_models_root() -> None:
    wan = get_model("wan22-t2v")
    assert wan is not None
    lora = next(c for c in wan.components if "/" in c.relpath)
    _touch(lora)

    removed = install.remove_model(wan)["removed"]

    assert removed == [f"loras/{lora.relpath}"]
    assert not lora.dest.exists()


def test_remove_model_deletes_its_workflow() -> None:
    model = _fake_model()
    other = _fake_model(id="fake-other")
    for component in model.components:
        _touch(component)
    wf = paths.workflows_dir()
    wf.mkdir(parents=True)
    (wf / model.workflow_filename).write_text("{}", encoding="utf-8")
    (wf / other.workflow_filename).write_text("{}", encoding="utf-8")

    removed = install.remove_model(model)["removed"]

    assert removed == [
        "checkpoints/fake-a.safetensors",
        "vae/fake-b.safetensors",
        "workflows/fake-model.json",
    ]
    assert not (wf / model.workflow_filename).exists()
    assert (wf / other.workflow_filename).exists()  # another model's workflow is not ours


def test_remove_model_deletes_the_workflow_even_when_every_weight_is_shared() -> None:
    """The workflow file is per model id; weights shared with a complete model do not keep it."""
    qwen = _complete("qwen-image")
    _complete("qwen-image-lightning")  # shares all three of qwen-image's files
    wf = paths.workflows_dir()
    wf.mkdir(parents=True)
    (wf / qwen.workflow_filename).write_text("{}", encoding="utf-8")

    result = install.remove_model(qwen)

    assert result["removed"] == ["workflows/qwen-image.json"]
    assert {entry["models"][0] for entry in result["kept"]} == {"qwen-image-lightning"}
    assert install_state(qwen).state == "complete"


def _ltx2_pair() -> tuple[ImageModel, ImageModel]:
    """``ltx2-t2v`` and ``ltx2-i2v`` load the same four files, so each completes the other."""
    t2v = _complete("ltx2-t2v")
    i2v = get_model("ltx2-i2v")
    assert i2v is not None
    assert install_state(i2v).state == "complete"
    assert {c.dest for c in t2v.components} == {c.dest for c in i2v.components}
    wf = paths.workflows_dir()
    wf.mkdir(parents=True, exist_ok=True)
    (wf / t2v.workflow_filename).write_text("{}", encoding="utf-8")
    (wf / i2v.workflow_filename).write_text("{}", encoding="utf-8")
    return t2v, i2v


def test_remove_identical_pair_keeps_shared_and_reports_kept() -> None:
    """Two models with one file set keep each other complete: the default remove frees the
    workflow only, and says which models hold each file back, so the operator can choose."""
    t2v, i2v = _ltx2_pair()

    result = install.remove_model(t2v)

    assert result["removed"] == ["workflows/ltx2-t2v.json"]
    assert result["kept"] == [
        {"relpath": f"{c.kind.subdir}/{c.relpath}", "models": ["ltx2-i2v"]} for c in t2v.components
    ]
    assert result["affected"] == []
    assert install_state(i2v).state == "complete"
    assert (paths.workflows_dir() / i2v.workflow_filename).is_file()  # the other's workflow stays


def test_remove_include_shared_deletes_and_reports_affected() -> None:
    t2v, i2v = _ltx2_pair()

    result = install.remove_model(t2v, include_shared=True)

    assert result["removed"] == [
        *(f"{c.kind.subdir}/{c.relpath}" for c in t2v.components),
        "workflows/ltx2-t2v.json",
        "workflows/ltx2-i2v.json",  # its weights are gone, so the launcher must not offer it
    ]
    assert result["kept"] == []
    assert result["affected"] == ["ltx2-i2v"]
    assert install_state(t2v).state == "none"
    assert install_state(i2v).state == "none"
    assert not (paths.workflows_dir() / t2v.workflow_filename).exists()
    assert not (paths.workflows_dir() / i2v.workflow_filename).exists()


def test_remove_include_shared_leaves_an_unaffected_models_workflow() -> None:
    """Only models that lost a weight lose their workflow: a partial neighbour keeps its own."""
    qwen = _complete("qwen-image")
    partial = get_model("qwen-image-edit-lightning")  # shares nothing complete with qwen-image
    assert partial is not None
    wf = paths.workflows_dir()
    wf.mkdir(parents=True, exist_ok=True)
    (wf / partial.workflow_filename).write_text("{}", encoding="utf-8")

    result = install.remove_model(qwen, include_shared=True)

    assert result["affected"] == []
    assert result["removed"] == [f"{c.kind.subdir}/{c.relpath}" for c in qwen.components]
    assert (wf / partial.workflow_filename).is_file()


def test_remove_include_shared_skips_an_affected_model_without_a_workflow() -> None:
    t2v, i2v = _ltx2_pair()
    (paths.workflows_dir() / i2v.workflow_filename).unlink()

    result = install.remove_model(t2v, include_shared=True)

    assert result["affected"] == ["ltx2-i2v"]
    assert result["removed"][-1] == "workflows/ltx2-t2v.json"
    assert "workflows/ltx2-i2v.json" not in result["removed"]


def test_remove_include_shared_failure_on_an_affected_workflow_reports_how_far_it_got(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t2v, i2v = _ltx2_pair()
    wf = paths.workflows_dir()
    _failing_unlink(monkeypatch, i2v.workflow_filename, PermissionError(13, "Permission denied"))

    with pytest.raises(install.RemoveFailed) as excinfo:
        install.remove_model(t2v, include_shared=True)

    assert excinfo.value.details == {
        "removed": [
            *(f"{c.kind.subdir}/{c.relpath}" for c in t2v.components),
            "workflows/ltx2-t2v.json",
        ],
        "failed_path": str(wf / i2v.workflow_filename),
        "error": "[Errno 13] Permission denied",
    }


def test_remove_include_shared_reports_each_affected_model_once_in_catalog_order() -> None:
    qwen = _complete("qwen-image")
    _complete("qwen-image-edit")
    _complete("qwen-image-lightning")  # shares all three of qwen-image's files
    partial = get_model("qwen-image-edit-lightning")
    assert partial is not None
    assert install_state(partial).state == "partial"
    wf = paths.workflows_dir()
    wf.mkdir(parents=True, exist_ok=True)
    for model_id in ("qwen-image-edit", "qwen-image-lightning", "qwen-image-edit-lightning"):
        (wf / f"{model_id}.json").write_text("{}", encoding="utf-8")

    result = install.remove_model(qwen, include_shared=True)

    # ``qwen-image-lightning`` shares three files, ``qwen-image-edit`` two; the partial
    # ``qwen-image-edit-lightning`` was not installed to begin with, so it is not affected.
    assert result["affected"] == ["qwen-image-lightning", "qwen-image-edit"]
    assert partial.id not in result["affected"]
    # Each affected model's workflow follows its weights, after the model's own, in that order.
    assert result["removed"][-2:] == [
        "workflows/qwen-image-lightning.json",
        "workflows/qwen-image-edit.json",
    ]


def test_remove_include_shared_without_neighbours_affects_nothing() -> None:
    model = _complete("esrgan-4x")

    result = install.remove_model(model, include_shared=True)

    assert result == {"removed": ["upscale_models/4x-UltraSharp.pth"], "kept": [], "affected": []}


def test_remove_lightning_keeps_base_for_qwen_image_by_default() -> None:
    """Installing the Lightning variant makes ``qwen-image`` complete, so removing the variant
    must not take the 40 GB base the operator may be using as ``qwen-image``."""
    lightning = _complete("qwen-image-lightning")
    base = get_model("qwen-image")
    assert base is not None
    assert install_state(base).state == "complete"

    result = install.remove_model(lightning)

    lora = next(c for c in lightning.components if c.kind is ComponentKind.lora)
    assert result["removed"] == [f"loras/{lora.relpath}"]
    assert result["kept"] == [
        {"relpath": f"{c.kind.subdir}/{c.relpath}", "models": [base.id]}
        for c in lightning.components
        if c is not lora
    ]
    assert result["affected"] == []
    assert install_state(base).state == "complete"


def test_remove_kept_entry_names_every_complete_holder() -> None:
    qwen = _complete("qwen-image")
    _complete("qwen-image-edit")
    _complete("qwen-image-lightning")

    result = install.remove_model(qwen)

    by_path = {entry["relpath"]: entry["models"] for entry in result["kept"]}
    assert by_path["vae/qwen_image_vae.safetensors"] == ["qwen-image-lightning", "qwen-image-edit"]
    assert by_path["diffusion_models/qwen_image_2512_bf16.safetensors"] == ["qwen-image-lightning"]


async def test_install_then_remove_leaves_nothing_behind() -> None:
    model = _fake_model()
    await _install(model, _Hub().transport)
    assert (paths.workflows_dir() / model.workflow_filename).is_file()

    removed = install.remove_model(model)["removed"]

    assert removed[-1] == "workflows/fake-model.json"
    assert install_state(model).state == "none"
    assert list(paths.workflows_dir().iterdir()) == []


# ── writable destinations (I2) ──────────────────────────────────────────────


def _deny_writes_under(monkeypatch: pytest.MonkeyPatch, *denied: Path) -> None:
    """Make ``os.access`` report a write refusal for each path in ``denied`` and below it.

    Patching beats ``chmod`` here: root ignores permission bits, and CI sometimes runs as root.
    """
    real_access = os.access

    def access(path: Any, mode: int, **kwargs: Any) -> bool:
        if mode & os.W_OK and any(Path(path).is_relative_to(d) for d in denied):
            return False
        return real_access(path, mode, **kwargs)

    monkeypatch.setattr(install.os, "access", access)


async def test_run_install_refuses_unwritable_destination_without_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read-only destination fails the job before a byte is requested, not after 40 GB."""
    model = _fake_model()
    blocked = _CKPT.dest.parent
    blocked.mkdir(parents=True)
    _deny_writes_under(monkeypatch, blocked)
    hub = _Hub()

    job = await _install(model, hub.transport)

    assert job.state == "failed"
    assert job.error_code == "comfyui.models_dir_not_writable"
    assert str(blocked) in (job.error or "")
    assert hub.requests == []  # nothing was requested
    assert not any(_tmp_dir().glob("*"))  # nothing was staged
    assert install_state(model).state == "none"


def test_require_writable_destinations_names_the_first_unwritable_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _fake_model()
    for component in model.components:
        component.dest.parent.mkdir(parents=True, exist_ok=True)
    _deny_writes_under(monkeypatch, _CKPT.dest.parent, _VAE.dest.parent)

    with pytest.raises(install.InstallDirNotWritable) as excinfo:
        install.require_writable_destinations(model)

    exc = excinfo.value
    assert exc.code == "comfyui.models_dir_not_writable"
    assert exc.status == 409
    assert exc.details == {"path": str(_CKPT.dest.parent)}  # component order: the checkpoint first
    assert str(_CKPT.dest.parent) in exc.message
    assert "hal0" in exc.message  # names the remedy


def test_require_writable_destinations_checks_the_nearest_existing_ancestor(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    """A fresh store has no models tree yet; the directory that must be writable is its ancestor."""
    store.mkdir(parents=True)
    assert not paths.models_root().exists()
    _deny_writes_under(monkeypatch, store)

    with pytest.raises(install.InstallDirNotWritable) as excinfo:
        install.require_writable_destinations(_fake_model())

    assert excinfo.value.details["path"] == str(store)


def test_require_writable_destinations_passes_when_every_dir_is_writable() -> None:
    model = _fake_model()
    _CKPT.dest.parent.mkdir(parents=True)  # the other destination does not exist yet
    install.require_writable_destinations(model)


def test_require_writable_destinations_only_looks_at_missing_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _fake_model()
    for component in model.components:
        _touch(component)
    _deny_writes_under(monkeypatch, paths.models_root())

    install.require_writable_destinations(model)  # nothing left to write


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
def test_require_writable_destinations_sees_a_real_read_only_directory() -> None:
    blocked = _CKPT.dest.parent
    blocked.mkdir(parents=True)
    blocked.chmod(0o555)
    try:
        with pytest.raises(install.InstallDirNotWritable):
            install.require_writable_destinations(_fake_model())
    finally:
        blocked.chmod(0o755)


# ── remove failures (I2) ────────────────────────────────────────────────────


def _failing_unlink(monkeypatch: pytest.MonkeyPatch, name: str, exc: OSError) -> None:
    """Make ``Path.unlink`` raise ``exc`` for the file called ``name`` and work for the rest."""
    real_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name == name:
            raise exc
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def test_remove_reports_partial_removal_on_permission_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file that cannot be deleted stops the removal with a typed 409, not a 500 (I2)."""
    model = _fake_model()
    for component in model.components:
        _touch(component)
    wf = paths.workflows_dir()
    wf.mkdir(parents=True)
    (wf / model.workflow_filename).write_text("{}", encoding="utf-8")
    _failing_unlink(monkeypatch, "fake-b.safetensors", PermissionError(13, "Permission denied"))

    with pytest.raises(install.RemoveFailed) as excinfo:
        install.remove_model(model)

    exc = excinfo.value
    assert exc.code == "comfyui.remove_failed"
    assert exc.status == 409
    assert exc.details == {
        "removed": ["checkpoints/fake-a.safetensors"],  # gone before the failure
        "failed_path": str(_VAE.dest),
        "error": "[Errno 13] Permission denied",
    }
    assert str(_VAE.dest) in exc.message
    assert not _CKPT.dest.exists()
    assert _VAE.dest.exists()
    assert (wf / model.workflow_filename).exists()  # the removal stopped at the first failure


def test_remove_include_shared_failure_reports_how_far_it_got(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t2v, _ = _ltx2_pair()
    stuck = t2v.components[1]
    _failing_unlink(monkeypatch, Path(stuck.relpath).name, PermissionError(13, "Permission denied"))

    with pytest.raises(install.RemoveFailed) as excinfo:
        install.remove_model(t2v, include_shared=True)

    first = t2v.components[0]
    assert excinfo.value.details == {
        "removed": [f"{first.kind.subdir}/{first.relpath}"],
        "failed_path": str(stuck.dest),
        "error": "[Errno 13] Permission denied",
    }


def test_remove_reports_a_workflow_that_cannot_be_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _fake_model()
    for component in model.components:
        _touch(component)
    wf = paths.workflows_dir()
    wf.mkdir(parents=True)
    (wf / model.workflow_filename).write_text("{}", encoding="utf-8")
    _failing_unlink(monkeypatch, model.workflow_filename, PermissionError(13, "Permission denied"))

    with pytest.raises(install.RemoveFailed) as excinfo:
        install.remove_model(model)

    assert excinfo.value.details["removed"] == [
        "checkpoints/fake-a.safetensors",
        "vae/fake-b.safetensors",
    ]
    assert excinfo.value.details["failed_path"] == str(wf / model.workflow_filename)


def test_remove_resolves_the_models_root_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Component.dest`` re-reads the config on every call; a removal must not (M11)."""
    qwen = _complete("qwen-image")
    _complete("qwen-image-edit")
    calls = 0
    real = paths.models_root

    def counting() -> Path:
        nonlocal calls
        calls += 1
        return real()

    monkeypatch.setattr(paths, "models_root", counting)

    install.remove_model(qwen)

    assert calls == 1


def test_component_path_follows_the_given_root() -> None:
    elsewhere = Path("/somewhere/else")
    assert component_path(_CKPT, elsewhere) == elsewhere / "checkpoints" / "fake-a.safetensors"
    assert _CKPT.dest == component_path(_CKPT, paths.models_root())


# ── preflight_disk ──────────────────────────────────────────────────────────

_Usage = namedtuple("_Usage", "total used free")


def _free_space(monkeypatch: pytest.MonkeyPatch, free_by_dir: Callable[[Path], int]) -> list[Path]:
    """Report ``free_by_dir(path)`` bytes free; return the paths that were probed."""
    probed: list[Path] = []

    def usage(path: Path | str) -> _Usage:
        probed.append(Path(path))
        return _Usage(0, 0, free_by_dir(Path(path)))

    monkeypatch.setattr(install.shutil, "disk_usage", usage)
    return probed


def test_preflight_disk_raises_507(monkeypatch: pytest.MonkeyPatch) -> None:
    model = CATALOG["sdxl-lightning"]
    _free_space(monkeypatch, lambda path: 1)

    with pytest.raises(install.InstallInsufficientDisk) as excinfo:
        install.preflight_disk(model)

    assert excinfo.value.code == "comfyui.insufficient_disk"
    assert excinfo.value.status == 507
    details = excinfo.value.details
    assert details["free_bytes"] == 1
    assert details["required_bytes"] == sum(round(c.approx_gb * 1e9) for c in model.components)
    assert Path(details["path"]).exists()  # the ancestor that was measured


def test_preflight_disk_passes_when_there_is_room(monkeypatch: pytest.MonkeyPatch) -> None:
    _free_space(monkeypatch, lambda path: 10**15)
    install.preflight_disk(CATALOG["qwen-image"])


def test_preflight_disk_counts_only_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _complete("sd-1.5-pruned-emaonly")
    _free_space(monkeypatch, lambda path: 1)
    install.preflight_disk(model)  # nothing left to download


def test_preflight_disk_measures_the_models_root_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """One filesystem holds both the staging dir and the models tree, so one probe is enough."""
    assert not paths.models_root().exists()  # a fresh store: the nearest ancestor is measured
    probed = _free_space(monkeypatch, lambda path: 10**15)

    install.preflight_disk(CATALOG["sd-1.5-pruned-emaonly"])

    assert len(probed) == 1
    assert probed[0].exists()
    assert paths.models_root().is_relative_to(probed[0])


def _big_model() -> ImageModel:
    """One 1 GB component (``approx_gb`` is estimate only; the tests stage sparse files)."""
    return _fake_model(components=(replace(_CKPT, approx_gb=1.0),))


def _stage_partial(model: ImageModel, component: Component, size: int, *, sidecar: bool) -> Path:
    """Leave a ``size``-byte partial of ``component`` where the downloader resumes from."""
    part, meta = _staging_paths(install._staging_key(model), component.hf_path)
    part.parent.mkdir(parents=True, exist_ok=True)
    with part.open("wb") as fh:
        fh.truncate(size)  # sparse: the test needs the size, not the bytes
    if sidecar:
        meta.write_text(json.dumps({"url": "u", "etag": None, "bytes": size, "total": 10**9}))
    return part


def test_preflight_subtracts_resumable_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """A staged partial with its resume sidecar is not downloaded again, so it needs no room."""
    model = _big_model()
    _stage_partial(model, model.components[0], 600_000_000, sidecar=True)
    _free_space(monkeypatch, lambda path: 500_000_000)  # 1 GB would not fit; the 0.4 GB left does

    install.preflight_disk(model)

    _free_space(monkeypatch, lambda path: 300_000_000)
    with pytest.raises(install.InstallInsufficientDisk) as excinfo:
        install.preflight_disk(model)
    assert excinfo.value.details["required_bytes"] == 400_000_000  # what is left, not the total
    assert "about 0.4 GB" in excinfo.value.message  # the message names the remainder too


def test_preflight_required_bytes_is_the_unrounded_sum_of_the_missing_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two 0.04 GB files need 80 MB, not the 100 MB their rounded 0.1 GB total would suggest."""
    model = _fake_model(components=(replace(_CKPT, approx_gb=0.04), replace(_VAE, approx_gb=0.04)))
    _free_space(monkeypatch, lambda path: 90_000_000)
    install.preflight_disk(model)  # 80 MB fits in 90 MB free

    _free_space(monkeypatch, lambda path: 70_000_000)
    with pytest.raises(install.InstallInsufficientDisk) as excinfo:
        install.preflight_disk(model)
    assert excinfo.value.details["required_bytes"] == 80_000_000
    assert "about 0.08 GB" in excinfo.value.message


def test_preflight_ignores_partial_without_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    """The downloader discards a partial it cannot vouch for, so its bytes buy nothing."""
    model = _big_model()
    _stage_partial(model, model.components[0], 600_000_000, sidecar=False)
    _free_space(monkeypatch, lambda path: 500_000_000)

    with pytest.raises(install.InstallInsufficientDisk) as excinfo:
        install.preflight_disk(model)

    assert excinfo.value.details["required_bytes"] == 1_000_000_000
    assert "about 1 GB" in excinfo.value.message


def test_preflight_ignores_a_registry_pull_partial_of_the_same_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry pull of the same id stages under the bare id; the installer never resumes it,
    so it must not be counted as already fetched."""
    model = _big_model()
    part, meta = _staging_paths(model.id, model.components[0].hf_path)
    part.parent.mkdir(parents=True, exist_ok=True)
    with part.open("wb") as fh:
        fh.truncate(600_000_000)
    meta.write_text(json.dumps({"url": "u", "etag": None, "bytes": 600_000_000, "total": 10**9}))
    _free_space(monkeypatch, lambda path: 500_000_000)

    with pytest.raises(install.InstallInsufficientDisk) as excinfo:
        install.preflight_disk(model)

    assert excinfo.value.details["required_bytes"] == 1_000_000_000
    assert excinfo.value.details["staged_bytes"] == 0


def test_preflight_finds_the_partial_under_the_installers_staging_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The partial is keyed by the model id and the component's ``hf_path``, not its ``relpath``."""
    model = _fake_model(components=(replace(_CKPT, approx_gb=1.0),))
    assert _CKPT.hf_path != _CKPT.relpath
    other = replace(model, id="another-model")  # same file, another model's staging key
    _stage_partial(other, other.components[0], 600_000_000, sidecar=True)
    _free_space(monkeypatch, lambda path: 500_000_000)

    with pytest.raises(install.InstallInsufficientDisk):
        install.preflight_disk(model)


def test_preflight_clamps_what_is_required_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A partial larger than the catalog estimate cannot make the requirement negative, nor
    cover another component's download."""
    first = replace(_CKPT, approx_gb=1.0)
    second = replace(_VAE, approx_gb=1.0)
    model = _fake_model(components=(first, second))
    _stage_partial(model, first, 1_500_000_000, sidecar=True)  # estimate was 1 GB
    _free_space(monkeypatch, lambda path: 900_000_000)

    with pytest.raises(install.InstallInsufficientDisk) as excinfo:
        install.preflight_disk(model)
    assert excinfo.value.details["required_bytes"] == 1_000_000_000  # only ``second`` is left

    _stage_partial(model, second, 1_500_000_000, sidecar=True)
    install.preflight_disk(model)  # both covered: nothing to download, nothing to measure


def test_preflight_ignores_the_partial_of_a_component_already_on_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _fake_model(components=(replace(_CKPT, approx_gb=1.0), replace(_VAE, approx_gb=1.0)))
    _touch(model.components[0])
    _stage_partial(model, model.components[0], 600_000_000, sidecar=True)  # stale leftover
    _free_space(monkeypatch, lambda path: 900_000_000)

    with pytest.raises(install.InstallInsufficientDisk) as excinfo:
        install.preflight_disk(model)

    assert excinfo.value.details["required_bytes"] == 1_000_000_000  # the VAE, in full


# ── install_models_blocking ─────────────────────────────────────────────────


def test_install_models_blocking_runs_sequentially_and_reports_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    async def fake_run_install(job: PullJob, model: ImageModel, **kwargs: Any) -> None:
        order.append(model.id)
        assert kwargs["hf_token"] == "hf_tok"
        job.state = "failed" if model.id == "sd-1.5-pruned-emaonly" else "completed"
        job.error = "boom" if job.state == "failed" else None

    monkeypatch.setattr(install, "run_install", fake_run_install)
    monkeypatch.setattr(install, "preflight_disk", lambda model: None)
    lines: list[str] = []

    results = install.install_models_blocking(
        ["sdxl-turbo", "sd-1.5-pruned-emaonly"], hf_token="hf_tok", on_line=lines.append
    )

    assert results == [("sdxl-turbo", "completed"), ("sd-1.5-pruned-emaonly", "failed")]
    assert order == ["sdxl-turbo", "sd-1.5-pruned-emaonly"]
    assert any("sdxl-turbo" in line for line in lines)
    assert any("boom" in line for line in lines)


def test_install_models_blocking_emits_throttled_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long download reports where it is, at most once per interval, between start and end."""
    interval = 0.04
    monkeypatch.setattr(install, "_PROGRESS_INTERVAL_S", interval)
    monkeypatch.setattr(install, "preflight_disk", lambda model: None)

    async def fake_run_install(job: PullJob, model: ImageModel, **kwargs: Any) -> None:
        job.state = "running"
        job.bytes_total = 8_000_000_000
        for step in range(1, 17):  # 16 updates, each far more often than the interval
            job.bytes_downloaded = step * 500_000_000
            await asyncio.sleep(0.01)
        job.state = "completed"

    monkeypatch.setattr(install, "run_install", fake_run_install)
    lines: list[str] = []

    began = time.monotonic()
    results = install.install_models_blocking(["sdxl-turbo"], hf_token=None, on_line=lines.append)
    elapsed = time.monotonic() - began

    assert results == [("sdxl-turbo", "completed")]
    progress = [
        line for line in lines if re.fullmatch(r"  sdxl-turbo: \d+% \(\d+\.\d/8\.0 GB\)", line)
    ]
    assert progress, lines
    assert len(progress) <= elapsed / interval  # never faster than one line per interval
    assert len(progress) < 16  # throttled: not one line per update
    percents = [int(line.split(":")[1].split("%")[0]) for line in progress]
    assert percents == sorted(percents)
    # the start and end lines are still there, around the progress
    assert "installing" in lines[0] and lines[-1].startswith("sdxl-turbo: completed")
    assert lines.index(progress[0]) > 0 and lines.index(progress[-1]) < len(lines) - 1


def test_install_models_blocking_survives_a_raising_on_line_during_progress(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A progress sink that raises must not lose a finished model's result or skip the rest."""
    monkeypatch.setattr(install, "_PROGRESS_INTERVAL_S", 0.02)
    monkeypatch.setattr(install, "preflight_disk", lambda model: None)

    async def fake_run_install(job: PullJob, model: ImageModel, **kwargs: Any) -> None:
        job.state = "running"
        job.bytes_total = 8_000_000_000
        job.bytes_downloaded = 1_000_000_000
        await asyncio.sleep(0.12)  # several intervals
        job.state = "completed"

    monkeypatch.setattr(install, "run_install", fake_run_install)
    progress_calls: list[str] = []
    other: list[str] = []

    def on_line(line: str) -> None:
        if line.startswith("  "):
            progress_calls.append(line)
            raise BrokenPipeError("terminal went away")
        other.append(line)

    results = install.install_models_blocking(
        ["sdxl-turbo", "sd-1.5-pruned-emaonly"], hf_token=None, on_line=on_line
    )

    assert results == [("sdxl-turbo", "completed"), ("sd-1.5-pruned-emaonly", "completed")]
    assert len(progress_calls) == 2  # one failed attempt per model, then it stopped reporting
    assert "sdxl-turbo: completed" in other and "sd-1.5-pruned-emaonly: completed" in other
    assert any(r.message == "comfyui.install_progress_report_failed" for r in caplog.records)


def test_install_models_blocking_is_silent_between_start_and_end_when_install_is_quick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default interval is seconds: a quick install prints no progress and waits for none."""

    async def fake_run_install(job: PullJob, model: ImageModel, **kwargs: Any) -> None:
        job.state = "running"
        job.bytes_total = 1_000_000_000
        job.bytes_downloaded = 500_000_000
        await asyncio.sleep(0.02)
        job.state = "completed"

    monkeypatch.setattr(install, "run_install", fake_run_install)
    monkeypatch.setattr(install, "preflight_disk", lambda model: None)
    lines: list[str] = []

    began = time.monotonic()
    install.install_models_blocking(["sdxl-turbo"], hf_token=None, on_line=lines.append)

    assert time.monotonic() - began < 1.0  # the progress task is cancelled, not waited for
    assert len(lines) == 2


def test_install_models_blocking_downloads_for_real(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _fake_model()
    monkeypatch.setitem(CATALOG, model.id, model)
    hub = _Hub()
    real = httpx.AsyncClient
    monkeypatch.setattr(
        install.httpx, "AsyncClient", lambda **kw: real(transport=hub.transport, **kw)
    )

    assert install.install_models_blocking([model.id], hf_token=None) == [(model.id, "completed")]
    assert install_state(model).state == "complete"
    assert (paths.workflows_dir() / model.workflow_filename).is_file()


def test_install_models_blocking_provisions_a_missing_workflow_for_a_complete_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup never skips a complete model: its workflow is written without a request."""
    model = _fake_model()
    monkeypatch.setitem(CATALOG, model.id, model)
    for component in model.components:
        _touch(component)
    hub = _Hub()
    real = httpx.AsyncClient
    monkeypatch.setattr(
        install.httpx, "AsyncClient", lambda **kw: real(transport=hub.transport, **kw)
    )

    assert install.install_models_blocking([model.id], hf_token=None) == [(model.id, "completed")]
    assert (paths.workflows_dir() / model.workflow_filename).is_file()
    assert hub.requests == []


def test_install_models_blocking_reports_unknown_ids_and_carries_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_install(job: PullJob, model: ImageModel, **kwargs: Any) -> None:
        job.state = "completed"

    monkeypatch.setattr(install, "run_install", fake_run_install)
    monkeypatch.setattr(install, "preflight_disk", lambda model: None)
    lines: list[str] = []

    results = install.install_models_blocking(
        ["no-such-model", "sdxl-turbo"], hf_token=None, on_line=lines.append
    )

    assert results == [("no-such-model", "failed"), ("sdxl-turbo", "completed")]
    assert any("no-such-model" in line for line in lines)


def test_install_models_blocking_turns_insufficient_disk_into_a_failed_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[str] = []

    async def fake_run_install(job: PullJob, model: ImageModel, **kwargs: Any) -> None:
        ran.append(model.id)

    monkeypatch.setattr(install, "run_install", fake_run_install)
    _free_space(monkeypatch, lambda path: 1)
    lines: list[str] = []

    results = install.install_models_blocking(["sdxl-turbo"], hf_token=None, on_line=lines.append)

    assert results == [("sdxl-turbo", "failed")]
    assert ran == []
    assert any("insufficient disk" in line.lower() for line in lines)


# ── errors and the token helper ─────────────────────────────────────────────


def test_error_classes_carry_their_codes() -> None:
    assert install.ImageModelNotFound.code == "comfyui.model_unknown"
    assert install.ImageModelNotFound.status == 404
    assert install.InstallInsufficientDisk.code == "comfyui.insufficient_disk"
    assert install.InstallInsufficientDisk.status == 507
    assert install.InstallStagingCrossDevice.code == "comfyui.staging_cross_device"
    assert install.InstallStagingCrossDevice.status == 409
