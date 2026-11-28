import { afterEach, describe, expect, it, vi } from 'vitest'

const { post, invalidate } = vi.hoisted(() => ({ post: vi.fn(), invalidate: vi.fn() }))

vi.mock('@/api/client', () => ({ apiPost: post }))
vi.mock('@/lib/queryClient', () => ({ queryClient: { invalidateQueries: invalidate } }))

const { cancelModelPull } = await import('../palette-actions')

afterEach(() => {
  post.mockReset()
  invalidate.mockReset()
})

describe('cancelModelPull (#4105)', () => {
  it('says cancelled only when the server accepted the cancel', async () => {
    post.mockResolvedValue(null)
    const toast = vi.fn()
    expect(await cancelModelPull('org/model q4', toast)).toBe(true)
    expect(post).toHaveBeenCalledWith('/api/models/org%2Fmodel%20q4/pull/cancel')
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledWith('Download cancelled', 'warn')
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['models'] })
  })

  it.each([
    ['signed out', 'sign in to continue'],
    ['the pull already ended', 'no pull in progress for this model'],
    ['a server error', 'internal server error'],
  ])('reports a failure when the server refuses (%s)', async (_case, message) => {
    post.mockRejectedValue(new Error(message))
    const toast = vi.fn()
    expect(await cancelModelPull('m', toast)).toBe(false)
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast).toHaveBeenCalledWith(`Cancel failed: ${message}`, 'err')
    expect(toast).not.toHaveBeenCalledWith('Download cancelled', 'warn')
    expect(invalidate).not.toHaveBeenCalled()
  })

  it('works without a toast function', async () => {
    post.mockRejectedValue(new Error('x'))
    await expect(cancelModelPull('m')).resolves.toBe(false)
  })
})
