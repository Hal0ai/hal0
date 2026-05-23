<script setup>
/**
 * mcp/ConnectClientModal.vue — three-tab "how do I point a client at
 * this host" onboarding modal.
 *
 * Mirrors the React `ConnectClientModal` in
 *   /tmp/hal0-design-v3/dash/mcp-modals.jsx (lines 279–360).
 *
 * Tabs: Claude Code / Claude Desktop / Cursor. Each shows a short
 * explainer paragraph + a copy-pasteable snippet inside a `<pre>`
 * block. The "Copy snippet" button writes the currently-displayed
 * snippet to the clipboard.
 */
import { computed, ref } from 'vue'
import Modal from '../primitives/Modal.vue'
import { useToastStore } from '../../stores/toast.js'
import { MCP_HOST_BASE } from '../../stores/mcp.js'

const props = defineProps({
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const toasts = useToastStore()
const client = ref('claude-code')

const url = `${MCP_HOST_BASE}/mcp/hal0-admin`

const snippets = {
  'claude-code': {
    label: 'Claude Code',
    cmd: `claude mcp add hal0-admin --url "${url}"`,
    explainer: 'Run this in any shell — your Claude Code installation persists the server to its global MCP config.',
  },
  'claude-desktop': {
    label: 'Claude Desktop',
    cmd: `// In claude_desktop_config.json:
{
  "mcpServers": {
    "hal0-admin": {
      "url": "${url}"
    }
  }
}`,
    explainer: 'Add the entry to the mcpServers object of your Claude Desktop config and restart the app.',
  },
  cursor: {
    label: 'Cursor',
    cmd: `// In ~/.cursor/mcp.json:
{
  "mcpServers": {
    "hal0-admin": {
      "url": "${url}"
    }
  }
}`,
    explainer: 'Settings → MCP → Add server. Cursor will pick this up at next reload.',
  },
}

const cur = computed(() => snippets[client.value])

async function copy() {
  const text = cur.value.cmd
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement('textarea')
      ta.value = text
      document.body.appendChild(ta)
      ta.select()
      try { document.execCommand('copy') } catch {}
      document.body.removeChild(ta)
    }
  } catch {}
  toasts.push('Snippet copied', 'info')
}
</script>

<template>
  <Modal
    :open="open"
    :on-close="() => emit('close')"
    eyebrow="MCP · onboarding"
    title="Point a client at hal0"
    :width="620"
  >
    <div class="mcp-onboard-intro">
      hal0 is an MCP host — your local Claude or Cursor connects to it the same way it would to any other MCP server, by URL.
    </div>

    <div class="mcp-onboard-tabs">
      <button
        v-for="(s, k) in snippets"
        :key="k"
        type="button"
        :class="['mcp-onboard-tab', { on: client === k }]"
        :data-testid="`mcp-onboard-tab-${k}`"
        @click="client = k"
      >{{ s.label }}</button>
    </div>

    <div class="mcp-onboard-explain">{{ cur.explainer }}</div>
    <pre class="mcp-onboard-code mono" data-testid="mcp-onboard-snippet">{{ cur.cmd }}</pre>

    <div class="mcp-onboard-foot-row">
      <span class="mcp-onboard-hint mono">
        Once connected, the client appears in the Connected clients ribbon and the server rows below.
      </span>
      <span class="spacer" />
      <button type="button" class="mcp-onboard-copy" data-testid="mcp-onboard-copy" @click="copy">Copy snippet</button>
    </div>

    <template #foot>
      <span class="mcp-onboard-foot-note">
        All servers exposed by this host live under
        <span class="mono">{{ MCP_HOST_BASE }}/mcp/&lt;name&gt;</span>
      </span>
      <button type="button" class="mcp-onboard-close" @click="emit('close')">Close</button>
    </template>
  </Modal>
</template>

<style scoped>
.spacer { flex: 1; }

.mcp-onboard-intro {
  font-size: 13px;
  color: var(--fg-2);
  line-height: 1.6;
  margin-bottom: 16px;
}

.mcp-onboard-tabs {
  display: flex;
  border-bottom: 1px solid var(--line);
  margin-bottom: 14px;
}
.mcp-onboard-tab {
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  padding: 8px 14px;
  font-family: var(--hal0-font-mono);
  font-size: 12px;
  color: var(--fg-3);
  cursor: pointer;
}
.mcp-onboard-tab.on { color: var(--accent); border-bottom-color: var(--accent); }

.mcp-onboard-explain {
  font-size: 12.5px;
  color: var(--fg-2);
  line-height: 1.55;
  margin-bottom: 10px;
}
.mcp-onboard-code {
  background: #070707;
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  padding: 14px 16px;
  font-size: 12px;
  color: var(--fg-2);
  margin: 0;
  white-space: pre;
  overflow-x: auto;
  font-family: var(--hal0-font-mono);
}

.mcp-onboard-foot-row {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.mcp-onboard-hint {
  font-size: 11px;
  color: var(--fg-4);
}
.mcp-onboard-copy {
  background: transparent;
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  color: var(--fg-3);
  font-family: var(--hal0-font-mono);
  font-size: 11px;
  padding: 4px 10px;
  cursor: pointer;
}
.mcp-onboard-copy:hover { color: var(--accent); border-color: var(--accent-line); }

.mcp-onboard-foot-note { color: var(--fg-4); }
.mcp-onboard-close {
  background: transparent;
  border: 1px solid var(--line);
  border-radius: var(--rad-sm);
  color: var(--fg-3);
  font-family: var(--hal0-font-mono);
  font-size: 11px;
  padding: 4px 10px;
  cursor: pointer;
}
.mcp-onboard-close:hover { color: var(--fg); border-color: var(--line-strong); }
</style>
