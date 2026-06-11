# Phase A: NPU Container Cutover — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The `npu` slot runs FLM in a podman container (`hal0-slot@npu`) instead of through lemond, with `[npu] asr/embed` TOML toggles, static-port STT/embed dispatch, and full dashboard integration — lemond keeps running as rollback for everything else.

**Architecture:** Reuse the existing `FLMProvider.container_spec()` (already correct: `/dev/accel/accel0`, XRT `LD_LIBRARY_PATH`, memlock) by teaching `ContainerProvider` to render a systemd unit from a generic `ContainerSpec` when `device == "npu"`. The dispatcher's `FLMTrioRouter` gains container-first backend resolution (static slot port) with lemond discovery as fallback. The capabilities orchestrator writes `[npu]` TOML booleans instead of lemond `flm.args` when the npu slot is containerized.

**Tech Stack:** Python 3.12 / FastAPI / pydantic v2, podman + systemd template units, FLM (FastFlowLM) NPU runtime, React dashboard, pytest + Playwright γ-suite.

**Spec:** `docs/superpowers/specs/2026-06-10-lemonade-removal-container-switchover-design.md` §3–§5, §8.

**Worktree:** `/home/halo/dev/wt-phase-a` (branch `feat/phase-a-npu-container`, base = main). Run all commands from the worktree root. Commits need DCO sign-off: `git commit -s`.

