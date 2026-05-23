// hal0 v3 dashboard — fetch wrapper + structured error envelope (Phase B1).
//
// Ported from `ui-vue.bak/src/composables/useApi.js`. Same contract:
//   - 2xx returns parsed JSON (or `null` on 204).
//   - non-2xx throws `Hal0Error` with the backend's `{error: {code,message,details}}`
//     envelope lifted out so callers can branch on `err.code`.
//
// The dev server proxies `/api` and `/v1` to the local hal0-api (8080).
// Production deploy lives behind Traefik on the same origin.

import { mockFetch } from './mock'

/**
 * Hal0Error — thrown on non-2xx responses. Carries the structured envelope
 * the backend defines in `hal0.api.middleware.error_codes`. `status` mirrors
 * the HTTP status; `code` defaults to `'system.unknown'` when the body
 * isn't a parseable envelope.
 */
export class Hal0Error extends Error {
  code: string
  status: number
  details: Record<string, unknown>

  constructor(
    message: string,
    init: { code?: string; status?: number; details?: Record<string, unknown> | null } = {},
  ) {
    super(message)
    this.name = 'Hal0Error'
    this.code = init.code ?? 'system.unknown'
    this.status = init.status ?? 0
    this.details = init.details ?? {}
  }
}

export interface ApiOptions extends Omit<RequestInit, 'body'> {
  /** Pre-serialised JSON or a plain object that we stringify for you. */
  body?: BodyInit | Record<string, unknown> | null
  /**
   * Skip the mockFetch fallback layer and call window.fetch directly.
   * Used by SSE attachments + raw download helpers.
   */
  raw?: boolean
}

function serialiseBody(body: ApiOptions['body']): BodyInit | undefined {
  if (body == null) return undefined
  if (typeof body === 'string') return body
  if (body instanceof FormData || body instanceof Blob || body instanceof URLSearchParams) {
    return body
  }
  if (body instanceof ArrayBuffer || ArrayBuffer.isView(body)) return body as BodyInit
  return JSON.stringify(body)
}

/**
 * Low-level fetch wrapper. Throws Hal0Error on non-2xx; returns parsed JSON
 * (or null for 204) otherwise. Use through the per-resource hooks; this is
 * exported for the SSE / WS helpers and for tests.
 */
export async function api<T = unknown>(path: string, options: ApiOptions = {}): Promise<T> {
  const { raw, body, headers, ...rest } = options
  const fetcher = raw ? fetch : mockFetch
  const init: RequestInit = {
    ...rest,
    body: serialiseBody(body),
    headers: {
      Accept: 'application/json',
      ...(body && !(body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
      ...headers,
    },
  }
  const res = await fetcher(path, init)

  if (!res.ok) {
    let message = `API error ${res.status}`
    let code = 'system.unknown'
    let details: Record<string, unknown> | null = null
    try {
      const parsed = await res.json()
      const env = parsed?.error
      if (env && typeof env === 'object') {
        message = env.message || message
        code = env.code || code
        details = env.details || null
      } else if (parsed?.detail) {
        message = parsed.detail
      }
    } catch {
      // body wasn't parseable — keep generic message
    }
    throw new Hal0Error(message, { code, status: res.status, details })
  }

  if (res.status === 204) return null as T
  // Some endpoints return an empty body with 200 — guard the .json() call.
  const text = await res.text()
  if (!text) return null as T
  try {
    return JSON.parse(text) as T
  } catch {
    return text as unknown as T
  }
}

/** Convenience: typed GET. */
export const apiGet = <T = unknown>(path: string) => api<T>(path)

/** Convenience: typed POST with optional JSON body. */
export const apiPost = <T = unknown>(path: string, body?: ApiOptions['body']) =>
  api<T>(path, { method: 'POST', body })

/** Convenience: typed PATCH with JSON body. */
export const apiPatch = <T = unknown>(path: string, body?: ApiOptions['body']) =>
  api<T>(path, { method: 'PATCH', body })

/** Convenience: typed PUT with JSON body. */
export const apiPut = <T = unknown>(path: string, body?: ApiOptions['body']) =>
  api<T>(path, { method: 'PUT', body })

/** Convenience: typed DELETE. */
export const apiDelete = <T = unknown>(path: string) =>
  api<T>(path, { method: 'DELETE' })
