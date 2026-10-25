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
  it('a loopback page is on the box, whatever link the server gave', () => {
    expect(isOnBox('localhost', 'http://localhost:8188')).toBe(true)
    expect(isOnBox('127.0.0.1', 'http://127.0.0.1:8188')).toBe(true)
    expect(isOnBox('[::1]', 'http://[::1]:8188')).toBe(true)
    expect(isOnBox('localhost', null)).toBe(true)
  })

  it('a LAN page with the link hal0 derives from its own host is not on the box', () => {
    // GET /api/config/urls answers http://<request host>:8188 unless HAL0_COMFYUI_PUBLIC_URL is
    // set, and ComfyUI binds loopback only (container.py _loopback_fence_command): the link's
    // host is the page's host, and it does not answer from there.
    expect(isOnBox('10.0.1.142', 'http://10.0.1.142:8188')).toBe(false)
    expect(isOnBox('hal0.local', 'http://hal0.local:8188')).toBe(false)
    expect(isOnBox('HAL0.local', 'http://hal0.local:8188')).toBe(false)
    expect(isOnBox('hal0.example.com', 'http://hal0.example.com:8188/')).toBe(false)
    expect(isOnBox('[fd00::1]', 'http://[fd00::1]:8188')).toBe(false)
  })

  it('a public URL the operator declared is reachable from where they declared it for', () => {
    expect(isOnBox('hal0.example.com', 'https://comfyui.example.com')).toBe(true)
    expect(isOnBox('10.0.1.142', 'http://10.0.1.142:8189')).toBe(true)
    expect(isOnBox('hal0.example.com', 'https://hal0.example.com/comfyui')).toBe(true)
  })

  it('no link, or one that is not a URL, is not on the box', () => {
    expect(isOnBox('hal0.local', null)).toBe(false)
    expect(isOnBox('hal0.local', undefined)).toBe(false)
    expect(isOnBox('hal0.local', '')).toBe(false)
    expect(isOnBox('hal0.local', 'not a url')).toBe(false)
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
