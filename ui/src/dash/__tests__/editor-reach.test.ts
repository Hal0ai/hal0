// Is this page on the box itself? Decides whether the ComfyUI editor link can work from here.
// Pure.
import { describe, expect, it } from 'vitest'
import { EDITOR_PORT, isLoopbackHost, isOnBox, tunnelCommand } from '../image/editor-reach'

describe('isLoopbackHost', () => {
  it.each(['localhost', 'LOCALHOST', '127.0.0.1', '::1', '[::1]'])('%s is loopback', (host) => {
    expect(isLoopbackHost(host)).toBe(true)
  })

  it.each(['', 'hal0.local', '10.0.1.142', '127.0.0.2', 'localhost.example.com', '[fd00::1]'])(
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

  it('a LAN page is not on the box: the link hal0 derives from its host does not answer there', () => {
    // GET /api/config/urls answers http://<request host>:8188 unless HAL0_COMFYUI_PUBLIC_URL is
    // set, and ComfyUI binds loopback only (container.py _loopback_fence_command). The server
    // says which it is: `comfyui_public` is false for the derived one.
    expect(isOnBox('10.0.1.142', false)).toBe(false)
    expect(isOnBox('hal0.local', false)).toBe(false)
    expect(isOnBox('hal0.example.com', false)).toBe(false)
    expect(isOnBox('[fd00::1]', false)).toBe(false)
  })

  it('a public URL the operator declared is reachable from where they declared it for', () => {
    expect(isOnBox('hal0.example.com', true)).toBe(true)
    expect(isOnBox('10.0.1.142', true)).toBe(true)
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
    expect(tunnelCommand('10.0.1.142')).toBe('ssh -L 8188:127.0.0.1:8188 <user>@10.0.1.142')
  })

  it('writes an IPv6 host the way ssh takes it, without the brackets', () => {
    expect(tunnelCommand('[fd00::1]')).toBe('ssh -L 8188:127.0.0.1:8188 <user>@fd00::1')
  })
})
