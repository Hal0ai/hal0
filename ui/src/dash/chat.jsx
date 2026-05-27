// hal0 dashboard — chat surface (Composer + ChatActive + ChatEmpty + PersonaPicker)
//
// Extracted from dashboard.jsx in #200 / fix/chat-surface-functional to:
//   1. Replace the prototype's scripted bubbles + no-op `onSend` with a real
//      round-trip against `/v1/chat/completions` (Lemonade-backed).
//   2. Isolate the chat surface from the sibling-agent edits that touch
//      MemoryMap / ThroughputCard / HealthCard.
//
// Chat-page-overhaul: the chat surface now lives on its own `#chat` route.
// `ChatView` is the page-level shell; `ChatActive` / `ChatEmpty` remain
// available for the dashboard demo composer-state toggles (legacy, kept
// so the Tweaks panel composer-state preview still has somewhere to live).
//
// New affordances on this page:
//   - Reasoning toggle (header pill) — gates `ReasoningBlock` rendering;
//     persisted to localStorage `hal0.chat.showReasoning`, default OFF.
//   - New chat — clears the conversation in-place via useChat().clear()
//     instead of reloading the window.
//   - Popout — opens a lean chat-only window at `#chat?popout=1`.

import { streamChatCompletion } from '@/api/hooks/useChatCompletions'
import { useSlots } from '@/api/hooks/useSlots'

const { useState: useStateC, useRef: useRefC, useEffect: useEffectC, useMemo: useMemoC } = React

const SHOW_REASONING_KEY = 'hal0.chat.showReasoning'

function readShowReasoning() {
  try {
    return window.localStorage.getItem(SHOW_REASONING_KEY) === '1'
  } catch {
    return false
  }
}

