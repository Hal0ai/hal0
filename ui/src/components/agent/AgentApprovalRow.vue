<script setup>
/**
 * AgentApprovalRow.vue — one pending-approval entry.
 *
 * Used by AgentInboxTab (full-width inline list) and by
 * AgentApprovalInbox modal. Same row, two surfaces — keeps the
 * decision UX identical regardless of where the operator landed.
 *
 * Per ADR-0004 §5: approve = amber primary action, deny = neutral
 * border (not destructive red — "deny" cancels, it does not delete).
 */
import { computed } from 'vue'
import { useAgentStore } from '../../stores/agent.js'
import { useToastsStore } from '../../stores/toasts.js'

const props = defineProps({
  entry: { type: Object, required: true },
  // When true, drops the surrounding row chrome so the modal can frame
  // it differently. Default false (inline list look).
  compact: { type: Boolean, default: false },
})

const agent = useAgentStore()
const toasts = useToastsStore()

// ── Display helpers ───────────────────────────────────────────────
function fmtAgo(epochSeconds) {
  if (!epochSeconds) return '—'
  const dt = Math.max(0, Date.now() / 1000 - epochSeconds)
  if (dt < 60) return `${Math.floor(dt)}s ago`
  if (dt < 3600) return `${Math.floor(dt / 60)}m ago`
  if (dt < 86400) return `${Math.floor(dt / 3600)}h ago`
  return `${Math.floor(dt / 86400)}d ago`
}

// The args dict can be anything — primary arg is the most action-
// distinguishing field (model id, slot name, etc.). Pick the first
// scalar value so the operator sees `model_pull: qwen3:0.6b` not
// `model_pull: [object Object]`.
const primaryArg = computed(() => {
  const a = props.entry?.args
  if (!a || typeof a !== 'object') return ''
  for (const v of Object.values(a)) {
    if (v == null) continue
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
      return String(v)
    }
  }
  return ''
})

const argsSummary = computed(() => {
  const a = props.entry?.args
  if (!a || typeof a !== 'object') return ''
  // Short form: key=value pairs joined by spaces, truncated.
  const parts = []
  for (const [k, v] of Object.entries(a)) {
    if (v == null) continue
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
      parts.push(`${k}=${v}`)
    } else {
      parts.push(`${k}=…`)
    }
    if (parts.join(' ').length > 80) break
  }
  return parts.join(' ')
})

// ── Action handlers ───────────────────────────────────────────────
async function onApprove() {
  try {
    await agent.approve(props.entry.id)
    toasts.info(`Approved: ${props.entry.tool}`)
  } catch (e) {
    toasts.error(e?.message || 'Approve failed')
  }
}

async function onDeny() {
  try {
    await agent.deny(props.entry.id)
    toasts.info(`Denied: ${props.entry.tool}`)
  } catch (e) {
    toasts.error(e?.message || 'Deny failed')
  }
}
</script>

<template>
  <div class="approval-row" :class="{ 'approval-row-compact': compact }" role="listitem">
    <div class="row-head">
      <span class="tool-name">{{ entry.tool }}</span>
      <span v-if="primaryArg" class="primary-arg">{{ primaryArg }}</span>
      <span class="ago" :title="new Date((entry.enqueued_at || 0) * 1000).toISOString()">{{ fmtAgo(entry.enqueued_at) }}</span>
    </div>
    <div class="row-meta">
      <span v-if="argsSummary" class="args">{{ argsSummary }}</span>
      <span class="client">client_id <span class="client-id">{{ entry.client_id || '—' }}</span></span>
      <span v-if="entry.hit_count > 1" class="hit-count" :title="`Hit ${entry.hit_count} times — agent retrying`">×{{ entry.hit_count }}</span>
    </div>
    <div class="row-actions">
      <button class="btn-approve" type="button" @click="onApprove">Approve</button>
      <button class="btn-deny" type="button" @click="onDeny">Deny</button>
    </div>
  </div>
</template>

<style scoped>
.approval-row {
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-rows: auto auto;
  gap: 6px 14px;
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface);
  transition: border-color 0.12s;
}
.approval-row:hover { border-color: var(--color-border-hi); }
.approval-row-compact { padding: 10px 12px; }

.row-head {
  grid-column: 1;
  grid-row: 1;
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  min-width: 0;
}
.tool-name {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 600;
  color: var(--color-fg);
  font-feature-settings: 'zero' 1, 'ss02' 1;
}
.primary-arg {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--hal0-accent);
  /* Light amber wash so the most-distinguishing field reads first. */
  padding: 1px 7px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--hal0-accent) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--hal0-accent) 30%, transparent);
}
.ago {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--color-fg-faint);
  margin-left: auto;
  font-feature-settings: 'zero' 1, 'tnum' 1;
}

.row-meta {
  grid-column: 1;
  grid-row: 2;
  display: flex;
  gap: 12px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-fg-faint);
  flex-wrap: wrap;
  min-width: 0;
}
.args { color: var(--color-fg-muted); }
.client-id { color: var(--color-fg-muted); }
.hit-count {
  color: var(--color-warning, #f5b049);
  font-weight: 600;
}

.row-actions {
  grid-column: 2;
  grid-row: 1 / span 2;
  display: flex;
  flex-direction: column;
  gap: 6px;
  justify-content: center;
  flex-shrink: 0;
}

.btn-approve {
  padding: 6px 16px;
  border-radius: var(--radius);
  background: var(--hal0-accent);
  color: #000;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
  border: none;
  cursor: pointer;
  transition: background 0.12s;
}
.btn-approve:hover { background: var(--hal0-accent-hover); }

.btn-deny {
  padding: 6px 16px;
  border-radius: var(--radius);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  border: 1px solid var(--color-border);
  cursor: pointer;
  transition: border-color 0.12s, color 0.12s;
}
.btn-deny:hover { border-color: var(--color-border-hi); color: var(--color-fg); }

/* Narrow screens: stack actions horizontally below the meta row. */
@media (max-width: 540px) {
  .approval-row {
    grid-template-columns: 1fr;
    grid-template-rows: auto auto auto;
  }
  .row-actions {
    grid-column: 1;
    grid-row: 3;
    flex-direction: row;
  }
  .btn-approve, .btn-deny { flex: 1; }
}
</style>
