// @vitest-environment happy-dom
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { describe, expect, it } from 'vitest'

import { FitChip, ModalityTag, VisionIcon } from '../fit-chip.jsx'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true

function render(node: React.ReactElement) {
  const host = document.createElement('div')
  act(() => {
    createRoot(host).render(node)
  })
  return host
}

describe('FitChip', () => {
  it.each([
    ['fits', 'ok', 'fits · needs ~3 GB', 3 * 1024],
    ['tight', 'warn', 'tight · needs ~16 GB', 16 * 1024],
    ['exceeds', 'err', "won't fit · needs ~34 GB", 34 * 1024],
    ['exceeds_total', 'err', "won't fit · needs ~34 GB", 34 * 1024],
  ])('%s → icon + GB, verdict word in title', (verdict, tone, title, mb) => {
    const chip = render(<FitChip verdict={verdict} neededMb={mb} />).querySelector('.chip')!
    expect(chip.classList.contains(tone)).toBe(true)
    expect(chip.getAttribute('title')).toBe(title)
    expect(chip.getAttribute('aria-label')).toBe(title)
    expect(chip.querySelector('svg')).not.toBeNull()
    expect(chip.textContent).toBe(`~${Math.round(mb / 1024)} GB`)
  })

  it('renders nothing for an unknown verdict', () => {
    expect(render(<FitChip verdict="unknown" neededMb={1} />).innerHTML).toBe('')
  })
})

describe('VisionIcon', () => {
  it('is a bare eye svg labelled vision, not a chip', () => {
    const el = render(<VisionIcon />).querySelector('.modality-icon')!
    expect(el.classList.contains('chip')).toBe(false)
    expect(el.getAttribute('title')).toBe('vision')
    expect(el.getAttribute('aria-label')).toBe('vision')
    expect(el.querySelector('svg')).not.toBeNull()
    expect(el.textContent).toBe('')
  })
})

describe('ModalityTag drafter bolt', () => {
  it('shows a bare bolt titled with the variant beside the eye', () => {
    const host = render(<ModalityTag tag="vision" draft="lilicorr" />)
    expect(host.querySelector('[title="vision"]')).not.toBeNull()
    const bolt = host.querySelector('[data-testid="drafter-bolt"]')!
    expect(bolt.classList.contains('chip')).toBe(false)
    expect(bolt.classList.contains('modality-icon')).toBe(true)
    expect(bolt.getAttribute('title')).toBe('drafter · LiLiCorr')
    expect(bolt.querySelector('svg')).not.toBeNull()
  })
  it('shows only the bolt when there is no modality tag', () => {
    const host = render(<ModalityTag tag={null} draft="eagle3" />)
    expect(host.querySelector('[data-testid="drafter-bolt"]')).not.toBeNull()
  })
  it('renders nothing without tag or draft', () => {
    expect(render(<ModalityTag tag={null} draft={null} />).innerHTML).toBe('')
  })
})
