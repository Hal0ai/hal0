# hal0-provider (Hermes `ProviderProfile` plugin)

Advertises the local **hal0-api** to Hermes as an OpenAI-compatible inference
provider so both the **chat** slot and the **aux** slot (compression, vision,
summarization, web_extract) stay local.

## Two copies (source + seed)

* **Canonical source (this dir):** `src/hal0/agents/hermes/plugins/provider_hal0/`
  — a valid Python module name, so hal0's own venv can `import
  hal0.agents.hermes.plugins.provider_hal0` for unit tests.
* **Installer seed:** `installer/agents/hermes/plugins/hal0-provider/` — a
  byte-identical copy (enforced by
  `tests/agents/hermes/plugins/test_hal0_provider_parity.py`). The hyphenated
  dir name is not importable, but it is what `hermes_provision._phase_install`
  copies verbatim into `$HERMES_HOME/plugins/model-providers/hal0/`.

Edit **both** copies together; the parity test fails otherwise.

## Contract

* Profile fields (frozen pin `9de9c25f`, `providers.base.ProviderProfile`):
  `name = "hal0"`, `api_mode = "chat_completions"`,
  `base_url = "http://127.0.0.1:8080/v1"` (`HAL0_PROVIDER_BASE`),
  `default_aux_model = "hal0/agent"`, `supports_vision = True`.
* Registration seam: **module-level** `providers.register_provider(profile)`
  (the general `PluginContext` has no provider registrar). This plugin ships a
  top-level `Hal0ProviderProfile` subclass, a `PROFILE` instance, a
  `register(ctx)` fallback, and a best-effort import-time registration.
* `fetch_models` does live `/v1/models` discovery with
  `X-hal0-Model-Filter: hal0`, drops routing aliases (`is_alias`), and holds no
  cache — every call reflects the current inventory, so retargeting a role on
  the hal0 side hot-swaps with no Hermes restart.

## Provisioning requirement

`hermes_provision._phase_install`'s `plugin_targets` dict must gain:

```python
"hal0-provider": hermes_home / "plugins" / "model-providers" / "hal0",
```

(`plugins/model-providers` is already a `standard_subdir`.) No forced config
key beyond the existing `providers.custom.*` wiring.
