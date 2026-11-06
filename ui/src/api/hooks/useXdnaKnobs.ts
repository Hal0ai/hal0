// hal0 v3 dashboard — GET /api/runners/xdna/knobs.
//
// The GGML_XDNA_* env knob catalogue + the pmode enum + the host_xrt doc
// string, for the slot drawer's XDNA engine-settings section. Static per
// hal0 build (no polling needed beyond the default staleTime): the knob
// catalogue does not change while the dashboard is open.
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'

export interface XdnaEnvKnob {
  name: string
  default: string | null
  doc: string
}

export interface XdnaKnobs {
  pmodes: string[]
  env: XdnaEnvKnob[]
  host_xrt: { doc: string }
}

export function useXdnaKnobs() {
  return useQuery<XdnaKnobs>({
    queryKey: ['runners', 'xdna', 'knobs'],
    queryFn: () => apiGet<XdnaKnobs>('/api/runners/xdna/knobs'),
    staleTime: Infinity,
  })
}
