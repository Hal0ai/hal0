<script setup>
/**
 * AgentChatTab.vue — read-only PTY-tap transcript view (CLI shape only).
 *
 * Per ADR-0004 + the Wave 2 brief's "Chat surface caveat":
 *   - this is NOT a chat UI connected to the primary slot,
 *   - this is NOT an input/send surface,
 *   - this is a read-only display of the pi shell session transcript.
 *
 * Input/send capability is a separate ADR if ever pursued. v0.2 just
 * surfaces the tap so an operator can see what the bundled CLI agent is
 * doing without ssh'ing to the box.
 *
 * v0.2 backend status: the PTY-tap endpoint is not landed yet. Until
 * then this tab renders a clear "transcript not available" surface so
 * the tab still answers "what would I see here" rather than 404'ing.
 */
import { ref, onMounted } from 'vue'
import Card from '../Card.vue'

const lines = ref([])
const connected = ref(false)
const errorMsg = ref(null)

onMounted(() => {
  // The PTY-tap stream endpoint lands in a follow-up; until then the
  // tab is "informational empty state". We attempt the connection so a
  // future backend ship lights it up automatically without a UI redeploy.
  try {
    const es = new EventSource('/api/agents/pi-coder/transcript')
    es.onopen = () => { connected.value = true }
    es.onmessage = (evt) => {
      lines.value.push({ ts: Date.now() / 1000, text: String(evt.data || '') })
      if (lines.value.length > 2000) lines.value = lines.value.slice(-1500)
    }
    es.onerror = () => {
      es.close()
      connected.value = false
      errorMsg.value = 'Transcript stream unavailable — backend tap not yet wired.'
    }
  } catch (e) {
    errorMsg.value = e?.message || 'EventSource construction failed.'
  }
})
</script>

<template>
  <div class="chat">
    <div class="chat-head">
      <h2 class="chat-title">Transcript</h2>
      <p class="chat-sub">
        Read-only view of the pi shell session — what the CLI agent
        sees on its own terminal. This tab is not a chat surface.
        Sending input is out of scope for v0.2 (ADR-0004).
      </p>
    </div>

    <Card v-if="lines.length === 0" class="empty-card">
      <p class="empty-msg">
        <template v-if="errorMsg">{{ errorMsg }}</template>
        <template v-else-if="connected">No output yet — the agent is idle.</template>
        <template v-else>Connecting to transcript stream…</template>
      </p>
      <p class="empty-hint">
        Run <code>pi</code> in a terminal on the hal0 box to see output here.
        Operators with shell access can also tail directly with
        <code>journalctl --user -u pi-coder.service -f</code>.
      </p>
    </Card>

    <Card v-else :padded="false">
      <pre class="transcript" aria-label="pi-coder transcript"><span
          v-for="(line, i) in lines"
          :key="i"
          class="line"
        >{{ line.text }}
</span></pre>
    </Card>
  </div>
</template>

<style scoped>
.chat { display: flex; flex-direction: column; gap: 14px; }

.chat-head { display: flex; flex-direction: column; gap: 4px; }
.chat-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0;
  letter-spacing: -0.01em;
}
.chat-sub {
  font-size: 12.5px;
  color: var(--color-fg-muted);
  margin: 0;
  line-height: 1.55;
  max-width: 65ch;
}

.empty-card { padding: 24px; display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.empty-msg { font-size: 13px; color: var(--color-fg); margin: 0; }
.empty-hint {
  font-size: 12px;
  color: var(--color-fg-muted);
  margin: 0;
  line-height: 1.55;
}
.empty-hint code {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 5px;
  background: var(--color-surface-2);
  border-radius: 3px;
  color: var(--hal0-accent);
}

.transcript {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--color-fg);
  background: var(--hal0-bg-sunken);
  padding: 12px 14px;
  margin: 0;
  max-height: 540px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.line { display: block; }
</style>
