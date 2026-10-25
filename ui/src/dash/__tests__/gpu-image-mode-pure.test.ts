import { describe, expect, it } from 'vitest'
import { IMAGE_MODE_BANNER_HREF, imageModeBanner } from '../gpu-image-mode-pure'

const img = (over: Record<string, unknown> = {}) => ({
  mode: 'img',
  pinned: false,
  switching: false,
  phase: null,
  unload_set: ['brain', 'coder'],
  llm_waiters: { count: 0, oldest_s: null },
  ...over,
})

describe('imageModeBanner', () => {
  it('shows nothing outside image mode or without an arbiter answer', () => {
    expect(imageModeBanner({ mode: 'llm', pinned: false })).toBeNull()
    expect(imageModeBanner(null)).toBeNull()
    expect(imageModeBanner(undefined)).toBeNull()
    expect(imageModeBanner({})).toBeNull()
  })

  it('promises the reload after the last image when image mode is not pinned', () => {
    const copy = imageModeBanner(img({ phase: 'grace' }))
    expect(copy?.heading).toBe('Image mode is on')
    expect(copy?.body).toMatch(/reload about 10 s after the last image/)
  })

  it('says a pinned image mode holds until it is unpinned', () => {
    const copy = imageModeBanner(img({ pinned: true }))
    expect(copy?.heading).toBe('Image mode pinned')
    expect(copy?.body).toMatch(/until it is unpinned/)
    expect(copy?.body).not.toMatch(/reload about/)
  })

  it('uses the status line headlines for the work the arbiter reports', () => {
    expect(imageModeBanner(img({ phase: 'draining' }))?.heading).toBe('Switching to image mode')
    expect(imageModeBanner(img({ phase: 'unloading' }))?.heading).toBe('Switching to image mode')
    expect(imageModeBanner(img({ phase: 'starting_engine' }))?.heading).toBe('Switching to image mode')
    // The lock is held a beat before any phase is set.
    expect(imageModeBanner(img({ switching: true }))?.heading).toBe('Switching to image mode')
    expect(imageModeBanner(img({ phase: 'handoff' }))?.heading).toBe('Image mode · hand-off')
    expect(imageModeBanner(img({ phase: 'rendering' }))?.heading).toBe('Rendering')
    expect(imageModeBanner(img({ phase: 'reloading' }))?.heading).toBe('Reloading GPU slots')
  })

  it('shows the work on a pinned box too, and says the pin keeps the slots stopped', () => {
    const copy = imageModeBanner(img({ pinned: true, phase: 'rendering' }))
    expect(copy?.heading).toBe('Rendering')
    expect(copy?.body).toMatch(/until you unpin image mode/)
    expect(imageModeBanner(img({ phase: 'rendering' }))?.body).toMatch(/until the last image is done/)
  })

  it('does not count down a grace the arbiter cannot see the end of', () => {
    const copy = imageModeBanner(img({ phase: 'grace' }))
    expect(copy?.heading).not.toMatch(/\d/)
  })

  it('does not say LLM slots are stopped when image mode paused none', () => {
    for (const unload_set of [[], null]) {
      const copy = imageModeBanner(img({ unload_set, phase: 'rendering' }))
      expect(copy?.body).not.toMatch(/LLM slots/)
      expect(imageModeBanner(img({ unload_set }))?.body).not.toMatch(/LLM slots/)
    }
  })

  it('appends the chat-wait line', () => {
    const copy = imageModeBanner(img({ phase: 'rendering', llm_waiters: { count: 2, oldest_s: 75 } }))
    expect(copy?.body).toMatch(/2 chat requests waiting, longest 1:15 of 2:00\.$/)
  })

  it('links to the Engine tab', () => {
    expect(IMAGE_MODE_BANNER_HREF).toBe('#slots/image/engine')
  })
})
