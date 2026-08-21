"""Upgrade migration: relabel legacy ``device = "gpu-vulkan"`` slot TOMLs.

Covers :func:`hal0.updater.updater.relabel_stale_vulkan_slots` — the #1924
follow-up to PR #1923 (#1888, Vulkan LLM lane retirement). PR #1923 relabeled
the SEED slot TOMLs the installer ships from ``gpu-vulkan`` to ``gpu-rocm``,
but deliberately left slot TOMLs already materialised on an existing install
untouched, so an updated box refuses to load them under the new
``require_kfd_for_gpu_slot`` preflight guard until an operator manually
relabels them. This migration does that relabel automatically, AMD-only
(matching ``require_kfd_for_gpu_slot``'s own scope exactly):

* not an AMD host (no amdgpu kernel driver bound) → left alone entirely.
  ``require_kfd_for_gpu_slot`` never gates a ``gpu-vulkan`` slot there either,
  so it was never broken by #1923 and needs no migration.
* AMD host, ``/dev/kfd`` present (working ROCm compute node) → ``gpu-rocm``,
  matching PR #1923's own seed relabel.
* AMD host, ``/dev/kfd`` absent → ``cpu``, logged loudly since it is a
  genuine operator-visible behavior change (the slot drops off the GPU).

#1948 narrowed the premise: the Vulkan LLM lane is supported again on a
Vulkan-validated runner image, so a ``gpu-vulkan`` slot resolving such an image
LOADS and must not be relabeled — that would be a reverse migration of a
working configuration, and on a kfd-less box it would push a functioning GPU
slot onto the CPU. Slots on an unvalidated image still refuse to load and are
still rescued exactly as before.

Only the ``device`` key is ever touched (#1867 rails: narrow scope, log every
mutation, idempotent).
"""

from __future__ import annotations

import tomllib

from hal0.config.paths import slots_config_dir
from hal0.updater.updater import relabel_stale_vulkan_slots


#: The runner image whose Vulkan backend carries #1888. Named LITERALLY, never
#: as ``DEFAULT_ROCMFPX_IMAGE``: the default pin is a moving target, and once
#: it moves to a Vulkan-validated image every fixture below that relies on
#: "the default is broken" would silently invert and stop exercising the
#: rescue path at all.
STALE_LLAMA_IMAGE = "ghcr.io/hal0ai/hal0-rocmfpx:ade07ba"


def _write_slot(name: str, body: str) -> None:
    """Write a slot TOML into the fixture's slots dir.

    Every ``gpu-vulkan`` fixture that does not pin an image of its own gets
    :data:`STALE_LLAMA_IMAGE` injected. That is what this migration's whole
    population looks like — slots materialised on a release whose runner
    image could not serve the Vulkan lane — and pinning it explicitly keeps
    each fixture's meaning fixed regardless of what the default pin becomes.
    A fixture that DOES pin an image is left exactly as written, which is how
    the #1948 "validated image is not migrated" cases opt out.
    """
    if 'device = "gpu-vulkan"' in body and "image_pin" not in body:
        body = body.rstrip("\n") + f'\nimage_pin = "{STALE_LLAMA_IMAGE}"\n'
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


# ── AMD-only scope ───────────────────────────────────────────────────────── #


def test_non_amd_host_is_a_full_noop(tmp_hal0_home: str) -> None:
    """require_kfd_for_gpu_slot never gates a gpu-vulkan slot on a non-AMD
    host (Intel iGPU, NVIDIA without CDI) — it has no /dev/kfd by design and
    keeps working post-#1923. Relabeling it here would be a pure regression:
    a working GPU slot silently downgraded to CPU because kfd_present() is
    (correctly) False on hardware that was never supposed to have kfd."""
    _write_slot("intel-tts", 'name = "intel-tts"\ndevice = "gpu-vulkan"\nport = 8090\n')

    assert relabel_stale_vulkan_slots(amd_host=False, kfd_present=False) == 0
    assert _device_of("intel-tts") == "gpu-vulkan"


def test_non_amd_host_logs_nothing(tmp_hal0_home: str, monkeypatch) -> None:
    import hal0.updater.updater as updater_mod

    _write_slot("intel-tts", 'name = "intel-tts"\ndevice = "gpu-vulkan"\n')

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(updater_mod.log, "warning", lambda event, **kw: calls.append((event, kw)))

    relabel_stale_vulkan_slots(amd_host=False, kfd_present=False)
    assert calls == []


def test_default_probes_real_amd_host(tmp_hal0_home: str, monkeypatch) -> None:
    """With no ``amd_host`` override, the function calls the real
    :func:`hal0.providers._gpu.host_is_amd_gpu` probe."""
    _write_slot("agent", 'name = "agent"\ndevice = "gpu-vulkan"\n')
    monkeypatch.setattr("hal0.providers._gpu.host_is_amd_gpu", lambda: False)

    assert relabel_stale_vulkan_slots(kfd_present=True) == 0
    assert _device_of("agent") == "gpu-vulkan"


