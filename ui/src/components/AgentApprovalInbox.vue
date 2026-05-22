<script setup>
/**
 * AgentApprovalInbox.vue — header-bell modal version of the inbox.
 *
 * Mirror of AgentInboxTab but framed as a dialog. Reuses
 * AgentApprovalRow so the row UX is identical to the /agent inbox tab.
 * Pinia store is the shared source — no separate fetch, no second SSE.
 *
 * Mounted lazily (v-if from AgentApprovalBell) so the dialog DOM only
 * exists when the bell is open.
 */
import { onMounted, onBeforeUnmount } from 'vue'
import { useAgentStore } from '../stores/agent.js'
import AgentApprovalRow from './agent/AgentApprovalRow.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const agent = useAgentStore()

function onKey(e) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

async function onClearAll() {
  if (!agent.pendingCount) return
  if (!confirm(`Deny all ${agent.pendingCount} pending approvals?`)) return
  await agent.clearAll()
}
</script>

<template>
  <div class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="approval-inbox-title" @click.self="emit('close')">
    <div class="modal-card">
      <header class="modal-head">
        <div class="modal-l">
          <h2 id="approval-inbox-title" class="modal-title">Pending approvals</h2>
          <p class="modal-sub">
            {{ agent.pendingCount }} waiting · canonical surface per ADR-0004 §5.
          </p>
        </div>
        <div class="modal-r">
          <button
            v-if="agent.pendingCount > 0"
            class="btn-ghost"
            type="button"
            @click="onClearAll"
          >Clear all</button>
          <router-link
            class="btn-ghost"
            :to="{ path: '/agent', query: { tab: 'inbox' } }"
            @click="emit('close')"
          >Open inbox tab →</router-link>
          <button class="modal-close" type="button" aria-label="Close" @click="emit('close')">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
      </header>

      <div class="modal-body">
        <p v-if="agent.pendingCount === 0" class="empty-msg">No pending approvals.</p>
        <div v-else class="rows" role="list" aria-label="Pending approvals">
          <AgentApprovalRow
            v-for="entry in agent.pending"
            :key="entry.id"
            :entry="entry"
            :compact="true"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 60px 16px 16px;
  z-index: 100;
}

.modal-card {
  width: min(600px, 100%);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.45);
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 80px);
  overflow: hidden;
}

.modal-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 16px 18px;
  border-bottom: 1px solid var(--color-border);
  gap: 12px;
}
.modal-l { flex: 1; min-width: 0; }
.modal-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0 0 4px;
  letter-spacing: -0.01em;
}
.modal-sub {
  font-size: 11.5px;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  margin: 0;
}

.modal-r { display: flex; gap: 8px; align-items: center; }
.btn-ghost {
  padding: 5px 12px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 11.5px;
  cursor: pointer;
  text-decoration: none;
}
.btn-ghost:hover { border-color: var(--color-border-hi); color: var(--color-fg); }
.modal-close {
  width: 28px;
  height: 28px;
  border-radius: var(--radius);
  background: transparent;
  border: none;
  color: var(--color-fg-faint);
  display: grid;
  place-items: center;
  cursor: pointer;
}
.modal-close:hover { color: var(--color-fg); background: var(--color-surface-2); }

.modal-body {
  padding: 14px 18px 18px;
  overflow-y: auto;
}
.rows { display: flex; flex-direction: column; gap: 6px; }
.empty-msg { font-size: 13px; color: var(--color-fg-muted); margin: 0; }
</style>
