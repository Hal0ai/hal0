// Download speed and time left for each running install, from successive polls of /jobs.
//
// `jobRate` (image-models-pure.ts) turns two samples of the same job into a rate; this hook keeps
// the previous sample of each running job and hands back the rates. The clock is the time the
// poll landed (`dataUpdatedAt`), not the render's, so a re-render that carries no new poll
// changes nothing.
import { useEffect, useRef, useState } from 'react'
import { isImageJobActive } from '@/api/hooks/imageJobState'
import type { ImageJob } from '@/api/hooks/useComfyui'
import { jobRate } from './image-models-pure'

export type JobRates = Record<string, { mbPerS: number; etaS: number }>

interface Sample {
  job: ImageJob
  at: number
}

const sameRates = (a: JobRates, b: JobRates): boolean => {
  const ka = Object.keys(a)
  return (
    ka.length === Object.keys(b).length &&
    ka.every((k) => b[k] && a[k].mbPerS === b[k].mbPerS && a[k].etaS === b[k].etaS)
  )
}

/** Rates by model id, for the jobs still queued or running. `updatedAt` is `dataUpdatedAt` (ms). */
export function useJobRates(jobs: readonly ImageJob[] | undefined, updatedAt: number): JobRates {
  const samples = useRef(new Map<string, Sample>())
  const [rates, setRates] = useState<JobRates>({})

  useEffect(() => {
    const seen = samples.current
    const next: JobRates = {}
    const running = new Set<string>()
    for (const job of jobs ?? []) {
      if (!isImageJobActive(job)) continue
      running.add(job.model_id)
      const prev = seen.get(job.model_id)
      const rate = prev ? jobRate(prev.job, job, (updatedAt - prev.at) / 1000) : null
      if (rate) next[job.model_id] = rate
      // A poll that moved no bytes keeps the older sample, so the next one that does is
      // measured over the whole stall; a different job (a re-install) starts afresh.
      if (!prev || prev.job.id !== job.id || job.bytes_downloaded !== prev.job.bytes_downloaded) {
        seen.set(job.model_id, { job, at: updatedAt })
      }
    }
    for (const id of [...seen.keys()]) if (!running.has(id)) seen.delete(id)
    setRates((prev) => (sameRates(prev, next) ? prev : next))
  }, [jobs, updatedAt])

  return rates
}
