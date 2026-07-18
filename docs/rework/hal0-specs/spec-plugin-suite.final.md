I have everything needed. Here is the implementation-ready build spec.

---

# hal0 Hermes Plugin Suite — BUILD Spec (verified vs code @ `rework/descar`)

## 0. Verification summary — docs are stale, here's ground truth

**Repo surfaces the plugins wire to (file:line, all confirmed live):**

| hal0 surface | Route handler | Notes |
|---|---|---|
| `POST /v1/chat/completions` | `src/hal0/api/routes/v1.py:826` | composite `hal0/agent` upstream; chat already works via config `provider:custom` + `base_url=…:8080/v1` |
| `GET /v1/models` | `src/hal0/api/routes/v1.py:673` (public_router) | live discovery; `X-hal0-Model-Filter: hal0` narrows to owned_by==hal0 |
| `POST /api/memory/{add,search,recall}` | `src/hal0/api/routes/memory.py:911 / 982 / 1026` | Hindsight front door; identity via `X-hal0-Agent`, private opt-in via `X-hal0-Private: 1` |
| `POST /v1/images/generations` | `src/hal0/api/routes/v1.py:1212` | ComfyUI provider; OpenAI shape in/out; img-slot cold-start |
| `POST /v1/audio/speech` | `src/hal0/api/routes/v1.py:1121` | TTS input dir; JSON `{model,input,voice,speed,response_format}` → binary audio (kokoro/qwen3tts) |

**Memory bank semantics (confirmed in `memory.py`):** write dataset resolved server-side from headers via `resolve_write_dataset(dataset, private, client_id)` — `X-hal0-Private:1` → `private:<agent>`, else `shared`. Reads via `resolve_read_datasets` → private-mode caller sees `[shared, private:<agent>]` union. **Client must NEVER send `dataset`** (and `source` is rejected — server-injected from header). This is exactly what `_client.py` already does.

**hermes_provision mechanics (confirmed):**
- `_copy_plugin_tree(src, dst)` — `hermes_provision.py:965` (rmtree+copytree, idempotent).
- Ship list = `plugin_targets` dict in `_phase_install` — **`hermes_provision.py:1053`** (currently only `{"hal0-memory": hermes_home/"plugins"/"hal0-memory"}`). This is the single add-point for new plugins.
- Plugin source root = `installer/agents/hermes/plugins/` (`hermes_provision.py:995`).
- Config keys applied via `hermes config set <dotted.key> <value>` — appliers `_build_config_overlay` (`:1514`) + `_apply_config_set` (`:1670`); list-valued keys via PyYAML deep-merge `_merge_config_yaml_layers` (`:1700`). **config.yaml is hermes-owned — never rewrite.** `_fmt_config_value` (`:1501`) handles bool/scalar; lists must NOT go through config set.
- Voice already wired: `_phase_voice_wire` (`:4287`) sets `tts.provider openai` + `TTS_OPENAI_BASE_URL` in secrets env when a tts slot is ready — **this is the pattern hal0-tts replaces/augments.**
- Secrets env: `HERMES_SECRETS_ENV = /var/lib/hal0/secrets/agents/hermes.env` (`:3948`, mode 0600, sourced by wrapper). Overrides: `/etc/hal0/agents/hermes/overrides.yaml` (`:1743`).
- Wrapper `installer/wrappers/hermes` injects `HAL0_AGENT_ID` (default `hermes`) → `X-hal0-Agent`.

---

## 1. hal0-memory — dedupe + upstream-alignment

### 1a. The actual copy state (the plan's "3→1" is partly done and partly wrong)

The task's premise ("confirm the 3-copy state") resolves to:

1. **`installer/agents/hermes/plugins/hal0-memory/`** — 4 files (`__init__.py`, `provider.py`, `_client.py`, `plugin.yaml`), **no README**. This is the shipped **seed** (`_copy_plugin_tree` copies it verbatim). Dir name has a hyphen → **not importable as a Python module**.
2. **`src/hal0/agents/hermes/plugins/memory_hindsight/`** — the **same 4 files, byte-identical** (`diff -q` clean) **+ `README.md`**. Valid module name → **importable** as `hal0.agents.hermes.plugins.memory_hindsight` for unit tests in hal0's own venv.
3. **pi_coder TS reimpl** — **GONE** (deleted with pi_coder; only unrelated substring matches remain in `tests/agents/test_hermes_wrapper.py` / `test_agent_memory_stats_endpoint.py`).

