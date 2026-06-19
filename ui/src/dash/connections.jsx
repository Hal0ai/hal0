// hal0 dashboard — Connections view (connections-overhaul).
//
// Rebuilds the old "providers + upstreams" surface into two stacked
// engine-block panes (the ComfyUI / Inference pane vocabulary) and folds in
// the old MCP page as a section (the standalone #mcp route now aliases here):
//
//   1. Local endpoints — the OpenAI-compatible API this box serves, one row
//      per slot. Each row expands to quick actions: a health-check "Test"
//      that fires a real ping through the gateway and reads back round-trip /
//      ttft / tok-s, and a cURL builder that targets the gateway `/v1` with
//      the slot's model id selecting the route.
//   2. MCP servers — the bundled hal0-admin / hal0-memory FastMCP servers,
//      each its own expandable pane with add-to-client config snippets and a
//      capability/blast-radius tool manifest (name · args · gated · hints).
//
// Wiring (vs. the design refs): endpoints target the GATEWAY (slot ports bind
// loopback inside the runtime, not the LAN); the model id — not a per-slot
// port — selects the slot. Auth is open on the LAN, so the cURL carries an
// optional `$HAL0_KEY` placeholder rather than a real key. Tool detail comes
// from GET /api/mcp/servers (`tool_details`).

import { useSlots } from '@/api/hooks/useSlots'
import { useMcpServers } from '@/api/hooks/useMcp'
import { useConfigUrls } from '@/api/hooks/useConfigUrls'

const { useState: useCS } = React

// ─── icons (name-based shim over the global <Icon>) ───────────────────
const CICONS = {
  chev: { d: 'M4 6l4 4 4-4' },
  check: { d: 'M3 8l3 3 7-7' },
  send: { d: 'M2 8l12-6-3 14-3-6-6-2z' },
  ext: { d: 'M6 3H3v10h10v-3M9 3h4v4M9 9l4-4' },
  copy: {
    c: (
      <>
        <rect x="5" y="5" width="8" height="8" rx="1" />
        <path d="M3 11V3h8" />
      </>
    ),
  },
  terminal: {
    c: (
      <>
        <rect x="2" y="3" width="12" height="10" rx="1" />
        <path d="M5 6.5l2 1.5-2 1.5" />
        <path d="M8.5 10.5h3" />
      </>
    ),
  },
  link: {
    c: (
      <>
        <path d="M6.5 9.5a2.5 2.5 0 0 0 3.5 0l2-2a2.5 2.5 0 0 0-3.5-3.5l-1 1" />
        <path d="M9.5 6.5a2.5 2.5 0 0 0-3.5 0l-2 2a2.5 2.5 0 0 0 3.5 3.5l1-1" />
      </>
    ),
  },
  gauge: {
    c: (
      <>
        <path d="M2.5 11a5.5 5.5 0 1 1 11 0" />
        <path d="M8 11l3-3" />
      </>
    ),
  },
  connections: {
    c: (
      <>
        <circle cx="6" cy="8" r="2.5" />
        <circle cx="11" cy="11" r="1.5" fill="currentColor" stroke="none" />
        <path d="M8 9.5l2 1M3.5 4.5h4M3.5 6.5h3" />
      </>
    ),
  },
  agent: {
    c: (
      <>
        <circle cx="8" cy="6" r="2.5" />
        <path d="M3 14c0-2.5 2.2-4.5 5-4.5s5 2 5 4.5" />
        <circle cx="13" cy="3" r="1.5" />
      </>
    ),
  },
}
function CIcon({ name, size = 14 }) {
  const ic = CICONS[name] || CICONS.chev
  return ic.d ? <Icon d={ic.d} size={size} /> : <Icon size={size}>{ic.c}</Icon>
}

// ─── helpers ─────────────────────────────────────────────────────────
function copyText(t) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(t)
      return
    }
  } catch (e) {
    /* fall through */
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = t
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  } catch (e) {
    /* no-op */
  }
}
function toast(msg) {
  if (typeof window !== 'undefined' && window.__hal0Toast) window.__hal0Toast(msg)
}
function devClass(d) {
  return 'chip dev-' + (d || 'cpu').replace('gpu-', '')
}
function devLabel(d) {
  return (d || 'cpu').replace('gpu-', '')
}
function modelId(s) {
  return s.model_id || s.model || s.name
}

