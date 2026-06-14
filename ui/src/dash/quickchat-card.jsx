// hal0 dashboard — QuickChatCard (W5)
//
// Real POST /v1/chat/completions stream tester.
// §0 NO STUB — every value comes from the live stream; no simulation.
// §1 Chat stream: POST /v1/chat/completions {model, messages, stream:true}
//    → OpenAI SSE frames parsed via fetch + ReadableStream getReader.
//
// Window-global module — Object.assign(window, {QuickChatCard}) at bottom.
// Consumed by the W3 grid as window.QuickChatCard.

import { useSlots } from '@/api/hooks/useSlots'

const { useState, useRef, useCallback, useEffect } = React

// ── SSE stream parser ─────────────────────────────────────────────────────────
// Reads OpenAI-format SSE: lines "data: <json>\n" terminated by "data: [DONE]".
// Calls onDelta(text) for each content chunk, onDone(usage) at end.
// Returns an abort() function.
function openChatStream({ model, messages, onDelta, onDone, onError }) {
  const controller = new AbortController()

  ;(async () => {
    let res
    try {
      res = await fetch('/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ model, messages, stream: true }),
        signal: controller.signal,
      })
    } catch (e) {
      if (e?.name !== 'AbortError') onError(e?.message ?? 'fetch failed')
      return
    }

    if (!res.ok) {
      let msg = `HTTP ${res.status}`
      try { const j = await res.json(); msg = j?.error?.message ?? j?.detail ?? msg } catch {}
      onError(msg)
      return
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''

    // eslint-disable-next-line no-constant-condition
    while (true) {
      let chunk
      try {
        chunk = await reader.read()
      } catch {
        break
      }
      if (chunk.done) break
      buf += decoder.decode(chunk.value, { stream: true })

      // Split on double-newline SSE frame boundary
      const frames = buf.split('\n\n')
      buf = frames.pop() ?? ''

      for (const frame of frames) {
        for (const line of frame.split('\n')) {
          if (!line.startsWith('data: ')) continue
          const raw = line.slice(6).trim()
          if (raw === '[DONE]') {
            onDone(null)
            return
          }
          try {
            const parsed = JSON.parse(raw)
            // Final usage frame (some backends send usage in the last chunk)
            if (parsed.usage) { onDone(parsed.usage); return }
            const delta = parsed?.choices?.[0]?.delta?.content
            if (delta) onDelta(delta)
          } catch {}
        }
      }
    }

    onDone(null)
  })()

  return () => controller.abort()
}

// ── Slot picker ───────────────────────────────────────────────────────────────
// Filters to chat-capable (group=chat or type=llm) + serving/ready.
function isChatCapable(slot) {
  if (slot._synthetic) return false
  const g = (slot.group ?? '').toLowerCase()
  const t = (slot.type ?? '').toLowerCase()
  const chatType = g === 'chat' || t === 'llm' || t === 'chat'
  const liveState = slot.state === 'serving' || slot.state === 'ready'
  return chatType && liveState
}

// Dot cls for slot state (mirrors §3 contract)
function stateToCls(state) {
  if (state === 'serving') return 'serving'
  if (state === 'ready') return 'stale'
  if (state === 'warming' || state === 'starting' || state === 'pulling') return 'warming'
  if (state === 'error') return 'error'
  return 'offline'
}

function SlotPicker({ slots, selected, onChange }) {
  if (!slots || slots.length === 0) {
    return (
      <select className="qc-picker" disabled>
        <option>no chat slots</option>
      </select>
    )
  }
  return (
    <select
      className="qc-picker"
      value={selected ?? ''}
      onChange={(e) => onChange(e.target.value)}
    >
      {slots.map((s) => (
        <option key={s.name} value={s.name}>
          {s.state === 'serving' ? '● ' : '○ '}
          {s.isDefault ? '★ ' : ''}
          {s.name}
        </option>
      ))}
    </select>
  )
}