So on disk today = **2 copies, byte-identical, and this is a DELIBERATE source+seed pair**, not drift:
- **`tests/agents/hermes_plugins/test_seed_parity.py`** asserts the two dirs are byte-identical for `[__init__.py, _client.py, provider.py, plugin.yaml]` **and that the seed contains no extra files** (`test_no_unexpected_seed_files`).
- **`tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`** imports the `memory_hindsight` package the normal way (for coverage attribution).
- **`tests/agents/test_hal0_memory_client.py`** loads the **installer** copy via importlib (contract tests).

### 1b. Canonical = ambiguous in docs; here's the reconciliation the implementer must make

**Three docstrings contradict each other and reality:**
- `installer/.../hal0-memory/__init__.py:5-8` says memory_hindsight "was deleted — this directory is the only copy now." — **FALSE.**
- `src/hal0/agents/hermes/__init__.py:9-11` says memory_hindsight "was removed rather than reconciled." — **FALSE.**
- `src/hal0/agents/hermes/plugins/memory_hindsight/README.md:7-9` says memory_hindsight is where it "lives" and the installer is "a byte-identical copy (enforced by `test_seed_parity.py`)." — **TRUE / matches the parity test.**

**Decision for the build:** the README is correct. `memory_hindsight/` (importable) is the **canonical source**; `hal0-memory/` (hyphen) is the **shipped seed**; parity is test-locked. **Do NOT delete either copy** — deleting `memory_hindsight` breaks two test modules + removes the only importable copy; deleting the seed breaks provisioning. The real "dedupe" work is:

1. **Fix the two stale docstrings** (`installer/.../hal0-memory/__init__.py` and `src/hal0/agents/hermes/__init__.py`) to state the true source(=memory_hindsight)/seed(=hal0-memory)/parity-test relationship, matching the README. (Docs-only; no behavior change; no test breakage.)
2. If a genuine single-copy is wanted later, the only safe route is replacing the seed dir with a build step / symlink resolved at provision time — **out of scope and not worth it**; the parity test already makes 2 copies safe. Recommend: keep 2, fix docs. **This is the "dedupe 3→1" — pi_coder gone (3→2), and 2 is the intended floor.**

### 1c. MemoryProvider surface — what's implemented vs. what the task lists

Implemented today (`provider.py`), all against the vendored-ABC + import-fallback pattern (`try: from agent.memory_provider import MemoryProvider` / `except ImportError:` local ABC — `provider.py:30-47`):

| Method | Status | Location |
|---|---|---|
| `name` (property) | ✅ returns `"hal0-memory"` | `provider.py:136` |
| `is_available()` | ✅ `True` (config-only, no net) | `:142` |
| `initialize(session_id, **kwargs)` | ✅ builds sync client, reads `HAL0_MEMORY_BASE`/`HAL0_AGENT_ID`, honors `agent_context` | `:147` |
| `get_tool_schemas()` | ✅ | `:227` |
| `handle_tool_call(tool_name, args, **kwargs)` | ✅ | `:230` |
| `prefetch(query, *, session_id)` | ✅ `/api/memory/recall`, 2048 tok, empty-on-fail | `:181` |
| `sync_turn(user, assistant, *, session_id)` | ✅ fire-and-forget `/api/memory/add`, skips cron/flush/subagent | `:208` |
| `system_prompt_block()` | ✅ two-bank preamble | `:170` |
| `on_memory_write(...)`, `shutdown()` | ✅ | `:278 / :157` |
| **`get_config_schema()`** | ❌ **NOT present** — must add | — |
| **`save_config(...)`** | ❌ **NOT present** — must add | — |

