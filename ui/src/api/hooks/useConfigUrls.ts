// hal0 v3 dashboard — service URL discovery hook.
//
// Wraps GET /api/config/urls (src/hal0/api/routes/config.py). The backend
// derives reachable hostnames from the request host, so the dashboard never
// hardcodes where sibling services live — the same links work on localhost,
// a raw LAN IP, an mDNS `hal0.local` name, or a custom reverse-proxy domain.
//
// Per-service `*_enabled` flags say whether to advertise a link at all:
//   - openwebui: false on a host with the unit down, or behind a proxy with
//     no HAL0_OPENWEBUI_PUBLIC_URL (OWUI can't be path-prefixed).
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