function useShowReasoning() {
  const [on, setOn] = useStateC(() => readShowReasoning())
  useEffectC(() => {
    try {
      window.localStorage.setItem(SHOW_REASONING_KEY, on ? '1' : '0')
    } catch {
      // ignore (Safari private mode etc.)
    }
  }, [on])
  return [on, setOn]
}

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
// Wrapped in a `.composer-input-card` so the typing surface has an
// explicit border + focus-within accent ring. The persona row (when
// `placement === 'above'`) and any state banner sit OUTSIDE the card so
// they read as page chrome, not part of the input.
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
      <div className="composer-input-card" onClick={(e) => e.stopPropagation()}>
        <div className="composer-bar">
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

  const clear = () => {
    if (abortRef.current) abortRef.current.abort()
    setMessages([])
    setPending(null)
  }

  return { messages, send, stop, clear, pending, personaSlot }
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
function ReasoningBlock({ text, autoExpand }) {
  const [expanded, setExpanded] = useStateC(autoExpand)
  const [userTouched, setUserTouched] = useStateC(false)
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

function MessageList({ messages, showReasoning }) {
  const scrollRef = useRefC(null)
  useEffectC(() => {
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
        const hasReasoning = !!(m.reasoning && m.reasoning.length > 0)
        const hasAnswer = !!(m.content && m.content.length > 0)
        const autoExpand = !!(m.streaming && hasReasoning && !hasAnswer)
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
            {showReasoning && hasReasoning && (
              <ReasoningBlock text={m.reasoning} autoExpand={autoExpand} />
            )}
            <div className="bubble">
              {hasAnswer ? (
                m.content
              ) : showWaitingDots ? (
                '…'
              ) : m.streaming ? (
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
function derivedComposerState({ tweakState, pending, lemondOffline }) {
  if (lemondOffline) return 'offline'
  if (pending === 'streaming') return 'streaming'
  if (pending === 'sending') return 'sending'
  return tweakState || 'idle'
}

// ─── Reasoning toggle pill ───
function ReasoningToggle({ on, onToggle }) {
  return (
    <button
      type="button"
      className={'reasoning-toggle' + (on ? ' on' : '')}
      onClick={onToggle}
      title={on ? 'Hide model reasoning' : 'Show model reasoning'}
      aria-pressed={on}
    >
      <span>reasoning</span>
      <span className="bullet">{on ? '●' : '○'}</span>
    </button>
  )
}

// ─── Chat header ───
//
// Shared header for the page-level ChatView and the legacy ChatActive /
// ChatEmpty dashboard demo surfaces. `popout` hides the popout-window
// button (you don't pop out of a popout) and the side-card chrome is
// already absent in the popout shell.
function ChatHeader({ title, pending, messageCount, showReasoning, onToggleReasoning, onNewChat, onPopout, popout }) {
  return (
    <div className="chat-head mono">
      <span>{title}</span>
      <span
        className="state-pill"
        style={
          messageCount === 0
            ? { background: 'var(--accent-soft)', color: 'var(--accent)', borderColor: 'var(--accent-line)' }
            : undefined
        }
      >
        <span className="dot" style={{ background: 'currentColor' }} />
        {pending === 'streaming' ? 'streaming' : pending === 'sending' ? 'sending' : 'ready'}
      </span>
      {messageCount > 0 && (
        <span className="ct" style={{ marginLeft: 8, color: 'var(--fg-5)' }}>
          · {messageCount} message{messageCount === 1 ? '' : 's'}
        </span>
      )}
      <ReasoningToggle on={showReasoning} onToggle={onToggleReasoning} />
      <div className="right">
        <span className="ic" title="New chat" onClick={onNewChat}>
          {Icons.plus}
        </span>
        {!popout && onPopout && (
          <span className="ic" title="Open in new window" onClick={onPopout}>
            {Icons.ext}
          </span>
        )}
      </div>
    </div>
  )
}

// ─── ChatPanel ───
//
// Source of truth for the chat surface. Picks between starter prompts
// (when the conversation is empty) and the message list. Used by the
// page-level ChatView; the legacy ChatActive / ChatEmpty wrappers
// reuse it with their own header styling for the Tweaks-panel demo.
function ChatPanel({
  slots,
  persona,
  onPersona,
  placement,
  composerState,
  popout,
}) {
  const [draft, setDraft] = useStateC('')
  const [showReasoning, setShowReasoning] = useShowReasoning()
  const { messages, send, stop, clear, pending } = useChat(slots, persona)
  const liveState = derivedComposerState({
    tweakState: composerState,
    pending,
    lemondOffline: composerState === 'offline',
  })
  const hasMessages = messages.length > 0
  const onSend = async () => {
    const text = draft
    setDraft('')
    await send(text)
  }
  const openPopout = () => {
    const url = window.location.origin + window.location.pathname + '#chat?popout=1'
    window.open(url, 'hal0-chat-popout', 'popup=yes,width=480,height=760')
  }
  const prompts = [
    'Refactor a file in this repo',
    'Generate a hero image',
    'Transcribe this audio',
    'Embed and rerank this passage',
    'What can you do?',
  ]
  return (
    <div className={'chat' + (popout ? ' chat-popout' : '')}>
      <ChatHeader
        title={hasMessages ? 'Conversation' : 'New conversation'}
        pending={pending}
        messageCount={messages.length}
        showReasoning={showReasoning}
        onToggleReasoning={() => setShowReasoning((v) => !v)}
        onNewChat={clear}
        onPopout={openPopout}
        popout={popout}
      />
      {hasMessages ? (
        <MessageList messages={messages} showReasoning={showReasoning} />
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
        placeholder={hasMessages ? undefined : 'Type a message…'}
        placement={placement}
        state={liveState}
      />
    </div>
  )
}

// ─── Page-level chat view ───
function ChatView({ slots: slotsProp, persona, setPersona, personaPlacement, composerState, popout }) {
  const slotsQuery = useSlots()
  const slots = (slotsQuery.data && slotsQuery.data.length > 0) ? slotsQuery.data : slotsProp
  return (
    <div className={'view chat-view' + (popout ? ' chat-view-popout' : '')}>
      <ChatPanel
        slots={slots}
        persona={persona}
        onPersona={setPersona}
        placement={personaPlacement}
        composerState={composerState}
        popout={popout}
      />
    </div>
  )
}

// ─── Legacy ChatActive / ChatEmpty (Tweaks-panel composer-state preview) ───
//
// The dashboard previously hosted the chat surface and exposed three
// states (empty / active / skip) through the Tweaks panel. Chat now
// lives at /chat; these wrappers stay so anything still wired to
// ChatActive / ChatEmpty (e.g. external demos, screenshot harnesses)
// keeps rendering. New code should use `<ChatView />`.
function ChatActive({ slots, persona, onPersona, placement, composerState }) {
  return (
    <ChatPanel
      slots={slots}
      persona={persona}
      onPersona={onPersona}
      placement={placement}
      composerState={composerState}
    />
  )
}

function ChatEmpty({ slots, persona, onPersona, placement, composerState }) {
  return (
    <ChatPanel
      slots={slots}
      persona={persona}
      onPersona={onPersona}
      placement={placement}
      composerState={composerState}
    />
  )
}

Object.assign(window, { ChatView, ChatPanel, ChatActive, ChatEmpty, Composer, PersonaPicker })