**Verified facts (don't re-derive):**
- `#679` is already fixed on main (`NPU_SEEDED_SLOTS = ("stt-npu", "embed-npu")`, `manager.py:79-83`). No task needed.
- FLM image `ghcr.io/hal0ai/hal0-toolbox-flm:v1` is already pulled on CT105 and pinned in `manifest.json`.
- FLM has **no** `/health` endpoint; it serves `/v1/models` and `/v1/chat/completions`.
- FLM model cache on CT105 lives at `/var/lib/hal0/.config/flm/models` (29 GB). The code default `_DEFAULT_FLM_MODELS_DIR = /var/lib/hal0/flm-models` is a stale path from a fixed bug.
- Live `npu.toml` on CT105: `provider="lemonade"`, `backend="flm"`, `device="npu"`, port 8088, model `gemma3-4b-FLM`.

---

### Task 1: `NpuConfig` schema + `flm-npu` seed profile

**Files:**
- Modify: `src/hal0/config/schema.py` (SlotConfig ~line 197, SEED_PROFILES ~line 512)
- Modify: `installer/etc-hal0/profiles.toml`
- Test: `tests/config/test_schema_npu.py` (create)

- [ ] **Step 1: Write failing tests**

```python
"""Tests for the [npu] slot table and the flm-npu seed profile (Phase A)."""

from hal0.config.schema import SEED_PROFILES, NpuConfig, SlotConfig


def test_npu_config_defaults_off() -> None:
    cfg = NpuConfig()
    assert cfg.asr is False
    assert cfg.embed is False


def test_slot_config_accepts_npu_table() -> None:
    slot = SlotConfig.model_validate(
        {
            "name": "npu",
            "port": 8088,
            "device": "npu",
            "runtime": "container",
            "profile": "flm-npu",
            "model": {"default": "gemma3:4b"},
            "npu": {"asr": True, "embed": True},
        }
    )
    assert slot.npu is not None
    assert slot.npu.asr is True
    assert slot.npu.embed is True


def test_slot_config_npu_table_optional() -> None:
    slot = SlotConfig.model_validate({"name": "chat", "port": 8102})
    assert slot.npu is None


def test_flm_npu_seed_profile() -> None:
    prof = SEED_PROFILES["flm-npu"]
    assert prof["image"] == "ghcr.io/hal0ai/hal0-toolbox-flm:v1"
    assert prof["flags"] == ""
    assert prof["mtp"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/config/test_schema_npu.py -x -q`
Expected: `ImportError: cannot import name 'NpuConfig'`

- [ ] **Step 3: Implement**

In `src/hal0/config/schema.py`, next to `ServerConfig` (~line 173):

```python
class NpuConfig(BaseModel):
    """One ``[npu]`` table on an NPU slot — FLM trio modality toggles.

    Maps to ``flm serve --asr 1 --embed 1``.  The config file is the
    single source of truth (replaces lemond's nested ``flm.args``).
    """

    model_config = {"extra": "forbid"}

    asr: bool = False
    embed: bool = False
```

Add to `SlotConfig` (after `server`):

```python
    npu: NpuConfig | None = None
```

Add to `SEED_PROFILES`:

```python
    "flm-npu": {
        "image": "ghcr.io/hal0ai/hal0-toolbox-flm:v1",
        "flags": "",
        "mtp": False,
    },
```

Mirror into `installer/etc-hal0/profiles.toml` (keep the "identical to SEED_PROFILES" invariant noted in that file's header):

```toml
[profile.flm-npu]
# NPU LLM/trio slot (FastFlowLM). Flags are built by FLMProvider.container_spec,
# not by this profile — image pin only.
image = "ghcr.io/hal0ai/hal0-toolbox-flm:v1"
flags = ""
mtp   = false
```

Export `NpuConfig` in the module `__all__` list (alphabetical position).

- [ ] **Step 4: Verify pass + no regressions**

Run: `python -m pytest tests/config/test_schema_npu.py tests/config -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/hal0/config/schema.py installer/etc-hal0/profiles.toml tests/config/test_schema_npu.py
git commit -s -m "feat(config): [npu] asr/embed slot table + flm-npu seed profile"
```

---

### Task 2: `FLMProvider.container_spec` reads `[npu]` toggles + correct models-dir default

**Files:**
- Modify: `src/hal0/providers/flm.py` (`_DEFAULT_FLM_MODELS_DIR` ~line 46-60, `build_env` lines 140-166)
- Test: `tests/providers/test_flm_container_spec.py` (create; mirror helper style of `tests/providers/test_container.py` — plain `_slot_cfg()` helpers, no fixtures)

- [ ] **Step 1: Write failing tests**

```python
"""FLM container_spec: [npu] toggles + model-cache default (Phase A)."""

from typing import Any

from hal0.providers.flm import _DEFAULT_FLM_MODELS_DIR, FLMProvider


def _slot_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "npu",
        "port": 8088,
        "device": "npu",
        "runtime": "container",
        "profile": "flm-npu",
        "model": {"default": "gemma3:4b", "context_size": 16384},
    }
    base.update(overrides)
    return base


def _model_info() -> dict[str, Any]:
    return {"_model_key": "gemma3:4b"}


def test_npu_table_drives_trio_flags() -> None:
    spec = FLMProvider().container_spec(
        _slot_cfg(npu={"asr": True, "embed": True}), _model_info()
    )
    assert "--asr" in spec.command and "--embed" in spec.command


def test_npu_table_off_means_chat_only() -> None:
    spec = FLMProvider().container_spec(
        _slot_cfg(npu={"asr": False, "embed": False}), _model_info()
    )
    assert "--asr" not in spec.command and "--embed" not in spec.command


def test_legacy_defaults_load_asr_still_honoured() -> None:
    # Back-compat: old lemond-era shape, removed in Phase E.
    spec = FLMProvider().container_spec(
        _slot_cfg(defaults={"load_asr": "1"}), _model_info()
    )
    assert "--asr" in spec.command


def test_default_models_dir_is_flm_cache() -> None:
    assert _DEFAULT_FLM_MODELS_DIR == "/var/lib/hal0/.config/flm/models"
    spec = FLMProvider().container_spec(_slot_cfg(), _model_info())
    assert (
        "/var/lib/hal0/.config/flm/models",
        "/var/lib/hal0/.config/flm/models",
    ) in spec.mounts
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/providers/test_flm_container_spec.py -x -q`
Expected: FAIL (`--asr` missing / models-dir mismatch)

- [ ] **Step 3: Implement**

In `build_env()` (flm.py lines 140-166), make the `[npu]` table the primary source, `defaults` the legacy fallback:

```python
        npu_table = slot_cfg.get("npu") or {}
        defaults = slot_cfg.get("defaults") or {}
        load_asr = "1" if npu_table.get("asr") else str(defaults.get("load_asr", "0"))
        load_embed = "1" if npu_table.get("embed") else str(defaults.get("load_embed", "0"))
```

(adapt to the existing variable names in `build_env` so `container_spec`'s existing `load_asr == "1"` checks keep working unchanged).

Change the constant:

```python
_DEFAULT_FLM_MODELS_DIR = "/var/lib/hal0/.config/flm/models"
```

- [ ] **Step 4: Verify pass + existing FLM tests**

Run: `python -m pytest tests/providers/test_flm_container_spec.py tests/providers -q`
Expected: PASS. If an existing test asserts the old `/var/lib/hal0/flm-models` default, update that assertion in the same commit (it encodes the stale path).

- [ ] **Step 5: Commit**

```bash
git add src/hal0/providers/flm.py tests/providers/
git commit -s -m "feat(providers): FLM container_spec reads [npu] toggles; fix model-cache default"
```

---

### Task 3: `ContainerProvider` renders generic `ContainerSpec` units + NPU branch + health fallback

**Files:**
- Modify: `src/hal0/providers/container.py` (`load_sync` lines 350-410, `health` ~line 310, new `_render_unit_from_spec`)
- Test: `tests/providers/test_container_npu.py` (create)

- [ ] **Step 1: Write failing tests**

```python
"""ContainerProvider NPU branch: spec-rendered units + FLM health fallback (Phase A)."""

import shlex
from typing import Any
from unittest.mock import patch

import pytest

from hal0.providers.base import ContainerSpec
from hal0.providers.container import ContainerProvider, _render_unit_from_spec

_TEST_RUNTIME = "/usr/bin/docker"


def _flm_spec(**overrides: Any) -> ContainerSpec:
    base = dict(
        image="ghcr.io/hal0ai/hal0-toolbox-flm:v1",
        command=["serve", "gemma3:4b", "--host", "0.0.0.0", "--port", "8088", "--ctx-len", "16384"],
        env={"LD_LIBRARY_PATH": "/opt/fastflowlm/lib:/opt/xilinx/xrt/lib:/usr/lib/x86_64-linux-gnu"},
        mounts=[("/var/lib/hal0/.config/flm/models", "/var/lib/hal0/.config/flm/models")],
        devices=["/dev/accel/accel0", "/dev/dri/renderD128"],
        cap_add=[],
        security_opt=["apparmor=unconfined"],
        group_add=["993"],
        port=8088,
        network_mode="",
        extra_args=["-p 127.0.0.1:8088:8088", "--ulimit memlock=-1"],
    )
    base.update(overrides)
    return ContainerSpec(**base)


def _exec_start(unit_text: str) -> list[str]:
    for line in unit_text.splitlines():
        if line.startswith("ExecStart="):
            return shlex.split(line[len("ExecStart="):])
    raise AssertionError("ExecStart not found")


class TestRenderUnitFromSpec:
    def test_devices_and_mounts_in_argv(self) -> None:
        unit = _render_unit_from_spec("npu", _flm_spec(), runtime_bin=_TEST_RUNTIME)
        argv = _exec_start(unit)
        assert "--device=/dev/accel/accel0" in argv
        assert "--device=/dev/dri/renderD128" in argv
        assert "--device=/dev/kfd" not in argv  # NPU != ROCm compute
        assert (
            "--volume=/var/lib/hal0/.config/flm/models:/var/lib/hal0/.config/flm/models"
            in argv
        )

    def test_command_env_memlock(self) -> None:
        unit = _render_unit_from_spec("npu", _flm_spec(), runtime_bin=_TEST_RUNTIME)
        argv = _exec_start(unit)
        assert argv[-7:] == ["serve", "gemma3:4b", "--host", "0.0.0.0", "--port", "8088", "--ctx-len", "16384"]
        assert "--ulimit" in argv and "memlock=-1" in argv
        assert any(a.startswith("--env=LD_LIBRARY_PATH=") for a in argv)

    def test_unit_name_matches_template(self) -> None:
        unit = _render_unit_from_spec("npu", _flm_spec(), runtime_bin=_TEST_RUNTIME)
        assert "--name=hal0-slot-npu" in unit


class TestLoadSyncNpuBranch:
    def test_npu_slot_routes_through_flm_spec(self) -> None:
        provider = ContainerProvider()
        slot_cfg = {
            "name": "npu",
            "port": 8088,
            "device": "npu",
            "runtime": "container",
            "profile": "flm-npu",
            "model": {"default": "gemma3:4b"},
            "npu": {"asr": True, "embed": False},
        }
        with (
            patch.object(provider, "_write_and_start_unit") as start,
            patch("hal0.providers.container._resolve_runtime_bin", return_value=_TEST_RUNTIME),
        ):
            provider.load_sync(slot_cfg, {"_model_key": "gemma3:4b"})
        unit_text = start.call_args.args[1]
        argv = _exec_start(unit_text)
        assert "--device=/dev/accel/accel0" in argv
        assert "--asr" in argv


@pytest.mark.anyio
async def test_health_falls_back_to_v1_models() -> None:
    """FLM has no /health; a 404 there + 200 on /v1/models is healthy."""
    provider = ContainerProvider()

    class _Resp:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    async def fake_get(url: str, *a: Any, **k: Any) -> _Resp:
        return _Resp(404) if url.endswith("/health") else _Resp(200)

    with patch("hal0.providers.container._http_get", side_effect=fake_get):
        h = await provider.health(8088)
    assert h["ok"] is True
```

NOTE for the implementing agent: `_write_and_start_unit` and `_resolve_runtime_bin` / `_http_get` are the names this plan assigns — if `load_sync` currently inlines unit-writing or HTTP, extract those seams as part of Step 3 (small refactor, behavior-preserving) so both the llama path and the NPU path share them. Keep existing tests green.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/providers/test_container_npu.py -x -q`
Expected: `ImportError: cannot import name '_render_unit_from_spec'`

- [ ] **Step 3: Implement**

Add to `container.py`:

```python
def _render_unit_from_spec(
    slot_name: str,
    spec: ContainerSpec,
    *,
    runtime_bin: str | None = None,
) -> str:
    """Render a systemd unit from a generic ContainerSpec.

    Counterpart of :func:`_render_unit` (which is llama-server-shaped:
    profile flags + --model/--port).  Providers that know their own argv
    (FLM, ComfyUI in Phase D) build a ContainerSpec and route here.
    """
    runtime = runtime_bin or _resolve_runtime_bin()
    argv: list[str] = [
        runtime, "run", "--rm", f"--name=hal0-slot-{slot_name}",
    ]
    argv += [f"--device={d}" for d in spec.devices]
    argv += [f"--group-add={g}" for g in spec.group_add]
    argv += [f"--security-opt={s}" for s in spec.security_opt]
    argv += [f"--volume={src}:{dst}" for src, dst in spec.mounts]
    argv += [f"--env={k}={v}" for k, v in spec.env.items()]
    for extra in spec.extra_args:
        argv += shlex.split(extra)
    argv.append(spec.image)
    argv += spec.command
    return _UNIT_TEMPLATE.format(
        slot_name=slot_name,
        exec_start=" ".join(shlex.quote(a) for a in argv),
    )
```

(`_UNIT_TEMPLATE`: reuse whatever `_render_unit` uses to wrap ExecStart into the `[Unit]/[Service]` skeleton — extract a shared constant/helper if it's currently inline. Same `--security-opt=seccomp=unconfined` handling: FLM's spec already carries apparmor; add seccomp into the spec's `security_opt` at build time in `FLMProvider.container_spec` if missing rather than special-casing here.)

Branch at the top of `load_sync` (before profile resolution):

```python
        if str(slot_cfg.get("device", "")) == "npu":
            from hal0.providers.flm import FLMProvider

            spec = FLMProvider().container_spec(slot_cfg, model_info)
            unit_text = _render_unit_from_spec(
                str(slot_cfg["name"]), spec, runtime_bin=_resolve_runtime_bin()
            )
            self._write_and_start_unit(str(slot_cfg["name"]), unit_text)
            return
```

`health()` fallback — after the `/health` probe, when status is 404/connection-refused-free non-200:

```python
        # FLM (and other OpenAI-only servers) have no /health — accept /v1/models.
        if not ok:
            models_resp = await _http_get(f"http://127.0.0.1:{port}/v1/models")
            ok = models_resp.status_code == 200
```

- [ ] **Step 4: Verify pass + full provider suite**

Run: `python -m pytest tests/providers -q`
Expected: PASS (including the pre-existing `test_container.py`)

- [ ] **Step 5: Commit**

```bash
git add src/hal0/providers/container.py tests/providers/test_container_npu.py
git commit -s -m "feat(providers): spec-rendered container units + NPU branch + /v1/models health fallback"
```

---

### Task 4: SlotManager — FLM-tag model resolution on the container path

**Files:**
- Modify: `src/hal0/slots/manager.py` (`_spawn_locked` lines 1390-1426, `_resolve_model_info` line 2159)
- Test: `tests/slots/test_manager_npu_container.py` (create)

**Why:** `_resolve_model_info()` is registry/GGUF-shaped. FLM tags (`gemma3:4b`) have no filesystem path; `load_sync`'s NPU branch never calls `_resolve_model_path`, but `_resolve_model_info` must not raise/return-junk for a tag before we get there.

- [ ] **Step 1: Write failing test**

```python
"""SlotManager: npu container slot spawns through ContainerProvider with the FLM tag."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Mirror the SlotManager construction pattern of tests/slots/test_manager.py
# (tmp_path slots dir, injected pull_runner/model_cache_check).


@pytest.mark.anyio
async def test_npu_container_slot_spawns_with_flm_tag(tmp_path: Any) -> None:
    from tests.slots.test_manager import make_manager  # reuse existing factory helper

    sm = make_manager(tmp_path)
    sm.update_config(
        "npu",
        {
            "name": "npu",
            "port": 8088,
            "device": "npu",
            "runtime": "container",
            "profile": "flm-npu",
            "model": {"default": "gemma3:4b"},
        },
    )
    fake_provider = MagicMock()
    with patch(
        "hal0.providers.container.container_provider", return_value=fake_provider
    ):
        await sm.load("npu", "gemma3:4b")
    cfg_arg, model_info_arg = fake_provider.load_sync.call_args.args
    assert cfg_arg["device"] == "npu"
    assert model_info_arg["_model_key"] == "gemma3:4b"
```

(If `tests/slots/test_manager.py` has no reusable `make_manager` helper, copy its inline SlotManager construction into a module-level helper in the new file — do not import private test internals across files in a brittle way.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/slots/test_manager_npu_container.py -x -q`
Expected: FAIL — `_resolve_model_info("gemma3:4b")` raises or returns a path-less dict that breaks before `load_sync` (capture the actual failure mode in the test output; if it accidentally passes, the task collapses to just adding the regression test).

- [ ] **Step 3: Implement**

In `_resolve_model_info` (manager.py:2159), before the registry lookup:

```python
        # FLM tags ("family:size") are not registry GGUFs — the container
        # serves them from FLM's own cache.  Pass the tag through.
        from hal0.providers.flm import is_flm_tag

        if model_id and is_flm_tag(model_id):
            return {"_model_key": model_id, "flm_tag": model_id}
```

`is_flm_tag` shells out to host `flm list` (cached). In tests, patch `hal0.providers.flm.flm_served_models` to return `[{"tag": "gemma3:4b", "installed": True}]` — add that patch to the test above if Step 2's failure shows the probe firing.

- [ ] **Step 4: Verify pass + manager suite**

Run: `python -m pytest tests/slots/test_manager_npu_container.py tests/slots/test_manager.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hal0/slots/manager.py tests/slots/test_manager_npu_container.py
git commit -s -m "feat(slots): FLM-tag model resolution for npu container slots"
```

---

### Task 5: Dispatcher — container-first backend resolution in `FLMTrioRouter`

**Files:**
- Modify: `src/hal0/dispatcher/flm_trio.py` (constructor + `find_flm_chat_backend_url`)
- Modify: `src/hal0/api/__init__.py` (FLMTrioRouter construction in lifespan — pass `slot_manager`)
- Test: `tests/dispatcher/test_flm_trio_container.py` (create)

**Behavior:** When the `npu` slot is a *ready container slot*, `find_flm_chat_backend_url()` returns `http://127.0.0.1:{port}` directly — no lemond `/v1/health` walk. Legacy lemond discovery stays as fallback (removed in Phase E). This makes `/v1/audio/transcriptions` + `/v1/embeddings` NPU dispatch work unchanged through the existing `_is_npu_trio_request` gate.

- [ ] **Step 1: Write failing tests**

```python
"""FLMTrioRouter: static-port resolution for containerized npu slot (Phase A)."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hal0.dispatcher.flm_trio import FLMTrioRouter


def _slot_manager_with_container_npu(state: str = "ready") -> MagicMock:
    sm = MagicMock()
    sm.get_config.return_value = {
        "name": "npu",
        "port": 8088,
        "device": "npu",
        "runtime": "container",
        "profile": "flm-npu",
        "enabled": True,
    }
    sm.state.return_value = {"state": state}
    return sm


@pytest.mark.anyio
async def test_container_npu_resolves_static_port() -> None:
    lemonade = MagicMock()
    lemonade.health = AsyncMock(side_effect=AssertionError("must not hit lemond"))
    router = FLMTrioRouter(lemonade, slot_manager=_slot_manager_with_container_npu())
    url = await router.find_flm_chat_backend_url()
    assert url == "http://127.0.0.1:8088"


@pytest.mark.anyio
async def test_non_ready_container_falls_back_to_lemond() -> None:
    lemonade = MagicMock()
    lemonade.health = AsyncMock(
        return_value={
            "loaded": [
                {"recipe": "flm", "type": "llm", "backend_url": "http://127.0.0.1:8201/v1"}
            ]
        }
    )
    router = FLMTrioRouter(
        lemonade, slot_manager=_slot_manager_with_container_npu(state="loading")
    )
    url = await router.find_flm_chat_backend_url()
    assert url == "http://127.0.0.1:8201"


@pytest.mark.anyio
async def test_no_slot_manager_keeps_legacy_path() -> None:
    lemonade = MagicMock()
    lemonade.health = AsyncMock(
        return_value={
            "loaded": [
                {"recipe": "flm", "type": "llm", "backend_url": "http://127.0.0.1:8201/v1"}
            ]
        }
    )
    router = FLMTrioRouter(lemonade)
    assert await router.find_flm_chat_backend_url() == "http://127.0.0.1:8201"
```

(Adapt `sm.state` / `sm.get_config` mock names to the real SlotManager read API — `SlotManager.state()` is public per PR #649's description; if #649 hasn't merged, use the accessor `_spawn_locked`-era tests use. Whatever accessor is chosen, it must be a read-only call.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/dispatcher/test_flm_trio_container.py -x -q`
Expected: `TypeError: FLMTrioRouter.__init__() got an unexpected keyword argument 'slot_manager'`

- [ ] **Step 3: Implement**

`flm_trio.py`:

```python
    def __init__(
        self,
        lemonade_client: LemonadeClient,
        *,
        slot_manager: Any | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self._slot_manager = slot_manager
        ...

    def _container_npu_url(self) -> str | None:
        """Static URL of the containerized npu slot, or None.

        Container ports are fixed in slot config — no discovery dance.
        Returns None when the slot isn't containerized/ready so the
        legacy lemond walk still applies (removed in Phase E).
        """
        if self._slot_manager is None:
            return None
        try:
            cfg = self._slot_manager.get_config("npu")
        except Exception:
            return None
        if not cfg or str(cfg.get("device")) != "npu":
            return None
        if not (cfg.get("profile") or str(cfg.get("runtime", "")) == "container"):
            return None
        if cfg.get("enabled") is False:
            return None
        state = self._slot_manager.state("npu") or {}
        if state.get("state") != "ready":
            return None
        return f"http://127.0.0.1:{int(cfg['port'])}"

    async def find_flm_chat_backend_url(self) -> str | None:
        url = self._container_npu_url()
        if url is not None:
            return url
        ...  # existing lemond /v1/health walk unchanged
```

`api/__init__.py` lifespan: pass `slot_manager=` where `FLMTrioRouter(...)` is constructed (the SlotManager instance is already in scope there for other wiring).

- [ ] **Step 4: Verify pass + trio suites**

Run: `python -m pytest tests/dispatcher/test_flm_trio_container.py tests/dispatcher/test_flm_trio.py tests/api/test_v1_npu_trio_routing.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hal0/dispatcher/flm_trio.py src/hal0/api/__init__.py tests/dispatcher/test_flm_trio_container.py
git commit -s -m "feat(dispatcher): FLM trio resolves containerized npu slot by static port"
```

---

### Task 6: `npu_swap_status` container-aware

**Files:**
- Modify: `src/hal0/dispatcher/npu_swap_status.py`
- Test: `tests/dispatcher/test_npu_swap_status_container.py` (create)

**Behavior:** Today swap-in-progress is inferred by diffing lemond's loaded FLM entry vs the configured slot model. For a container npu slot: swap == container restarting → report from slot state (`loading`/`starting` = swap in progress; `ready` = settled). Keep the lemond path as fallback.

- [ ] **Step 1: Write failing test**

```python
"""npu_swap_status: containerized npu reports swap from slot state."""

from unittest.mock import MagicMock

import pytest

from hal0.dispatcher.npu_swap_status import npu_swap_status


def _sm(state: str) -> MagicMock:
    sm = MagicMock()
    sm.get_config.return_value = {
        "name": "npu", "port": 8088, "device": "npu",
        "runtime": "container", "profile": "flm-npu",
        "model": {"default": "gemma3:4b"},
    }
    sm.state.return_value = {"state": state}
    return sm


@pytest.mark.anyio
async def test_container_loading_means_swap_in_progress() -> None:
    status = await npu_swap_status(slot_manager=_sm("loading"), lemonade_client=None)
    assert status["swapping"] is True


@pytest.mark.anyio
async def test_container_ready_means_settled() -> None:
    status = await npu_swap_status(slot_manager=_sm("ready"), lemonade_client=None)
    assert status["swapping"] is False
    assert status["model"] == "gemma3:4b"
```

(Adapt the function/entry-point name to the real module surface — read `npu_swap_status.py` first; the module currently exposes the lemond-health-diff function consumed by `api/routes/npu.py`. Preserve its return shape exactly; only add the container branch ahead of the lemond call.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/dispatcher/test_npu_swap_status_container.py -x -q`
Expected: FAIL (lemond client required / container branch missing)

- [ ] **Step 3: Implement** — container branch first (same `_container_npu` detection logic as Task 5; factor the small `is_container_npu(cfg)` predicate into `hal0/dispatcher/_npu_common.py` if duplicating it twice feels wrong — two call sites is acceptable, three is not).

- [ ] **Step 4: Verify pass**

Run: `python -m pytest tests/dispatcher -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hal0/dispatcher/npu_swap_status.py tests/dispatcher/test_npu_swap_status_container.py
git commit -s -m "feat(dispatcher): npu swap status from container slot state"
```

---

### Task 7: Capabilities orchestrator — modality toggles write `[npu]` TOML for container slots

**Files:**
- Modify: `src/hal0/capabilities/orchestrator.py` (`_set_flm_modality` lines 728-745)
- Test: `tests/capabilities/test_npu_container_modality.py` (create)

- [ ] **Step 1: Write failing test**

```python
"""Capability apply: NPU modality toggle writes [npu] TOML when npu slot is containerized."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.anyio
async def test_set_flm_modality_writes_npu_table_for_container_slot() -> None:
    # Build the orchestrator the way tests/capabilities/test_npu_phase2_integration.py does,
    # with a slot_manager whose "npu" slot is runtime=container.
    from tests.capabilities.test_npu_phase2_integration import make_orchestrator  # reuse/copy factory

    orch, sm = make_orchestrator(npu_container=True)
    sm.update_config = MagicMock()
    sm.restart = AsyncMock()

    await orch._set_flm_modality("stt", enable=True)

    sm.update_config.assert_called_once_with("npu", {"npu": {"asr": True}})
    sm.restart.assert_awaited_once_with("npu")
    # lemond must NOT be touched on the container path
    assert orch._lemonade_provider is None or not orch._lemonade_provider().internal_set.called
```

(Factory note: if `make_orchestrator` doesn't exist, build the orchestrator with the same constructor args the phase2 integration test uses and add the `npu_container` knob locally. The mapping is `stt → asr`, `embed → embed` — mirror `_CHILD_TO_SLOT_TYPE` naming.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/capabilities/test_npu_container_modality.py -x -q`
Expected: FAIL (lemond client path taken / AttributeError)

- [ ] **Step 3: Implement**

In `_set_flm_modality`:

```python
    _CHILD_TO_NPU_FIELD = {"stt": "asr", "embed": "embed"}

    async def _set_flm_modality(self, child: str, *, enable: bool) -> None:
        cfg = self._slot_manager.get_config("npu")
        if cfg is not None and (cfg.get("profile") or str(cfg.get("runtime", "")) == "container"):
            field = self._CHILD_TO_NPU_FIELD[child]
            self._slot_manager.update_config("npu", {"npu": {field: enable}})
            await self._slot_manager.restart("npu")  # new flags need a new flm serve argv
            return
        # legacy lemond read-modify-write (removed in Phase E)
        ...
```

(Adapt: if SlotManager has no `restart()`, use the existing unload+load pair the orchestrator already uses elsewhere; `update_config` does one-level deep-merge for sub-tables, so `{"npu": {"asr": True}}` won't clobber the sibling `embed` key — verify against `update_config`'s merge and assert it in the test.)

- [ ] **Step 4: Verify pass + orchestrator suites**

Run: `python -m pytest tests/capabilities -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hal0/capabilities/orchestrator.py tests/capabilities/test_npu_container_modality.py
git commit -s -m "feat(capabilities): NPU modality toggles write [npu] TOML on container slots"
```

---

### Task 8: API — surface `npu` toggles on slot views

**Files:**
- Modify: `src/hal0/api/routes/slots.py` (slot serialization)
- Test: `tests/api/test_slots_npu_fields.py` (create)

- [ ] **Step 1: Write failing test** — follow the client/app fixture pattern of `tests/api/test_slots_container_state.py` (it builds the FastAPI app with a stub SlotManager):

```python
@pytest.mark.anyio
async def test_slot_list_includes_npu_toggles(client_with_npu_container_slot) -> None:
    resp = await client_with_npu_container_slot.get("/api/slots")
    slot = next(s for s in resp.json()["slots"] if s["name"] == "npu")
    assert slot["npu"] == {"asr": True, "embed": False}
```

(Build `client_with_npu_container_slot` by copying the fixture in `test_slots_container_state.py` and adding `"npu": {"asr": True, "embed": False}` to the stubbed slot config.)

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/api/test_slots_npu_fields.py -x -q` → KeyError `'npu'`.

- [ ] **Step 3: Implement** — in the slot-view serializer in `slots.py`, pass the table through:

```python
        npu_table = cfg.get("npu")
        if npu_table:
            view["npu"] = {"asr": bool(npu_table.get("asr")), "embed": bool(npu_table.get("embed"))}
```

Writes already work: `PUT /api/slots/npu/config` shallow-merges `{"npu": {...}}` via `update_config` (Task 7 relies on the same path) — add one test asserting a PUT round-trips.

- [ ] **Step 4: Verify pass** — `python -m pytest tests/api/test_slots_npu_fields.py tests/api/test_slots_container_state.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hal0/api/routes/slots.py tests/api/test_slots_npu_fields.py
git commit -s -m "feat(api): expose [npu] asr/embed toggles on slot views"
```

---

### Task 9: UI — NPU stack container mode

**Files:**
- Modify: `ui/src/api/hooks/useSlots.ts` (Slot interface ~line 48)
- Modify: `ui/src/dash/slots.jsx` (`NpuFlmStack` lines 727-871, `onToggleModality` lines 816-825)
- Test: γ-suite spec `ui/tests/npu-container.spec.ts` (create, mirroring existing slots-page specs) + mock fixture update in `ui/src/api/mock.ts` / `HAL0_DATA` fixtures

- [ ] **Step 1: Add type + failing γ test**

`useSlots.ts`:

```typescript
  /** [npu] trio toggles (container npu slots, Phase A). */
  npu?: { asr: boolean; embed: boolean } | null
```

γ spec (mock fixture gains a container-runtime npu slot with `npu: {asr: true, embed: false}`):

```typescript
test('container npu slot renders TOML-backed trio toggles', async ({ page }) => {
  await page.goto('/')
  const stack = page.getByTestId('npu-flm-stack')
  await expect(stack.getByRole('switch', { name: /asr/i })).toBeChecked()
  await expect(stack.getByRole('switch', { name: /embed/i })).not.toBeChecked()
})
```

- [ ] **Step 2: Run to verify failure** — `cd ui && npx playwright test npu-container --project=gamma` → FAIL (toggles read `flm_args` from lemond config, absent for container slot).

- [ ] **Step 3: Implement** — in `NpuFlmStack`:
  - Detect container mode: `const containerNpu = chatSlot?.runtime === 'container' || !!chatSlot?.profile`.
  - Container mode: trio state comes from `chatSlot.npu` (not `parseFlmArgs(useLemonadeConfig())`); `onToggleModality(which, slot)` becomes a single slot-config mutation: `editMut.mutateAsync({ name: 'npu', body: { npu: { [which === 'asr' ? 'asr' : 'embed']: next } } })` — **no** `cfgSet.mutateAsync({ flm_args })` call. Show the existing "restarting" chip while the slot bounces (slot state already streams).
  - Legacy mode: unchanged (deleted in Phase E).
  - Model picker (`NpuModelSelect`) works as-is — FLM tags come from the models catalog, and a model change for a container slot already triggers the swap path.

- [ ] **Step 4: Verify** — `cd ui && npx playwright test npu-container --project=gamma` → PASS; `npm run build` → clean.

- [ ] **Step 5: Commit**

```bash
git add ui/src/api/hooks/useSlots.ts ui/src/dash/slots.jsx ui/src/api/mock.ts ui/tests/npu-container.spec.ts
git commit -s -m "feat(ui): NPU stack container mode — TOML-backed trio toggles"
```

---

### Task 10: Seed `npu.toml` + installer

**Files:**
- Create: `installer/etc-hal0/slots/npu.toml`
- Modify: `installer/install.sh` (slot-seeding section — copy the new TOML alongside `img.toml`)
- Test: extend `tests/config/test_schema_npu.py` with a seed-validation test

- [ ] **Step 1: Write failing test**

```python
def test_seed_npu_toml_validates() -> None:
    import tomllib
    from pathlib import Path

    raw = tomllib.loads(
        Path("installer/etc-hal0/slots/npu.toml").read_text(encoding="utf-8")
    )
    slot = SlotConfig.model_validate(raw)
    assert slot.runtime == "container"
    assert slot.profile == "flm-npu"
    assert slot.device == "npu"
    assert slot.npu is not None and slot.npu.asr is False
```

- [ ] **Step 2: Verify failure** — `python -m pytest tests/config/test_schema_npu.py -x -q` → FileNotFoundError.

- [ ] **Step 3: Create the seed**

```toml
# NPU LLM slot — FastFlowLM in a podman container (hal0-slot@npu).
# One FLM process per NPU; model swap = container restart.
name = "npu"
port = 8088
device = "npu"
runtime = "container"
profile = "flm-npu"

[model]
default = "gemma3:4b"
context_size = 16384

[npu]
# Trio modalities (flm serve --asr/--embed). Toggled from the dashboard.
asr = false
embed = false
```

Wire into `install.sh` where `img.toml` is copied (grep `img.toml` in install.sh; add `npu.toml` to the same copy list, no-clobber semantics identical).

- [ ] **Step 4: Verify pass** — `python -m pytest tests/config/test_schema_npu.py -q` and `bash -n installer/install.sh`.

- [ ] **Step 5: Commit**

```bash
git add installer/etc-hal0/slots/npu.toml installer/install.sh tests/config/test_schema_npu.py
git commit -s -m "feat(installer): seed containerized npu slot TOML"
```

---

### Task 11: Full-suite gate, PR, CT105 deploy + e2e validation

**Files:** none new (ops task)

- [ ] **Step 1: Full local gate**

```bash
python -m pytest tests/ -q --ignore=tests/harness -x   # NOTE: full suite hangs on hal0-dev only when lemond health waits trigger — if it hangs, run per-directory: tests/config tests/providers tests/slots tests/dispatcher tests/api tests/capabilities
ruff check . && ruff format --check .                  # format check is a separate fatal CI step
cd ui && npm run build && cd ..
```

- [ ] **Step 2: PR**

```bash
git push -u origin feat/phase-a-npu-container
gh pr create --title "feat: Phase A — NPU container cutover (spec 2026-06-10)" --body "$(cat <<'EOF'
Containerizes the npu slot (FLM, podman, hal0-slot@npu) per the lemonade-removal
design spec §3-§5. [npu] asr/embed TOML toggles; static-port trio dispatch with
lemond fallback; orchestrator writes TOML not lemond config; UI container mode.
Lemonade untouched for all other slots (rollback intact).

Refs #652 follow-on, #578 retest, #485 groundwork. Spec: docs/superpowers/specs/2026-06-10-lemonade-removal-container-switchover-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Wait for CI green; merge per pipeline rules (base-first, no admin-merge through red).

- [ ] **Step 3: CT105 deploy (Tier 2/3 — verify first)**

```bash
ssh hal0 '~/.claude/bin/wip hal0 status || true'   # MUST be on main + clean; coordinate if not
# claim: wip hal0 claim "Phase A npu container deploy" /etc/hal0/slots/npu.toml
ssh hal0 'cp /etc/hal0/slots/npu.toml /etc/hal0/slots/npu.toml.bak-phase-a'
ssh hal0 'cd /opt/hal0 && scripts/deploy.sh'        # rebuilds ui/dist + restarts + healthcheck
```

Migrate the live slot (deploy does NOT rewrite /etc/hal0): edit `/etc/hal0/slots/npu.toml` to the Task 10 seed shape (keep port 8088 + current model tag `gemma3:4b` — the live config's `gemma3-4b-FLM` is a registry alias; use the FLM tag form the catalog probe returns).

- [ ] **Step 4: e2e validation matrix (record results in PR comment)**

```bash
# 1. container up + NPU device bound
ssh hal0 'systemctl status hal0-slot@npu --no-pager | head -5; podman inspect hal0-slot-npu --format "{{.HostConfig.Devices}}"'
# 2. chat round-trip on the NPU
ssh hal0 'curl -s http://127.0.0.1:8080/v1/chat/completions -H "content-type: application/json" -d "{\"model\":\"npu\",\"messages\":[{\"role\":\"user\",\"content\":\"say hi\"}],\"max_tokens\":10}" | head -c 400'
# 3. toggle asr on from the API, verify restart + flag
ssh hal0 'curl -s -X PUT http://127.0.0.1:8080/api/slots/npu/config -H "content-type: application/json" -d "{\"npu\":{\"asr\":true}}"'
ssh hal0 'sleep 20; podman inspect hal0-slot-npu --format "{{.Config.Cmd}}"'   # expect --asr 1
# 4. STT via static-port trio dispatch (#485 groundwork)
ssh hal0 'curl -s http://127.0.0.1:8080/v1/audio/transcriptions -F model=stt-npu -F file=@/opt/hal0/tests/fixtures/audio/hello.wav | head -c 200'
# 5. #578 retest: embed toggle on, then
ssh hal0 'curl -s http://127.0.0.1:8080/v1/embeddings -H "content-type: application/json" -d "{\"model\":\"embed-npu\",\"input\":\"hello\"}" | head -c 200'
```

Expected: 1-4 green. 5 (#578): if 404 reproduces against the bare container → comment findings on #578 + file upstream FastFlowLM issue, leave `embed=false` in the live TOML (ships dark per spec §4).

- [ ] **Step 5: Close out**

- `wip hal0 release` + `wip release`
- Dashboard check: NPU stack shows container chips + working toggles at https://hal0.thinmint.dev
- Comment validation matrix on the merged PR; update memory + spec status; Phase B plan is next.

---

## Self-review notes

- **Spec coverage (Phase A scope):** npu container slot ✓ (T1-T4, T10), asr/embed TOML toggles ✓ (T1, T2, T7, T8, T9), static-port stt/embed routing ✓ (T5), swap status ✓ (T6), #679 ✓ (already on main, verified), #578 retest ✓ (T11), model cache mount fix ✓ (T2), lemond untouched as rollback ✓ (fallback paths in T5-T7).
- **Known seam risk:** T3 names `_write_and_start_unit`/`_resolve_runtime_bin`/`_http_get` as extraction targets — the implementer must adapt to the real private helpers if they differ; the tests define the contract, not the helper names.
- **Out of scope (later phases):** deleting trio/lemond code paths (E), stt/embed CPU/GPU container fallbacks (B/C), profile CRUD + drawer editability (C), `utility`/`rerank`/`tts`/`img` (B/C/D).