**Build item M1 — add config surface (upstream-shape alignment).** Add `get_config_schema()` returning the JSON-schema for `{base_url, agent_id}` (defaults `http://127.0.0.1:8080` / `hermes`) and `save_config(cfg)` persisting to **`~/.hermes/hindsight/config.json`** with `mode: "local_external"`, `api_url: "<base>/api/memory"`, and hal0's bank extension (`private_bank_template: "private:{agent}"`, `shared_bank: "shared"`). This mirrors the upstream hindsight plugin's `~/.hermes/hindsight/config.json` `local_external` layout (ref `hermes-hindsight-plugin.md:5-8`) while keeping hal0's front door + dual banks. Resolution order stays: ctor arg → env (`HAL0_MEMORY_BASE`/`HAL0_AGENT_ID`) → `config.json` → default.

**Build item M2 — align tool NAMES to upstream (keep behavior + front door).** Rename the three tool schemas + their `handle_tool_call` branches:
- `hal0_memory_search` → keep as an alias, but primary = **`hindsight_recall`** (semantic/entity — maps to `_client.search` or `.recall`)
- `hal0_memory_recall` → **`hindsight_recall`** (token-budgeted; consolidate: one `hindsight_recall` tool covering both, or keep search under recall)
- `hal0_memory_add` → **`hindsight_retain`** (store + entity extract → `_client.add`, keep `shared` param for the shared-bank extension)
- add **`hindsight_reflect`** (cross-memory synthesis → `_client.recall` with a synthesis/`types` hint, best-effort)

