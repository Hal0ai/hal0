<script setup>
/**
 * mcp/NoClientsState.vue — empty state shown in lieu of ClientsRibbon
 * when no MCP clients have connected to this hal0 host yet.
 *
 * Mirrors `NoClientsState` in
 *   /tmp/hal0-design-v3/dash/mcp.jsx (lines 542–562).
 * Pure dashed-border monospace illustration — no cartoon art — and
 * a single CTA that bubbles `teach` to the parent so it can open the
 * ConnectClientModal.
 */
defineEmits(['teach'])
</script>

<template>
  <div class="mcp-clients no-clients" data-testid="mcp-no-clients">
    <div class="mcp-clients-h">
      <span class="mono">Connected clients<span class="ct">· 0</span></span>
    </div>
    <div class="mcp-empty-clients">
      <div class="mcp-empty-illo mono">
        <span class="fade">client</span>
        <span class="dash">– – – –</span>
        <span class="brand">hal0</span>
      </div>
      <div class="mcp-empty-body">
        <div class="mcp-empty-title mono">No MCP clients have connected to this host yet.</div>
        <div class="mcp-empty-sub">
          Point Claude Code, Claude Desktop, or Cursor at one of the running servers below.
          The connect URL is on each row.
        </div>
        <button
          type="button"
          class="mcp-show-me"
          data-testid="mcp-no-clients-cta"
          @click="$emit('teach')"
        >Show me how →</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mcp-clients {
  border: 1px solid var(--line);
  border-radius: var(--rad-lg);
  background: var(--bg-1);
  margin-bottom: 16px;
  overflow: hidden;
}
.mcp-clients-h {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line-soft);
  font-family: var(--hal0-font-mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fg-3);
}
.mcp-clients-h .ct { color: var(--fg-5); margin-left: 6px; }

.mcp-empty-clients {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 24px;
  padding: 28px 24px;
  align-items: center;
}
.mcp-empty-illo {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  padding: 18px 22px;
  border: 1px dashed var(--line-strong);
  border-radius: var(--rad);
  background: var(--bg);
  letter-spacing: 0.04em;
}
.mcp-empty-illo .fade  { color: var(--fg-4); }
.mcp-empty-illo .dash  { color: var(--fg-5); }
.mcp-empty-illo .brand { color: var(--accent); }

.mcp-empty-title {
  font-size: 14px;
  color: var(--fg);
  margin-bottom: 4px;
}
.mcp-empty-sub {
  font-size: 13px;
  color: var(--fg-2);
  margin-bottom: 12px;
  max-width: 480px;
  line-height: 1.55;
}
.mcp-show-me {
  background: var(--accent);
  color: #0a0a0a;
  border: 1px solid var(--accent);
  border-radius: var(--rad-sm);
  padding: 6px 12px;
  font-family: var(--hal0-font-mono);
  font-size: 12px;
  cursor: pointer;
}
.mcp-show-me:hover { filter: brightness(1.06); }
</style>
