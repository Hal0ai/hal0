// What the dashboard says in place of a link hal0 would not build. `GET /api/config/urls` returns
// `openwebui` / `comfyui` as "" with `openwebui_reason` / `comfyui_reason` set to this value when
// the dashboard was reached through a reverse proxy and HAL0_<APP>_PUBLIC_URL is not declared: the
// proxy's :3001 / :8188 is not the app (src/hal0/api/routes/config.py, PROXY_NEEDS_PUBLIC_URL).

export const PROXY_NEEDS_PUBLIC_URL = 'proxy_needs_public_url'

/** The hint shown in place of the link. `app` is the `<APP>` of the env var. */
export const publicUrlHint = (app: 'OPENWEBUI' | 'COMFYUI') =>
  `Set HAL0_${app}_PUBLIC_URL in /etc/hal0/api.env`
