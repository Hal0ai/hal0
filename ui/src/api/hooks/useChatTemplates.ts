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
}

export function useChatTemplates() {
  return useQuery({
    queryKey: ['chat-templates'],
    queryFn: () => apiGet<ChatTemplate[]>(ENDPOINTS.chatTemplates),
    staleTime: 300_000,
  })
}
