// hal0 dashboard — chat surface (Composer + ChatActive + ChatEmpty + PersonaPicker)
//
// Extracted from dashboard.jsx in #200 / fix/chat-surface-functional to:
//   1. Replace the prototype's scripted bubbles + no-op `onSend` with a real
//      round-trip against `/v1/chat/completions` (Lemonade-backed).
//   2. Isolate the chat surface from the sibling-agent edits that touch
//      MemoryMap / ThroughputCard / HealthCard. Two agents editing the same
//      .jsx with `git add <file>` would sweep each other's hunks (see
//      project memory `feedback_multi_agent_one_file`).
//
// Phase B2 scope (locked to brief):
//   - Send button posts the typed message + the conversation history with
//     `model = persona slot's model_id` and renders the stream/response.
//   - State persists across sends within the page session (`useState` here).
//   - Errors render visibly as an error row.
//   - All hardcoded dummy bubbles deleted from the production path.
//
// Out of scope (deferred):
//   - Tool calls / function-calling visualisation (the toolblock surface
//     from the prototype is gone for now; the renderer handles assistant +
//     user messages only).
//   - Multimodal attachments, voice input (composer icons stay as toasts).
//   - Persistence across reload (no localStorage yet).
//   - Model picker in the composer (defer; persona pick still routes).
//   - Streaming reasoning split (think/answer surface — separate scaffold).
//
// The dummy "scripted demo" was a prototype shell; the project memory
// `hal0_dashboard_v2_rework_in_flight` flags this as the open follow-up
// item (PR #200). The new ChatActive renders only the messages the user
// has actually sent + received.

import { streamChatCompletion } from '@/api/hooks/useChatCompletions'
import { useSlots } from '@/api/hooks/useSlots'

const { useState: useStateC, useRef: useRefC, useEffect: useEffectC, useMemo: useMemoC } = React

