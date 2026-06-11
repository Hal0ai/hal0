# Phase B: Voice — Kokoro TTS Container Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The `tts` slot runs the hal0 kokoro toolbox image as a podman container (`hal0-slot@tts`), `/v1/audio/speech` dispatches to it through the gateway, and the slot's current error state is gone — second slot off Lemonade.

**Architecture:** Reuse the Phase A seam: a new `KokoroProvider.container_spec()` builds a generic `ContainerSpec` (no GPU devices, weights mount, port publish) rendered by `_render_unit_from_spec`. `load_sync` grows a small spec-provider dispatch (npu→FLM, kokoro→Kokoro) instead of more inline branches. Dispatch gets a path rule: `/audio/speech` → `tts` slot (path-keyed, immune to the model-id alias mismatch). Lemonade keeps rerank/utility (Phase C) and observability (Phase E).

**Tech Stack:** Python 3.12/FastAPI/pydantic v2, podman+systemd, `ghcr.io/hal0ai/hal0-toolbox-kokoro:v1` (kokoro-onnx FastAPI server, digest-pinned in manifest.json), pytest + γ-suite.

**Spec:** `docs/superpowers/specs/2026-06-10-lemonade-removal-container-switchover-design.md` §3 (tts), §12-B.
**Worktree:** `/home/halo/dev/wt-phase-b` (branch `feat/phase-b-voice`, base 28d4312). `.venv/bin/python -m pytest`. Commits `git commit -s`.