# ── runtime scope: only llama.cpp-backed slots, never Kokoro/ComfyUI/etc ── #
#
# capabilities/catalog.py:429-431 deliberately KEEPS gpu-vulkan for the
# non-llama runtimes (Kokoro TTS, whisper.cpp/Moonshine STT, ComfyUI) — they
# run genuinely-Vulkan images, unlike llama.cpp's unified ROCmFPX runner. A
# migration that matches on the bare device string alone would relabel those
# slots too:
#   - kfd present: gpu-rocm is a label their image can't honor
#     (gpu_visibility_env swaps GGML_VK_VISIBLE_DEVICES -> HIP_VISIBLE_DEVICES,
#     dropping any gpu_index pin).
#   - kfd absent: demoting to cpu strips /dev/dri from a working Vulkan slot.
# container.py's _spec_provider_for is the authoritative "is this slot
# llama.cpp-backed" discriminator (None == the default llama-server GPU
# provider; non-None == Kokoro/Moonshine/ComfyUI/FLM/Qwen3TTS) — this
# migration must gate on it and leave every non-llama slot COMPLETELY
# untouched: no relabel, no warning, no log line, on EITHER kfd axis. The
# kfd-absent non-llama case (require_kfd_for_gpu_slot itself over-firing for
# non-llama runtimes) is tracked separately as #1941 and must NOT be
# addressed here.


def test_kokoro_slot_survives_untouched_kfd_present(tmp_hal0_home: str) -> None:
    _write_slot(
        "voice",
        'name = "voice"\ntype = "tts"\ndevice = "gpu-vulkan"\nport = 8087\n',
    )

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 0
    assert _device_of("voice") == "gpu-vulkan"


def test_kokoro_slot_survives_untouched_kfd_absent(tmp_hal0_home: str) -> None:
    _write_slot(
        "voice",
        'name = "voice"\ntype = "tts"\ndevice = "gpu-vulkan"\nport = 8087\n',
    )

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=False) == 0
    assert _device_of("voice") == "gpu-vulkan"


def test_comfyui_slot_survives_untouched_kfd_present(tmp_hal0_home: str) -> None:
    _write_slot(
        "imggen",
        'name = "imggen"\ntype = "image"\ndevice = "gpu-vulkan"\nport = 8188\n',
    )

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 0
    assert _device_of("imggen") == "gpu-vulkan"


def test_comfyui_slot_survives_untouched_kfd_absent(tmp_hal0_home: str) -> None:
    _write_slot(
        "imggen",
        'name = "imggen"\ntype = "image"\ndevice = "gpu-vulkan"\nport = 8188\n',
    )

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=False) == 0
    assert _device_of("imggen") == "gpu-vulkan"


def test_transcription_slot_survives_untouched(tmp_hal0_home: str) -> None:
    """type=transcription with no NPU device dispatches to Moonshine
    (whisper.cpp-shaped), never llama-server."""
    _write_slot(
        "stt",
        'name = "stt"\ntype = "transcription"\ndevice = "gpu-vulkan"\nport = 8085\n',
    )

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 0
    assert _device_of("stt") == "gpu-vulkan"
    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=False) == 0
    assert _device_of("stt") == "gpu-vulkan"


def test_non_llama_slots_log_nothing_on_either_kfd_axis(tmp_hal0_home: str, monkeypatch) -> None:
    import hal0.updater.updater as updater_mod

    _write_slot("voice", 'name = "voice"\ntype = "tts"\ndevice = "gpu-vulkan"\n')
    _write_slot("imggen", 'name = "imggen"\ntype = "image"\ndevice = "gpu-vulkan"\n')

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(updater_mod.log, "warning", lambda event, **kw: calls.append((event, kw)))

    relabel_stale_vulkan_slots(amd_host=True, kfd_present=True)
    relabel_stale_vulkan_slots(amd_host=True, kfd_present=False)

    assert calls == []


def test_llama_slot_still_relabels_alongside_untouched_non_llama_slot(
    tmp_hal0_home: str,
) -> None:
    """Sanity check that the runtime gate is scoped correctly: a genuine
    llama.cpp slot (no profile, plain type=llm) in the SAME directory as a
    non-llama slot still relabels, while its neighbor does not."""
    _write_slot("agent", 'name = "agent"\ntype = "llm"\ndevice = "gpu-vulkan"\nport = 8081\n')
    _write_slot("voice", 'name = "voice"\ntype = "tts"\ndevice = "gpu-vulkan"\nport = 8087\n')

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 1
    assert _device_of("agent") == "gpu-rocm"
    assert _device_of("voice") == "gpu-vulkan"