// Lifecycle → the five dot states the pane styles. Unknown / transitional
// backend states fold into the nearest visual bucket.
function epState(s) {
  const st = s.state
  if (['serving', 'ready', 'warming', 'idle', 'offline'].includes(st)) return st
  if (st === 'loading' || st === 'starting' || st === 'pending') return 'warming'
  if (st === 'error' || st === 'failed' || st === 'stopped' || st === 'unloaded') return 'offline'
  return 'idle'
}

// Slot → the OpenAI route family. Maps the slot `type` (a true modality) onto
// a route family; any unrecognised type defaults to chat.
function slotGroup(s) {
  const t = s.type
  if (t === 'llm') return 'chat'
  if (t === 'embedding' || t === 'reranking') return 'embed'
  if (t === 'image') return 'img'
  if (t === 'tts' || t === 'transcription') return 'voice'
  return 'chat'
}
function isStt(s) {
  return s.name === 'stt' || s.type === 'transcription'
}
function routeFor(s) {
  const g = slotGroup(s)
  if (g === 'embed') return '/v1/embeddings'
  if (g === 'img') return '/v1/images/generations'
  if (isStt(s)) return '/v1/audio/transcriptions'
  if (g === 'voice') return '/v1/audio/speech'
  return '/v1/chat/completions'
}
function slotToks(s) {
  const t = s.toks != null ? s.toks : s.metrics?.toks
  return typeof t === 'number' ? t : null
}
function slotCtx(s) {
  const c = s.ctx != null ? s.ctx : s.metrics?.ctx
  if (c == null) return '—'
  if (typeof c === 'number') return c >= 1024 ? Math.round(c / 1024) + 'k' : String(c)
  return String(c)
}

function defaultPrompt(s) {
  const g = slotGroup(s)
  if (g === 'embed') return 'the quick brown fox'
  if (g === 'img') return 'sodium-amber LED on a dark server rack, macro'
  if (isStt(s)) return '(clip.wav)'
  if (g === 'voice') return 'system steady on strix halo one'
  return 'Reply with the single word: pong'
}

// cURL targets the gateway /v1; the model id selects the slot. The runtime
// is open on the LAN (no inbound auth in v0.3), so the command carries no
// Authorization header.
function buildCurl(s, apiBase, prompt) {
  const url = apiBase + routeFor(s)
  const model = modelId(s)
  if (isStt(s)) {
    return 'curl ' + url + ' \\\n  -F model=' + model + ' \\\n  -F file=@clip.wav'
  }
  const g = slotGroup(s)
  let body
  if (g === 'embed') body = { model, input: prompt }
  else if (g === 'img') body = { model, prompt, size: '1024x1024' }
  else if (g === 'voice') body = { model, input: prompt, voice: 'af_sky' }
  else body = { model, messages: [{ role: 'user', content: prompt }], stream: false }
  const json = JSON.stringify(body, null, 2)
  return (
    'curl ' + url + ' \\\n  -H "Content-Type: application/json" \\\n  -d \'' + json + "'"
  )
}

// crude per-line colouriser for the curl well
function colorLine(ln, key) {
  const re = /(curl)|(https?:\/\/[^\s\\]+)|("[^"]*")|('[^']*'?)/g
  const out = []
  let last = 0
  let m
  let i = 0
  while ((m = re.exec(ln))) {
    if (m.index > last) out.push(ln.slice(last, m.index))
    const cls = m[1] ? 'tok-cmd' : m[2] ? 'tok-url' : 'tok-str'
    out.push(
      <span key={key + '-' + i++} className={cls}>
        {m[0]}
      </span>,
    )
    last = re.lastIndex
  }
  if (last < ln.length) out.push(ln.slice(last))
  return <div key={key}>{out}</div>
}

// ─── real endpoint ping (through the gateway) ─────────────────────────
function _netErr(e) {
  return {
    ok: false,
    status: 'network',
    latText: 'unreachable',
    metrics: [],
    sample: 'could not reach the gateway — ' + (e?.message || 'network error') + '. Is hal0-api up?',
  }
}
async function _httpErr(res) {
  let body = ''
  try {
    body = (await res.text()).slice(0, 180)
  } catch (e) {
    /* ignore */
  }
  return {
    ok: false,
    status: String(res.status),
    latText: res.statusText || 'error',
    metrics: [],
    sample:
      body ||
      'the endpoint returned ' +
        res.status +
        ' — the slot may be offline; load it from Slots, then re-test.',
  }
}

