// @vitest-environment happy-dom
// #3052: a gated call's target-selecting arguments must stay visible on the
// approval cards even when the agent puts a long body field first, and the
// full argument set must be reachable instead of cut off at 120 characters.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { describe, expect, it } from 'vitest'

import { ApprovalArgs, approvalArgParts } from '../approval-args.jsx'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true

function render(node: React.ReactElement) {
  const host = document.createElement('div')
  act(() => {
    createRoot(host).render(node)
  })
  return host
}

const longBody = 'd'.repeat(300)
const args = { description: longBody, model_id: 'victim-model' }

describe('approvalArgParts', () => {
  it('puts the target args first whatever the key order', () => {
    const parts = approvalArgParts(args, ['model_id'])
    expect(parts.targets).toEqual([['model_id', '"victim-model"']])
    expect(parts.rest).toEqual([['description', JSON.stringify(longBody)]])
  })

  it('ignores target names the args do not carry and survives missing target_args', () => {
    expect(approvalArgParts(args, ['slot']).targets).toEqual([])
    expect(approvalArgParts(args, undefined).rest.map(([k]) => k)).toEqual(['description', 'model_id'])
  })

  it('keeps the full argument JSON, untruncated', () => {
    expect(approvalArgParts(args, ['model_id']).full).toBe(JSON.stringify(args, null, 2))
  })
})

describe('ApprovalArgs', () => {
  it('renders the target arg before a long body field placed first', () => {
    const host = render(<ApprovalArgs args={args} targetArgs={['model_id']} />)
    const rows = [...host.querySelectorAll('.apx-row')]
    expect(rows[0].classList.contains('target')).toBe(true)
    expect(rows[0].textContent).toContain('model_id')
    expect(rows[0].textContent).toContain('victim-model')
    // The whole body is reachable in the expandable full view.
    const full = host.querySelector('details.apx-full pre')!
    expect(full.textContent).toContain(longBody)
    expect(full.textContent).toContain('"model_id": "victim-model"')
  })

  it('renders an empty argument set honestly', () => {
    const host = render(<ApprovalArgs args={{}} targetArgs={[]} />)
    expect(host.textContent).toContain('no arguments')
    expect(host.querySelector('details')).toBeNull()
  })
})