// ─── Persona dropdown ───
function PersonaPicker({ slots, current, onPick, open, onToggle, noTools }) {
  if (!slots || slots.length === 0) return null
  const cur = slots.find((s) => s.name === current) || slots[0]
  const chatSlots = slots.filter((s) => s.type === 'llm')
  return (
    <div className="persona" onClick={onToggle}>
      <span className="dot" />
      <span className="nm">
        Persona <b>{cur.name}</b>
        <span className="sub">· {(cur.device || 'cpu').replace('gpu-', '')}</span>
      </span>
      {noTools && <span className="chip warn" style={{ marginLeft: 6 }}>no tools</span>}
      <span className="chev">{Icons.chev}</span>
      {open && (
        <div className="persona-menu" onClick={(e) => e.stopPropagation()}>
          <div className="pm-h">Chat personas</div>
          {chatSlots.map((s) => {
            const isNpu = s.device === 'npu'
            const isCur = s.name === cur.name
            return (
              <div
                key={s.name}
                className={'pm-item' + (isCur ? ' active' : '')}
                onClick={() => {
                  onPick(s.name)
                  onToggle()
                }}
              >
                <span className={'dot ' + s.state} />
                <div>
                  <div className="name">
                    {s.name}{' '}
                    {s.isDefault && (
                      <span style={{ color: 'var(--accent)', fontSize: 10, marginLeft: 4 }}>
                        · default
                      </span>
                    )}
                  </div>
                  <div className="sub">
                    {s.model} · {(s.device || 'cpu').replace('gpu-', '')}
                  </div>
                  {isNpu && !isCur && cur.device === 'npu' && (
                    <div className="warn">Pauses voice + embed ~14s while FLM swaps</div>
                  )}
                  {isNpu && !isCur && cur.device !== 'npu' && (
                    <div className="sub" style={{ color: 'var(--dev-npu)' }}>
                      coresident with stt-npu + embed-npu
                    </div>
                  )}
                </div>
                <span style={{ color: 'var(--fg-4)', fontSize: 10 }}>
                  {s.metrics.toks ? `${s.metrics.toks}t/s` : ''}
                </span>
              </div>
            )
          })}
          <div
            className="pm-add"
            onClick={() => {
              onToggle()
              window.location.hash = '#slots?new=llm'
              window.__hal0Toast &&
                window.__hal0Toast('Create-slot modal — coming in next batch', 'info')
            }}
          >
            {Icons.plus} <span>Add chat slot</span>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Composer ───
//
// `state` is now derived from the live send/stream state when present,
// falling back to the prop (for the Tweaks-panel demo states). When the
// composer is actively sending we ignore the prop and use the real state.
function Composer({
  slots,
  persona,
  onPersona,
  draft,
  setDraft,
  onSend,
  onStop,
  placeholder,
  placement = 'composer-left',
  state = 'idle',
}) {
  const [open, setOpen] = useStateC(false)
  useEffectC(() => {
    const off = () => setOpen(false)
    document.addEventListener('click', off)
    return () => document.removeEventListener('click', off)
  }, [])
  const cur = slots.find((s) => s.name === persona)
  const isOffline = state === 'offline'
  const isSending = state === 'sending'
  const isStreaming = state === 'streaming'
  const isSwapping = state === 'swap'
  const noTools = state === 'no-tools'
  const dimmed = isOffline || isSwapping

  const stateBanner = (() => {
    if (isOffline)
      return (
        <div className="composer-banner err">
          <span>{Icons.warn}</span>
          <span>
            <b>lemond is offline.</b> Slot state is stale. Sending is disabled.
          </span>
          <button
            className="btn ghost sm"
            onClick={() =>
              window.__hal0Toast && window.__hal0Toast('Restart lemond — stubbed', 'info')
            }
          >
            Restart lemond
          </button>
        </div>
      )
    if (isSwapping)
      return (
        <div className="composer-banner warn">
          <span>{Icons.warn}</span>
          <span>
            <b>Swapping NPU chat: gemma3:1b → llama-3.2-3b-npu.</b> Voice + embed paused ~14s.
          </span>
        </div>
      )
    if (noTools)
      return (
        <div className="composer-banner info">
          <span>{Icons.warn}</span>
          <span>
            <b>Persona has no tool-calling.</b> Attach / mic disabled. Pick a tool-calling-labeled
            model to enable tools.
          </span>
        </div>
      )
    return null
  })()

  const personaCtl = (
    <PersonaPicker
      slots={slots}
      current={persona}
      onPick={onPersona}
      open={open}
      onToggle={() => setOpen((v) => !v)}
      noTools={noTools}
    />
  )

  // Submit on Enter (no shift). Empty drafts no-op (the button is also
  // disabled, but the keybinding should match).
  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (draft.trim() && !isSending && !isOffline) onSend()
    }
  }

  return (
    <div className={'composer' + (dimmed ? ' dimmed' : '')}>
      {stateBanner}
      {placement === 'above' && (
        <div className="composer-persona-row" onClick={(e) => e.stopPropagation()}>
          {personaCtl}
          <span
            className="mono"
            style={{ fontSize: 10, color: 'var(--fg-5)', marginLeft: 'auto' }}
          >
            persona surfaced above input
          </span>
        </div>
      )}
      <div className="composer-bar" onClick={(e) => e.stopPropagation()}>
        {placement !== 'above' && personaCtl}
        <div
          className={'composer-ic' + (noTools ? ' disabled' : '')}
          title="Attach"
          onClick={() =>
            !noTools &&
            window.__hal0Toast &&
            window.__hal0Toast('Attachment picker — coming in next batch', 'info')
          }
        >
          {Icons.attach}
        </div>
        <div
          className={'composer-ic' + (noTools ? ' disabled' : '')}
          title="Voice input"
          onClick={() =>
            !noTools &&
            window.__hal0Toast &&
            window.__hal0Toast('Voice input — coming in next batch', 'info')
          }
        >
          {Icons.mic}
        </div>
        <div className="composer-input-wrap">
          <textarea
            className="composer-input"
            placeholder={
              isOffline ? 'lemond is offline — cannot send' : placeholder || `Ask ${cur?.name || 'hal0'}…`
            }
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            disabled={isOffline || isSending}
          />
        </div>
        {isStreaming ? (
          <button
            className="composer-stop"
            onClick={() => {
              if (onStop) onStop()
              else window.__hal0Toast && window.__hal0Toast('Stopped stream', 'info')
            }}
          >
            <span className="stop-sq" />
            Stop
          </button>
        ) : (
          <button
            className="composer-send"
            onClick={onSend}
            disabled={isOffline || isSending || !draft.trim()}
            aria-label="Send"
          >
            {isSending ? <span className="spinner-sm" /> : Icons.send}
          </button>
        )}
      </div>
      <div className="composer-meta mono">
        <span>
          routing → <span style={{ color: 'var(--accent)' }}>{cur?.name}</span>
        </span>
        <span className="spacer" />
        {noTools ? (
          <span style={{ color: 'var(--warn)' }}>● tools: 0 (persona has no tool-calling label)</span>
        ) : (
          <span>
            <span className="ok">●</span> tools: idle
          </span>
        )}
        {isStreaming && <span style={{ color: 'var(--accent)' }}>● streaming</span>}
        {isSending && <span style={{ color: 'var(--info)' }}>● sending…</span>}
        <span>
          <kbd className="kbd">enter</kbd> send · <kbd className="kbd">⇧↵</kbd> newline
        </span>
      </div>
    </div>
  )
}

// ─── Shared chat hook ───
//
// Owns the conversation array + the in-flight send/stream lifecycle. Lifted
// to a hook so ChatActive and ChatEmpty can share the same state machine
// (the surface only differs in what it renders when `messages.length === 0`).
//
// On `send(text)`:
//   1. Resolve the persona's slot → `model_id` (or `model`). If neither is
//      present we surface an error message instead of hitting the wire.
//   2. Append a user message and an empty assistant placeholder.
//   3. Stream tokens into the placeholder's `content` (or, for short
//      reasoning-only replies, into `reasoning`).
//   4. On completion: replace the placeholder with the final assistant
//      message. On error: replace the placeholder with an error row.
function useChat(slots, persona) {
  const [messages, setMessages] = useStateC(() => [])
  const [pending, setPending] = useStateC(null) // 'sending' | 'streaming' | null
  const abortRef = useRefC(null)

  const personaSlot = useMemoC(
    () => slots.find((s) => s.name === persona) || null,
    [slots, persona],
  )

  const send = async (text) => {
    const trimmed = (text || '').trim()
    if (!trimmed) return
    if (pending) return // single-flight; brief excludes cancel-and-resend

    const modelId = personaSlot?.model_id || personaSlot?.model
    if (!modelId) {
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: trimmed, ts: Date.now() },
        {
          role: 'error',
          content: `No model on persona ${persona}. Pick a chat slot with a model loaded.`,
          ts: Date.now(),
        },
      ])
      return
    }

    // Build the request history from the current messages + the new user
    // turn, filtering out any prior error rows (those are UI-only).
    const history = [
      ...messages
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({ role: m.role, content: m.content })),
      { role: 'user', content: trimmed },
    ]
    const userMsg = { role: 'user', content: trimmed, ts: Date.now() }
    const placeholderTs = Date.now() + 1
    const placeholder = {
      role: 'assistant',
      content: '',
      reasoning: '',
      ts: placeholderTs,
      model: modelId,
      streaming: true,
    }
    setMessages((prev) => [...prev, userMsg, placeholder])
    setPending('sending')

    const ac = new AbortController()
    abortRef.current = ac

    try {
      const final = await streamChatCompletion({
        model: modelId,
        messages: history,
        stream: true,
        signal: ac.signal,
        onDelta: ({ content, reasoning }) => {
          setPending('streaming')
          setMessages((prev) =>
            prev.map((m) =>
              m.ts === placeholderTs
                ? { ...m, content, reasoning, streaming: true }
                : m,
            ),
          )
        },
      })
      setMessages((prev) =>
        prev.map((m) =>
          m.ts === placeholderTs
            ? {
                ...m,
                content: final.content,
                reasoning: final.reasoning ?? m.reasoning ?? '',
                model: final.model,
                streaming: false,
                reasoningOnly: final.reasoningOnly,
              }
            : m,
        ),
      )
    } catch (err) {
      if (ac.signal.aborted) {
        // User pressed Stop — keep whatever we streamed, mark non-streaming.
        setMessages((prev) =>
          prev.map((m) =>
            m.ts === placeholderTs ? { ...m, streaming: false, aborted: true } : m,
          ),
        )
      } else {
        setMessages((prev) =>
          prev.map((m) =>
            m.ts === placeholderTs
              ? {
                  role: 'error',
                  content: err && err.message ? String(err.message) : 'Request failed',
                  ts: placeholderTs,
                }
              : m,
          ),
        )
      }
    } finally {
      abortRef.current = null
      setPending(null)
    }
  }

  const stop = () => {
    if (abortRef.current) abortRef.current.abort()
  }

  return { messages, send, stop, pending, personaSlot }
}

