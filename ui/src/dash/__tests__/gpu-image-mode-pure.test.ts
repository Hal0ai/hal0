import { describe, expect, it } from 'vitest'
import { imageModeBanner } from '../gpu-image-mode-pure'

describe('imageModeBanner', () => {
  it('shows nothing outside image mode or without an arbiter answer', () => {
    expect(imageModeBanner({ mode: 'llm', pinned: false })).toBeNull()
    expect(imageModeBanner(null)).toBeNull()
    expect(imageModeBanner(undefined)).toBeNull()
  })

  it('promises the reload after the last render when image mode is not pinned', () => {
    const copy = imageModeBanner({ mode: 'img', pinned: false })
    expect(copy?.heading).toBe('GPU: image mode')
    expect(copy?.body).toMatch(/reload shortly after the last render/)
  })

  it('says a pinned image mode holds until it is unpinned', () => {
    const copy = imageModeBanner({ mode: 'img', pinned: true })
    expect(copy?.heading).toBe('GPU: image mode (pinned)')
    expect(copy?.body).toMatch(/until it is unpinned/)
    expect(copy?.body).not.toMatch(/reload shortly/)
  })
})
