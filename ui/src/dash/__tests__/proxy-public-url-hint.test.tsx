// @vitest-environment happy-dom
// Behind a reverse proxy with no HAL0_<APP>_PUBLIC_URL, /api/config/urls sends "" plus a reason
// (#3381). The dashboard shows the variable to set, never a host:port it builds itself.

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it, vi } from 'vitest'
import type { EditorAccess } from '../image/useEditorAccess'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const box: { urls: Record<string, unknown> | undefined } = { urls: undefined }
vi.mock('@/api/hooks/useConfigUrls', () => ({ useConfigUrls: () => ({ data: box.urls }) }))

const { useEditorAccess } = await import('../image/useEditorAccess')

function access(urls: Record<string, unknown>): EditorAccess {
  box.urls = urls
  let out!: EditorAccess
  function Probe() {
    out = useEditorAccess()
    return null
  }
  const root = createRoot(document.createElement('div'))
  act(() => root.render(<Probe />))
  act(() => root.unmount())
  return out
}

describe('useEditorAccess behind a proxy without HAL0_COMFYUI_PUBLIC_URL', () => {
  it('gives no link, even with the LAN flag set, and says which variable to set', () => {
    const a = access({
      comfyui: '',
      comfyui_public: false,
      comfyui_lan: true,
      comfyui_reason: 'proxy_needs_public_url',
    })
    expect(a.onBox).toBe(false)
    expect(a.url).toBeNull()
    expect(a.needsPublicUrl).toBe('Set HAL0_COMFYUI_PUBLIC_URL in /etc/hal0/api.env')
  })

  it('is unchanged when hal0 sends a link and an empty reason', () => {
    const a = access({
      comfyui: 'http://hal0.lan:8188',
      comfyui_public: false,
      comfyui_lan: true,
      comfyui_reason: '',
    })
    expect(a.onBox).toBe(true)
    expect(a.url).toBe('http://hal0.lan:8188')
    expect(a.needsPublicUrl).toBeNull()
  })
})