async function _pingChat(apiBase, model, prompt) {
  const t0 = performance.now()
  let res
  try {
    res = await fetch(apiBase + '/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model,
        messages: [{ role: 'user', content: prompt }],
        max_tokens: 16,
        stream: true,
        stream_options: { include_usage: true },
      }),
    })
  } catch (e) {
    return _netErr(e)
  }
  if (!res.ok || !res.body) return _httpErr(res)
  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let ttft = null
  let completion = 0
  let usage = null
  let content = ''
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    if (ttft === null) ttft = performance.now() - t0
    buf += dec.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx).trim()
      buf = buf.slice(idx + 1)
      if (!line.startsWith('data:')) continue
      const data = line.slice(5).trim()
      if (!data || data === '[DONE]') continue
      try {
        const j = JSON.parse(data)
        if (j.usage) usage = j.usage
        const delta = j.choices?.[0]?.delta?.content
        if (delta) {
          content += delta
          completion++
        }
      } catch (e) {
        /* partial frame — wait for more */
      }
    }
  }
  const total = performance.now() - t0
  const compTok = usage?.completion_tokens ?? completion ?? 0
  const genMs = Math.max(1, total - (ttft || 0))
  const tps = compTok > 0 ? compTok / (genMs / 1000) : null
  return {
    ok: true,
    status: '200 OK',
    latText: Math.round(total) + ' ms',
    metrics: [
      { l: 'round-trip', v: Math.round(total), u: 'ms' },
      { l: 'ttft', v: Math.round(ttft || 0), u: 'ms' },
      { l: 'tok/s', v: tps != null ? tps.toFixed(0) : '—', amber: true },
    ],
    sample: content.trim() ? '"' + content.trim().slice(0, 120) + '"' : '(empty completion)',
  }
}

async function _pingEmbed(apiBase, model, prompt) {
  const t0 = performance.now()
  let res
  try {
    res = await fetch(apiBase + '/v1/embeddings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, input: prompt || 'ping' }),
    })
  } catch (e) {
    return _netErr(e)
  }
  if (!res.ok) return _httpErr(res)
  const j = await res.json().catch(() => null)
  const lat = Math.round(performance.now() - t0)
  const vec = j?.data?.[0]?.embedding
  const dims = Array.isArray(vec) ? vec.length : null
  return {
    ok: true,
    status: '200 OK',
    latText: lat + ' ms',
    metrics: [
      { l: 'round-trip', v: lat, u: 'ms' },
      { l: 'dims', v: dims ?? '—' },
      { l: 'vectors', v: j?.data?.length ?? 1 },
    ],
    sample: dims
      ? 'float32[' + dims + '] · ' + vec.slice(0, 3).map((x) => x.toFixed(4)).join(', ') + ' …'
      : 'embedding returned',
  }
}

// img / voice / stt — a reachability probe via /v1/models, NOT a real
// generation (we don't fire expensive GPU work from a dashboard button).
async function _probeReachable(apiBase, model, group, stt) {
  const t0 = performance.now()
  let res
  try {
    res = await fetch(apiBase + '/v1/models', { headers: { Accept: 'application/json' } })
  } catch (e) {
    return _netErr(e)
  }
  if (!res.ok) return _httpErr(res)
  const j = await res.json().catch(() => null)
  const lat = Math.round(performance.now() - t0)
  const list = Array.isArray(j?.data) ? j.data : []
  const listed = list.some((m) => m.id === model)
  const route = stt ? '/v1/audio/transcriptions' : group === 'img' ? '/v1/images/generations' : '/v1/audio/speech'
  return {
    ok: true,
    status: '200 OK',
    latText: lat + ' ms',
    metrics: [
      { l: 'round-trip', v: lat, u: 'ms' },
      { l: 'models', v: list.length },
      { l: 'listed', v: listed ? 'yes' : 'no' },
    ],
    sample:
      'reachability probe · /v1/models — ' +
      (listed
        ? "'" + model + "' is advertised; POST " + route + ' to generate.'
        : 'model not currently advertised — load it from Slots.') +
      ' (generation not fired)',
  }
}