// ─── Message rendering ───
function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

// ─── ReasoningBlock ───
//
// Renders the model's chain-of-thought ABOVE the answer in a greyed,
// 3-line-clamped box with a "thinking ▾ / ▴" toggle.
//
// Auto-collapse behaviour (per design brief):
//   - If reasoning streams in but no answer yet → block is auto-EXPANDED so
//     the user knows the model is alive.
//   - Once the answer starts streaming (or finishes) → block auto-COLLAPSES.
//   - The auto-collapse only fires ONCE per message; if the user toggles
//     manually after that, their choice sticks even if more deltas land.
function ReasoningBlock({ text, autoExpand }) {
  const [expanded, setExpanded] = useStateC(autoExpand)
  const [userTouched, setUserTouched] = useStateC(false)
  // Drive auto-collapse from `autoExpand`. While streaming + no answer
  // yet, `autoExpand` is true → block stays open. When the answer starts
  // streaming, the parent sets `autoExpand=false` → we auto-collapse,
  // unless the user already clicked the toggle (their choice wins).
  useEffectC(() => {
    if (userTouched) return
    setExpanded(autoExpand)
  }, [autoExpand, userTouched])
  if (!text) return null
  return (
    <div className={'bubble-reasoning' + (expanded ? ' open' : '')}>
      <button
        type="button"
        className="toggle mono"
        aria-expanded={expanded}
        onClick={() => {
          setUserTouched(true)
          setExpanded((v) => !v)
        }}
      >
        thinking {expanded ? '▴' : '▾'}
      </button>
      <div className="text">{text}</div>
    </div>
  )
}

