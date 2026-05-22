<script setup>
/**
 * AgentInboxTab.vue — full-page approval inbox (variant B style).
 *
 * Roomy inline list. The header bell modal mounts the same rows for the
 * "wherever I am in the dashboard" surface; this tab is the deliberate
 * focus-mode home. Both read from the same Pinia store + share one SSE.
 */
import { useAgentStore } from '../../stores/agent.js'
import Card from '../Card.vue'
import AgentApprovalRow from './AgentApprovalRow.vue'

const agent = useAgentStore()

async function onClearAll() {
  if (!agent.pendingCount) return
  if (!confirm(`Deny all ${agent.pendingCount} pending approvals?`)) return
  await agent.clearAll()
}
</script>

<template>
  <div class="inbox">
    <div class="inbox-head">
      <div class="inbox-l">
        <h2 class="inbox-title">Inbox</h2>
        <p class="inbox-sub">
          Gated tool calls awaiting your decision. The bell in the header is
          canonical — this tab is the focus-mode view of the same queue.
        </p>
      </div>
      <button
        v-if="agent.pendingCount > 0"
        class="btn-ghost"
        type="button"
        @click="onClearAll"
      >Clear all ({{ agent.pendingCount }})</button>
    </div>

    <Card v-if="agent.pendingCount === 0" class="empty-card" :padded="true">
      <div class="empty-inner">
        <span class="empty-dot" aria-hidden="true" />
        <p class="empty-msg">No pending approvals.</p>
        <p class="empty-hint">
          Approvals show up here when a bundled agent calls a gated MCP tool
          (e.g. <code>model_pull</code>, <code>slot_delete</code>). Per
          ADR-0004 §5 the queue never auto-expires — entries sit here until
          you decide.
        </p>
      </div>
    </Card>

    <div v-else class="rows" role="list" aria-label="Pending approvals">
      <AgentApprovalRow
        v-for="entry in agent.pending"
        :key="entry.id"
        :entry="entry"
      />
    </div>
  </div>
</template>

<style scoped>
.inbox { display: flex; flex-direction: column; gap: 14px; }

.inbox-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.inbox-l { flex: 1; min-width: 0; }
.inbox-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0 0 4px;
  letter-spacing: -0.01em;
}
.inbox-sub {
  font-size: 12.5px;
  color: var(--color-fg-muted);
  margin: 0;
  line-height: 1.55;
  max-width: 65ch;
}

.btn-ghost {
  padding: 7px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
}
.btn-ghost:hover { border-color: var(--color-border-hi); color: var(--color-fg); }

.rows { display: flex; flex-direction: column; gap: 8px; }

.empty-card { padding: 28px 24px; }
.empty-inner {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}
.empty-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-fg-faint);
}
.empty-msg {
  font-size: 13.5px;
  color: var(--color-fg);
  margin: 0;
  font-weight: 500;
}
.empty-hint {
  font-size: 12px;
  color: var(--color-fg-muted);
  margin: 0;
  line-height: 1.55;
  max-width: 60ch;
}
.empty-hint code {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 5px;
  background: var(--color-surface-2);
  border-radius: 3px;
  color: var(--hal0-accent);
}
</style>
