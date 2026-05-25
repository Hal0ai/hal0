// hal0 v3 dashboard — chat-completions client (Phase B2, #200).
//
// Drives the main dashboard chat surface. Calls `POST /v1/chat/completions`
// against the hal0-api proxy, which forwards to Lemonade on 127.0.0.1:13305.
//
// We use `fetch` + a manual ReadableStream reader (not EventSource) for two
// reasons:
//   1. EventSource only supports GET; the OpenAI shape is POST-with-JSON.
//   2. We want to share the auth/header story with the rest of the dashboard
//      (handled implicitly by same-origin proxy + Vite dev proxy on /v1).
//
// Streaming semantics:
//   - Each SSE event is `data: { ... }\n\n`, plus a final `data: [DONE]\n\n`.
//   - The Qwen3.5 family emits a `reasoning_content` delta BEFORE the final
//     `content` delta. We surface both into the same buffer (reasoning is
//     dropped if content arrives — keeps the bubble looking like an answer,
//     not a thought stream; the dedicated think/answer scaffold owns the
//     proper split, this is just "don't look broken when the model thinks
//     out loud").
//
// Out of scope for #200: tool calls, function calling, multimodal, abort,
// retry, draft-model speculative decoding visualisation, persistence across
// reload. v1 is "send a message, see a real response".

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
}

export interface ChatRequestOptions {
  model: string
  messages: ChatMessage[]
  /** When true, parse SSE and call `onDelta` for each token. */
  stream?: boolean
  /** Hard cap on response length. Lemonade defaults to ~1024 if omitted. */
  max_tokens?: number
  /** Optional abort signal so the caller can cancel an in-flight stream. */
  signal?: AbortSignal
  /** Called for each streamed delta (only when `stream: true`). */
  onDelta?: (chunk: { content: string; reasoning: string }) => void
}

export interface ChatResponse {
  /** Full assistant message text (content preferred, reasoning_content as fallback). */
  content: string
  /** The model the server actually replied as (may differ from request model). */
  model: string
  /** True when the response is a fallback to reasoning_content (no real answer). */
  reasoningOnly: boolean
}

const ENDPOINT = '/v1/chat/completions'

/**
 * Non-streaming chat call. Returns once the full response is materialised.
 *
 * Used by the composer when streaming is disabled (e.g. mock dev). Errors
 * throw with the response body included so the caller can render them.
 */
export async function chatCompletion(opts: ChatRequestOptions): Promise<ChatResponse> {
  const res = await fetch(ENDPOINT, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json' },
    body: JSON.stringify({
      model: opts.model,
      messages: opts.messages,
      stream: false,
      ...(opts.max_tokens != null ? { max_tokens: opts.max_tokens } : {}),
    }),
    signal: opts.signal,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`chat-completions ${res.status}: ${text.slice(0, 200) || res.statusText}`)
  }
  const json = (await res.json()) as {
    model?: string
    choices?: Array<{
      message?: { content?: string | null; reasoning_content?: string | null }
    }>
  }
  const choice = json.choices?.[0]?.message ?? {}
  const content = (choice.content ?? '').toString()
  const reasoning = (choice.reasoning_content ?? '').toString()
  if (content) return { content, model: json.model ?? opts.model, reasoningOnly: false }
  return { content: reasoning, model: json.model ?? opts.model, reasoningOnly: !!reasoning }
}

/**
 * Streaming chat call. Reads the SSE stream, parses each `data:` line, and
 * calls `onDelta` with the running buffers. Returns the final `ChatResponse`
 * once `[DONE]` arrives (or the stream closes).
 *
 * The stream may interleave `reasoning_content` and `content` deltas. We
 * accumulate both; the final response prefers `content` if non-empty so the
 * UI shows the answer rather than the thought. While streaming, the caller
 * sees both buffers and can choose how to render them (the composer's v1
 * shows reasoning faded while it's the only thing present, then swaps to
 * content once the model starts answering).
 */
export async function streamChatCompletion(opts: ChatRequestOptions): Promise<ChatResponse> {
  const res = await fetch(ENDPOINT, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
    body: JSON.stringify({
      model: opts.model,
      messages: opts.messages,
      stream: true,
      ...(opts.max_tokens != null ? { max_tokens: opts.max_tokens } : {}),
    }),
    signal: opts.signal,
  })
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '')
    throw new Error(
      `chat-completions ${res.status}: ${text.slice(0, 200) || res.statusText}`,
    )
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let content = ''
  let reasoning = ''
  let model = opts.model

  // SSE frames are separated by a blank line. We split on `\n\n`, parse each
  // frame's `data:` line, and stash any partial trailing frame back in
  // `buffer` for the next iteration.
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sepIdx: number
    while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sepIdx)
      buffer = buffer.slice(sepIdx + 2)
      const dataLine = frame
        .split('\n')
        .find((l) => l.startsWith('data:'))
      if (!dataLine) continue
      const payload = dataLine.slice(5).trim()
      if (payload === '[DONE]') {
        return {
          content: content || reasoning,
          model,
          reasoningOnly: !content && !!reasoning,
        }
      }
      let evt: {
        model?: string
        choices?: Array<{
          delta?: { content?: string | null; reasoning_content?: string | null }
        }>
      }
      try {
        evt = JSON.parse(payload)
      } catch {
        // Malformed line — skip rather than abort the whole stream.
        continue
      }
      if (evt.model) model = evt.model
      const delta = evt.choices?.[0]?.delta ?? {}
      if (typeof delta.content === 'string' && delta.content) content += delta.content
      if (typeof delta.reasoning_content === 'string' && delta.reasoning_content) {
        reasoning += delta.reasoning_content
      }
      opts.onDelta?.({ content, reasoning })
    }
  }
  return {
    content: content || reasoning,
    model,
    reasoningOnly: !content && !!reasoning,
  }
}
