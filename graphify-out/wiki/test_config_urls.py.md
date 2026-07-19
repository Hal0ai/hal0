# test_config_urls.py

> 29 nodes · cohesion 0.10

## Key Concepts

- **test_config_urls.py** (14 connections) — `tests/api/test_config_urls.py`
- **TestClient** (13 connections)
- **test_urls_api_port_honours_env()** (3 connections) — `tests/api/test_config_urls.py`
- **test_urls_behind_proxy_without_public_url_uses_openwebui_port()** (3 connections) — `tests/api/test_config_urls.py`
- **test_urls_comfyui_behind_proxy_without_env_uses_port_8188()** (3 connections) — `tests/api/test_config_urls.py`
- **test_urls_comfyui_lan_direct_default_port_8188()** (3 connections) — `tests/api/test_config_urls.py`
- **test_urls_comfyui_public_url_env_wins()** (3 connections) — `tests/api/test_config_urls.py`
- **test_urls_hermes_advertised_even_behind_proxy()** (3 connections) — `tests/api/test_config_urls.py`
- **test_urls_hermes_keys_present_and_hidden_by_default()** (3 connections) — `tests/api/test_config_urls.py`
- **test_urls_hermes_public_url_env_wins()** (3 connections) — `tests/api/test_config_urls.py`
- **test_urls_openwebui_enabled_false_when_systemctl_missing()** (3 connections) — `tests/api/test_config_urls.py`
- **test_urls_public_url_env_overrides_lan_direct()** (3 connections) — `tests/api/test_config_urls.py`
- **test_urls_public_url_env_wins_behind_proxy()** (3 connections) — `tests/api/test_config_urls.py`
- **test_urls_returns_three_required_keys()** (3 connections) — `tests/api/test_config_urls.py`
- **test_urls_use_request_host()** (3 connections) — `tests/api/test_config_urls.py`
- **Tests for /api/config/urls.  The dashboard reads this endpoint on mount to disco** (1 connections) — `tests/api/test_config_urls.py`
- **The env var also overrides the LAN-direct host:3001 default.** (1 connections) — `tests/api/test_config_urls.py`
- **Hermes keys are always present; hidden (loopback) without the env var.      Herm** (1 connections) — `tests/api/test_config_urls.py`
- **HAL0_HERMES_PUBLIC_URL is the canonical override for the hermes link.** (1 connections) — `tests/api/test_config_urls.py`
- **The hermes public URL is honoured on reverse-proxy deploys too.** (1 connections) — `tests/api/test_config_urls.py`
- **All three keys land in the response, with the documented types.** (1 connections) — `tests/api/test_config_urls.py`
- **ComfyUI's own web UI is advertised at the request host on :8188.      The dashbo** (1 connections) — `tests/api/test_config_urls.py`
- **HAL0_COMFYUI_PUBLIC_URL is the canonical override.      This is how a reverse-pr** (1 connections) — `tests/api/test_config_urls.py`
- **Proxy deploys without the env var still get a host:8188 link.      The port-stri** (1 connections) — `tests/api/test_config_urls.py`
- **Both URLs echo the hostname the request came in on (not localhost).** (1 connections) — `tests/api/test_config_urls.py`
- *... and 4 more nodes in this community*

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/api/test_config_urls.py`

## Audit Trail

- EXTRACTED: 80 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*