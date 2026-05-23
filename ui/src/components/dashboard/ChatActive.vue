<script setup>
/**
 * dashboard/ChatActive.vue — slice #169.
 *
 * Renders an active conversation. Messages: user / assistant rows.
 * Assistant rows can contain inline tool-call blocks via native
 * <details> markup (slice #175 owns full a11y polish — this just
 * lays down the collapsible scaffold).
 *
 * Message shape (from Dashboard):
 *   {
 *     id, role: 'user' | 'assistant' | 'system',
 *     content?: string,
 *     reasoning?: string,
 *     tool_calls?: [{ id, name, args, result?, duration_ms? }],
 *     attachments?: [{ kind: 'image'|'audio'|'text', src?, text? }],
 *     swap?: { from, to }                       // persona-swap marker
 *   }
 *
 * Auto-scrolls to bottom when new messages arrive.
 */
import { ref, watch, nextTick } from 'vue'

const props = defineProps({
  messages: { type: Array, required: true },
})

const bodyEl = ref(null)

watch(
  () => props.messages.length,
  async () => {
    await nextTick()
    if (bodyEl.value) bodyEl.value.scrollTop = bodyEl.value.scrollHeight
  },
)

function shortArgs(args) {
  if (!args) return ''
  let s = typeof args === 'string' ? args : JSON.stringify(args)
  if (s.length > 60) s = s.slice(0, 57) + '…'
  return s
}

function prettyArgs(args) {
  if (!args) return ''
  return typeof args === 'string' ? args : JSON.stringify(args, null, 2)
}
</script>

