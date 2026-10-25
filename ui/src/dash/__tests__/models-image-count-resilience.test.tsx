// @vitest-environment happy-dom
//
// Models page vs. GET /api/comfyui/files. ModelsView asks for the file listing on
// every visit, only to put a count on the "Image / ComfyUI" tab. That count must never
// take the whole page down: a body with no `files` (a catch-all or an older proxy
// answering `{}`) crashed the view with "Cannot read properties of undefined (reading
// 'length')" and blanked the Inference tab with it. Real hooks and a fetch stub, so the
// request really goes out and the body really comes back the way the failing e2e
// run saw it.
//
// models.jsx is a window-globals dash module (`const {...} = React` at module top),
// so the globals are installed before the dynamic import — same pattern as
// runner-images-view.test.tsx.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

// The modals and the edit drawer are window globals from other dash modules; the
// view only mounts them closed, so inert stand-ins are all this test needs.
for (const name of ['AddByHfModal', 'AddByPathModal', 'ScanDirectoryModal', 'ModelDrawer', 'DeleteModelDialog']) {
  ;(globalThis as Record<string, unknown>)[name] = () => null
}

const { ModelsView } = (await import('../models.jsx').then(() => window)) as unknown as {
  ModelsView: React.ComponentType<{ modelParam?: string | null }>
}

type Reply = { status: number; body: unknown }

// What the listing endpoint answers in the test at hand; every other endpoint gets
// an empty-but-valid body, so the only variable is the one under test.
let filesReply: Reply = { status: 200, body: {} }

function stubFetch() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://x')
      let reply: Reply = { status: 200, body: {} }
      if (url.pathname === '/api/comfyui/files') reply = filesReply
      else if (url.pathname === '/api/models') reply = { status: 200, body: { models: [], count: 0 } }
      else if (url.pathname === '/api/slots') reply = { status: 200, body: { slots: [] } }
      return new Response(JSON.stringify(reply.body), {
        status: reply.status,
        headers: { 'content-type': 'application/json' },
      })
    }),
  )
}

async function mount() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const root = createRoot(host)
  await act(async () => {
    root.render(React.createElement(QueryClientProvider, { client: qc }, React.createElement(ModelsView, {})))
  })
  // Let the queries settle and the view re-render with their answers.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 50))
  })
  return { host, unmount: () => act(() => root.unmount()) }
}

const imageTabCount = (host: HTMLElement) => {
  const tab = [...host.querySelectorAll('button[role="tab"]')].find((b) => b.textContent?.includes('Image / ComfyUI'))
  return tab?.querySelector('.slot-tab-ct')?.textContent
}

describe('Models page image-tab count', () => {
  beforeEach(() => stubFetch())
  afterEach(() => {
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it.each([
    ['an empty object', { status: 200, body: {} }],
    ['files that are not a list', { status: 200, body: { root: '/models/comfyui', files: null } }],
    ['a server error', { status: 500, body: { detail: 'boom' } }],
  ])('keeps the page and counts 0 when the listing is %s', async (_label, reply) => {
    filesReply = reply
    const { host, unmount } = await mount()
    expect(host.textContent).not.toContain('This view hit an error')
    // The default Inference tab still rendered its list...
    expect(host.querySelector('.models-layout')).not.toBeNull()
    // ...and the tab that owns the listing reads 0.
    expect(imageTabCount(host)).toBe('0')
    await unmount()
  })

  it('counts the files a good listing carries', async () => {
    filesReply = {
      status: 200,
      body: {
        root: '/models/comfyui',
        files: [
          { kind: 'checkpoint', subdir: 'checkpoints', relpath: 'a.safetensors', size_bytes: 1, models: [] },
          { kind: 'vae', subdir: 'vae', relpath: 'b.safetensors', size_bytes: 1, models: [] },
        ],
      },
    }
    const { host, unmount } = await mount()
    expect(imageTabCount(host)).toBe('2')
    await unmount()
  })
})
