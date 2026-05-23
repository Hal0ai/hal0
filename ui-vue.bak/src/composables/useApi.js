import { useToastsStore } from '../stores/toasts.js'

const BASE = ''  // dev server proxies /api and /v1 to :8080

/**
 * Hal0Error — thrown from `api()` on non-2xx responses.
 *
 * Carries the structured error envelope shape ({code, message, details})
 * the backend defines in `hal0.api.middleware.error_codes`. UI code that
 * needs to branch on the code (e.g. RestartBanner showing rollback only on
 * `system.update_failed`) can do `if (err.code === '...')` instead of
 * regex-matching the message.
 *
 * `status` mirrors the HTTP status so callers can treat 501 (stub) and 4xx
 * (user-actionable) differently without parsing the body twice.
 */
export class Hal0Error extends Error {
  constructor(message, { code = 'system.unknown', status = 0, details = null } = {}) {
    super(message)
    this.name = 'Hal0Error'
    this.code = code
    this.status = status
    this.details = details ?? {}
  }
}

/**
 * Minimal fetch wrapper.
 *
 * Usage:
 *   const data = await api('/api/slots')
 *   await api('/api/slots/primary/restart', { method: 'POST' })
 *
 * On a non-2xx response, throws `Hal0Error` with `code` + `details`
 * lifted out of the structured envelope; falls back to a generic
 * `system.unknown` code if the body isn't parseable.
 */
export async function api(path, options = {}) {
  const url = BASE + path
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })

  if (!res.ok) {
    let message = `API error ${res.status}`
    let code = 'system.unknown'
    let details = null
    try {
      const body = await res.json()
      const env = body?.error
      if (env && typeof env === 'object') {
        message = env.message || message
        code = env.code || code
        details = env.details || null
      } else if (body?.detail) {
        message = body.detail
      }
    } catch {
      // ignore parse failure
    }
    throw new Hal0Error(message, { code, status: res.status, details })
  }

  if (res.status === 204) return null
  return res.json()
}

/**
 * Same as api() but automatically shows a toast on error and re-throws.
 */
export function useApi() {
  const toasts = useToastsStore()

  async function call(path, options = {}) {
    try {
      return await api(path, options)
    } catch (err) {
      toasts.error(err.message)
      throw err
    }
  }

  return { call, api }
}