// ── QuickChatCard ─────────────────────────────────────────────────────────────
export function QuickChatCard() {
  const slotsQ = useSlots()
  const allSlots = slotsQ.data ?? []
  const chatSlots = allSlots.filter(isChatCapable)

  // Find default slot or first available
  const defaultSlot = chatSlots.find((s) => s.isDefault) ?? chatSlots[0] ?? null
  const [selectedName, setSelectedName] = useState(null)

  // On first load / when slots arrive, set selection to default
  useEffect(() => {
    if (selectedName === null && defaultSlot) {
      setSelectedName(defaultSlot.name)
    }
  }, [defaultSlot, selectedName])

  const selectedSlot = chatSlots.find((s) => s.name === selectedName) ?? defaultSlot

  // Chat state
  const [input, setInput] = useState('')
  const [phase, setPhase] = useState('idle') // idle | thinking | streaming | done | error
  const [output, setOutput] = useState('')
  const [errorMsg, setErrorMsg] = useState(null)
  const [metrics, setMetrics] = useState(null) // { ttft, toks, tokS }

  // Streaming internals
  const abortRef = useRef(null)
  const startTimeRef = useRef(null)
  const firstTokenRef = useRef(null)
  const tokenCountRef = useRef(0)
  const outputRef = useRef('')
  const outputAreaRef = useRef(null)

  // Auto-scroll output area
  useEffect(() => {
    if (outputAreaRef.current) {
      outputAreaRef.current.scrollTop = outputAreaRef.current.scrollHeight
    }
  }, [output])

  // Cleanup on unmount
  useEffect(() => () => { abortRef.current?.() }, [])

  const send = useCallback(() => {
    const text = input.trim()
    if (!text || !selectedSlot || phase === 'streaming' || phase === 'thinking') return

    // Abort any previous stream
    abortRef.current?.()

    setPhase('thinking')
    setOutput('')
    setErrorMsg(null)
    setMetrics(null)
    outputRef.current = ''
    tokenCountRef.current = 0
    startTimeRef.current = performance.now()
    firstTokenRef.current = null

    abortRef.current = openChatStream({
      model: selectedSlot.name,
      messages: [{ role: 'user', content: text }],
      onDelta: (delta) => {
        const now = performance.now()
        if (firstTokenRef.current === null) {
          firstTokenRef.current = now
          setPhase('streaming')
        }
        outputRef.current += delta
        tokenCountRef.current += 1
        setOutput(outputRef.current)

        // Live metrics update
        const ttft = Math.round(firstTokenRef.current - startTimeRef.current)
        const elapsed = (now - firstTokenRef.current) / 1000
        const tokS = elapsed > 0.1 ? Math.round(tokenCountRef.current / elapsed) : null
        setMetrics({ ttft, toks: tokenCountRef.current, tokS })
      },
      onDone: (usage) => {
        setPhase('done')
        abortRef.current = null
        // Final metrics — use usage.completion_tokens if present
        const now = performance.now()
        const ttft = firstTokenRef.current
          ? Math.round(firstTokenRef.current - startTimeRef.current)
          : null
        const elapsed = firstTokenRef.current ? (now - firstTokenRef.current) / 1000 : 0
        const finalToks = usage?.completion_tokens ?? tokenCountRef.current
        const tokS = elapsed > 0.1 ? Math.round(finalToks / elapsed) : null
        setMetrics({ ttft, toks: finalToks, tokS })
      },
      onError: (msg) => {
        setPhase('error')
        setErrorMsg(msg)
        abortRef.current = null
      },
    })

    setInput('')
  }, [input, selectedSlot, phase])

  const handleKey = useCallback(
    (e) => {
      // ⌘+Enter or Ctrl+Enter sends
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        send()
      }
    },
    [send],
  )

  // ── Header right slot: slot picker ───────────────────────────────────────
  const pickerEl = (
    <SlotPicker
      slots={chatSlots}
      selected={selectedName ?? defaultSlot?.name ?? null}
      onChange={setSelectedName}
    />
  )

  // ── Output area content ──────────────────────────────────────────────────
  let outputContent
  if (phase === 'idle') {
    outputContent = (
      <span className="qc-hint">
        {selectedSlot
          ? `${selectedSlot.name} · send a message to test the stream`
          : 'select a slot to begin'}
      </span>
    )
  } else if (phase === 'thinking') {
    const ttftHint = selectedSlot?.metrics?.ttft
      ? `ttft ~${Math.round(selectedSlot.metrics.ttft * 1000)}ms…`
      : 'ttft …'
    outputContent = (
      <span className="qc-thinking">
        <span className="qc-spinner" aria-label="waiting" />
        <span className="qc-thinking-text">
          {selectedSlot?.name} · {ttftHint}
        </span>
      </span>
    )
  } else if (phase === 'streaming' || phase === 'done') {
    outputContent = (
      <div className="qc-message">
        <span className="qc-role-label">assistant</span>
        <span className="qc-text">
          {output}
          {phase === 'streaming' && <span className="qc-caret" aria-hidden="true" />}
        </span>
      </div>
    )
  } else if (phase === 'error') {
    outputContent = (
      <span className="qc-error">
        stream error: {errorMsg ?? 'unknown'}
      </span>
    )
  }

  // ── Metrics row ──────────────────────────────────────────────────────────
  const metricsEl = (metrics || selectedSlot) && (phase === 'streaming' || phase === 'done') ? (
    <div className="qc-metrics">
      <span className="qc-met-item">
        ttft <span className="qc-met-val">
          {metrics?.ttft != null ? `${metrics.ttft}ms` : '—'}
        </span>
      </span>
      <span className="qc-met-sep">·</span>
      <span className="qc-met-item">
        tok/s <span className="qc-met-val qc-met-accent">
          {metrics?.tokS != null ? metrics.tokS : '—'}
        </span>
      </span>
      <span className="qc-met-spacer" />
      {selectedSlot && (
        <span className="qc-met-route">
          {selectedSlot.port ? `:${selectedSlot.port}` : ''}
          {selectedSlot.port && selectedSlot.device ? ' · ' : ''}
          {selectedSlot.device ?? ''}
        </span>
      )}
    </div>
  ) : null

  return (
    <DCard
      title="QUICK CHAT"
      right={pickerEl}
    >
      {/* Output area */}
      <div className="qc-output" ref={outputAreaRef}>
        {outputContent}
      </div>

      {/* Metrics row */}
      {metricsEl}

      {/* Input row */}
      <div className="qc-input-row">
        <textarea
          className="qc-textarea"
          rows={2}
          placeholder="message…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKey}
          disabled={!selectedSlot || phase === 'thinking' || phase === 'streaming'}
        />
        <button
          className="qc-send"
          onClick={send}
          disabled={!selectedSlot || !input.trim() || phase === 'thinking' || phase === 'streaming'}
          title="Send (⌘+Enter)"
          aria-label="Send"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M14 8H2M14 8l-4-4M14 8l-4 4" />
          </svg>
        </button>
      </div>
    </DCard>
  )
}

Object.assign(window, { QuickChatCard })