async function pingEndpoint(slot, apiBase, prompt) {
  if (epState(slot) === 'offline') {
    return {
      ok: false,
      status: '503',
      latText: 'unreachable',
      metrics: [],
      sample: 'slot "' + slot.name + '" is offline — load it from Slots, then test this endpoint.',
    }
  }
  const g = slotGroup(slot)
  if (g === 'chat') return _pingChat(apiBase, modelId(slot), prompt)
  if (g === 'embed') return _pingEmbed(apiBase, modelId(slot), prompt)
  return _probeReachable(apiBase, modelId(slot), g, isStt(slot))
}

// ─── small copy buttons ──────────────────────────────────────────────
function CopyIcon({ text, title }) {
  const [done, setDone] = useCS(false)
  return (
    <button
      className={'copybtn' + (done ? ' done' : '')}
      title={title || 'Copy'}
      onClick={(e) => {
        e.stopPropagation()
        copyText(text)
        setDone(true)
        toast((title || 'copied') + ' · clipboard')
        setTimeout(() => setDone(false), 1200)
      }}
    >
      <CIcon name={done ? 'check' : 'copy'} size={13} />
    </button>
  )
}
function CopyBtn({ text, label, icon, cls }) {
  const [done, setDone] = useCS(false)
  return (
    <button
      className={(cls || 'cbtn') + (done ? ' ok-flash' : '')}
      onClick={(e) => {
        e.stopPropagation()
        copyText(text)
        setDone(true)
        toast(label + ' copied · clipboard')
        setTimeout(() => setDone(false), 1300)
      }}
    >
      <CIcon name={done ? 'check' : icon || 'copy'} size={13} />
      {done ? 'Copied' : label}
    </button>
  )
}

// ─── reusable engine-block pane ──────────────────────────────────────
function EnginePane({ live, glyph, eyebrow, title, sub, pill, headRight, strip, foot, defaultOpen, children }) {
  const [open, setOpen] = useCS(defaultOpen !== false)
  return (
    <section>
      <div className="conn-eyebrow">{eyebrow}</div>
      <div className={'engine cpane' + (live ? ' live active' : '') + (open ? ' open' : '')}>
        <div className="engine-h cpane-h" onClick={() => setOpen((o) => !o)}>
          <span className="engine-glyph cpane-glyph">
            <CIcon name={glyph} size={16} />
          </span>
          <span className="cpane-titles">
            <span className="engine-title cpane-title">{title}</span>
            <span className="engine-sub cpane-sub">{sub}</span>
          </span>
          <span className={'cpill ' + (pill.tone || '')}>
            <span className="dot" />
            {pill.text}
          </span>
          <span className="grow" />
          {headRight && (
            <span className="eh-right" onClick={(e) => e.stopPropagation()}>
              {headRight}
            </span>
          )}
          <span className="caret">
            <CIcon name="chev" size={16} />
          </span>
        </div>
        {strip && <div className="cpane-strip">{strip}</div>}
        <div className="cpane-body">
          <div className="cpane-body-inner">{children}</div>
        </div>
        {foot && <div className="cpane-foot">{foot}</div>}
      </div>
    </section>
  )
}

