"""Upgrade migration: relabel legacy ``device = "gpu-vulkan"`` slot TOMLs.

Covers :func:`hal0.updater.updater.relabel_stale_vulkan_slots` — the #1924
follow-up to PR #1923 (#1888, Vulkan LLM lane retirement). PR #1923 relabeled
the SEED slot TOMLs the installer ships from ``gpu-vulkan`` to ``gpu-rocm``,
but deliberately left slot TOMLs already materialised on an existing install
untouched, so an updated box refuses to load them under the new
``require_kfd_for_gpu_slot`` preflight guard until an operator manually
relabels them. This migration does that relabel automatically:

* ``/dev/kfd`` present (AMD host with a working ROCm compute node) →
  ``gpu-rocm``, matching PR #1923's own seed relabel.
* ``/dev/kfd`` absent → ``cpu``, logged loudly since it is a genuine
  operator-visible behavior change (the slot drops off the GPU).

Only the ``device`` key is ever touched (#1867 rails: narrow scope, log every
mutation, idempotent).
"""

from __future__ import annotations

import tomllib

from hal0.config.paths import slots_config_dir
from hal0.updater.updater import relabel_stale_vulkan_slots


def _write_slot(name: str, body: str) -> None:
    d = slots_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.toml").write_text(body, encoding="utf-8")


def _raw(name: str) -> dict:
    return tomllib.loads((slots_config_dir() / f"{name}.toml").read_text(encoding="utf-8"))


def _device_of(name: str):
    raw = _raw(name)
    if "device" in raw:
        return raw.get("device")
    slot = raw.get("slot")
    return slot.get("device") if isinstance(slot, dict) else None


# ── kfd present → gpu-rocm ──────────────────────────────────────────────── #


def test_vulkan_slot_relabels_to_rocm_when_kfd_present(tmp_hal0_home: str) -> None:
    _write_slot("agent", 'name = "agent"\ntype = "llm"\ndevice = "gpu-vulkan"\nport = 8081\n')

    assert relabel_stale_vulkan_slots(kfd_present=True) == 1
    assert _device_of("agent") == "gpu-rocm"


def test_nested_slot_table_relabels_to_rocm_when_kfd_present(tmp_hal0_home: str) -> None:
    _write_slot(
        "nested",
        '[slot]\nname = "nested"\ndevice = "gpu-vulkan"\nport = 8082\n',
    )

    assert relabel_stale_vulkan_slots(kfd_present=True) == 1
    assert _device_of("nested") == "gpu-rocm"
    raw = _raw("nested")
    assert "slot" in raw and raw["slot"]["device"] == "gpu-rocm"


# ── kfd absent → cpu, loud warning ──────────────────────────────────────── #


def test_vulkan_slot_relabels_to_cpu_when_kfd_absent(tmp_hal0_home: str) -> None:
    _write_slot("brain", 'name = "brain"\ntype = "llm"\ndevice = "gpu-vulkan"\nport = 8089\n')

    assert relabel_stale_vulkan_slots(kfd_present=False) == 1
    assert _device_of("brain") == "cpu"


def test_cpu_fallback_logs_a_loud_warning(tmp_hal0_home: str, monkeypatch) -> None:
    """The cpu-fallback path must never be silent — it is a real
    operator-visible behavior change (#1867 rails)."""
    import hal0.updater.updater as updater_mod

    _write_slot("coder", 'name = "coder"\ntype = "llm"\ndevice = "gpu-vulkan"\nport = 8082\n')

    calls: list[tuple[str, dict]] = []
    original_warning = updater_mod.log.warning

    def _spy(event, **kwargs):
        calls.append((event, kwargs))
        return original_warning(event, **kwargs)

    monkeypatch.setattr(updater_mod.log, "warning", _spy)

    assert relabel_stale_vulkan_slots(kfd_present=False, job_id="job-cpu") == 1

    cpu_events = [c for c in calls if c[0] == "updater.slot_vulkan_relabeled_cpu_fallback"]
    assert len(cpu_events) == 1
    _event, fields = cpu_events[0]
    assert fields["slot"] == "coder"
    assert fields["old"] == "gpu-vulkan"
    assert fields["new"] == "cpu"
    assert fields["job_id"] == "job-cpu"
    assert "BEHAVIOR CHANGE" in fields["note"]


def test_rocm_relabel_logs_a_distinct_breadcrumb(tmp_hal0_home: str, monkeypatch) -> None:
    """The gpu-rocm relabel is also logged, distinct from the cpu-fallback
    event so an operator scanning the journal can tell the two apart."""
    import hal0.updater.updater as updater_mod

    _write_slot("embed", 'name = "embed"\ntype = "embedding"\ndevice = "gpu-vulkan"\nport = 8083\n')

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(updater_mod.log, "warning", lambda event, **kw: calls.append((event, kw)))

    assert relabel_stale_vulkan_slots(kfd_present=True, job_id="job-rocm") == 1

    rocm_events = [c for c in calls if c[0] == "updater.slot_vulkan_relabeled_rocm"]
    assert len(rocm_events) == 1
    _event, fields = rocm_events[0]
    assert fields["slot"] == "embed"
    assert fields["old"] == "gpu-vulkan"
    assert fields["new"] == "gpu-rocm"
    assert fields["job_id"] == "job-rocm"


