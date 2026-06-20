// hal0 v3 dashboard — chat-templates hook (Phase 3 Task 4).
//
// Fetches /api/chat-templates — the list of available chat template ids
// (e.g. chatml, llama3) that can be pinned as per-model defaults.
// The "auto" sentinel (use the GGUF's embedded template) is added UI-side
// as the first option; the backend omits it from the catalogue.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface ChatTemplate {
  id: string
  label: string
  // Best-effort render lint from the backend (see api/routes/chat_templates.py).
  // `valid` is false when a template in the store dir fails to render against a
  // sample conversation; `error` carries the short reason. `auto` is always
  // valid. Absent on older backends → treat as valid (optional fields).
  valid?: boolean
  error?: string | null
}

// `enabled` gates the fetch — pass the host modal/drawer's `open` so the
// query only runs while the picker is actually visible. Without this, an
// always-mounted-but-closed editor would fire the request on every page
// visit and re-render (detaching sibling controls mid-interaction).
export function useChatTemplates(enabled: boolean = true) {
  return useQuery({
    queryKey: ['chat-templates'],
    queryFn: () => apiGet<ChatTemplate[]>(ENDPOINTS.chatTemplates),
    staleTime: 300_000,
    enabled: !!enabled,
    // A catalog read — don't retry-storm on failure (it just churns re-renders
    // of the host modal); settle to the empty list and let the UI fall back to
    // the always-present "auto" option.
    retry: false,
  })
}
