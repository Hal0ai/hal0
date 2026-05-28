/**
 * wsHarness — install an in-page WebSocket shim so specs can drive the
 * Hermes JSON-RPC event stream deterministically. `page.route` can't
 * intercept WebSocket upgrades, so we replace `window.WebSocket` with
 * a FakeWebSocket that exposes a registry of open connections to the
 * spec via `window.__wsStreams`.
 *
 * Symmetrically to sseHarness, we expose:
 *
 *   - emitWs(page, urlSubstring, frame)  — push one text frame on each
 *     matching open socket. `frame` may be a string or any JSON-serializable
 *     value (we stringify it).
 *   - waitForWs(page, urlSubstring, timeoutMs?) — resolve when at least
 *     one socket whose URL contains the substring is open.
 *   - getWsSent(page, urlSubstring) — return the list of frames the page
 *     has sent on a matching socket (used to assert prompt.submit /
 *     approval.respond / persona-activate fired the right RPC).
 *   - closeWs(page, urlSubstring, code?) — drop the socket so the
 *     component exercises its reconnect path.
 *
 * The shim auto-opens (state → 1) on next microtask; specs that need to
 * defer the open can call `pauseWsOpens(page)` before `goto` and
 * `resumeWsOpens(page)` once they've installed routes / event listeners.
 */
import { Page } from '@playwright/test'

export async function installWsHarness(page: Page) {
  await page.addInitScript(() => {
    const w = window as any
    if (w.__wsStreams) return // already installed
    const streams: any[] = []
    w.__wsStreams = streams

    let autoOpen = true
    w.__wsPause = () => { autoOpen = false }
    w.__wsResume = () => {
      autoOpen = true
      for (const s of streams) if (s.readyState === 0) s._open()
    }

    class FakeWebSocket extends EventTarget {
      url: string
      readyState = 0
      bufferedAmount = 0
      protocol = ''
      extensions = ''
      binaryType = 'blob'
      onopen: ((ev: any) => any) | null = null
      onmessage: ((ev: any) => any) | null = null
      onclose: ((ev: any) => any) | null = null
      onerror: ((ev: any) => any) | null = null
      sentFrames: string[] = []
      CONNECTING = 0
      OPEN = 1
      CLOSING = 2
      CLOSED = 3
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3

      constructor(url: string, _protocols?: string | string[]) {
        super()
        this.url = String(url)
        streams.push(this)
        Promise.resolve().then(() => {
          if (autoOpen) this._open()
        })
      }

      _open() {
        if (this.readyState !== 0) return
        this.readyState = 1
        const ev = new Event('open')
        try { this.onopen && this.onopen(ev) } catch (_e) {}
        this.dispatchEvent(ev)
      }

      send(data: any) {
        if (this.readyState !== 1) return
        const s = typeof data === 'string' ? data : String(data)
        this.sentFrames.push(s)
      }

      close(code?: number, _reason?: string) {
        if (this.readyState === 3) return
        this.readyState = 3
        // CloseEvent ctor is browser-only; on jsdom-light envs fall back
        // to a plain Event with a code property. Always pass the type
        // argument so the constructor accepts.
        let ev: any
        try {
          ev = new (window as any).CloseEvent('close', { code: code ?? 1000 })
        } catch (_e) {
          ev = Object.assign(new Event('close'), { code: code ?? 1000 })
        }
        try { this.onclose && this.onclose(ev) } catch (_e) {}
        this.dispatchEvent(ev)
      }

      _push(text: string) {
        if (this.readyState !== 1) return
        const ev = new MessageEvent('message', { data: text })
        try { this.onmessage && this.onmessage(ev) } catch (_e) {}
        this.dispatchEvent(ev)
      }
    }
    w.WebSocket = FakeWebSocket
  })
}

export async function pauseWsOpens(page: Page) {
  await page.evaluate(() => (window as any).__wsPause && (window as any).__wsPause())
}

export async function resumeWsOpens(page: Page) {
  await page.evaluate(() => (window as any).__wsResume && (window as any).__wsResume())
}

export async function waitForWs(
  page: Page,
  urlSubstring: string,
  timeoutMs = 5000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const ok = await page.evaluate((sub) => {
      const s = (window as any).__wsStreams || []
      return s.some((w: any) => w.url && w.url.indexOf(sub) !== -1 && w.readyState === 1)
    }, urlSubstring)
    if (ok) return
    await page.waitForTimeout(40)
  }
  throw new Error(`waitForWs: no open WebSocket for "${urlSubstring}" within ${timeoutMs}ms`)
}

export async function emitWs(
  page: Page,
  urlSubstring: string,
  frame: any,
): Promise<number> {
  const payload = typeof frame === 'string' ? frame : JSON.stringify(frame)
  return await page.evaluate(
    ({ sub, body }) => {
      const streams = (window as any).__wsStreams || []
      let count = 0
      for (const w of streams) {
        if (w.url && w.url.indexOf(sub) !== -1 && w.readyState === 1) {
          w._push(body)
          count++
        }
      }
      return count
    },
    { sub: urlSubstring, body: payload },
  )
}

export async function getWsSent(
  page: Page,
  urlSubstring: string,
): Promise<string[]> {
  return await page.evaluate((sub) => {
    const streams = (window as any).__wsStreams || []
    const out: string[] = []
    for (const w of streams) {
      if (w.url && w.url.indexOf(sub) !== -1) {
        for (const f of w.sentFrames) out.push(f)
      }
    }
    return out
  }, urlSubstring)
}

export async function closeWs(page: Page, urlSubstring: string, code = 1006): Promise<number> {
  return await page.evaluate(
    ({ sub, c }) => {
      const streams = (window as any).__wsStreams || []
      let count = 0
      for (const w of streams) {
        if (w.url && w.url.indexOf(sub) !== -1 && w.readyState === 1) {
          w.close(c)
          count++
        }
      }
      return count
    },
    { sub: urlSubstring, c: code },
  )
}