function MessageList({ messages }) {
  const scrollRef = useRefC(null)
  useEffectC(() => {
    // Pin to bottom on new content. Cheap; ok for short sessions.
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])
  return (
    <div className="chat-body" ref={scrollRef}>
      {messages.map((m) => {
        if (m.role === 'error') {
          return (
            <div key={m.ts} className="msg">
              <div className="meta mono" style={{ color: 'var(--err, #ff6464)' }}>
                error · {fmtTime(m.ts)}
              </div>
              <div
                className="bubble"
                style={{
                  borderColor: 'var(--err, #ff6464)',
                  background: 'rgba(255, 100, 100, 0.06)',
                }}
              >
                {m.content}
              </div>
            </div>
          )
        }
        if (m.role === 'user') {
          return (
            <div key={m.ts} className="msg user">
              <div className="meta mono">you · {fmtTime(m.ts)}</div>
              <div className="bubble">{m.content}</div>
            </div>
          )
        }
        // assistant — reasoning (if any) renders ABOVE the answer, never inline.
        const hasReasoning = !!(m.reasoning && m.reasoning.length > 0)
        const hasAnswer = !!(m.content && m.content.length > 0)
        // Auto-expand reasoning while the model is still thinking (streaming
        // with no answer yet). Collapse as soon as the answer surface starts
        // filling, or once the message is no longer streaming.
        const autoExpand = !!(m.streaming && hasReasoning && !hasAnswer)
        // Bubble placeholder: dim ellipsis while we wait for any content at
        // all (no reasoning + no answer yet on a streaming message).
        const showWaitingDots = m.streaming && !hasAnswer && !hasReasoning
        return (
          <div key={m.ts} className="msg">
            <div className="meta mono">
              <b>assistant</b>
              {m.model ? <> · {m.model}</> : null}
              {m.ts ? <> · {fmtTime(m.ts)}</> : null}
              {m.streaming ? <> · streaming…</> : null}
              {m.aborted ? <> · stopped</> : null}
            </div>
            {hasReasoning && (
              <ReasoningBlock text={m.reasoning} autoExpand={autoExpand} />
            )}
            <div className="bubble">
              {hasAnswer ? (
                m.content
              ) : showWaitingDots ? (
                '…'
              ) : m.streaming ? (
                // Streaming + we already have reasoning above but the answer
                // hasn't started — render a faint blinking caret so the
                // bubble visibly exists.
                <span className="bubble-caret" aria-hidden="true">▌</span>
              ) : m.reasoningOnly ? (
                <span style={{ color: 'var(--fg-4)', fontStyle: 'italic' }}>
                  (no final answer — see thinking above)
                </span>
              ) : (
                ''
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ─── Live composer-state derivation ───
//
// The Tweaks panel still drives a demo composer state for QA; merge it with
// the live send/stream state so the real flow wins when the user is actually
// sending.
function derivedComposerState({ tweakState, pending, lemondOffline }) {
  if (lemondOffline) return 'offline'
  if (pending === 'streaming') return 'streaming'
  if (pending === 'sending') return 'sending'
  return tweakState || 'idle'
}

// ─── Chat view (active conversation) ───
function ChatActive({ slots, persona, onPersona, placement, composerState }) {
  const [draft, setDraft] = useStateC('')
  const { messages, send, stop, pending } = useChat(slots, persona)
  const liveState = derivedComposerState({
    tweakState: composerState,
    pending,
    lemondOffline: composerState === 'offline',
  })
  const onSend = async () => {
    const text = draft
    setDraft('')
    await send(text)
  }
  return (
    <div className="chat">
      <div className="chat-head mono">
        <span>Conversation</span>
        <span className="state-pill">
          <span className="dot" style={{ background: 'currentColor' }} />
          {pending === 'streaming' ? 'streaming' : pending === 'sending' ? 'sending' : 'ready'}
        </span>
        <span className="ct" style={{ marginLeft: 8, color: 'var(--fg-5)' }}>
          · {messages.length} message{messages.length === 1 ? '' : 's'}
        </span>
        <div className="right">
          <span className="ic" title="New chat" onClick={() => window.location.reload()}>
            {Icons.plus}
          </span>
          <span className="ic" title="Export (coming soon)">
            {Icons.ext}
          </span>
        </div>
      </div>
      <MessageList messages={messages} />
      <Composer
        slots={slots}
        persona={persona}
        onPersona={onPersona}
        draft={draft}
        setDraft={setDraft}
        onSend={onSend}
        onStop={stop}
        placement={placement}
        state={liveState}
      />
    </div>
  )
}

// ─── Empty chat ───
//
// "Empty" is now the actual zero-state — no scripted welcome bubbles. When
// the user sends the first message we keep using this hook but the view
// flips to showing the message list, then back to prompts if cleared.
function ChatEmpty({ slots, persona, onPersona, placement, composerState }) {
  const [draft, setDraft] = useStateC('')
  const { messages, send, stop, pending } = useChat(slots, persona)
  const liveState = derivedComposerState({
    tweakState: composerState,
    pending,
    lemondOffline: composerState === 'offline',
  })
  const prompts = [
    'Refactor a file in this repo',
    'Generate a hero image',
    'Transcribe this audio',
    'Embed and rerank this passage',
    'What can you do?',
  ]
  const hasMessages = messages.length > 0
  const onSend = async () => {
    const text = draft
    setDraft('')
    await send(text)
  }
  return (
    <div className="chat">
      <div className="chat-head mono">
        <span>{hasMessages ? 'Conversation' : 'New conversation'}</span>
        <span
          className="state-pill"
          style={{
            background: 'var(--accent-soft)',
            color: 'var(--accent)',
            borderColor: 'var(--accent-line)',
          }}
        >
          <span className="dot" style={{ background: 'currentColor' }} />
          {pending === 'streaming' ? 'streaming' : pending === 'sending' ? 'sending' : 'ready'}
        </span>
        <div className="right">
          <span className="ic" title="History (coming soon)">
            {Icons.logs}
          </span>
        </div>
      </div>
      {hasMessages ? (
        <MessageList messages={messages} />
      ) : (
        <div className="empty-chat">
          <div className="glyph mono">
            <Wordmark size={32} />
          </div>
          <h3>What should we build?</h3>
          <p>
            hal0 is running locally on{' '}
            <span className="mono" style={{ color: 'var(--fg)' }}>
              {HAL0_DATA.host.name}
            </span>
            . Default persona is{' '}
            <span className="mono" style={{ color: 'var(--accent)' }}>
              {persona}
            </span>
            . Type below or pick a starting prompt.
          </p>
          <div className="prompts">
            {prompts.map((p, i) => (
              <div key={i} className="prompt" onClick={() => setDraft(p)}>
                {p}
              </div>
            ))}
          </div>
        </div>
      )}
      <Composer
        slots={slots}
        persona={persona}
        onPersona={onPersona}
        draft={draft}
        setDraft={setDraft}
        onSend={onSend}
        onStop={stop}
        placeholder="Type a message…"
        placement={placement}
        state={liveState}
      />
    </div>
  )
}

Object.assign(window, { ChatActive, ChatEmpty, Composer, PersonaPicker })
