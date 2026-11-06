// @vitest-environment happy-dom
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiPost: () => Promise.resolve({ ok: true }) }
})

const { useServiceAction } = await import('@/api/hooks/useServices')

describe('service action config URL invalidation', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('invalidates config URLs after a service lifecycle action settles', async () => {
    let mutation!: ReturnType<typeof useServiceAction>
    function Probe() {
      mutation = useServiceAction()
      return null
    }

    const host = document.createElement('div')
    document.body.appendChild(host)
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['services', 'list'], { services: [] })
    qc.setQueryData(['config', 'urls'], { openwebui_enabled: false })
    const root = createRoot(host)

    act(() => {
      root.render(React.createElement(QueryClientProvider, { client: qc }, React.createElement(Probe)))
    })
    expect(qc.getQueryState(['config', 'urls'])?.isInvalidated).toBe(false)

    await act(async () => {
      await mutation.mutateAsync({ id: 'openwebui', action: 'start' })
    })

    expect(qc.getQueryState(['services', 'list'])?.isInvalidated).toBe(true)
    expect(qc.getQueryState(['config', 'urls'])?.isInvalidated).toBe(true)
    act(() => root.unmount())
  })
})
