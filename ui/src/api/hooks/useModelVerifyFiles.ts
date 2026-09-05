// hal0 v3 dashboard — on-disk file verification for one model row (#2212).
//
// POST /api/models/{id}/verify-files → {model: {...}, mmproj: {...}|None}.
// The server side is a pure stat (services/models_service.py verify_files) —
// no hashing, no HuggingFace round trip — so this is cheap, but it is still
// fired ON DEMAND ONLY: the drawer's Source disclosure has an explicit
// "Verify files" button and nothing probes without a click.
//
// Warn-never-block, exactly like useModelsFeasibility: the result renders as a
// chip beside each path and never gates a save. Pure probe, no cache to
// invalidate — a mutation rather than a query so the caller owns when it runs.

import { useMutation } from '@tanstack/react-query'
import { apiPost, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface VerifiedFile {
  path: string
  exists: boolean
  /** null when nothing is on disk to size (or the path is a directory). */
  size_bytes: number | null
  /** Three-state: null when there is no stored size to compare against — the
   *  mmproj sidecar is always null, since the row records no size for it. */
  size_matches: boolean | null
}

export interface VerifyFilesResponse {
  model: VerifiedFile
  /** null when the row pairs no projector. */
  mmproj: VerifiedFile | null
}

export function useModelVerifyFiles() {
  return useMutation<VerifyFilesResponse, Hal0Error, string>({
    mutationFn: (id: string) => apiPost<VerifyFilesResponse>(ENDPOINTS.modelVerifyFiles(id)),
  })
}