def test_qwen3tts_profile_slot_survives_untouched(tmp_hal0_home: str) -> None:
    """A slot dispatched to a non-llama runtime via its PROFILE rather than
    its device/type (qwen3-tts resolves to the qwen3tts runtime_family
    ahead of the generic type="tts" -> Kokoro fallback in
    _spec_provider_for) must be left alone too, on both kfd axes. Reviewer
    manually verified this path on the earlier fix; this pins it as a
    regression guard."""
    body = (
        'name = "voice2"\ntype = "llm"\nprofile = "qwen3-tts"\ndevice = "gpu-vulkan"\nport = 8091\n'
    )
    _write_slot("voice2", body)

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 0
    assert _device_of("voice2") == "gpu-vulkan"
    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=False) == 0
    assert _device_of("voice2") == "gpu-vulkan"


def test_runtime_resolution_error_leaves_slot_untouched_and_logs(
    tmp_hal0_home: str, monkeypatch
) -> None:
    """If _spec_provider_for itself raises (an unrecognized runtime_family —
    see UnknownRuntimeFamilyError in container.py), the migration must not
    optimistically fall through to "must be llama.cpp, relabel it": the
    slot is left byte-identical, the pass returns cleanly (no crash), and
    the failure is visible in the log rather than swallowed. This is the
    one branch none of the other tests exercise — a mutation that turned
    the except's `continue` into a relabel would pass every other test in
    this file."""
    import hal0.updater.updater as updater_mod

    body = 'name = "agent"\ntype = "llm"\ndevice = "gpu-vulkan"\nport = 8081\n'
    _write_slot("agent", body)
    toml_path = slots_config_dir() / "agent.toml"
    before = toml_path.read_bytes()

    def _boom(slot_cfg: dict) -> None:
        raise RuntimeError("no provider registered for runtime family 'bogus'")

    monkeypatch.setattr("hal0.providers.container._spec_provider_for", _boom)

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(updater_mod.log, "warning", lambda event, **kw: calls.append((event, kw)))

    result = relabel_stale_vulkan_slots(amd_host=True, kfd_present=True)

    assert result == 0
    assert toml_path.read_bytes() == before  # byte-identical — no write happened
    assert _device_of("agent") == "gpu-vulkan"  # unchanged

    unresolved = [c for c in calls if c[0] == "updater.vulkan_migration_slot_runtime_unresolved"]
    assert len(unresolved) == 1
    _event, fields = unresolved[0]
    assert fields["slot"] == "agent"
    assert "bogus" in fields["error"]


# ── kfd present → gpu-rocm (AMD host) ────────────────────────────────────── #


def test_vulkan_slot_relabels_to_rocm_when_kfd_present(tmp_hal0_home: str) -> None:
    _write_slot("agent", 'name = "agent"\ntype = "llm"\ndevice = "gpu-vulkan"\nport = 8081\n')

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 1
    assert _device_of("agent") == "gpu-rocm"


def test_nested_slot_table_relabels_to_rocm_when_kfd_present(tmp_hal0_home: str) -> None:
    _write_slot(
        "nested",
        '[slot]\nname = "nested"\ndevice = "gpu-vulkan"\nport = 8082\n',
    )

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 1
    assert _device_of("nested") == "gpu-rocm"
    raw = _raw("nested")
    assert "slot" in raw and raw["slot"]["device"] == "gpu-rocm"


# ── kfd absent → cpu, loud warning (AMD host) ────────────────────────────── #


def test_vulkan_slot_relabels_to_cpu_when_kfd_absent(tmp_hal0_home: str) -> None:
    _write_slot("brain", 'name = "brain"\ntype = "llm"\ndevice = "gpu-vulkan"\nport = 8089\n')

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=False) == 1
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

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=False, job_id="job-cpu") == 1

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

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True, job_id="job-rocm") == 1

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

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 1

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

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 0
    assert _device_of("rocm-slot") == "gpu-rocm"
    assert _device_of("cpu-slot") == "cpu"
    assert _device_of("npu-slot") == "npu"
    assert _device_of("no-device-slot") is None


def test_unreadable_slot_is_skipped_not_fatal(tmp_hal0_home: str) -> None:
    _write_slot("broken", "not [ valid toml")
    _write_slot("agent", 'name = "agent"\ndevice = "gpu-vulkan"\n')

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 1
    assert _device_of("agent") == "gpu-rocm"


# ── idempotency ──────────────────────────────────────────────────────────── #


def test_relabel_is_idempotent(tmp_hal0_home: str) -> None:
    _write_slot("agent", 'name = "agent"\ndevice = "gpu-vulkan"\n')

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 1
    assert _device_of("agent") == "gpu-rocm"
    # second run: device is now gpu-rocm, no longer matches the guard.
    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 0
    assert _device_of("agent") == "gpu-rocm"