# ── narrow scope: other keys / other devices untouched ──────────────────── #


def test_other_slot_fields_are_untouched(tmp_hal0_home: str) -> None:
    body = (
        'name = "agent"\n'
        'type = "llm"\n'
        'device = "gpu-vulkan"\n'
        'runtime = "container"\n'
        'profile = "chadrock-moe"\n'
        "port = 8081\n"
        "n_gpu_layers = 99\n"
        "\n"
        "[model]\n"
        'default = "some-model"\n'
    )
    _write_slot("agent", body)

    assert relabel_stale_vulkan_slots(kfd_present=True) == 1

    raw = _raw("agent")
    assert raw["device"] == "gpu-rocm"
    # everything else survives byte-for-byte in value (structural comparison
    # since write_toml_atomic round-trips through tomllib/tomli_w, same as
    # every other migration pass in this module).
    assert raw["name"] == "agent"
    assert raw["type"] == "llm"
    assert raw["runtime"] == "container"
    assert raw["profile"] == "chadrock-moe"
    assert raw["port"] == 8081
    assert raw["n_gpu_layers"] == 99
    assert raw["model"]["default"] == "some-model"


def test_non_vulkan_devices_are_left_alone(tmp_hal0_home: str) -> None:
    _write_slot("rocm-slot", 'name = "rocm-slot"\ndevice = "gpu-rocm"\n')
    _write_slot("cpu-slot", 'name = "cpu-slot"\ndevice = "cpu"\n')
    _write_slot("npu-slot", 'name = "npu-slot"\ndevice = "npu"\n')
    _write_slot("no-device-slot", 'name = "no-device-slot"\n')

    assert relabel_stale_vulkan_slots(kfd_present=True) == 0
    assert _device_of("rocm-slot") == "gpu-rocm"
    assert _device_of("cpu-slot") == "cpu"
    assert _device_of("npu-slot") == "npu"
    assert _device_of("no-device-slot") is None


def test_unreadable_slot_is_skipped_not_fatal(tmp_hal0_home: str) -> None:
    _write_slot("broken", "not [ valid toml")
    _write_slot("agent", 'name = "agent"\ndevice = "gpu-vulkan"\n')

    assert relabel_stale_vulkan_slots(kfd_present=True) == 1
    assert _device_of("agent") == "gpu-rocm"


# ── idempotency ──────────────────────────────────────────────────────────── #


def test_relabel_is_idempotent(tmp_hal0_home: str) -> None:
    _write_slot("agent", 'name = "agent"\ndevice = "gpu-vulkan"\n')

    assert relabel_stale_vulkan_slots(kfd_present=True) == 1
    assert _device_of("agent") == "gpu-rocm"
    # second run: device is now gpu-rocm, no longer matches the guard.
    assert relabel_stale_vulkan_slots(kfd_present=True) == 0
    assert _device_of("agent") == "gpu-rocm"


def test_relabel_is_idempotent_on_cpu_fallback(tmp_hal0_home: str) -> None:
    _write_slot("brain", 'name = "brain"\ndevice = "gpu-vulkan"\n')

    assert relabel_stale_vulkan_slots(kfd_present=False) == 1
    assert _device_of("brain") == "cpu"
    assert relabel_stale_vulkan_slots(kfd_present=False) == 0
    assert _device_of("brain") == "cpu"


def test_idempotent_second_run_logs_nothing_new(tmp_hal0_home: str, monkeypatch) -> None:
    import hal0.updater.updater as updater_mod

    _write_slot("agent", 'name = "agent"\ndevice = "gpu-vulkan"\n')

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(updater_mod.log, "warning", lambda event, **kw: calls.append((event, kw)))

    relabel_stale_vulkan_slots(kfd_present=True)
    first_run_calls = len(calls)
    assert first_run_calls == 1

    relabel_stale_vulkan_slots(kfd_present=True)
    assert len(calls) == first_run_calls  # no new log entries on the no-op re-run


# ── real host probe (no override) ───────────────────────────────────────── #


def test_default_probes_real_kfd_present(tmp_hal0_home: str, monkeypatch) -> None:
    """With no ``kfd_present`` override, the function calls the real
    :func:`hal0.providers._gpu.kfd_present` probe."""
    _write_slot("agent", 'name = "agent"\ndevice = "gpu-vulkan"\n')
    monkeypatch.setattr("hal0.providers._gpu.kfd_present", lambda: True)

    assert relabel_stale_vulkan_slots() == 1
    assert _device_of("agent") == "gpu-rocm"
