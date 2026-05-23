/**
 * sseHarness — install a tiny EventSource shim on the page so specs
 * can drive SSE streams deterministically. `page.route` doesn't help
 * here: it fulfils the whole response at once; the UI's EventSource
 * never sees individual events.
 *
 * Usage in a spec:
 *
 *   await installSseHarness(page)
 *   // navigate / trigger UI code that opens an EventSource…
 *   await waitForSse(page, '/api/models/m1/pull/stream')
 *   await emitSse(page, '/api/models/m1/pull/stream', { state: 'pulling', bytes_downloaded: 1024, bytes_total: 4096 })
 *   await emitSse(page, '/api/models/m1/pull/stream', { state: 'completed' })
 *
 * The shim records every EventSource ever constructed in
 * `window.__sseStreams` keyed by URL (substring match — multiple
 * streams for the same URL are all dispatched to).
 */
import { Page } from '@playwright/test'

export async function installSseHarness(page: Page) {
  await page.addInitScript(() => {
    const streams = {}
    ;(window).__sseStreams = streams

    class FakeEventSource extends EventTarget {
      constructor(url) {
        super()
        this.CONNECTING = 0
        this.OPEN = 1
        this.CLOSED = 2
        this.url = String(url)
        this.readyState = 0
        this.withCredentials = false
        this.onopen = null
        this.onmessage = null
        this.onerror = null

        // Defer the open so consumers can attach onmessage first.
        Promise.resolve().then(() => {
          this.readyState = 1
          const ev = new Event('open')
          try { this.onopen && this.onopen(ev) } catch (e) {}
          this.dispatchEvent(ev)
        })
        const self = this
        const entry = {
          url: this.url,
          dispatch: function (data) {
            if (self.readyState !== 1) return
            const ev = new MessageEvent('message', { data })
            try { self.onmessage && self.onmessage(ev) } catch (e) {}
            self.dispatchEvent(ev)
          },
          // PR-11: typed-event support. Real SSE streams can declare
          // ``event: <name>`` for each frame; addEventListener(name, cb)
          // only fires for that name. The default ``dispatch`` only
          // emits 'message' events; ``dispatchTyped(type, data)`` fires
          // a MessageEvent of the requested type so subscribers using
          // addEventListener still trigger.
          dispatchTyped: function (type, data) {
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
    FakeEventSource.CONNECTING = 0
    FakeEventSource.OPEN = 1
    FakeEventSource.CLOSED = 2
    ;(window).EventSource = FakeEventSource
  })
}

/**
 * Push a single SSE message into the streams whose URL contains the
 * given substring. Returns the number of streams that received the
 * event — 0 means no EventSource was open for that URL (specs should
 * await on UI state before calling this).
 */
export async function emitSse(page: Page, urlSubstring: string, data: any): Promise<number> {
  const payload = typeof data === 'string' ? data : JSON.stringify(data)
  return await page.evaluate(
    ({ sub, body }) => {
      const streams = (window).__sseStreams || {}
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

/**
 * Push a single TYPED SSE event into the streams whose URL contains
 * the given substring. Use this for streams that declare
 * ``event: <type>`` framing — ``addEventListener(type, cb)`` only
 * fires for the matching type, so the default ``emitSse`` (which
 * dispatches 'message' events) won't trigger them.
 *
 * Returns the number of streams that received the event.
 */
export async function emitSseTyped(
  page: Page,
  urlSubstring: string,
  type: string,
  data: any,
): Promise<number> {
  const payload = typeof data === 'string' ? data : JSON.stringify(data)
  return await page.evaluate(
    ({ sub, evType, body }) => {
      const streams = (window).__sseStreams || {}
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

/**
 * Block until at least one EventSource matching the URL substring has
 * been opened on the page. Polls every 50ms up to `timeoutMs`.
 */
export async function waitForSse(page: Page, urlSubstring: string, timeoutMs = 5000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const found = await page.evaluate((sub) => {
      const streams = (window).__sseStreams || {}
      return Object.keys(streams).some((u) => u.indexOf(sub) !== -1)
    }, urlSubstring)
    if (found) return
    await page.waitForTimeout(50)
  }
  throw new Error(`waitForSse: no EventSource for "${urlSubstring}" within ${timeoutMs}ms`)
}