def test_relabel_is_idempotent_on_cpu_fallback(tmp_hal0_home: str) -> None:
    _write_slot("brain", 'name = "brain"\ndevice = "gpu-vulkan"\n')

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=False) == 1
    assert _device_of("brain") == "cpu"
    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=False) == 0
    assert _device_of("brain") == "cpu"


def test_idempotent_second_run_logs_nothing_new(tmp_hal0_home: str, monkeypatch) -> None:
    import hal0.updater.updater as updater_mod

    _write_slot("agent", 'name = "agent"\ndevice = "gpu-vulkan"\n')

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(updater_mod.log, "warning", lambda event, **kw: calls.append((event, kw)))

    relabel_stale_vulkan_slots(amd_host=True, kfd_present=True)
    first_run_calls = len(calls)
    assert first_run_calls == 1

    relabel_stale_vulkan_slots(amd_host=True, kfd_present=True)
    assert len(calls) == first_run_calls  # no new log entries on the no-op re-run


# ── real host probes (no overrides) ──────────────────────────────────────── #


def test_default_probes_real_kfd_present(tmp_hal0_home: str, monkeypatch) -> None:
    """With no ``kfd_present`` override, the function calls the real
    :func:`hal0.providers._gpu.kfd_present` probe."""
    _write_slot("agent", 'name = "agent"\ndevice = "gpu-vulkan"\n')
    monkeypatch.setattr("hal0.providers._gpu.host_is_amd_gpu", lambda: True)
    monkeypatch.setattr("hal0.providers._gpu.kfd_present", lambda: True)

    assert relabel_stale_vulkan_slots() == 1
    assert _device_of("agent") == "gpu-rocm"


# ── #1948: a slot on a Vulkan-validated image is NOT migrated ───────────── #


def _vulkan_slot_on(image: str) -> str:
    return f'name = "utility"\ndevice = "gpu-vulkan"\nport = 8082\nimage_pin = "{image}"\n'


def test_slot_pinned_to_a_vulkan_validated_image_is_left_alone(tmp_hal0_home: str) -> None:
    """The re-enabled lane must survive an update.

    Without this, every ``hal0 update`` would silently undo a deliberate
    ``gpu-vulkan`` choice — the migration would see the label, not the image.
    """
    from hal0.config.schema import VULKAN_FIXED_IMAGE

    _write_slot("utility", _vulkan_slot_on(VULKAN_FIXED_IMAGE))

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 0
    assert _device_of("utility") == "gpu-vulkan"


def test_it_is_left_alone_on_a_kfd_less_box_too(tmp_hal0_home: str) -> None:
    """The ct151 shape, and the case with the most to lose: Vulkan is the only
    lane this box has, so relabeling to ``cpu`` would strand the GPU."""
    from hal0.config.schema import VULKAN_FIXED_IMAGE

    _write_slot("utility", _vulkan_slot_on(VULKAN_FIXED_IMAGE))

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=False) == 0
    assert _device_of("utility") == "gpu-vulkan"


def test_slot_pinned_to_an_unvalidated_image_is_still_rescued(tmp_hal0_home: str) -> None:
    """The rescue this pass exists for is untouched: the ade07ba lineage's
    Vulkan backend emits invalid tokens, the slot refuses to load, and the
    migration moves it to a lane that works."""
    _write_slot("utility", _vulkan_slot_on(STALE_LLAMA_IMAGE))

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 1
    assert _device_of("utility") == "gpu-rocm"


def test_an_unresolvable_image_fails_closed_and_is_rescued(tmp_hal0_home: str, monkeypatch) -> None:
    """Fail-closed: if the image cannot be resolved at all, the slot has not
    been established safe, so the conservative relabel still happens."""
    import hal0.updater.updater as updater_mod

    def _boom(*_a: object, **_kw: object) -> str:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr("hal0.providers.container._resolve_image_ref", _boom)
    _write_slot("utility", 'name = "utility"\ndevice = "gpu-vulkan"\nport = 8082\n')

    assert updater_mod.relabel_stale_vulkan_slots(amd_host=True, kfd_present=True) == 1
    assert _device_of("utility") == "gpu-rocm"


def test_it_never_relabels_gpu_rocm_back_to_vulkan(tmp_hal0_home: str) -> None:
    """No reverse migration, ever. A box that already ran this pass keeps the
    device it was given — the re-enabled lane is offered to new slots, not
    forced onto existing ones."""
    _write_slot("utility", 'name = "utility"\ndevice = "gpu-rocm"\nport = 8082\n')

    assert relabel_stale_vulkan_slots(amd_host=True, kfd_present=False) == 0
    assert _device_of("utility") == "gpu-rocm"
