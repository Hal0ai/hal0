import { describe, expect, it } from 'vitest'
import { modelsRedirectForHash } from './models-redirect'

describe('modelsRedirectForHash', () => {
  it('sends the old Models upstream tab to Providers', () => {
    expect(modelsRedirectForHash('#models/upstream')).toBe('#models/providers')
    expect(modelsRedirectForHash('models/upstream')).toBe('#models/providers')
  })

  it('sends the old Endpoints upstream focus to Providers', () => {
    expect(modelsRedirectForHash('#slots/endpoints?focus=upstream')).toBe('#models/providers')
  })

  it('leaves Endpoints without the focus query where it is', () => {
    expect(modelsRedirectForHash('#slots/endpoints')).toBeNull()
    expect(modelsRedirectForHash('#slots/endpoints?focus=local')).toBeNull()
  })

  it('leaves every other address alone', () => {
    expect(modelsRedirectForHash('')).toBeNull()
    expect(modelsRedirectForHash('#models')).toBeNull()
    expect(modelsRedirectForHash('#models/providers')).toBeNull()
    expect(modelsRedirectForHash('#models/inference')).toBeNull()
    expect(modelsRedirectForHash('#dashboard')).toBeNull()
  })
})