**Verified facts (research 2026-06-11, don't re-derive):**
- Image `ghcr.io/hal0ai/hal0-toolbox-kokoro:v1` published + digest-pinned (`manifest.json:49-56`); NOT yet pulled on CT105.
- Server (`packaging/toolbox/kokoro/kokoro_server.py`): `GET /health` (`{status, model_loaded, default_voice}`), `GET /v1/models` (hardcoded id `"kokoro"`), `POST /v1/audio/speech` (OpenAI-compat), `GET /v1/audio/voices`. Default port 8090; accepts `--port`, `--host`, `--model_path` CLI flags; NO `--alias` support. mp3/opus paths shell to ffmpeg via tempfile (needs writable /tmp — podman default OK).
- Weights already on CT105: `/mnt/ai-models/local/kokoro-v1/kokoro-onnx/{kokoro-v1.0.onnx,voices-v1.0.bin}` (311+27 MB). Without `--model_path` the server auto-downloads to `$HF_HOME/kokoro-onnx/`.
- Live `tts.toml`: `type="tts"`, `device="cpu"`, `backend="cpu"`, `model.default="kokoro-v1"`, `[server] port=8084`, NO profile/runtime → lemonade path → error state (stale `vibevoice-1.5b` HTTP 500 in state.json).
- `SELF_MANAGED_PROVIDERS` (state.py:124) includes `"kokoro"`; `provider_requires_model("kokoro") is False` — modelless-ready guard already permits READY.
- Dispatch today: `/v1/audio/speech` → `_dispatch_and_forward` → no TTS rule anywhere → legacy fallback Rule 7 lands on `chat`. Container Step-0 preemption would need model-id match, but the server advertises `"kokoro"` while clients send `"kokoro-v1"`/`"tts"` → path rule is the robust fix.
- `_render_unit` (llama-shaped) enumerates GPU devices unconditionally — do NOT use it for kokoro; use `_render_unit_from_spec` (A3 seam).
- `_resolve_model_path` raises on path-less models — kokoro must NOT flow through it; weights path goes in the spec command via profile flags.
- #485: STT half covered (Phase A trio + lemond whispercpp for non-NPU); rerank is broken end-to-end at lemond itself → **Phase C, out of scope here**.
- tests: `tests/api/test_v1_audio.py` TTS happy path seeds the `chat` slot as fake TTS upstream with `model="tts"`.

---

### Task B1: `kokoro-cpu` seed profile

**Files:**
- Modify: `src/hal0/config/schema.py` (SEED_PROFILES)
- Modify: `installer/etc-hal0/profiles.toml`
- Test: extend `tests/config/test_schema_npu.py`-style coverage in `tests/config/test_profiles.py` (seed count 4→5 + parity test already guards file/SEED sync)

- [ ] **Step 1: failing test** — in `tests/config/test_profiles.py` add:

```python
def test_kokoro_cpu_seed_profile() -> None:
    prof = SEED_PROFILES["kokoro-cpu"]
    assert prof["image"] == "ghcr.io/hal0ai/hal0-toolbox-kokoro:v1"
    assert "--model_path" in prof["flags"]
    assert prof["mtp"] is False
```

Update the existing seed-count assertion (5) and `test_returns_seed_profiles`/`test_profiles_route` count in `tests/api/test_profiles_route.py` (4→5, rename-safe — it asserts `len(SEED_PROFILES)` plus a literal; bump the literal).

- [ ] **Step 2: run** — `.venv/bin/python -m pytest tests/config/test_profiles.py -x -q` → KeyError.

- [ ] **Step 3: implement** — `SEED_PROFILES` entry:

```python
    "kokoro-cpu": {
        "image": "ghcr.io/hal0ai/hal0-toolbox-kokoro:v1",
        "flags": "--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx",
        "mtp": False,
    },
```

Mirror into `installer/etc-hal0/profiles.toml`:

```toml
[profile.kokoro-cpu]
# CPU TTS slot (kokoro-onnx). No GPU devices. Flags feed kokoro_server.py;
# --model_path points at the shared model store so no download on start.
image = "ghcr.io/hal0ai/hal0-toolbox-kokoro:v1"
flags = "--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx"
mtp   = false
```

- [ ] **Step 4:** `.venv/bin/python -m pytest tests/config tests/api/test_profiles_route.py -q` → PASS.
- [ ] **Step 5:** `git commit -s -m "feat(config): kokoro-cpu seed profile"`

---

### Task B2: `KokoroProvider.container_spec()`

**Files:**
- Create: `src/hal0/providers/kokoro.py`
- Test: `tests/providers/test_kokoro_container_spec.py` (mirror `tests/providers/test_flm_container_spec.py` style)

- [ ] **Step 1: failing tests:**

```python
"""Kokoro TTS container spec (Phase B)."""

from typing import Any

from hal0.providers.kokoro import KokoroProvider


def _slot_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "tts",
        "port": 8084,
        "device": "cpu",
        "type": "tts",
        "runtime": "container",
        "profile": "kokoro-cpu",
        "model": {"default": "kokoro-v1"},
    }
    base.update(overrides)
    return base


def test_spec_has_no_gpu_devices_or_groups() -> None:
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    assert spec.devices == []
    assert spec.group_add == []


def test_spec_command_carries_port_host_and_model_path() -> None:
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    assert "--port" in spec.command
    assert spec.command[spec.command.index("--port") + 1] == "8084"
    assert "--host" in spec.command
    assert spec.command[spec.command.index("--host") + 1] == "0.0.0.0"
    assert "--model_path" in spec.command
    assert (
        spec.command[spec.command.index("--model_path") + 1]
        == "/mnt/ai-models/local/kokoro-v1/kokoro-onnx"
    )


def test_spec_mounts_model_store_ro_and_publishes_loopback() -> None:
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    assert ("/mnt/ai-models", "/mnt/ai-models:ro") in spec.mounts or (
        "/mnt/ai-models",
        "/mnt/ai-models",
    ) in spec.mounts  # match ContainerSpec mount convention — see Step 3 note
    assert spec.port == 8084
    assert spec.network_mode == ""


def test_spec_security_opts_for_lxc() -> None:
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    assert "apparmor=unconfined" in spec.security_opt
    assert "seccomp=unconfined" in spec.security_opt
```

NOTE on mounts: read `providers/base.py` `ContainerSpec.mounts` — A3's renderer emits `--volume={src}:{dst}`. If read-only needs a third component, check how FLM/GPU paths express `:ro` (the GPU `_render_unit` hardcodes `:ro` in the volume string). Express ro the way the renderer supports (e.g. `dst="/mnt/ai-models:ro"` if that's the only way, or extend `ContainerSpec`/renderer with an `ro` mount triple — prefer the smallest correct change and pin it with the test).

- [ ] **Step 2: run** — ImportError expected.

- [ ] **Step 3: implement** `src/hal0/providers/kokoro.py`:

```python
"""Kokoro TTS provider — builds the ContainerSpec for the CPU toolbox image.

The image (ghcr.io/hal0ai/hal0-toolbox-kokoro:v1) wraps kokoro-onnx in a
FastAPI server: /health, /v1/models, /v1/audio/speech, /v1/audio/voices.
Self-managed weights (SELF_MANAGED_PROVIDERS includes "kokoro") — the
--model_path flag points at the shared store; no registry GGUF resolution.
"""

from typing import Any

from hal0.providers.base import ContainerSpec

_MODEL_STORE = "/mnt/ai-models"


class KokoroProvider:
    name = "kokoro"

    def container_spec(
        self, slot_cfg: dict[str, Any], model_info: dict[str, Any]
    ) -> ContainerSpec:
        port = int(slot_cfg.get("port") or 8084)
        # Profile flags carry --model_path; resolve the profile the same way
        # ContainerProvider does (load_profiles_config) so operators can
        # override the path in profiles.toml without code changes.
        from hal0.config.loader import load_profiles_config
        from hal0.config.schema import resolve_profile_flags
        import shlex

        profile_name = str(slot_cfg.get("profile") or "kokoro-cpu")
        catalog = load_profiles_config()
        profile = catalog.profile[profile_name]
        flag_tokens = shlex.split(resolve_profile_flags(profile)) if resolve_profile_flags(profile).strip() else []

        command = ["--host", "0.0.0.0", "--port", str(port), *flag_tokens]
        return ContainerSpec(
            image=profile.image,
            command=command,
            env={},
            mounts=[(_MODEL_STORE, f"{_MODEL_STORE}:ro")],  # adapt per Step 1 note
            devices=[],
            cap_add=[],
            security_opt=["apparmor=unconfined", "seccomp=unconfined"],
            group_add=[],
            port=port,
            network_mode="",
            extra_args=[],
        )
```

