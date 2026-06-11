# Phase D: ComfyUI img Slot + Exclusive GPU Arbiter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ComfyUI runs as the `img` podman container slot (`hal0-slot@img`), the SlotManager gains an exclusive GPU arbiter (LLM slots ⇄ img with drain, idle-restore, manual pin, 503+Retry-After), the #690 switchover API is re-implemented on the arbiter (shell scripts retired), and #599/#691 close.

**Architecture:** `ComfyUIProvider` already has the full OpenAI→ComfyUI pipeline (`infer()`: build_workflow → POST /prompt → poll /history → GET /view) and a `container_spec()` — Phase D reconciles that spec with the live-validated kyuz0 deployment, wires `_spec_provider_for`, and adds a `GpuArbiter` owned by SlotManager that uses the existing `in_flight_count()`/`serving()` primitives to drain before stopping. The dispatcher's legacy img path-pin exists but its acceptance gate rejects container remotes (Rule 4 never sets `path_pinned`) — one-flag fix + tests.

**Tech Stack:** Python 3.12/FastAPI/pydantic v2, podman+systemd, `kyuz0/amd-strix-halo-comfyui` (digest-pinned), React dashboard, pytest + γ-suite.

**Spec:** `docs/superpowers/specs/2026-06-10-lemonade-removal-container-switchover-design.md` §3 (img row), §7, §12-D.
**Worktree:** `/home/halo/dev/wt-phase-d` (branch `feat/phase-d-comfyui-img-gpu-arbiter`, base 5ed8793). `.venv/bin/python -m pytest`. Commits `git commit -s`.