Upstream tool set = `hindsight_retain` / `hindsight_recall` / `hindsight_reflect` (ref `:10`). Keep the hal0-only `shared=true` param on `retain` (upstream has no shared bank). **Files touched:** `provider.py` (schemas `SEARCH_SCHEMA`/`RECALL_SCHEMA`/`ADD_SCHEMA` + `ALL_TOOL_SCHEMAS` + `handle_tool_call` dispatch), the **byte-identical seed copy**, and **both tests** (`test_memory_hindsight_plugin.py`, `test_hal0_memory_client.py`) + parity holds automatically if you edit both dirs. `_client.py` REST verbs stay unchanged (endpoints don't move; only tool-facing names change).

**Manifest:** `plugin.yaml` stays `kind: exclusive` (MemoryManager single-external-provider invariant — do not change). Bump `version` 1.1.0 → 1.2.0.

**Env vars:** `HAL0_MEMORY_BASE` (default `http://127.0.0.1:8080`, loopback), `HAL0_AGENT_ID` (default `hermes`, injected by wrapper → `X-hal0-Agent`). Both read at `initialize()`.

**Ship:** already in `plugin_targets` (`hermes_provision.py:1053`). Config apply already present: `memory.provider hal0-memory` (`_build_config_overlay:1602`). **Optionality:** activated only when `memory.provider == hal0-memory`; operator can set `honcho` or a built-in instead (`_resolve_memory_provider` exists). No change needed to stay optional.

---

## 2. hal0-image — `ImageGenProvider`, `kind: backend`

**Dir:** `installer/agents/hermes/plugins/hal0-image/` (+ importable mirror `src/hal0/agents/hermes/plugins/image_hal0/` **only if** you want unit coverage the same way memory has it; otherwise ship seed-only and test via importlib like `test_hal0_memory_client.py`). Recommend seed-only + importlib test to avoid a second parity lock.

**Files:**
- `plugin.yaml`:
  ```yaml
  name: hal0-image
  kind: backend
  version: 1.0.0
  description: hal0-image provider — OpenAI-compatible image generation via hal0-api /v1/images/generations (ComfyUI).
  ```
- `_client.py` — sync `httpx.Client` (same rationale as memory: hooks are sync), `base_url` from `HAL0_IMAGE_BASE` (default `http://127.0.0.1:8080`), `X-hal0-Agent` header, **180s read timeout** (img-slot cold-start — plan §18 gotcha), 3s connect.
- `__init__.py` — vendored-ABC + import-fallback:
  ```python
  try:
      from agent.image_gen_provider import ImageGenProvider  # resolves in hermes venv
  except ImportError:
      from abc import ABC, abstractmethod
      class ImageGenProvider(ABC):
          @property
          @abstractmethod
          def name(self) -> str: ...
          @abstractmethod
          def generate(self, prompt, *, aspect_ratio=None, **kw): ...
  ```
  Provide **both** discovery paths (mirror memory): top-level `Hal0ImageProvider` subclass **and** `register(ctx)`:
  ```python
  def register(ctx):
      ctx.register_image_gen_provider(Hal0ImageProvider())
  ```
- `provider.py` — `Hal0ImageProvider(ImageGenProvider)`:
  - `name` → `"hal0"` (so it shadows/pins as the hal0 image backend).
  - `generate(self, prompt: str, aspect_ratio: str | None = None, *, n=1, **kw) -> ...`:
    - Map `aspect_ratio` → `size` (`"1:1"`→`"1024x1024"`, `"16:9"`→`"1344x768"`, `"9:16"`→`"768x1344"`, default `"1024x1024"`); accept explicit `size` passthrough.
    - `POST {base}/v1/images/generations` with `{"model": kw.get("model","sdxl-turbo"), "prompt": prompt, "n": n, "size": size, "response_format": "b64_json", "extra_body": {seed,steps,cfg,negative_prompt}}` (only curated models — server enforces: current built-ins `sdxl-turbo`, `sd-1.5-pruned-emaonly`, `v1.py:1316`).
    - **`success_response` / `save_b64_image`**: read `data[].b64_json`, base64-decode, write PNG to hermes's image output dir, return the provider's image result object (path + meta). Response shape confirmed at `v1.py:1354-1373` (`{created, data:[{b64_json}|{url}], _hal0:{...}}`).
  - `capabilities()` / `list_models()` → GET `{base}/v1/models` filtered to `owned_by==hal0` + `capability==image` (or hardcode the two curated ids as fallback).

**Env:** `HAL0_IMAGE_BASE` (default `http://127.0.0.1:8080`, loopback).

**hermes_provision changes:**
- `plugin_targets` add: `"hal0-image": hermes_home / "plugins" / "image_gen" / "hal0-image"` — **note dir root differs**: image-gen plugins live under `plugins/image_gen/` (plan §18: "`plugins/`, `plugins/image_gen/`"; the standard subdir `plugins/model-providers` already exists at `home_init:1279`). Ensure `_phase_home_init`'s `standard_subdirs` gains `"plugins/image_gen"`.
- Config apply (new pair in `_build_config_overlay`, applied via `hermes config set`): `("image_gen.provider", "hal0")`. Scalar → safe through config set. **Never** in a whole-file rewrite (gotcha: clobbered on migrate).

**Optionality:** only registered if the plugin dir is present + `image_gen.provider == hal0`. Absent/other value → hermes uses its own image path. Ship it but make the `image_gen.provider` set conditional (e.g. only when an img slot exists — reuse the `_find_slot(slots,"img")`-style guard so hosts without an img slot don't force it).

---

## 3. hal0-tts — config-only command provider + `hal0-tts-speak` shim

TTS is **not** an ABC plugin — it's a `config.yaml` `tts.providers.hal0` block of `type: command` plus an executable shim on PATH. (Contrast with the existing `_phase_voice_wire` which wires `tts.provider: openai` + `TTS_OPENAI_BASE_URL`; hal0-tts is the alternative "command" mechanism that shells out to a hal0 shim instead of hermes's OpenAI client.)

**config.yaml block (applied via `hermes config set`, all scalars):**
```
tts.providers.hal0.type     = command
tts.providers.hal0.command  = hal0-tts-speak --input {input_path} --output {output_path} --voice {voice} --model {model} --format {format}
tts.provider                = hal0
```
Placeholders `{input_path}{output_path}{voice}{model}{format}` are hermes's command-provider substitution tokens. Each key is a scalar → goes through `config set` fine (`_fmt_config_value` handles strings). **Name MUST be `hal0`** — built-in provider names shadow, and `tts.provider hal0` selects it (plan §18 gotcha: "TTS name must be `hal0`").

**The shim `hal0-tts-speak`:**
- **Installs to** `/usr/local/bin/hal0-tts-speak` (same dir as the `hermes` wrapper, `HERMES_CLI_INSTALL_PATH` neighbor; on PATH for the hal0 service user). Source lives at **`installer/wrappers/hal0-tts-speak`** (alongside `installer/wrappers/hermes`). Copied + chmod 0755 by a provision step mirroring `_copy_wrapper` (`hermes_provision.py:930`).
- **Behavior:** read text — command provider convention is hermes writes the text to `{input_path}` and expects audio at `{output_path}`. Shim: read `--input` file → `POST {HAL0_TTS_BASE}/v1/audio/speech` with `{"model": model, "input": <text>, "voice": voice, "response_format": <fmt from --format>}` → write the binary response body to `--output`. Endpoint confirmed `v1.py:1121`; body `{model,input,voice,speed,response_format}`, returns binary audio; `model` is required (400 otherwise, `v1.py:1132`). Add `X-hal0-Agent: ${HAL0_AGENT_ID:-hermes}`. No auth (loopback LAN).
- Language: POSIX `sh` + `curl` (matches `installer/wrappers/hermes` which is `/bin/sh`), or a tiny Python using stdlib `urllib` for robustness. Prefer `sh`+`curl` to avoid a venv dependency on PATH.

**Env:** `HAL0_TTS_BASE` (default `http://127.0.0.1:8080`, loopback); `HAL0_AGENT_ID` (from environment/wrapper).

**hermes_provision changes:**
- New shim-copy call in `_phase_install` (mirror `_copy_wrapper`): copy `installer/wrappers/hal0-tts-speak` → `/usr/local/bin/hal0-tts-speak`, chmod 0755, `_chown_tree_to_hal0` not needed (root-owned bin is fine, it's world-executable).
- Config apply: the three `tts.providers.hal0.*` + `tts.provider` pairs added to `_build_config_overlay` (or a dedicated tts overlay), applied via `_apply_config_set`. **Guard**: only set `tts.provider hal0` when a tts slot is ready (reuse `_find_slot(slots,"tts")` from `_phase_voice_wire`) so hosts without TTS don't force it. This should coordinate with / supersede the `tts.provider openai` line in `_phase_voice_wire:4339` — decide one mechanism (recommend hal0-tts command provider replaces the openai wiring for local slots; keep openai for remote).
- No `plugin_targets` entry (no plugin dir — it's config + a bin shim).

**Optionality:** absent shim + unset `tts.provider` → hermes falls back to its own TTS. Fully optional.

---

## 4. hal0-provider — optional `ProviderProfile` (aux-slot-local)

**Status: genuinely optional.** Chat already works today without it: `_build_config_overlay:1547-1579` sets `model.provider custom`, `model.base_url http://127.0.0.1:8080/v1`, `providers.custom.name hal0`, `providers.custom.discover_models true`, `providers.custom.extra_headers.X-hal0-Model-Filter hal0`, `model.default hal0/agent`. The old `hal0` model-provider plugin was **removed** (dead `base_url=127.0.0.1:8000/api/v1`; `_phase_install` even rm's a leftover at `:1059`). Do not resurrect that.

**Its only real value (plan §18):** pinning the **aux slot** local via `default_aux_model`, so compression/vision/summarization/web_extract LLM calls also stay on hal0. Note hal0 **already** wires aux via `auxiliary.<task>` config (`_build_config_overlay:1636-1644`, `_resolve_auxiliary_tasks:3831`) pointing tasks at the utility-slot `base_url`. So a `ProviderProfile` is **redundant with existing aux wiring** unless hermes's `ProviderProfile.default_aux_model` gives finer control than `auxiliary.*` config.

**If built:**
- **Dir:** `installer/agents/hermes/plugins/hal0-provider/` (+ optional importable mirror). Manifest `kind: provider` (or upstream's provider-profile kind — confirm against hermes venv; `MemoryProvider`=exclusive, `ImageGenProvider`=backend, `ProviderProfile`=provider). `plugin.yaml`:
  ```yaml
  name: hal0
  kind: provider
  version: 1.0.0
  description: hal0 ProviderProfile — pins chat + aux slots to local hal0-api.
  ```
- **Class fields (base):** `name = "hal0"`, `base_url = "http://127.0.0.1:8080/v1"`, `api_mode = "chat_completions"`, `default_aux_model = "hal0/agent"` (or a utility-slot alias). Vendored-ABC + import-fallback (`try: from agent.provider_profile import ProviderProfile`). `register(ctx).register_provider_profile(...)` if that seam exists; otherwise top-level subclass discovery.
- **Env:** `HAL0_PROVIDER_BASE` (default `http://127.0.0.1:8080/v1`).
- **Ship:** `plugin_targets` add `"hal0-provider": hermes_home/"plugins"/"model-providers"/"hal0"` (dir root `plugins/model-providers/` — already a `standard_subdir` at `home_init:1279`; this is where the removed legacy plugin lived, `:1059`). No forced config key beyond the existing `providers.custom.*`.

**Recommendation:** ship as a no-op-by-default optional; the existing `providers.custom` + `auxiliary.*` wiring already delivers local chat + local aux, so gate this behind a flag (`HAL0_HERMES_PROVIDER_PROFILE=1`) and don't add it to `plugin_targets` by default. Its incremental value over current config is marginal — build last, or skip.

---

## 5. Cross-cutting: how all stay OPTIONAL + the ship checklist

**Standing constraint held ✓:** every plugin uses the `try: from agent.<X> import <ABC> / except ImportError: local ABC` pattern (memory already does, `provider.py:30`), so **hal0 core never imports hermes** and each module stays importable/testable in hal0's own venv.

**Optionality mechanism (uniform):**
- Presence in `$HERMES_HOME/plugins/…` only enables discovery; **activation is a config key** hermes reads: `memory.provider`, `image_gen.provider`, `tts.provider`. Unset/other value → built-in fallback. hal0 sets each key **conditionally** (guard on the relevant slot being ready via `_find_slot`), so a host missing an img/tts slot never gets a broken forced provider.
- All keys go through `hermes config set` (idempotent, `_apply_config_set:1670`) — **never a whole-file render** (would clobber `image_gen.provider`/`tts.provider` on `hermes config migrate`, plan §18 gotcha).

**Single edit-point per plugin in `hermes_provision.py`:**
1. `plugin_targets` dict (`:1053`) — add dir-drop entries for hal0-image (`plugins/image_gen/hal0-image`) and hal0-provider (`plugins/model-providers/hal0`); memory already there; hal0-tts has none.
2. `_phase_home_init` `standard_subdirs` (`:1275`) — add `"plugins/image_gen"` (model-providers already present).
3. `_build_config_overlay` (`:1514`) — add scalar pairs `image_gen.provider=hal0`, `tts.providers.hal0.{type,command}`, `tts.provider=hal0` (guarded); memory's `memory.provider` already there.
4. `_phase_install` (`:972`) — add a `_copy_wrapper`-style copy for `installer/wrappers/hal0-tts-speak` → `/usr/local/bin/hal0-tts-speak`.

**New source files to create:**
- `installer/agents/hermes/plugins/hal0-image/{plugin.yaml,__init__.py,provider.py,_client.py}`
- `installer/wrappers/hal0-tts-speak` (sh+curl shim)
- (optional) `installer/agents/hermes/plugins/hal0-provider/{plugin.yaml,__init__.py,profile.py}`
- (optional importable mirrors under `src/hal0/agents/hermes/plugins/…` + parity tests, matching the memory pattern — only if unit coverage is wanted)

**Docs to fix (dedupe):** `installer/agents/hermes/plugins/hal0-memory/__init__.py:5-8` and `src/hal0/agents/hermes/__init__.py:9-11` both falsely claim `memory_hindsight` was deleted; correct them to describe the source(`memory_hindsight`)/seed(`hal0-memory`)/parity-test relationship per the (accurate) `memory_hindsight/README.md`.

**Env var defaults (all loopback, all read at init/runtime):** `HAL0_MEMORY_BASE`, `HAL0_IMAGE_BASE`, `HAL0_TTS_BASE`, `HAL0_PROVIDER_BASE` → `http://127.0.0.1:8080` (image/tts append `/v1/...` in the path; provider uses `…:8080/v1`); `HAL0_AGENT_ID` → `hermes` (wrapper-injected → `X-hal0-Agent`). No Bearer anywhere (post-ADR-0012 LAN identity model). External creds (if any) only ever in `/var/lib/hal0/secrets/agents/hermes.env` (0600), never in world-readable config.yaml.