(Adapt: check the image ENTRYPOINT before finalizing `command` — `ssh hal0 'podman pull -q ghcr.io/hal0ai/hal0-toolbox-kokoro:v1 && podman image inspect ghcr.io/hal0ai/hal0-toolbox-kokoro:v1 --format "{{.Config.Entrypoint}} {{.Config.Cmd}}"'`. If the ENTRYPOINT already includes the server binary, `command` is flags-only as drafted; if not, prepend what's needed. Record the inspect output in your report.)

- [ ] **Step 4:** `.venv/bin/python -m pytest tests/providers -q` → all PASS.
- [ ] **Step 5:** `git commit -s -m "feat(providers): KokoroProvider container spec (CPU TTS)"`

---

### Task B3: `load_sync` spec-provider dispatch (replace the npu special-case)

**Files:**
- Modify: `src/hal0/providers/container.py` (`load_sync` — the `device == "npu"` branch from A3)
- Test: `tests/providers/test_container_spec_dispatch.py` (create)

- [ ] **Step 1: failing tests:**

```python
"""load_sync routes slots to their spec provider (FLM/NPU, Kokoro/TTS)."""

import shlex
from typing import Any
from unittest.mock import patch

from hal0.providers.container import ContainerProvider


def _exec_start(unit_text: str) -> list[str]:
    for line in unit_text.splitlines():
        if line.startswith("ExecStart="):
            return shlex.split(line[len("ExecStart="):])
    raise AssertionError("ExecStart not found")


def test_tts_kokoro_slot_renders_spec_unit() -> None:
    provider = ContainerProvider()
    slot_cfg = {
        "name": "tts", "port": 8084, "device": "cpu", "type": "tts",
        "runtime": "container", "profile": "kokoro-cpu",
        "model": {"default": "kokoro-v1"},
    }
    with (
        patch.object(provider, "_write_and_start_unit") as start,
        patch("hal0.providers.container._container_runtime", return_value="/usr/bin/docker"),
    ):
        provider.load_sync(slot_cfg, {"_model_key": "kokoro-v1"})
    argv = _exec_start(start.call_args.args[1])
    assert "--model_path" in argv          # kokoro spec path, not llama --model
    assert "--device=/dev/kfd" not in argv  # no GPU enumeration for CPU spec
    assert "--publish=127.0.0.1:8084:8084" in argv


def test_npu_slot_still_renders_flm_spec() -> None:
    # regression: Phase A behavior unchanged by the dispatch refactor
    ...  # copy the existing npu branch test from tests/providers/test_container_npu.py
         # (test_npu_slot_routes_through_flm_spec) and keep BOTH files' versions green


def test_gpu_slot_unaffected() -> None:
    ...  # profile=moe-rocmfp4 slot still goes down the llama _render_unit path
         # (assert --model in argv / spec markers absent) — mirror existing test style
```

- [ ] **Step 2: run** — kokoro test fails (npu-only branch).

- [ ] **Step 3: implement.** In `load_sync`, replace the inline npu branch with a dispatch:

```python
        spec_provider = _spec_provider_for(slot_cfg)
        if spec_provider is not None:
            tag = (
                model_info.get("flm_tag")
                or model_info.get("_model_key")
                or (slot_cfg.get("model") or {}).get("default")
            )
            if str(slot_cfg.get("device", "")) == "npu" and not tag:
                raise ValueError("npu slot has no FLM model tag — set [model].default")
            spec = spec_provider.container_spec(slot_cfg, model_info)
            unit_text = _render_unit_from_spec(
                str(slot_cfg["name"]), spec, runtime_bin=_container_runtime()
            )
            self._write_and_start_unit(str(slot_cfg["name"]), unit_text)
            return
```

with, module-level:

```python
def _spec_provider_for(slot_cfg: dict[str, Any]) -> Any | None:
    """Spec-building provider for non-llama container slots, or None.

    llama-server slots (GPU profiles) use the flag-bundle _render_unit path;
    FLM (NPU) and Kokoro (CPU TTS) know their own argv and build a
    ContainerSpec rendered by _render_unit_from_spec. ComfyUI joins in
    Phase D.
    """
    if str(slot_cfg.get("device", "")) == "npu":
        from hal0.providers.flm import FLMProvider

        return FLMProvider()
    if str(slot_cfg.get("type", "")) == "tts" or str(slot_cfg.get("profile", "")) == "kokoro-cpu":
        from hal0.providers.kokoro import KokoroProvider

        return KokoroProvider()
    return None
```

(Read the current A3 branch first — preserve its exact loud-fail and logging behavior; the npu tag check above mirrors it. Keep `tests/providers/test_container_npu.py` green UNCHANGED — that's the regression suite for this refactor.)

- [ ] **Step 4:** `.venv/bin/python -m pytest tests/providers tests/slots -q` → all PASS.
- [ ] **Step 5:** `git commit -s -m "feat(providers): spec-provider dispatch in load_sync (kokoro joins flm)"`

---

### Task B4: Dispatcher — `/audio/speech` routes to the tts slot

**Files:**
- Modify: `src/hal0/dispatcher/proxy.py` (legacy fallback path rules) and/or `src/hal0/dispatcher/router.py` `_default_for_path` (read both first — there are TWO path-default mechanisms: `_RERANK_DEFAULT`/`_EMBED_DEFAULT` in router.py `_default_for_path` and `_EMBED_PATHS` in proxy.py; add TTS to BOTH the same way embed/rerank are handled, so the container Step-0 preemption AND the legacy fallback agree)
- Test: `tests/dispatcher/test_tts_path_routing.py` (create) + update `tests/api/test_v1_audio.py` happy path

- [ ] **Step 1: failing tests:**

```python
"""/v1/audio/speech path-routes to the tts slot regardless of model id."""

# Mirror the fixture style of the existing dispatcher path-default tests
# (find the tests covering _RERANK_DEFAULT/_EMBED_DEFAULT routing and clone).


def test_audio_speech_path_defaults_to_tts_slot() -> None:
    # request path /v1/audio/speech, body model="kokoro-v1" (NOT the slot name,
    # NOT the served id) → dispatcher candidate/upstream == "tts"
    ...


def test_audio_speech_container_upstream_preempts() -> None:
    # tts registered as container upstream (kind=remote, port 8084) → dispatch
    # forwards there; assert via the upstream-selection seam the suite uses
    ...
```

Update `tests/api/test_v1_audio.py::test_audio_speech_happy_path` (and `_seed_tts_upstream`) to seed a `tts` upstream instead of piggybacking on `chat`, and add a case where `model="kokoro-v1"` (registry id) still lands on tts.

- [ ] **Step 2: run** — fails (path falls through to chat).

- [ ] **Step 3: implement** — in router.py `_default_for_path`, add `_TTS_DEFAULT = "tts"` for paths ending `/audio/speech`; in proxy.py add `_TTS_PATHS = ("/audio/speech",)` → `candidate = "tts"` rule placed BEFORE the chat fallback (mirror the `_EMBED_PATHS` rule structure exactly, including comment style). Do NOT touch `/audio/transcriptions` (trio/STT handled in Phase A).

- [ ] **Step 4:** `.venv/bin/python -m pytest tests/dispatcher tests/api/test_v1_audio.py -q` → PASS.
- [ ] **Step 5:** `git commit -s -m "feat(dispatcher): /audio/speech path-routes to the tts slot"`

---

### Task B5: Seed `tts.toml` + state-mismatch reconciliation

**Files:**
- Create: `installer/etc-hal0/slots/tts.toml`
- Modify: `installer/install.sh` (extend the A10 seed block — generalize the single-file cp into a small loop over `npu.toml tts.toml`, same no-clobber + bundle-incomplete guards)
- Test: extend `tests/config/test_schema_npu.py`'s seed-validation pattern with `test_seed_tts_toml_validates` (new sibling test; consider renaming the file's seed section comment, not the file)

- [ ] **Step 1: failing test:**

```python
def test_seed_tts_toml_validates() -> None:
    raw = tomllib.loads(
        (_REPO_ROOT / "installer/etc-hal0/slots/tts.toml").read_text(encoding="utf-8")
    )
    slot = SlotConfig.model_validate(raw)
    assert slot.runtime == "container"
    assert slot.profile == "kokoro-cpu"
    assert slot.device == "cpu"
    assert slot.port == 8084
```

(reuse the `_REPO_ROOT`/path helper the A10 test established.)

- [ ] **Step 2:** FileNotFoundError.

- [ ] **Step 3:** seed file:

```toml
# TTS slot — kokoro-onnx in a podman container (hal0-slot@tts), CPU-only.
name = "tts"
type = "tts"
device = "cpu"
runtime = "container"
profile = "kokoro-cpu"
enabled = true
port = 8084

[model]
default = "kokoro-v1"
```

install.sh: convert the A10 single-file block into `for seed in npu tts; do ... done` preserving per-file no-clobber + `die` guard semantics (existing A10 messages/behavior must not regress — `bash -n` + re-read the block).

- [ ] **Step 4:** `.venv/bin/python -m pytest tests/config -q` + `bash -n installer/install.sh` → PASS.
- [ ] **Step 5:** `git commit -s -m "feat(installer): seed containerized tts slot TOML"`

---

### Task B6: Gate, PR, CT105 deploy + e2e

- [ ] **Step 1: full gate** — `.venv/bin/python -m pytest tests/ -q --ignore=tests/harness` (expect: the hermes-provision docker-smoke test may fail env-dependently — pre-existing, note it), `ruff check src tests`, `ruff format --check src tests`, `cd ui && npm run build`.
- [ ] **Step 2: PR** — push `feat/phase-b-voice`, `gh pr create --head feat/phase-b-voice` (title "feat: Phase B — voice/TTS kokoro container (lemonade-removal spec)"), CI green, squash-merge.
- [ ] **Step 3: deploy (Tier 2/3)** — `wip hal0 claim`; backup `/etc/hal0/slots/tts.toml` → `.bak-phase-b`; pull image: `ssh hal0 'podman pull ghcr.io/hal0ai/hal0-toolbox-kokoro:v1'`; `scripts/deploy.sh`; migrate live tts.toml to seed shape (KEEP port 8084; clear stale state: delete `/var/lib/hal0/slots/tts/state.json` stale `vibevoice` entry or POST a fresh load); `POST /api/slots/tts/load`.
- [ ] **Step 4: e2e matrix (comment results on the PR):**
  1. `systemctl is-active hal0-slot@tts` + `podman ps` + NO GPU devices in `podman inspect ... .HostConfig.Devices`
  2. `curl -s http://127.0.0.1:8084/health` → `{"status":"ok","model_loaded":true,...}` (weights from the mount, NOT downloaded — check container logs for download lines)
  3. Gateway round-trip: `curl -s http://127.0.0.1:8080/v1/audio/speech -H 'content-type: application/json' -d '{"model":"kokoro-v1","input":"hal0 voice is alive","voice":"af_heart"}' -o /tmp/tts-test.wav && file /tmp/tts-test.wav` → RIFF/WAV audio bytes (also test `model="tts"`)
  4. Dashboard: tts slot card shows container runtime, ready state, no error banner
  5. Stale-state regression: confirm state.json no longer references `vibevoice-1.5b`
- [ ] **Step 5: close out** — `wip release`; tracker events; memory update; Phase C next.

---

## Self-review notes
- Spec coverage: tts container (B1-B3, B5), dispatch (B4), error-state fix (B6 migration), #485 voice residual = none needed (research-verified, recorded here), rerank explicitly deferred to C.
- The B2 `:ro` mount note and ENTRYPOINT check are the two reality-checks the implementer must resolve against the actual image/renderer rather than this plan's assumption.
- B3 keeps `test_container_npu.py` untouched as the Phase A regression suite.
