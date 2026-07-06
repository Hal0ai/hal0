"""WS-G (#1113): ComfyUI per-variant download driver + img-slot activation."""

from __future__ import annotations

from hal0.comfyui.provision import (
    estimate_totals,
    provision_comfyui_downloads,
    resolve_variants,
)


def test_resolve_variants_maps_and_collects_unknown():
    variants, unknown = resolve_variants(
        (("txt2img", "sdxl"), ("txt2img", "nope"), ("bogus_cap", "x"))
    )
    assert [v.family for v in variants] == ["sdxl"]
    assert unknown == [("txt2img", "nope"), ("bogus_cap", "x")]


def test_estimate_totals_sums_size_and_time():
    variants, _ = resolve_variants((("txt2img", "sdxl"), ("image_upscale", "esrgan")))
    total_gb, total_s = estimate_totals(variants)
    assert total_gb == 7.1  # 7.0 + 0.1
    assert total_s == 20  # 10 + 10


class _FakeClock:
    """Injectable sleep that advances a step counter so poll loops progress."""

    def __init__(self, jobs_status):
        # jobs_status: job_id -> list of statuses to return on successive polls
        self.status = jobs_status
        self.poll_count: dict[str, int] = {}

    def poll(self, job_id):
        i = self.poll_count.get(job_id, 0)
        seq = self.status[job_id]
        status = seq[min(i, len(seq) - 1)]
        self.poll_count[job_id] = i + 1
        return {"id": job_id, "status": status}

    def sleep(self, _):  # no real waiting
        return None


def test_queues_fetch_and_activates_on_first_landed():
    fetched: list[str] = []
    activations: list[int] = []

    def fake_fetch(v):
        job_id = f"job-{v.family}"
        fetched.append(v.family)
        return job_id

    clock = _FakeClock(
        {
            "job-sdxl": ["running", "done"],
            "job-esrgan": ["running", "running", "done"],
        }
    )

    result = provision_comfyui_downloads(
        (("txt2img", "sdxl"), ("image_upscale", "esrgan")),
        fetch=fake_fetch,
        poll=clock.poll,
        activate=lambda: activations.append(1),
        sleep=clock.sleep,
    )

    assert set(fetched) == {"sdxl", "esrgan"}
    assert set(result.landed) == {"sdxl", "esrgan"}
    assert result.failed == []
    assert result.activated is True
    # img slot activated exactly ONCE (on the first landed model).
    assert activations == [1]


def test_no_activation_when_every_fetch_fails():
    activations: list[int] = []
    clock = _FakeClock({"job-sdxl": ["failed"]})

    result = provision_comfyui_downloads(
        (("txt2img", "sdxl"),),
        fetch=lambda v: f"job-{v.family}",
        poll=clock.poll,
        activate=lambda: activations.append(1),
        sleep=clock.sleep,
    )

    assert result.failed == ["sdxl"]
    assert result.landed == []
    assert result.activated is False
    assert activations == []


def test_empty_selection_is_a_noop():
    called = {"fetch": 0, "activate": 0}
    result = provision_comfyui_downloads(
        (),
        fetch=lambda v: called.__setitem__("fetch", called["fetch"] + 1) or "j",
        poll=lambda j: {"status": "done"},
        activate=lambda: called.__setitem__("activate", called["activate"] + 1),
        sleep=lambda _: None,
    )
    assert result.jobs == {}
    assert result.activated is False
    assert called == {"fetch": 0, "activate": 0}
