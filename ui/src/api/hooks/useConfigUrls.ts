// hal0 v3 dashboard — service URL discovery hook.
//
// Wraps GET /api/config/urls (src/hal0/api/routes/config.py). The backend
// derives reachable hostnames from the request host, so the dashboard never
// hardcodes where sibling services live — the same links work on localhost,
// a raw LAN IP, an mDNS `hal0.local` name, or a custom reverse-proxy domain.
//
// Per-service `*_enabled` flags say whether to advertise a link at all:
//   - openwebui: false on a host with the unit down; otherwise points at
//     HAL0_OPENWEBUI_PUBLIC_URL or the default http://<host>:3001 fallback.
//   - hermes:    false unless HAL0_HERMES_PUBLIC_URL is set — the dashboard
//     binds loopback-only (127.0.0.1:9119), so there's no host:port fallback.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface ConfigUrls {
  api: string
  openwebui: string
  openwebui_enabled: boolean
  hermes: string
  hermes_enabled: boolean
  // ComfyUI's own web UI. Defaults to http://<host>:8188 (the request host and ComfyUI's port);
  // a reverse-proxy deploy sets HAL0_COMFYUI_PUBLIC_URL to a clean HTTPS hostname so the link
  // isn't blocked as mixed content.
  comfyui: string
  // True only when `comfyui` is the URL the operator declared (HAL0_COMFYUI_PUBLIC_URL), which
  // answers wherever they declared it for. False for the derived http://<host>:8188. Absent from
  // an older hal0.
  comfyui_public?: boolean
  // True while ComfyUI binds the LAN ([network].comfyui_listen = "lan", the default), so the
  // derived http://<host>:8188 answers from other machines too. False under "loopback", where it
  // answers on the box itself only. Absent from an older hal0 (which was always loopback).
  comfyui_lan?: boolean

}

export function useConfigUrls() {
  return useQuery({
    queryKey: ['config', 'urls'],
    queryFn: () => apiGet<ConfigUrls>(ENDPOINTS.configUrls),
    // Hostnames/ports only change on a redeploy — cache generously so the
    // sidebar widget doesn't refetch on every mount.
    staleTime: 60_000,
  })
}