// ─── one endpoint row (inline list + expandable quick actions) ───────
function EndpointRow({ slot, apiBase }) {
  const [open, setOpen] = useCS(false)
  const [prompt, setPrompt] = useCS(defaultPrompt(slot))
  const [testing, setTesting] = useCS(false)
  const [result, setResult] = useCS(null)
  const route = routeFor(slot)
  const base = apiBase
  const st = epState(slot)
  const toks = slotToks(slot)
  const curl = buildCurl(slot, apiBase, prompt)
  const dimmed = st === 'offline' || st === 'idle'
  const grp = slotGroup(slot)

  async function runTest() {
    if (st === 'offline') {
      setResult(await pingEndpoint(slot, apiBase, prompt))
      return
    }
    setTesting(true)
    setResult(null)
    try {
      setResult(await pingEndpoint(slot, apiBase, prompt))
    } catch (e) {
      setResult(_netErr(e))
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className={'eprow' + (open ? ' expanded' : '') + (dimmed ? ' dim' : '')}>
      <div className="eprow-main" onClick={() => setOpen((o) => !o)}>
        <span className={'ep-dot ' + st} />
        <span className="ep-name">
          {slot.name}
          {slot.isDefault && <span className="star">★</span>}
        </span>
        <span className="ep-model">{modelId(slot)}</span>
        <span className="ep-dev">
          <span className={devClass(slot.device)}>{devLabel(slot.device)}</span>
        </span>
        <span className="ep-port">
          <span className="colon">:</span>
          {slot.port ?? '—'}
        </span>
        <span className={'ep-tps' + (toks ? '' : ' muted')}>{toks ? toks.toFixed(0) + ' t/s' : st}</span>
        <span className="ep-caret">
          <CIcon name="chev" size={14} />
        </span>
      </div>

      <div className="ep-drawer">
        <div className="ep-drawer-in">
          <div className="ep-cols">
            {/* endpoint detail */}
            <div className="ep-block">
              <div className="ep-block-h">
                <span className="ic">
                  <CIcon name="link" size={12} />
                </span>{' '}
                endpoint<span className="grow" />
                <span className="chip outlined">POST</span>
              </div>
              <div className="ep-kv">
                <div className="kv">
                  <span className="k">base</span>
                  <span className="copyline">
                    <span className="val url">{base}</span>
                    <CopyIcon text={base} title="base url" />
                  </span>
                </div>
                <div className="kv">
                  <span className="k">route</span>
                  <span className="val">{route}</span>
                </div>
                <div className="kv">
                  <span className="k">model</span>
                  <span className="copyline">
                    <span className="val">{modelId(slot)}</span>
                    <CopyIcon text={modelId(slot)} title="model id" />
                  </span>
                </div>
                <div className="kv">
                  <span className="k">auth</span>
                  <span className="val">none · open on lan</span>
                </div>
                <div className="kv">
                  <span className="k">ctx</span>
                  <span className="val">
                    {slotCtx(slot)}
                    {slot.coresident ? ' · coresident (npu)' : ''}
                  </span>
                </div>
              </div>
            </div>

            {/* health check */}
            <div className="ep-block">
              <div className="ep-block-h">
                <span className="ic">
                  <CIcon name="gauge" size={12} />
                </span>{' '}
                health check<span className="grow" />
              </div>
              <div className="ep-actions">
                <button className="cbtn primary" onClick={runTest} disabled={testing}>
                  {testing ? <span className="ep-spin" /> : <CIcon name="send" size={13} />}
                  {testing ? 'Testing…' : result ? 'Re-test' : 'Test endpoint'}
                </button>
                <span className="grow" />
                <span style={{ fontFamily: 'var(--jbm)', fontSize: 10, color: 'var(--fg-5)' }}>
                  {grp === 'chat'
                    ? 'real 16-token ping · nothing stored'
                    : grp === 'embed'
                      ? 'real embed ping · nothing stored'
                      : 'reachability probe · no generation'}
                </span>
              </div>
              <div className="ep-test">
                {!result && !testing && (
                  <div className="ep-test-empty">
                    <span className="glyph">▸ </span>
                    Ping the endpoint through the gateway to confirm it is reachable and read back
                    round-trip · ttft · throughput.
                  </div>
                )}
                {testing && (
                  <div className="ep-spinner">
                    <span className="ep-spin" />
                    POST {route} …
                  </div>
                )}
                {result && (
                  <div className={'ep-result' + (result.ok ? '' : ' err')}>
                    <div className="ep-result-h">
                      <span className="status">
                        <span className="dot" />
                        {result.status}
                      </span>
                      <span className="grow" />
                      <span className="lat">{result.latText}</span>
                    </div>
                    {result.metrics.length > 0 && (
                      <div className="ep-metrics">
                        {result.metrics.map((m, i) => (
                          <div className="m" key={i}>
                            <span className="l">{m.l}</span>
                            <span className={'v' + (m.amber ? ' amber' : '')}>
                              {m.v}
                              {m.u && <span className="u">{m.u}</span>}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="ep-sample">
                      <span className="lbl">{result.ok ? 'response' : 'error'}</span>
                      {result.sample}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* curl builder */}
          <div className="curl">
            <div className="curl-h">
              <span className="ic">
                <CIcon name="terminal" size={12} />
              </span>{' '}
              cURL · gateway /v1 · model selects slot<span className="grow" />
              <span style={{ textTransform: 'none', letterSpacing: 0, color: 'var(--fg-5)' }}>
                edit the prompt — the command updates
              </span>
            </div>
            {!isStt(slot) && (
              <div className="curl-prompt">
                <label>prompt</label>
                <input value={prompt} onChange={(e) => setPrompt(e.target.value)} spellCheck={false} />
              </div>
            )}
            <div className="curl-code">
              <pre>{curl.split('\n').map((ln, i) => colorLine(ln, 'l' + i))}</pre>
              <div className="curl-copy">
                <CopyBtn text={curl} label="Copy cURL" icon="copy" cls="cbtn sm" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── MCP client config builders ──────────────────────────────────────
const CLIENT_META = {
  claude: { label: 'Claude Desktop', file: 'claude_desktop_config.json' },
  codex: { label: 'Codex', file: '~/.codex/config.toml' },
  cursor: { label: 'Cursor', file: '.cursor/mcp.json' },
}
function buildClientConfig(client, servers) {
  if (client === 'codex')
    return servers
      .map((s) => '[mcp_servers.' + s.name + ']\nurl = "' + s.url + '"\ntransport = "' + s.transport + '"')
      .join('\n\n')
  const obj = { mcpServers: {} }
  servers.forEach((s) => {
    obj.mcpServers[s.name] = { url: s.url, transport: s.transport }
  })
  return JSON.stringify(obj, null, 2)
}
function AddTo({ client, servers }) {
  const meta = CLIENT_META[client]
  return (
    <button
      className="clientchip"
      onClick={(e) => {
        e.stopPropagation()
        copyText(buildClientConfig(client, servers))
        toast(meta.label + ' config copied · paste into ' + meta.file)
      }}
    >
      <span className="ic">
        <CIcon name="ext" size={12} />
      </span>
      {meta.label}
    </button>
  )
}

// ─── one MCP server (expandable pane with its tools) ─────────────────
function McpServerRow({ srv, defaultOpen }) {
  const [open, setOpen] = useCS(!!defaultOpen)
  const tools = srv.tools || []
  return (
    <div className={'mcprow' + (open ? ' expanded' : '')}>
      <div className="mcprow-main" onClick={() => setOpen((o) => !o)}>
        <span className={'mcp-dot' + (srv.up ? '' : ' down')} />
        <span className="mcp-id">
          <span>
            <span className="nm">{srv.name}</span>
            <span className="path">{srv.path}</span>
          </span>
          <div className="desc">{srv.desc}</div>
        </span>
        <span className="mcp-meta">
          {srv.connected > 0 && <span className="chip ok">{srv.connected} connected</span>}
          <span className="chip info">{srv.transport}</span>
        </span>
        <span className="mcp-toolct">
          <b>{tools.length || srv.toolCount}</b> tools
        </span>
        <span className="mcp-caret">
          <CIcon name="chev" size={14} />
        </span>
      </div>
      <div className="mcp-drawer">
        <div className="mcp-drawer-in">
          <div className="mcp-conn">
            <span className="urlpill">
              <span className="scheme">{srv.transport} ·</span>
              {srv.url}
            </span>
            <CopyIcon text={srv.url} title="server url" />
            <span className="grow" />
            <div className="mcp-links">
              <span className="lbl">add to</span>
              <AddTo client="claude" servers={[srv]} />
              <AddTo client="codex" servers={[srv]} />
              <AddTo client="cursor" servers={[srv]} />
            </div>
          </div>
          <div className="mcp-tools-h">
            <CIcon name="terminal" size={12} /> tools
            <span className="ct">· {tools.length} exposed</span>
            {srv.resources > 0 && <span className="ct">· {srv.resources} resources</span>}
            {srv.prompts > 0 && <span className="ct">· {srv.prompts} prompts</span>}
          </div>
          <div className="mcp-tools">
            {tools.map((t) => (
              <div className="mcp-tool" key={t.name}>
                <div className="mt-top">
                  <span className="mt-name">{t.name}</span>
                  {t.gated && <span className="mt-badge gated">approval</span>}
                  {t.destructive && <span className="mt-badge destructive">destructive</span>}
                  {t.read_only && <span className="mt-badge read">read-only</span>}
                  {t.open_world && <span className="mt-badge world">external</span>}
                  <span className="grow" />
                  <span className="mt-args">
                    {!t.args || t.args === '—' ? <span className="muted">no args</span> : t.args}
                  </span>
                </div>
                <div className="mt-desc">{t.description}</div>
              </div>
            ))}
            {tools.length === 0 && (
              <div className="mcp-tool" style={{ gridColumn: '1 / -1' }}>
                <div className="mt-desc">
                  No tool detail available — this server reports {srv.toolCount} tool
                  {srv.toolCount === 1 ? '' : 's'} but isn't live-introspected (registry-only).
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── data mapping: McpServer (API) → row shape ───────────────────────
function mcpPath(connectUrl) {
  try {
    return new URL(connectUrl).pathname
  } catch (e) {
    const i = connectUrl.indexOf('/mcp/')
    return i >= 0 ? connectUrl.slice(i) : connectUrl
  }
}
function toMcpRow(s) {
  return {
    name: s.name || s.id,
    path: mcpPath(s.connect_url || ''),
    url: s.connect_url || '',
    desc: s.description || '',
    transport: s.transport || 'http',
    up: s.state === 'running',
    connected: Array.isArray(s.connected) ? s.connected.length : 0,
    resources: s.resources || 0,
    prompts: s.prompts || 0,
    toolCount: s.tools || 0,
    tools: (s.tool_details || []).map((t) => ({
      name: t.name,
      args: t.args,
      description: t.description,
      gated: !!t.gated,
      destructive: !!t.destructive,
      read_only: !!t.read_only,
      open_world: !!t.open_world,
    })),
  }
}

// ─── extracted panels (embedded as tabs on Slots / Agents) ────────────
// v0.5 nav: the Connections page was dissolved. Its "Local endpoints"
// section now renders as the Slots ▸ Endpoints tab, and its "MCP servers"
// section as the Agents ▸ MCP tab. Each panel owns its own data hooks so
// the host tab can render it standalone; both reuse the EnginePane /
// EndpointRow / McpServerRow primitives defined above.

function LocalEndpointsPanel() {
  const slotsQuery = useSlots()
  const cfg = useConfigUrls()

  const slots = slotsQuery.data ?? []

  // Gateway base — prefer the backend-derived API URL (request-host aware),
  // fall back to the browser origin. Slot ports bind loopback inside the
  // runtime, so everything routes through this one base.
  const origin = typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8080'
  const apiBase = (cfg.data?.api || origin).replace(/\/$/, '')
  const gatewayV1 = apiBase + '/v1'
  const host = typeof window !== 'undefined' ? window.location.hostname : 'hal0'
  const port =
    typeof window !== 'undefined' && window.location.port
      ? window.location.port
      : typeof window !== 'undefined' && window.location.protocol === 'https:'
        ? '443'
        : '80'
  const tls = typeof window !== 'undefined' && window.location.protocol === 'https:'

  const serving = slots.filter((s) => epState(s) === 'serving').length
  const reachable = slots.filter((s) => epState(s) !== 'offline').length

  return (
    <EnginePane
      live
      glyph="connections"
      eyebrow={
        <>
          <b>local endpoints</b>
          <span className="dim">·</span>
          <span className="meta">openai-compatible</span>
          <span className="dim">·</span>
          <span className="mono" style={{ color: 'var(--fg-3)' }}>
            {slots.length} ports
          </span>
          <span className="grow" />
          <span className="meta">gateway · :{port}</span>
        </>
      }
      title="Local endpoints"
      sub="openai-compatible · /v1/*"
      pill={{ tone: 'live', text: serving + ' serving · ' + reachable + ' reachable' }}
      headRight={<CopyBtn text={gatewayV1} label="Base URL" icon="link" />}
      strip={
        <>
          <span className="ss-summary">
            <b>{serving}</b> serving · {reachable} reachable · {slots.length} local ports
          </span>
          <span className="grow" />
          <span className="ss-summary" style={{ fontFamily: 'var(--jbm)', color: 'var(--fg-4)' }}>
            {gatewayV1}
          </span>
        </>
      }
      foot={
        <>
          <span className="k">gateway</span>
          <span className="v amber">:{port}</span>
          <span className="sep">·</span>
          <span className="k">host</span>
          <span className="v">{host}</span>
          <span className="sep">·</span>
          <span className="k">tls</span>
          <span className="v">{tls ? 'on' : 'off · lan only'}</span>
          <span className="sep">·</span>
          <span className="k">auth</span>
          <span className="v">open · lan</span>
        </>
      }
    >
      <div className="eplist">
        <div className="ep-head">
          <span />
          <span>slot</span>
          <span>model</span>
          <span>device</span>
          <span>port</span>
          <span>tok/s</span>
          <span />
        </div>
        {slotsQuery.isPending && <div className="cn-empty mono">Loading endpoints…</div>}
        {!slotsQuery.isPending && slots.length === 0 && (
          <div className="cn-empty mono">No slots configured. Create one from Slots.</div>
        )}
        {slots.map((s) => (
          <EndpointRow key={s.name} slot={s} apiBase={apiBase} />
        ))}
      </div>
    </EnginePane>
  )
}

function McpServersPanel() {
  const mcpQuery = useMcpServers()
  const cfg = useConfigUrls()

  const servers = (mcpQuery.data ?? []).map(toMcpRow)

  const origin = typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8080'
  void (cfg.data?.api || origin)
  const host = typeof window !== 'undefined' ? window.location.hostname : 'hal0'
  const port =
    typeof window !== 'undefined' && window.location.port
      ? window.location.port
      : typeof window !== 'undefined' && window.location.protocol === 'https:'
        ? '443'
        : '80'

  const toolTotal = servers.reduce((a, m) => a + (m.tools.length || m.toolCount || 0), 0)
  const mcpUp = servers.filter((m) => m.up).length

  return (
    <EnginePane
      glyph="agent"
      eyebrow={
        <>
          <b>mcp</b>
          <span className="dim">·</span>
          <span className="meta">model context protocol</span>
          <span className="dim">·</span>
          <span className="mono" style={{ color: 'var(--fg-3)' }}>
            {servers.length} servers
          </span>
          <span className="grow" />
          <span className="meta">
            {host}:{port}/mcp/*
          </span>
        </>
      }
      title="MCP servers"
      sub="model context protocol · bundled"
      pill={{ tone: 'ok', text: mcpUp + ' up · ' + toolTotal + ' tools' }}
      headRight={
        servers.length > 0 ? (
          <CopyBtn text={buildClientConfig('claude', servers)} label="Copy config" icon="copy" />
        ) : null
      }
      strip={
        <>
          <span className="ss-summary">
            <b>{mcpUp}</b> servers up · {toolTotal} tools exposed
          </span>
          <span className="grow" />
          {servers.length > 0 && (
            <div className="mcp-links">
              <span className="lbl">add all to</span>
              <AddTo client="claude" servers={servers} />
              <AddTo client="codex" servers={servers} />
              <AddTo client="cursor" servers={servers} />
            </div>
          )}
        </>
      }
      foot={
        <>
          <span className="k">mount</span>
          <span className="v">/mcp/*</span>
          <span className="sep">·</span>
          <span className="k">servers</span>
          <span className="v">{servers.length}</span>
          <span className="sep">·</span>
          <span className="k">tools</span>
          <span className="v amber">{toolTotal}</span>
        </>
      }
    >
      <div className="mcplist">
        {mcpQuery.isPending && <div className="cn-empty mono">Loading MCP servers…</div>}
        {!mcpQuery.isPending && servers.length === 0 && (
          <div className="cn-empty mono">No MCP servers hosted.</div>
        )}
        {servers.map((m, i) => (
          <McpServerRow key={m.name} srv={m} defaultOpen={i === 0} />
        ))}
      </div>
    </EnginePane>
  )
}

// ─── the view (legacy #connections → redirected to #slots/endpoints) ──
// Kept as a composed fallback so any direct render still works; the route
// alias in main.jsx means users no longer land here.
function ConnectionsView() {
  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Network</span>
        <h1>Connections</h1>
      </div>
      <div className="conn">
        <LocalEndpointsPanel />
        <McpServersPanel />
      </div>
    </div>
  )
}

Object.assign(window, { ConnectionsView, LocalEndpointsPanel, McpServersPanel, EnginePane, EndpointRow, McpServerRow })