**Verified facts (research 2026-06-11 — don't re-derive):**
- `ComfyUIProvider` (`src/hal0/providers/comfyui.py`) is COMPLETE for translation: `infer()` (line 277) consumes `_hal0_model_class`/`_hal0_ckpt_filename` injected by the `/v1/images/generations` route (`api/routes/v1.py:1015`, curated entry → `curated.hf_file` as ckpt). `health()` probes `/system_stats`. Registry lookup happens at REQUEST time, not slot-load time — `SlotManager._resolve_model_info` (manager.py:2178) treats registry miss as non-fatal (returns `{_model_key, flm_tag}`), and `container_spec` ignores `model_info["path"]` except as a diagnostic env var. No registry precheck needed for slot load (unlike Phase C llama slots) — but the curated image entries (sdxl-turbo etc.) MUST resolve at request time (deploy precheck).
- `container_spec()` (comfyui.py:172) does NOT match the live container. Live (docker inspect, CT105): image `kyuz0/amd-strix-halo-comfyui@sha256:0066678ae9043f69a1c8c7699e70626ceffd35c1a8ca03227a05640ad0241ed2` (RepoDigest verified pullable), cmd `bash -lc 'cd /opt/ComfyUI && exec python main.py --listen 0.0.0.0 --port 8188 --disable-mmap --bf16-vae --cache-none'`, mounts `/mnt/ai-models/comfyui/{models→/root/comfy-models, output, input, user, custom_nodes}` + `extra_model_paths.yaml` (ro), `--ipc=host --shm-size=8g`, `apparmor=unconfined seccomp=unconfined label=disable`, devices kfd+dri, group-add video+render, bridge net `-p 8188:8188`, restart `no`. The kyuz0 image has ComfyUI at `/opt/ComfyUI` (venv `/opt/venv`) — NOT `/app` as the provider's `_COMFYUI_APP_DIR` assumes. Models = 241 GB under `/mnt/ai-models/comfyui/models/`.
- The "100%-CPU idle spin" from the handoff is NOT reproducible — the container exited (137, external kill via comfy-down.sh) 5h before research; flip-on-demand is its designed mode. No debugging task; D9 adds an idle-CPU assertion post-migration instead.
- `_spec_provider_for` (providers/container.py:311): npu→FLM, tts/kokoro-cpu→Kokoro, comment "ComfyUI joins in Phase D". `_render_unit_from_spec` handles ContainerSpec→unit incl. `--publish=127.0.0.1:{port}` when `network_mode=""`; `extra_args` is the escape hatch (FLM uses it for `--ulimit memlock=-1`).
- Dispatcher: `_IMAGE_PATHS` (proxy.py:49) → candidate "img" via Rule 4 but `path_pinned` is NOT set (unlike embed/tts/rerank rules) → the acceptance gate (proxy.py:184) REJECTS a container-remote img upstream. Rule 6 (`_IMAGE_NAME_PREFIXES` model-prefix → img) same gap. `_UPSTREAM_PATH_REWRITES` (router.py:175) exists (rerank precedent). `_default_for_path` (router.py:1074) has no image arm (falls to "chat" — harmless once path-pin works, but add the arm for Step-0 preemption symmetry).
- Drain primitives EXIST: `SlotManager.in_flight_count(slot)` (manager.py:2033), `serving()` ctx-mgr + `_serving_count`/`_serving_lock` (manager.py:1995-2031, 282-283). `state()`/`is_ready_for_dispatch()` public (#696, manager.py:399-427, ready-set READY|SERVING|IDLE). 503 envelope precedent: `SlotLoading` (router.py:233) + `_build_loading_response` with `retry_after_s`.
- #690 switchover (`api/routes/comfyui.py:373`): 202 + BackgroundTasks `_run_switch(mode)` → script pairs `_SWITCH_PAIRS` (line 54: stop-inference.sh/comfy-up.sh etc., live at `/mnt/ai-models/comfyui/control-scripts/`), gate `HAL0_COMFYUI_SWITCHOVER_ENABLED=1` (ON in CT105 api.env), `_switch` dict tracks `{active, target, error}`, busy-queue 409 + `force`, root-aware exec (sudoers grant unused — api runs as root). It does NOT drain LLM requests and does NOT unload GPU containers (= #691). Phase D replaces `_run_switch`'s script body with arbiter calls; the route contract (202/409/501 + status polling) is preserved so #686/#690 UI keeps working.
- UI: `slots.jsx:1270-1294` tabs (Inference|Image Gen) → `ComfyuiPane` (`comfyui-pane.jsx`); `useComfyui`/`useComfyuiSwitchover` hooks (`useComfyui.ts`, endpoints `/api/comfyui/status|switchover`); `SwitchoverConfirm` (lines 188-269) with force-consent + 1.5s refetch poll. Banners: `primitives.jsx` `BANNER_CATALOG` (line 219+) + `useBanners()`. Drawer fixed-profile rendering for non-GPU classes already exists (slot-modals.jsx:769-787) — comfyui profile (`device_class="img"`) renders read-only automatically. Mock data `data.jsx:209` has an img slot entry; no comfyui status mock (pane falls back to `COMFYUI_FALLBACK`).
- Schema: `_SLOT_PORT_MIN/MAX = 8081/8099` (schema.py:94-95, `ge`/`le` on `port`) — img needs 8188 (ComfyUI stock port; operator bookmarks + kyuz0 tooling assume it). No `type` literal validation (`type="image"` is free). `_VALID_DEVICES` has gpu-rocm. `comfyui` already in `_VALID_PROVIDERS` (#682). `ProfileConfig.device_class` accepts `"img"` (reserved, unused). manifest.json HAS a `comfyui` entry but pinned to the unpublished `ghcr.io/hal0ai/hal0-toolbox-comfyui:v1` — Phase D repoints it at the live-validated kyuz0 digest; `manifest_image_ref("comfyui")` (loader.py:657) already feeds `ComfyUIProvider.image_ref` (priority: slot override → env → manifest → default tag).
- `installer/etc-hal0/slots/img.toml` is the OLD lazy-load shape (provider=comfyui, port 8186, backend=rocm, no runtime/profile); img NOT in the install.sh seed loop (install.sh:961). Seed tests: `tests/config/test_schema.py::TestSeededSlotTomls` validates ALL seed TOMLs against `_VALID_PROVIDERS`; per-phase seed tests in `test_schema_npu.py` / `test_schema_seeds_c5.py`.
- Issues folding in: **#599** → `[image]` section in img.toml (defaults persisted). **#691** → arbiter unloads GPU containers on switch-to-img (root cause of the GTT-resident defect). #687/#649 remain Phase E — do not touch `lemonade_proxy.py` fall-through or omni-router classification.

---

### Task D1: Schema + seeds — comfyui profile, manifest repin, port range, img.toml, `[image]` section (#599)

**Files:**
- Modify: `src/hal0/config/schema.py` (SEED_PROFILES ~589, `_SLOT_PORT_MAX` ~95, SlotConfig — new optional `image` section model)
- Modify: `manifest.json` (comfyui entry)
- Modify: `installer/etc-hal0/profiles.toml` (comfyui profile parity)
- Rewrite: `installer/etc-hal0/slots/img.toml`
- Modify: `installer/install.sh:961` (seed loop gains `img`)
- Test: `tests/config/test_schema_seeds_d1.py` (create)

- [ ] **Step 1: failing tests**

```python
# tests/config/test_schema_seeds_d1.py  (mirror test_schema_seeds_c5.py fixtures)
def test_comfyui_seed_profile() -> None:
    p = SEED_PROFILES["comfyui"]
    assert p["device_class"] == "img"
    assert "kyuz0/amd-strix-halo-comfyui" in p["image"]


def test_seed_img_toml_validates(seed_slot_toml) -> None:
    cfg = seed_slot_toml("img")          # parses via SlotConfig
    assert cfg.runtime == "container"
    assert cfg.profile == "comfyui"
    assert cfg.provider == "comfyui"
    assert cfg.port == 8188
    assert cfg.device == "gpu-rocm"
    assert cfg.image_gen.idle_restore_minutes == 5      # #599 section


def test_port_range_admits_comfyui_stock_port() -> None:
    # 8188 is ComfyUI's well-known port; range widened 8099 → 8200 for it.
    SlotConfig(name="x", port=8188)      # no ValidationError


def test_image_gen_section_defaults() -> None:
    s = ImageGenConfig()
    assert (s.idle_restore_minutes, s.default_size, s.default_steps) == (5, "1024x1024", 0)


def test_manifest_comfyui_pinned_to_kyuz0() -> None:
    ref = manifest_image_ref("comfyui")
    assert ref == (
        "docker.io/kyuz0/amd-strix-halo-comfyui"
        "@sha256:0066678ae9043f69a1c8c7699e70626ceffd35c1a8ca03227a05640ad0241ed2"
    )
```

- [ ] **Step 2:** run → KeyError/ValidationError/FileNotFoundError.
- [ ] **Step 3: implement.**
  - `_SLOT_PORT_MAX = 8200` (update the line-93 comment: "slots get 8081-8099; 8188 = ComfyUI's stock port for the img slot — kept well-known so operator bookmarks/tooling keep working").
  - `ImageGenConfig(BaseModel)`: `idle_restore_minutes: int = Field(default=5, ge=0)` (0 = never auto-restore), `default_size: str = "1024x1024"`, `default_steps: int = Field(default=0, ge=0)` (0 = model-class default). Docstring cites #599. SlotConfig gains `image_gen: ImageGenConfig = Field(default_factory=ImageGenConfig, alias="image")` — TOML section `[image]` (alias, since `image` the string field may exist for container override — CHECK: if `SlotConfig.image` already exists as a str override field, name the section `[image_gen]` in TOML instead and skip the alias; pick whichever avoids the collision and assert it in the test).
  - `SEED_PROFILES["comfyui"] = {"image": "docker.io/kyuz0/amd-strix-halo-comfyui:latest", "flags": "--disable-mmap --bf16-vae --cache-none", "mtp": False, "device_class": "img"}` + mirror in installer profiles.toml. (Provider resolves the DIGEST via manifest; the profile carries the human-readable tag + the bench-validated ComfyUI flags, consumed by `container_spec` in D2.)
  - manifest.json comfyui entry → `{"tag": "docker.io/kyuz0/amd-strix-halo-comfyui:latest", "digest": "sha256:0066678ae9043f69a1c8c7699e70626ceffd35c1a8ca03227a05640ad0241ed2", "_notes": "live-validated on CT105 2026-06-11 (Wan2.2/Qwen-Image/Flux/SDXL on gfx1151); repinned from unpublished hal0-toolbox-comfyui (Phase D)"}`.
  - img.toml rewrite:

```toml
# hal0 image-generation slot — ComfyUI in a podman container (hal0-slot@img).
# Exclusive-GPU slot: the GpuArbiter stops LLM GPU slots while img runs and
# restores them after [image].idle_restore_minutes with no jobs (spec §7).

name = "img"
type = "image"
provider = "comfyui"
device = "gpu-rocm"
runtime = "container"
profile = "comfyui"
enabled = true
port = 8188

[model]
default = "sdxl-turbo"

[image]                       # persisted image-gen settings (#599)
idle_restore_minutes = 5
default_size = "1024x1024"
default_steps = 0
```

  - install.sh seed loop: `for seed_slot in npu tts rerank utility img`.
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/config -q` green + `bash -n installer/install.sh`.
- [ ] **Step 5:** `git commit -s -m "feat(config): comfyui seed profile + img slot seed + [image] settings (#599)"`

---

### Task D2: ComfyUIProvider live-parity `container_spec` + `_spec_provider_for` img arm

**Files:**
- Modify: `src/hal0/providers/comfyui.py` (`container_spec`, constants)
- Modify: `src/hal0/providers/container.py` (`_spec_provider_for` ~311)
- Test: `tests/providers/test_comfyui_container_spec.py` (create; mirror `test_container_*` patterns), extend the `_spec_provider_for` dispatch test

- [ ] **Step 1: failing tests**

```python
def test_comfyui_spec_matches_live_deployment(monkeypatch) -> None:
    # base dir mounts redirected via monkeypatched _COMFYUI_DATA_ROOT or tmp dirs as siblings do
    spec = ComfyUIProvider().container_spec(_IMG_CFG, {})
    assert spec.devices and "/dev/kfd" in spec.devices            # via resolve_gpu_device_paths
    assert ("/mnt/ai-models/comfyui/models", "/root/comfy-models") in spec.mounts
    for sub in ("output", "input", "user", "custom_nodes"):
        assert (f"/mnt/ai-models/comfyui/{sub}", f"/opt/ComfyUI/{sub}") in spec.mounts
    assert any("extra_model_paths.yaml" in src for src, _ in spec.mounts)
    assert "--ipc=host" in spec.extra_args and "--shm-size=8g" in spec.extra_args
    assert set(spec.security_opt) == {"seccomp=unconfined", "apparmor=unconfined", "label=disable"}
    assert spec.network_mode == "host"          # LXC = the host; web UI stays on :8188 LAN-reachable
    assert spec.command[-1].endswith("--cache-none")  # profile flags flow into argv


def test_comfyui_argv_uses_opt_comfyui_workdir() -> None:
    spec = ComfyUIProvider().container_spec(_IMG_CFG, {})
    # kyuz0 image: app at /opt/ComfyUI, venv /opt/venv — argv must cd there
    assert spec.command[:2] == ["bash", "-lc"]
    assert "/opt/ComfyUI" in spec.command[2] and "--port 8188" in spec.command[2]


def test_spec_provider_for_dispatches_comfyui() -> None:
    assert isinstance(_spec_provider_for({"provider": "comfyui", "type": "image"}), ComfyUIProvider)
    assert isinstance(_spec_provider_for({"profile": "comfyui"}), ComfyUIProvider)
```

- [ ] **Step 2:** run → mounts/extra_args mismatch, dispatch returns None.
- [ ] **Step 3: implement.**
  - Rework `container_spec` to the live shape: data root constant `_COMFYUI_DATA_ROOT = "/mnt/ai-models/comfyui"` (override via `HAL0_COMFYUI_DATA_ROOT` env for tests/other installs); mounts per the test; devices via `resolve_gpu_device_paths()` (NOT the bare `["/dev/kfd", "/dev/dri"]` — podman needs explicit nodes, same fix as #674); `extra_args=["--ipc=host", "--shm-size=8g"]` (Wan/Hunyuan video need the shm); `label=disable` added to security_opt; command = `["bash", "-lc", f"cd /opt/ComfyUI && exec python main.py --listen 0.0.0.0 --port {port} {profile_flags}"]` where `profile_flags` come from the slot's resolved profile `flags` (read how llama_server's `_render_unit` pulls profile flags and mirror the lookup; fallback to the D1 defaults when no profile). Update `_COMFYUI_APP_DIR = "/opt/ComfyUI"` + module docstring (image is kyuz0, not hal0-toolbox). Keep `network_mode="host"` (document: LXC is the host, ComfyUI web UI must stay LAN-reachable on :8188, matches pre-migration behavior; `--publish` not emitted for host mode — verify `_render_unit_from_spec` skips publish when host, it does).
  - `_spec_provider_for`: add arm before the return — `if str(slot_cfg.get("provider", "")) == "comfyui" or str(slot_cfg.get("profile", "")) == "comfyui" or str(slot_cfg.get("type", "")) == "image": return ComfyUIProvider()` (lazy import, mirror FLM/Kokoro arms). Update its docstring.
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/providers -q` green.
- [ ] **Step 5:** `git commit -s -m "feat(providers): ComfyUI container_spec live-parity (kyuz0) + spec-provider dispatch"`

---

### Task D3: Dispatcher — img path-pin container acceptance + image default arm

**Files:**
- Modify: `src/hal0/dispatcher/proxy.py` (Rules 4 + 6)
- Modify: `src/hal0/dispatcher/router.py` (`_default_for_path` ~1074)
- Test: extend `tests/dispatcher/test_image_routing.py`

- [ ] **Step 1: failing tests**

```python
def test_image_path_accepts_container_remote() -> None:
    # upstream kind="remote" slot_name="img" registered (how container slots register, #656)
    # resolve_slot("/v1/images/generations") → that upstream, no LegacyResolutionFailed
def test_image_model_prefix_accepts_container_remote() -> None:
    # body {"model": "sdxl-turbo"}, path /v1/chat-agnostic → candidate img, remote accepted
def test_genuine_external_remote_still_rejected() -> None:
    # kind="remote" slot_name=None for img path → LegacyResolutionFailed (unchanged)
def test_default_for_path_images() -> None:
    # router._default_for_path("/v1/images/generations") == "img"
```

- [ ] **Step 2:** run → LegacyResolutionFailed / "chat".
- [ ] **Step 3: implement.** Rule 4: add `path_pinned = True` (comment: img is a container slot post-Phase-D — same acceptance as embed/tts/rerank). Rule 6 (model-prefix): also set `path_pinned = True` with a comment that the flag means "deterministically pinned" (the curated `sdxl-`/`flux-` prefixes are exact catalogue prefixes, not guesses — same trust level as a path pin; rename the variable to `pinned` ONLY if the diff stays mechanical). `_default_for_path`: insert `if "/images/" in path: return _IMAGE_DEFAULT` with `_IMAGE_DEFAULT = "img"` next to the other defaults.
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/dispatcher -q -p no:randomly` green.
- [ ] **Step 5:** `git commit -s -m "feat(dispatcher): img container-remote acceptance + image path default"`

---

### Task D4: GpuArbiter core (RISKY — Opus review gate)

**Files:**
- Create: `src/hal0/slots/arbiter.py`
- Modify: `src/hal0/slots/manager.py` (own a `GpuArbiter` instance, expose `self.arbiter`; construct with the manager + `idle_restore_minutes` read from the img slot config when present)
- Test: `tests/slots/test_gpu_arbiter.py` (create)

Design (spec §7, locked): exclusive groups derived not declared — `gpu_exclusive_group(slot_cfg) -> Literal["llm","img"] | None`: GPU device (`gpu-rocm`/`gpu-vulkan`) + `runtime=="container"` → `"img"` if provider/profile/type is comfyui/image else `"llm"`; everything else None (npu/cpu slots never arbitrated). Arbiter state persisted to `/var/lib/hal0/gpu_arbiter.json` (`{"mode", "pinned", "saved_llm_slots", "last_img_activity"}`) so an api restart mid-image-mode can still restore the LLM set.

- [ ] **Step 1: failing tests** (fake SlotManager: dict-driven `state()`/`in_flight_count()`/`load()`/`unload()` recorders — mirror how existing manager tests stub):

```python
def test_group_derivation() -> None:
    assert gpu_exclusive_group({"device": "gpu-rocm", "runtime": "container", "provider": "comfyui"}) == "img"
    assert gpu_exclusive_group({"device": "gpu-vulkan", "runtime": "container"}) == "llm"
    assert gpu_exclusive_group({"device": "npu", "runtime": "container"}) is None
    assert gpu_exclusive_group({"device": "gpu-rocm", "runtime": "lemonade"}) is None

async def test_ensure_img_drains_then_stops_then_starts(fake_mgr, tmp_path) -> None:
    # chat+agent running (READY), in_flight: chat 2→1→0 across polls
    # ensure_img(): unload NOT called until in_flight hits 0; order = drain → unload llm → load img
    # saved_llm_slots == {"chat", "agent"}; state file written; mode == IMG

async def test_ensure_img_noop_when_already_img(fake_mgr, tmp_path) -> None: ...
async def test_drain_timeout_proceeds(fake_mgr, tmp_path) -> None:
    # in_flight stuck at 1 → after DRAIN_TIMEOUT_S (patch to 0.1) unload proceeds + warning logged

async def test_restore_llm_reloads_saved_set(fake_mgr, tmp_path) -> None:
    # mode IMG, saved {chat, agent} → restore_llm(): img unloaded first, then both loaded; mode LLM; file updated

async def test_restore_blocked_when_pinned_unless_force(fake_mgr, tmp_path) -> None: ...
async def test_state_survives_restart(fake_mgr, tmp_path) -> None:
    # new GpuArbiter over an existing state file → mode/saved set recovered

def test_guard_llm_dispatch_raises_in_img_mode(fake_mgr, tmp_path) -> None:
    # mode IMG → guard("chat") raises GpuImageMode (503, code "gpu.image_mode",
    # details carry retry_after_s); guard("npu") passes; mode LLM → all pass
```

- [ ] **Step 2:** run → ImportError.
- [ ] **Step 3: implement** `arbiter.py` (~200 lines):

```python
class GpuMode(str, Enum):
    LLM = "llm"
    IMG = "img"

class GpuImageMode(Hal0Error):
    """LLM dispatch refused — the GPU is in exclusive image mode."""
    code = "gpu.image_mode"
    status = 503

_DRAIN_TIMEOUT_S = 120.0
_DRAIN_POLL_S = 0.5

class GpuArbiter:
    def __init__(self, manager, *, state_path: Path, idle_restore_minutes: int = 5) -> None: ...
    # mode / pinned / saved_llm_slots properties (read persisted state lazily)
    async def ensure_img(self, *, pin: bool = False) -> None:
        # single asyncio.Lock around the whole switch (no concurrent flips);
        # snapshot running llm-group slots via manager slot configs + is_ready_for_dispatch;
        # drain: while any(manager.in_flight_count(s) for s in llm) and not timeout: sleep(_DRAIN_POLL_S)
        # for s in llm: await manager.unload(s)
        # await manager.load("img", <img cfg [model].default>)
        # persist; touch activity
    async def restore_llm(self, *, force: bool = False) -> None:
        # pinned and not force → GpuImageMode-shaped 409? NO — raise ArbiterPinned (409, code "gpu.pinned")
        # unload img → reload saved set → persist; mode → LLM
    def guard_llm_dispatch(self, slot_name: str) -> None: ...
    def touch_img_activity(self) -> None: ...      # stamps last_img_activity + persists (cheap json)
    def set_pin(self, pinned: bool) -> None: ...
    def status(self) -> dict: ...                  # {"mode","pinned","saved_llm_slots","idle_restore_at"}
```

  Manager wiring: lazy `@property arbiter` constructing on first use (state path under the same var-lib root the manager already resolves; `idle_restore_minutes` from the img slot's `[image]` section when the slot config loads, default 5). Slot-config access for group derivation: reuse whatever the manager already holds for slot configs (read how `unload`/`load` fetch cfg — do NOT add a new config loader).
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/slots -q` green.
- [ ] **Step 5:** `git commit -s -m "feat(slots): GpuArbiter — exclusive llm/img GPU groups with drain + persistence"`

---

### Task D5: Dispatch integration — auto-switch on image requests, 503 guard for LLM

**Files:**
- Modify: `src/hal0/api/routes/v1.py` (images route ~1015: `await arbiter.ensure_img()` before dispatch + `touch_img_activity()` start AND finish — long Wan jobs must keep the window open)
- Modify: `src/hal0/dispatcher/router.py` (guard hook in `_check_slot_ready_for_dispatch` or its caller: `arbiter.guard_llm_dispatch(call.slot_name)` BEFORE the readiness check, so the 503 carries the image-mode envelope not slot.loading; Retry-After from `idle_restore_at` remaining seconds, floor 15)
- Test: `tests/dispatcher/test_arbiter_dispatch.py` (create) + extend `tests/api/test_images_route*` if present

- [ ] **Step 1: failing tests:**

```python
async def test_image_request_triggers_switch() -> None:
    # mode LLM, img offline, POST /v1/images/generations (mock provider.infer)
    # → arbiter.ensure_img awaited (chat unloaded, img loaded) → 200
async def test_llm_request_in_img_mode_503_retry_after() -> None:
    # mode IMG → /v1/chat/completions → 503, body code "gpu.image_mode",
    # Retry-After header present and >= 15
async def test_npu_and_cpu_slots_unaffected_in_img_mode() -> None:
    # tts/npu/embed dispatch passes while mode IMG
async def test_img_activity_touched_on_completion() -> None: ...
```

- [ ] **Step 2:** run → no switch / 200s.
- [ ] **Step 3: implement.** Wire the two hooks. Error→HTTP mapping: confirm `Hal0Error.status` 503 propagates with headers — read how `SlotLoading.details.retry_after_s` becomes the Retry-After header today and reuse that exact mechanism for `GpuImageMode` (if it's response-shaping in an exception handler, add the new code there — no parallel plumbing).
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/dispatcher tests/api -q -p no:randomly` green.
- [ ] **Step 5:** `git commit -s -m "feat(dispatcher): auto img switch on image requests + gpu.image_mode 503 guard"`

---

### Task D6: Idle-restore loop

**Files:**
- Modify: `src/hal0/slots/arbiter.py` (`run_idle_loop()` async task) + `src/hal0/slots/manager.py` or app lifespan (start/stop the task — find where the api app starts manager-owned background tasks today; if none exists, the FastAPI lifespan in `create_app` is the home, mirror an existing lifespan task)
- Test: extend `tests/slots/test_gpu_arbiter.py`

- [ ] **Step 1: failing tests:**

```python
async def test_idle_restore_fires_after_window(fake_mgr, tmp_path) -> None:
    # mode IMG, last activity > window (patch window to 0.05s, loop interval 0.01)
    # → restore_llm called once; loop keeps running; mode LLM
async def test_idle_restore_skipped_when_pinned(fake_mgr, tmp_path) -> None: ...
async def test_idle_restore_skipped_when_window_zero(fake_mgr, tmp_path) -> None:
    # idle_restore_minutes=0 → never auto-restores
async def test_in_flight_img_job_defers_restore(fake_mgr, tmp_path) -> None:
    # manager.in_flight_count("img") > 0 → window not consumed (long video render)
```

- [ ] **Step 2-3:** implement `run_idle_loop` (30s interval constant, patchable; checks mode==IMG, not pinned, window>0, `in_flight_count("img")==0`, elapsed>window → `await restore_llm()`; exceptions logged, loop never dies). Start in lifespan, cancel on shutdown.
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/slots -q` green.
- [ ] **Step 5:** `git commit -s -m "feat(slots): arbiter idle-restore loop (default 5 min, pin-aware)"`

---

### Task D7: API — switchover route on the arbiter (scripts retired), status + pin

**Files:**
- Modify: `src/hal0/api/routes/comfyui.py` (`_run_switch`, `_SWITCH_PAIRS` deleted; body gains `pin`; new `POST /api/comfyui/pin`; status gains `arbiter` block)
- Test: extend `tests/api/test_comfyui_proxy.py`

- [ ] **Step 1: failing tests:**

```python
async def test_switchover_generation_calls_arbiter(client, fake_arbiter) -> None:
    # POST {"mode":"generation"} → 202; background invokes arbiter.ensure_img(pin=False)
    # scripts NOT executed (no subprocess)
async def test_switchover_inference_calls_restore(client, fake_arbiter) -> None: ...
async def test_switchover_pin_param(client, fake_arbiter) -> None:
    # {"mode":"generation","pin":true} → ensure_img(pin=True)
async def test_pin_endpoint_toggles(client, fake_arbiter) -> None:
    # POST /api/comfyui/pin {"pinned": true} → 200, arbiter.set_pin(True)
async def test_status_carries_arbiter_block(client, fake_arbiter) -> None:
    # GET /status → {"arbiter": {"mode","pinned","idle_restore_at","saved_llm_slots"}}
async def test_busy_queue_409_preserved(client) -> None: ...   # force-consent contract unchanged
```

- [ ] **Step 2:** run → scripts subprocess attempted / missing keys.
- [ ] **Step 3: implement.** `_run_switch(mode, pin)` body becomes: generation → `await arbiter.ensure_img(pin=pin)`; inference → `await arbiter.restore_llm(force=force)` (ArbiterPinned → recorded in `_switch["error"]` same as script failures were). Keep `_switch` {active,target,error} contract + the 409 busy-queue/force gate + the 501 feature gate UNCHANGED (UI #690 depends on them). Delete `_SWITCH_PAIRS`, `_script_argv`, `_run_script` + their sudoers docstring (note in PR body: control-scripts stay on disk for manual ops but the API no longer shells out; the sudoers grant story is obsolete). Status: merge `arbiter.status()` under `"arbiter"`.
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/api/test_comfyui_proxy.py -q` green.
- [ ] **Step 5:** `git commit -s -m "feat(api): comfyui switchover drives the GpuArbiter; pin endpoint; scripts retired"`

---

### Task D8: UI — arbitration status, pin toggle, "GPU: image mode" banner, mocks

**Files:**
- Modify: `ui/src/api/hooks/useComfyui.ts` (status type gains `arbiter`; `useComfyuiPin` mutation)
- Modify: `ui/src/dash/comfyui-pane.jsx` (arbiter mode chip + pin toggle + restore countdown; SwitchoverConfirm copy mentions LLM slots stopping/restoring)
- Modify: `ui/src/dash/primitives.jsx` (`BANNER_CATALOG` entry `gpu-image-mode`: scope global, kind info, heading "GPU: image mode", body "LLM slots are stopped while image generation holds the GPU — they restore automatically after idle." action → slots page; banner active when status.arbiter.mode === "img")
- Modify: `ui/src/dash/data.jsx` (mock comfyui status incl. arbiter block; img slot entry gains `profile: "comfyui"`, `runtime: "container"`)
- Test: γ spec `ui/tests/e2e/specs/comfyui-arbiter-v3.spec.ts` (create; apiMock pattern per profiles-crud-v3.spec.ts)

- [ ] **Steps:** failing γ spec (img-mode status → banner visible + pane shows mode chip + countdown; pin toggle issues POST /api/comfyui/pin; inference mode → banner gone) → implement → `cd ui && npx playwright test comfyui-arbiter 2>&1 | tail -3` + `npm run build` green → `git commit -s -m "feat(ui): GPU image-mode banner + arbiter status/pin in ComfyUI pane"`.
- Note: the drawer needs NO work — `device_class:"img"` profiles render read-only via the existing fixed-profile branch (slot-modals.jsx:769-787).

---

### Task D9: Gate, final Opus cross-task review, PR, CT105 docker→podman migration + e2e

- [ ] **Step 1:** full gate — `.venv/bin/python -m pytest tests/ --ignore=tests/harness -q -p no:randomly` (~30 min, background; known env-dependent failure: `tests/agents/test_hermes_provision_idempotency`); `ruff check src tests && ruff format --check src tests`; `cd ui && npm run build`.
- [ ] **Step 2:** FINAL CROSS-TASK OPUS REVIEW (working agreement — caught the only showstoppers in B and C; never skip). Focus: arbiter races (concurrent ensure_img/restore; dispatch during switch), persistence-recovery edge (api restart mid-switch), the Rule-6 `path_pinned` semantic change, port-range widening blast radius.
- [ ] **Step 3:** push, PR (`gh pr create --head feat/phase-d-comfyui-img-gpu-arbiter`), body: closes #599, closes #691; notes scripts retired (API path), sudoers story obsolete, manifest repinned to kyuz0; CI green → squash-merge, delete branch.
- [ ] **Step 4 (deploy, Tier 3 — shared host):** `wip hal0 claim "phase D img slot deploy" /etc/hal0/slots/img.toml`; verify `wip hal0 status` clean; backups: none needed for img (new slot) but snapshot `/etc/hal0/profiles.toml.bak-phase-d`; `ssh hal0 'cd /opt/hal0 && git pull && scripts/deploy.sh'` (rebuilds ui/dist — required, memory `hal0_ct105_deploy_rebuilds_ui`).
- [ ] **Step 5 (migration on CT105):**
  1. Seed live config: copy new img.toml → `/etc/hal0/slots/img.toml`; add comfyui profile to `/etc/hal0/profiles.toml` (CRUD API or seed-merge — remember REPLACE-on-load: write the FULL catalog).
  2. REQUEST-TIME REGISTRY PRECHECK: `curl 127.0.0.1:8080/api/models` — confirm curated image entries (sdxl-turbo at minimum) resolve with `model_class` + `hf_file` matching filenames under `/mnt/ai-models/comfyui/models/checkpoints/`. Missing → register via API/CLI (NEVER hand-splice registry.toml).
  3. Stop + remove the docker container: `docker rm -f comfyui` (image stays cached for rollback); `podman pull docker.io/kyuz0/amd-strix-halo-comfyui@sha256:0066678...` (podman pulls its own copy).
  4. `curl -X POST 127.0.0.1:8080/api/comfyui/switchover -d '{"mode":"generation"}'` → arbiter path: chat/agent/utility/rerank units stop, `hal0-slot@img` starts.
- [ ] **Step 6 (e2e matrix → PR comment):**
  1. `systemctl is-active hal0-slot@img` + `podman ps` shows kyuz0 digest + `curl 127.0.0.1:8188/system_stats` → 200
  2. `curl 127.0.0.1:8080/v1/images/generations -d '{"model":"sdxl-turbo","prompt":"a lighthouse at dusk","size":"1024x1024"}'` → 200 b64 PNG (gateway → dispatcher → container remote → infer)
  3. DURING a generation: `curl 127.0.0.1:8080/v1/chat/completions -d '{"model":"hal0/primary",...}'` → 503 + `Retry-After` header + code `gpu.image_mode`; dashboard shows the banner
  4. tts/npu/embed round-trips still 200 during img mode (non-GPU slots unaffected)
  5. Idle restore: temporarily set `idle_restore_minutes = 1` live → LLM slots return within ~90s; reset to 5
  6. Pin: `POST /api/comfyui/pin {"pinned":true}` → no restore after window; unpin → restores
  7. Switchover both directions from the dashboard UI (force-consent intact)
  8. Idle-CPU assertion (handoff follow-up): with img resident and queue empty, `podman stats --no-stream` CPU < 10% over 60s — the old "100%-CPU spin" must not reproduce under podman; if it does, capture `podman exec img ps aux` and file upstream (kyuz0) per third-party-first rule
  9. GTT: confirm LLM-mode GTT returns to ~51 GB after restore (#691 closed-loop evidence)
- [ ] **Step 7:** close-out — `wip release`; `tracker event lemonade-rm "Phase D shipped"` + task statuses; comment+close #599 #691; update memory `hal0_lemonade_removal_epic_phase_a_done.md` (D done, E next + debt registry); Phase E remains: lemonade extraction per spec §12-E.

---

## Self-review notes
- Spec coverage: §3 img row ✓ (D1/D2), §7 arbiter+drain+idle+pin+503 ✓ (D4/D5/D6), §7 docker→podman ✓ (D9), #599 ✓ (D1), #691 ✓ (arbiter unload + D9 evidence), builds on #686/#690 ✓ (D7/D8 preserve contracts), digest pin ✓ (D1 manifest), CPU-spin follow-up ✓ (D9.6.8 — not reproducible as a bug, assert instead).
- Type consistency: `GpuMode/GpuImageMode/ArbiterPinned`, `ensure_img(pin)/restore_llm(force)/guard_llm_dispatch/touch_img_activity/set_pin/status` used identically in D4-D7. `ImageGenConfig` named in D1, consumed in D4 (idle window) + v1 route (defaults; D5 may defer default_size wiring if the route already defaults — implementer verifies, the FIELD ships regardless for #599).
- Weakest points, flagged for the Opus review: (1) Rule-6 `path_pinned` reuse stretches the flag's name — semantic is "deterministic pin", acceptable; (2) `[image]` vs existing `SlotConfig.image` field-name collision — D1 Step 3 carries the contingency; (3) arbiter restore after api crash mid-switch relies on the persisted file written BEFORE unloads begin — D4 test orders persistence first.
- NOT in scope (Phase E, do not touch): lemonade_proxy fall-through, omni-router prefix pins (#695 guardrail), control-scripts deletion from disk, #687/#649.
