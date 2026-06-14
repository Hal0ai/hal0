// hal0 dashboard overhaul — W6: power/thermal stats hook
//
// GET /api/stats/power (NEW endpoint, §5 spike confirmed real via /sys/class/hwmon).
// Polls every 5s. Fail-soft: 404 → isPending=true so PowerCard shows gated
// "source pending" body rather than crashing.
//
// Shape confirmed by BE spike: { gpu_power_w, gpu_temp_c, gpu_sclk_mhz, cpu_temp_c }
// All fields nullable — absent means hwmon sensor not exposed (fanless box,
// no fan field at all).

import { useQuery } from '@tanstack/react-query'
import { apiGet, Hal0Error } from '../client'

export interface StatsPower {
  gpu_power_w: number | null
  gpu_temp_c: number | null
  gpu_sclk_mhz: number | null
  cpu_temp_c: number | null
}

export interface UseStatsPowerResult {
  data: StatsPower | null
  isPending: boolean
  isError: boolean
}

const POLL_MS = 5_000
const POWER_URL = '/api/stats/power'

export function useStatsPower(): UseStatsPowerResult {
  const query = useQuery<StatsPower>({
    queryKey: ['stats', 'power'],
    queryFn: async () => {
      try {
        return await apiGet<StatsPower>(POWER_URL)
      } catch (err) {
        // 404 = endpoint not yet deployed; treat as pending not crash.
        if (
          err instanceof Hal0Error &&
          (err.status === 404 || err.status === 0 || err.status === 501)
        ) {
          // Return null to signal pending; isPending gate picks it up below.
          return null as unknown as StatsPower
        }
        throw err
      }
    },
    refetchInterval: POLL_MS,
    retry: false,
    staleTime: 0,
  })

  const isPending =
    query.isLoading ||
    query.isError ||
    !query.data

  return {
    data: query.data ?? null,
    isPending,
    isError: query.isError,
  }
}
