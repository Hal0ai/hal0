"""hal0.comfyui.install: the image-model installer on the registry downloader (ADR-0036)."""

from __future__ import annotations

import hashlib
import json
from collections import namedtuple
from collections.abc import Callable
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
    get_model,
    install_state,
    render_template,
)
from hal0.registry.pull import (
    _CHUNK_BYTES,
    PullJob,
    _staging_paths,
    _tmp_dir,
    _write_resume_sidecar,
    hf_download_url,
    make_job,
)
from hal0.upstreams import huggingface

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
    part, sidecar = _staging_paths(model.id, _CKPT.hf_path)
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


# ── staging ─────────────────────────────────────────────────────────────────


def test_every_catalog_model_stages_its_components_apart() -> None:
    for model in CATALOG.values():
        parts = {_staging_paths(model.id, c.hf_path)[0] for c in model.components}
        assert len(parts) == len(model.components), model.id


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

    removed = install.remove_model(qwen)

    assert removed == ["diffusion_models/qwen_image_2512_bf16.safetensors"]
    assert install_state(edit).state == "complete"
    assert install_state(qwen).state == "partial"


def test_remove_deletes_component_shared_only_with_partial_model() -> None:
    qwen = _complete("qwen-image")
    lightning = get_model("qwen-image-lightning")
    assert lightning is not None
    assert install_state(lightning).state == "partial"  # shares all three, lacks its LoRA

    removed = install.remove_model(qwen)

    assert sorted(removed) == sorted(f"{c.kind.subdir}/{c.relpath}" for c in qwen.components)
    assert install_state(qwen).state == "none"
    assert install_state(lightning).state == "none"


def test_remove_ignores_missing_files_and_reports_only_what_it_deleted() -> None:
    model = _fake_model()
    _touch(_VAE)

    assert install.remove_model(model) == ["vae/fake-b.safetensors"]
    assert install.remove_model(model) == []


def test_remove_reports_nested_relpaths_from_the_models_root() -> None:
    wan = get_model("wan22-t2v")
    assert wan is not None
    lora = next(c for c in wan.components if "/" in c.relpath)
    _touch(lora)

    removed = install.remove_model(wan)

    assert removed == [f"loras/{lora.relpath}"]
    assert not lora.dest.exists()


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
    assert details["required_bytes"] == int(model.approx_gb * 1024**3)
    assert Path(details["path"]).exists()  # the ancestor that was measured


def test_preflight_disk_passes_when_there_is_room(monkeypatch: pytest.MonkeyPatch) -> None:
    _free_space(monkeypatch, lambda path: 10**15)
    install.preflight_disk(CATALOG["qwen-image"])


def test_preflight_disk_counts_only_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _complete("sd-1.5-pruned-emaonly")
    _free_space(monkeypatch, lambda path: 1)
    install.preflight_disk(model)  # nothing left to download


def test_preflight_disk_measures_the_nearest_existing_ancestor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert not paths.models_root().exists()
    probed = _free_space(monkeypatch, lambda path: 10**15)

    install.preflight_disk(CATALOG["sd-1.5-pruned-emaonly"])

    assert probed
    assert all(p.exists() for p in probed)


def test_preflight_disk_checks_the_staging_filesystem_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bytes are staged in the registry tmp dir, which can be on another device."""
    staging = tmp_path / "other-disk" / ".tmp"
    staging.mkdir(parents=True)
    models = paths.models_root()
    models.mkdir(parents=True)
    monkeypatch.setattr(install, "_tmp_dir", lambda: staging)
    monkeypatch.setattr(
        install, "_device_of", lambda p: 2 if Path(p).is_relative_to(staging) else 1
    )
    _free_space(monkeypatch, lambda path: 1 if path == staging else 10**15)

    with pytest.raises(install.InstallInsufficientDisk) as excinfo:
        install.preflight_disk(CATALOG["sd-1.5-pruned-emaonly"])

    assert excinfo.value.details["path"] == str(staging)
    assert excinfo.value.details["free_bytes"] == 1


def test_preflight_disk_names_the_models_root_when_it_is_the_short_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    staging = tmp_path / "other-disk" / ".tmp"
    staging.mkdir(parents=True)
    models = paths.models_root()
    models.mkdir(parents=True)
    monkeypatch.setattr(install, "_tmp_dir", lambda: staging)
    monkeypatch.setattr(
        install, "_device_of", lambda p: 2 if Path(p).is_relative_to(staging) else 1
    )
    _free_space(monkeypatch, lambda path: 10**15 if path == staging else 1)

    with pytest.raises(install.InstallInsufficientDisk) as excinfo:
        install.preflight_disk(CATALOG["sd-1.5-pruned-emaonly"])

    assert excinfo.value.details["path"] == str(models)


def test_preflight_disk_measures_a_shared_device_once(monkeypatch: pytest.MonkeyPatch) -> None:
    probed = _free_space(monkeypatch, lambda path: 10**15)
    # models_root and the staging dir both live under the one store in this fixture.

    install.preflight_disk(CATALOG["sd-1.5-pruned-emaonly"])

    assert len(probed) == 1


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


def test_install_exposes_the_one_hf_token_helper() -> None:
    assert install.hf_token_from_env is huggingface.hf_token_from_env
