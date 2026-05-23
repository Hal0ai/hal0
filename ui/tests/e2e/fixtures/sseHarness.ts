/**
 * sseHarness — install a tiny EventSource shim on the page so specs can
 * drive SSE streams deterministically. `page.route` doesn't help here:
 * it fulfils the whole response at once; the UI's EventSource never
 * sees individual events.
 *
 * Phase A doesn't open EventSources (the dash is HAL0_DATA-driven). Kept
 * in place so Phase B1 specs that wire streaming endpoints (model pulls,
 * agent approvals, log tail) inherit the same harness shape used by the
 * retired Vue suite.
 */
import { Page } from '@playwright/test'

export async function installSseHarness(page: Page) {
  await page.addInitScript(() => {
    const streams: Record<string, any[]> = {}
    ;(window as any).__sseStreams = streams

    class FakeEventSource extends EventTarget {
      CONNECTING = 0
      OPEN = 1
      CLOSED = 2
      url: string
      readyState = 0
      withCredentials = false
      onopen: ((ev: Event) => any) | null = null
      onmessage: ((ev: MessageEvent) => any) | null = null
      onerror: ((ev: Event) => any) | null = null
      _entry: any
      static CONNECTING = 0
      static OPEN = 1
      static CLOSED = 2

      constructor(url: string) {
        super()
        this.url = String(url)
        Promise.resolve().then(() => {
          this.readyState = 1
          const ev = new Event('open')
          try { this.onopen && this.onopen(ev) } catch (_e) {}
          this.dispatchEvent(ev)
        })
        const self = this
        const entry = {
          url: this.url,
          dispatch(data: string) {
            if (self.readyState !== 1) return
            const ev = new MessageEvent('message', { data })
            try { self.onmessage && self.onmessage(ev) } catch (_e) {}
            self.dispatchEvent(ev)
          },
          dispatchTyped(type: string, data: string) {
            if (self.readyState !== 1) return
            const ev = new MessageEvent(type, { data })
            self.dispatchEvent(ev)
          },
        }
        if (!streams[this.url]) streams[this.url] = []
        streams[this.url].push(entry)
        this._entry = entry
      }

      close() {
        this.readyState = 2
        for (const url of Object.keys(streams)) {
          streams[url] = streams[url].filter((e) => e !== this._entry)
        }
      }
    }
    ;(window as any).EventSource = FakeEventSource
  })
}

export async function emitSse(page: Page, urlSubstring: string, data: any): Promise<number> {
  const payload = typeof data === 'string' ? data : JSON.stringify(data)
  return await page.evaluate(
    ({ sub, body }) => {
      const streams = (window as any).__sseStreams || {}
      let count = 0
      for (const url of Object.keys(streams)) {
        if (url.indexOf(sub) !== -1) {
          for (const entry of streams[url]) {
            entry.dispatch(body)
            count++
          }
        }
      }
      return count
    },
    { sub: urlSubstring, body: payload },
  )
}

export async function emitSseTyped(page: Page, urlSubstring: string, type: string, data: any): Promise<number> {
  const payload = typeof data === 'string' ? data : JSON.stringify(data)
  return await page.evaluate(
    ({ sub, evType, body }) => {
      const streams = (window as any).__sseStreams || {}
      let count = 0
      for (const url of Object.keys(streams)) {
        if (url.indexOf(sub) !== -1) {
          for (const entry of streams[url]) {
            if (typeof entry.dispatchTyped === 'function') {
              entry.dispatchTyped(evType, body)
              count++
            }
          }
        }
      }
      return count
    },
    { sub: urlSubstring, evType: type, body: payload },
  )
}

export async function waitForSse(page: Page, urlSubstring: string, timeoutMs = 5000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const found = await page.evaluate((sub) => {
      const streams = (window as any).__sseStreams || {}
      return Object.keys(streams).some((u) => u.indexOf(sub) !== -1)
    }, urlSubstring)
    if (found) return
    await page.waitForTimeout(50)
  }
  throw new Error(`waitForSse: no EventSource for "${urlSubstring}" within ${timeoutMs}ms`)
}
