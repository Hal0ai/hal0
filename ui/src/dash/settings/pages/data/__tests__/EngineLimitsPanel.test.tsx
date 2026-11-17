// @vitest-environment happy-dom
// The Engine limits panel (#3759, #3762): a row per [memory.lanes] limit, hidden
// on an API without the key, and the nested PUT body a save sends.
import React, { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { cleanup, click, mount } from '../../../../memory-area/shared/test-harness'

;(globalThis as any).FieldInfoIcon = ({ description }: any) => <span data-sub={description} />

const state: { live: any; schema: any; sent: any[] } = { live: null, schema: null, sent: [] }

vi.mock('../../../data/settingsClient.js', () => ({
  useSettingsClient: () => ({
    settings: { data: state.live },
    schema: { data: state.schema },
    update: {
      isPending: false,
      mutateAsync: async (body: unknown) => {
        state.sent.push(body)
        return {}
      },
    },
  }),
}))

const { EngineLimitsPanel } = await import('../EngineLimitsPanel.jsx')

const SCHEMA = {
  type: 'object',
  properties: {
    memory: {
      type: 'object',
      properties: {
        lanes: {
          type: 'object',
          properties: {
            reflect: {
              type: 'object',
              properties: {
                wall_timeout_s: {
                  type: 'integer',
                  default: 1800,
                  minimum: 60,
                  maximum: 86400,
                  description: 'reflect wall clock',
                },
              },
            },
          },
        },
      },
    },
  },
}

const input = (host: HTMLElement) => host.querySelector('input[type="number"]') as HTMLInputElement
const save = (host: HTMLElement) => host.querySelector('[data-testid="mem-engine-limits-save"]') as HTMLButtonElement

function setValue(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

beforeEach(() => {
  state.live = { memory: { lanes: { reflect: { wall_timeout_s: 1800 } } } }
  state.schema = SCHEMA
  state.sent = []
  ;(window as any).__hal0Toast = vi.fn()
})
afterEach(cleanup)

describe('EngineLimitsPanel', () => {
  it('shows the saved reflect time limit and keeps save off until it changes', () => {
    const { host } = mount(<EngineLimitsPanel registry={{}} />)
    expect(host.textContent).toContain('Reflect and page refresh time limit')
    expect(input(host).value).toBe('1800')
    expect(save(host).disabled).toBe(true)
  })

  it('sends the nested key for a changed value', async () => {
    const { host } = mount(<EngineLimitsPanel registry={{}} />)
    setValue(input(host), '7200')
    expect(save(host).disabled).toBe(false)
    await act(async () => {
      click(save(host))
    })
    expect(state.sent).toEqual([{ memory: { lanes: { reflect: { wall_timeout_s: 7200 } } } }])
  })

  it('refuses a value outside the schema bounds', () => {
    const { host } = mount(<EngineLimitsPanel registry={{}} />)
    setValue(input(host), '30')
    expect(save(host).disabled).toBe(true)
  })

  it('renders nothing on an API that does not know the key', () => {
    state.schema = { type: 'object', properties: {} }
    const { host } = mount(<EngineLimitsPanel registry={{}} />)
    expect(host.querySelector('[data-testid="mem-engine-limits"]')).toBeNull()
  })
})