<template>
  <div ref="bodyEl" class="chat-body" data-testid="chat-active" role="log" aria-live="polite">
    <template v-for="msg in messages" :key="msg.id">
      <!-- Persona-swap divider -->
      <div
        v-if="msg.swap"
        class="swap-line"
        data-testid="chat-swap-line"
        role="separator"
      >
        Persona swap · <b>{{ msg.swap.from }}</b> → <b>{{ msg.swap.to }}</b>
      </div>

      <!-- User message -->
      <div v-else-if="msg.role === 'user'" class="msg user" :data-testid="`msg-user-${msg.id}`">
        <div class="bubble">{{ msg.content }}</div>
        <div class="meta">you</div>
      </div>

      <!-- Assistant message -->
      <div v-else class="msg assistant" :data-testid="`msg-assistant-${msg.id}`">
        <div v-if="msg.reasoning" class="reasoning">
          <details>
            <summary>thinking…</summary>
            <pre>{{ msg.reasoning }}</pre>
          </details>
        </div>
        <div v-if="msg.content" class="bubble">{{ msg.content }}</div>
        <div class="meta"><b>{{ msg.persona || 'assistant' }}</b></div>

        <!-- Inline tool calls -->
        <details
          v-for="tc in msg.tool_calls || []"
          :key="tc.id"
          class="toolblock"
          :data-testid="`tool-call-${tc.id}`"
        >
          <summary class="tb-h">
            <span>tool call</span>
            <span class="arr">→</span>
            <b>{{ tc.name }}</b>
            <span class="right">{{ shortArgs(tc.args) }}</span>
          </summary>
          <div class="tb-body">
            <div class="kv">
              <span class="k">args</span>
              <pre class="v">{{ prettyArgs(tc.args) }}</pre>
            </div>
            <div v-if="tc.result != null" class="kv">
              <span class="k">result</span>
              <pre class="v">{{ typeof tc.result === 'string' ? tc.result : JSON.stringify(tc.result, null, 2) }}</pre>
            </div>
          </div>
          <div v-if="tc.duration_ms != null" class="tb-foot">
            <span><b>{{ (tc.duration_ms / 1000).toFixed(2) }}s</b> · {{ tc.bytes || 0 }} bytes</span>
          </div>
        </details>

        <!-- Attachments -->
        <div
          v-for="(att, i) in msg.attachments || []"
          :key="i"
          class="attach"
          :data-testid="`attach-${msg.id}-${i}`"
        >
          <div v-if="att.kind === 'image'" class="img-ph" :title="att.src">image · {{ att.src || 'preview' }}</div>
          <audio v-else-if="att.kind === 'audio'" controls :src="att.src" />
          <pre v-else class="att-text">{{ att.text }}</pre>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.chat-body {
  flex: 1;
  overflow-y: auto;
  padding: 22px 22px 14px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.msg { display: flex; flex-direction: column; gap: 4px; max-width: 80%; }
.msg.user { align-self: flex-end; }
.msg.user .bubble {
  background: var(--hal0-accent, var(--accent, #feaf00));
  color: #0a0a0a;
  border-color: var(--hal0-accent, var(--accent, #feaf00));
}
.msg .bubble {
  padding: 10px 14px;
  border: 1px solid var(--color-border, var(--line, #2a2a2a));
  background: var(--color-surface-2, var(--bg-2, #181818));
  border-radius: 6px;
  font-size: 13.5px;
  line-height: 1.5;
  color: var(--color-fg, var(--fg, #e5e5e5));
  white-space: pre-wrap;
  word-break: break-word;
}
.msg .meta {
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 10px;
  color: var(--color-fg-faint, var(--fg-4, #777));
  letter-spacing: 0.04em;
}
.msg .meta b { color: var(--hal0-accent, var(--accent, #feaf00)); font-weight: 500; }
.msg.user .meta { text-align: right; }

.reasoning { font-family: var(--font-mono, var(--jbm, monospace)); font-size: 11px; }
.reasoning summary { color: var(--color-fg-faint, var(--fg-4, #777)); cursor: pointer; }
.reasoning pre {
  background: var(--color-surface, var(--bg, #0a0a0a));
  border-left: 2px solid var(--color-border, #2a2a2a);
  padding: 8px 10px;
  color: var(--color-fg-faint, var(--fg-4, #777));
  margin: 6px 0 0;
  white-space: pre-wrap;
}

.toolblock {
  align-self: stretch;
  margin: 0 6%;
  background: var(--color-surface, var(--bg, #0a0a0a));
  border: 1px solid var(--color-border, var(--line, #2a2a2a));
  border-left: 2px solid var(--hal0-accent, var(--accent, #feaf00));
  border-radius: 6px;
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 11.5px;
  overflow: hidden;
}
.toolblock summary.tb-h {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  color: var(--color-fg-muted, var(--fg-3, #888));
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  cursor: pointer;
  list-style: none;
  user-select: none;
}
.toolblock summary.tb-h::-webkit-details-marker { display: none; }
.toolblock summary.tb-h::before {
  content: "▸";
  font-size: 10px;
  color: var(--color-fg-faint, var(--fg-5, #555));
  transition: transform 0.15s;
}
.toolblock[open] summary.tb-h::before { transform: rotate(90deg); }
.toolblock summary.tb-h b { color: var(--hal0-accent, var(--accent, #feaf00)); font-weight: 500; }
.toolblock summary.tb-h .arr { color: var(--color-fg-faint, var(--fg-5, #555)); }
.toolblock summary.tb-h .right {
  margin-left: auto;
  color: var(--color-fg-faint, var(--fg-4, #777));
  text-transform: none;
  letter-spacing: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 50%;
}
.toolblock .tb-body { padding: 10px 12px; color: var(--color-fg-muted, var(--fg-2, #bbb)); border-top: 1px solid var(--color-border, var(--line-soft, #1d1d1d)); }
.toolblock .tb-body .kv { display: grid; grid-template-columns: 80px 1fr; gap: 4px 14px; font-size: 11px; margin-bottom: 8px; }
.toolblock .tb-body .kv:last-child { margin-bottom: 0; }
.toolblock .tb-body .kv .k { color: var(--color-fg-faint, var(--fg-4, #777)); }
.toolblock .tb-body .kv .v {
  color: var(--color-fg-muted, var(--fg-2, #bbb));
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}
.toolblock .tb-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-top: 1px solid var(--color-border, var(--line-soft, #1d1d1d));
  color: var(--color-fg-faint, var(--fg-4, #777));
  font-size: 10px;
}
.toolblock .tb-foot b { color: var(--color-success, var(--ok, #22c55e)); font-weight: 500; }

.attach {
  align-self: stretch;
  margin: 0 6%;
  border: 1px solid var(--color-border, var(--line, #2a2a2a));
  border-radius: 6px;
  overflow: hidden;
  max-width: 320px;
  background: var(--color-surface-2, var(--bg-2, #181818));
}
.attach .img-ph {
  height: 180px;
  background:
    repeating-linear-gradient(45deg, rgba(255, 176, 0, 0.06) 0 12px, transparent 12px 24px),
    linear-gradient(135deg, #1a1610 0%, #0a0a0a 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 10px;
  color: var(--color-fg-faint, var(--fg-4, #777));
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.attach .att-text {
  margin: 0;
  padding: 10px 12px;
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 12px;
  color: var(--color-fg-muted, var(--fg-2, #bbb));
  white-space: pre-wrap;
}

.swap-line {
  align-self: stretch;
  margin: 0 6%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 10.5px;
  color: var(--color-fg-faint, var(--fg-4, #777));
  letter-spacing: 0.02em;
}
.swap-line::before, .swap-line::after {
  content: "";
  flex: 1;
  height: 1px;
  background: var(--color-border, var(--line-soft, #1d1d1d));
}
.swap-line b { color: var(--hal0-accent, var(--accent, #feaf00)); font-weight: 500; }
</style>
