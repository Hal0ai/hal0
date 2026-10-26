// Can the ComfyUI editor link work from this page? On the box, on the LAN while ComfyUI binds it,
// or at a URL the operator declared. Pure.

import { describe, expect, it } from 'vitest'
import { EDITOR_PORT, isLoopbackHost, isOnBox, tunnelCommand } from '../image/editor-reach'

describe('isLoopbackHost', () => {
  it.each(['localhost', 'LOCALHOST', '127.0.0.1', '::1', '[::1]'])('%s is loopback', (host) => {
    expect(isLoopbackHost(host)).toBe(true)
  })

  it.each(['', 'hal0.local', '192.0.2.10', '127.0.0.2', 'localhost.example.com', '[fd00::1]'])(
    '%s is not loopback',
    (host) => {
      expect(isLoopbackHost(host)).toBe(false)
    },
  )
})

describe('isOnBox', () => {
  it('a loopback page is on the box, whatever the server says about the link', () => {
    expect(isOnBox('localhost', false)).toBe(true)
    expect(isOnBox('127.0.0.1', false)).toBe(true)
    expect(isOnBox('[::1]', false)).toBe(true)
    expect(isOnBox('LOCALHOST', true)).toBe(true)
  })

  it('a LAN page is not on the box while ComfyUI is fenced: the derived link does not answer there', () => {
    // GET /api/config/urls answers http://<request host>:8188 unless HAL0_COMFYUI_PUBLIC_URL is
    // set. Under [network].comfyui_listen = "loopback" ComfyUI binds loopback only (container.py
    // _loopback_fence_command) and the server says so: `comfyui_lan` false, `comfyui_public`
    // false for the derived link.
    expect(isOnBox('192.0.2.10', false, false)).toBe(false)
    expect(isOnBox('hal0.local', false, false)).toBe(false)
    expect(isOnBox('hal0.example.com', false, false)).toBe(false)
    expect(isOnBox('[fd00::1]', false, false)).toBe(false)
  })

  it('a LAN page opens the derived link while ComfyUI binds the LAN (ADR-0041)', () => {
    // [network].comfyui_listen = "lan", the default: the server says `comfyui_lan` true and the
    // http://<request host>:8188 link answers from any machine, declared URL or not.
    expect(isOnBox('192.0.2.10', false, true)).toBe(true)
    expect(isOnBox('hal0.local', false, true)).toBe(true)
    expect(isOnBox('[fd00::1]', false, true)).toBe(true)
    expect(isOnBox('hal0.example.com', true, true)).toBe(true)
  })

  it('the LAN flag is left out (an older hal0) or not a literal true: fenced', () => {
    expect(isOnBox('hal0.local', false)).toBe(false)
    expect(isOnBox('hal0.local', false, undefined)).toBe(false)
    expect(isOnBox('hal0.local', false, null)).toBe(false)
    expect(isOnBox('hal0.local', false, 'true' as unknown as boolean)).toBe(false)
    expect(isOnBox('hal0.local', false, 1 as unknown as boolean)).toBe(false)
  })


  it('a public URL the operator declared is reachable from where they declared it for', () => {
    expect(isOnBox('hal0.example.com', true)).toBe(true)
    expect(isOnBox('192.0.2.10', true)).toBe(true)
  })

  it('a server that does not say (an older hal0, a body without the flag) is not on the box', () => {
    expect(isOnBox('hal0.local', undefined)).toBe(false)
    expect(isOnBox('hal0.local', null)).toBe(false)
  })

  it('only a literal true declares a URL', () => {
    expect(isOnBox('hal0.local', 'true' as unknown as boolean)).toBe(false)
    expect(isOnBox('hal0.local', 1 as unknown as boolean)).toBe(false)
  })
})

describe('tunnelCommand', () => {
  it('forwards the editor port to the hal0 host and leaves the user to the operator', () => {
    expect(EDITOR_PORT).toBe(8188)
    expect(tunnelCommand('hal0.local')).toBe('ssh -L 8188:127.0.0.1:8188 <user>@hal0.local')
    expect(tunnelCommand('192.0.2.10')).toBe('ssh -L 8188:127.0.0.1:8188 <user>@192.0.2.10')
  })

  it('writes an IPv6 host the way ssh takes it, without the brackets', () => {
    expect(tunnelCommand('[fd00::1]')).toBe('ssh -L 8188:127.0.0.1:8188 <user>@fd00::1')
  })
})
