// @vitest-environment happy-dom
//
// useImageGenerate -> POST /v1/images/generations.
//
// A render can run for minutes (Qwen-Image at 20 steps is about six), so the request must not
// be cut short on the client. client.ts has no default timeout and the hook must not add one:
// the only thing that ends the request early is the caller's own AbortSignal (the Generate
// panel's Cancel). Harness mirrors comfyuiFilesHooks.test.tsx, with fetch stubbed so the real
// client path runs.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { generateImage, useImageGenerate } = await import('@/api/hooks/useComfyui')
const { Hal0Error } = await import('@/api/client')

// Below this nothing may end a render: the longest one the catalog measures is about six
// minutes, and the margin is for a cold model load in front of it.
const MIN_TIMEOUT_MS = 610_000

const OK_BODY = {
  created: 1,
  data: [{ url: '/api/images/cache/abc.png' }],
  _hal0: { model: 'sdxl', template: 'sdxl', prompt_id: 'p1', upstream: 'img', meta: { seed: 7 } },
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mountHook<T>(useHook: () => T) {
  let latest!: T
  function Probe() {
    latest = useHook()
    return null
  }
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  act(() => {
    root.render(React.createElement(QueryClientProvider, { client: qc }, React.createElement(Probe)))
  })
  return { root, get: () => latest }
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('useImageGenerate', () => {
  it('useImageGenerate uses no client timeout below 610s', async () => {
    vi.useFakeTimers()
    // The two ways a client times a request out: AbortSignal.timeout, and a timer that aborts.
    const abortTimeout = vi.fn()
    const realTimeout = AbortSignal.timeout
    if (realTimeout) {
      vi.spyOn(AbortSignal, 'timeout').mockImplementation((ms: number) => {
        abortTimeout(ms)
        return realTimeout.call(AbortSignal, ms)
      })
    }
    let init: RequestInit | undefined
    let finish!: (res: Response) => void
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, options: RequestInit) => {
        init = options
        return new Promise<Response>((resolve) => {
          finish = resolve
        })
      }),
    )

    const controller = new AbortController()
    const run = generateImage({ model: 'qwen-image', prompt: 'a cat', signal: controller.signal })
    await vi.advanceTimersByTimeAsync(MIN_TIMEOUT_MS - 1)

    // Still waiting for the server: nothing has given up on it...
    expect(init?.signal?.aborted).toBe(false)
    // ...and the signal fetch got is the caller's own, not one composed with a timeout.
    expect(init?.signal).toBe(controller.signal)
    for (const [ms] of abortTimeout.mock.calls) expect(ms).toBeGreaterThanOrEqual(MIN_TIMEOUT_MS)

    finish(jsonResponse(OK_BODY))
    await expect(run).resolves.toMatchObject({ data: [{ url: '/api/images/cache/abc.png' }] })
  })

  it('passes no signal and starts no timer when the caller gives none', async () => {
    vi.useFakeTimers()
    const abortTimeout = vi.fn()
    const realTimeout = AbortSignal.timeout
    if (realTimeout) {
      vi.spyOn(AbortSignal, 'timeout').mockImplementation((ms: number) => {
        abortTimeout(ms)
        return realTimeout.call(AbortSignal, ms)
      })
    }
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => jsonResponse(OK_BODY))
    vi.stubGlobal('fetch', fetchMock)

    const run = generateImage({ model: 'sdxl', prompt: 'a cat' })
    // Nothing has been scheduled for the request: no timeout of any length.
    expect(vi.getTimerCount()).toBe(0)
    await run

    expect(fetchMock.mock.calls[0][1].signal).toBeUndefined()
    expect(abortTimeout).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('ends the request only when the caller aborts it', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, options: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            // Like fetch: an aborted signal rejects at once, one that aborts later rejects then.
            const abort = () => reject(new DOMException('aborted', 'AbortError'))
            if (options.signal?.aborted) abort()
            else options.signal?.addEventListener('abort', abort)
          }),
      ),
    )
    const controller = new AbortController()
    const probe = mountHook(() => useImageGenerate())
    let run!: Promise<unknown>
    await act(async () => {
      run = probe.get().mutateAsync({ model: 'sdxl', prompt: 'a cat', signal: controller.signal })
      run.catch(() => {})
      controller.abort()
    })
    await expect(run).rejects.toMatchObject({ name: 'AbortError' })
    act(() => probe.root.unmount())
  })

  it('posts the model, prompt, count, size and extra_body to /v1 and returns the body', async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => jsonResponse(OK_BODY))
    vi.stubGlobal('fetch', fetchMock)
    const probe = mountHook(() => useImageGenerate())

    let result: unknown
    await act(async () => {
      result = await probe.get().mutateAsync({
        model: 'sdxl',
        prompt: 'a cat in a hat',
        n: 2,
        size: '1024x1024',
        seed: 42,
        steps: 20,
        cfg: 5.5,
        negative_prompt: 'blurry',
      })
    })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/v1/images/generations')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({
      model: 'sdxl',
      prompt: 'a cat in a hat',
      n: 2,
      size: '1024x1024',
      response_format: 'url',
      extra_body: { seed: 42, steps: 20, cfg: 5.5, negative_prompt: 'blurry' },
    })
    expect(result).toEqual(OK_BODY)
    act(() => probe.root.unmount())
  })

  it('leaves unset options out so the server applies its own defaults', async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => jsonResponse(OK_BODY))
    vi.stubGlobal('fetch', fetchMock)
    const body = await (async () => {
      await generateImage({ model: 'sdxl', prompt: 'a cat', seed: null, steps: undefined, cfg: null })
      return JSON.parse(fetchMock.mock.calls[0][1].body as string)
    })()
    expect(body).toEqual({ model: 'sdxl', prompt: 'a cat', response_format: 'url', extra_body: {} })
  })

  it('surfaces the server error envelope with its code and details', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          {
            error: {
              code: 'image.wont_fit',
              message: 'Needs 31.0 GB of GPU memory; 12.4 GB is free after the unload.',
              details: { model: 'flux-dev', size: '1024x1024' },
            },
          },
          422,
        ),
      ),
    )
    const err = await generateImage({ model: 'flux-dev', prompt: 'x' }).catch((e) => e)
    expect(err).toBeInstanceOf(Hal0Error)
    expect(err).toMatchObject({
      status: 422,
      code: 'image.wont_fit',
      message: 'Needs 31.0 GB of GPU memory; 12.4 GB is free after the unload.',
      details: { model: 'flux-dev', size: '1024x1024' },
    })
  })
})
