<script setup>
/**
 * BackendRow.vue — single row in the backends table.
 *
 * Mirrors the per-row markup in `BackendsView` in
 *   /tmp/hal0-design/hal0-v2/project/dash/extras.jsx (lines 153–187).
 *
 * The parent owns modal open/close — this row emits intents.
 */
import { computed } from 'vue'

const props = defineProps({
  backend: { type: Object, required: true },
})
const emit = defineEmits(['install', 'uninstall', 'reinstall'])

const state = computed(() => props.backend.state || 'unavailable')
const isInstalled = computed(() => state.value === 'installed')
const isInstalling = computed(() => state.value === 'installing')
const isUninstalling = computed(() => state.value === 'uninstalling')
const isUnavailable = computed(() => state.value === 'unavailable')
const isError = computed(() => state.value === 'error')

const usedBy = computed(() => Array.isArray(props.backend.usedBy) ? props.backend.usedBy : [])
</script>

<template>
  <div
    class="row"
    :class="{ dim: isUnavailable }"
    :data-testid="`backend-row-${backend.id}`"
  >
    <span class="name">
      <span class="name-id">{{ backend.id }}</span>
      <span v-if="backend.recommended" class="chip chip-amber">★ recommended</span>
    </span>
    <span class="ver">
      {{ backend.version || '—' }}
      <span v-if="backend.note" class="note">· {{ backend.note }}</span>
    </span>
    <span class="state">
      <span v-if="isInstalled" class="chip chip-ok">installed</span>
      <span v-else-if="isInstalling" class="chip chip-warn">installing…</span>
      <span v-else-if="isUninstalling" class="chip chip-warn">uninstalling…</span>
      <span v-else-if="isError" class="chip chip-err">error</span>
      <span v-else-if="isUnavailable" class="chip">not avail on platform</span>
      <span v-else class="chip">not installed</span>
    </span>
    <span class="used">
      <template v-if="usedBy.length > 0">
        <span v-for="s in usedBy" :key="s" class="usedchip mono">{{ s }}</span>
      </template>
      <span v-else class="dim">—</span>
    </span>
    <span class="actions">
      <template v-if="isInstalled">
        <button
          class="btn-ghost sm"
          type="button"
          :data-testid="`backend-reinstall-${backend.id}`"
          @click="emit('reinstall', backend)"
        >Reinstall</button>
        <button
          class="btn-ghost sm"
          type="button"
          :data-testid="`backend-uninstall-${backend.id}`"
          @click="emit('uninstall', backend)"
        >Uninstall</button>
      </template>
      <template v-else-if="isUnavailable">
        <button class="btn-ghost sm" type="button" disabled>Install</button>
      </template>
      <template v-else>
        <button
          class="btn-ghost sm"
          type="button"
          :data-testid="`backend-install-${backend.id}`"
          @click="emit('install', backend)"
        >Install</button>
      </template>
    </span>
  </div>
</template>

<style scoped>
.row {
  padding: 12px 18px;
  border-bottom: 1px solid var(--color-border);
  display: grid;
  grid-template-columns: 1fr 200px 160px 1fr auto;
  gap: 16px;
  align-items: center;
  font-family: var(--font-mono);
  font-size: 12px;
}
.row.dim { opacity: 0.55; }
.row:last-child { border-bottom: none; }

.name { display: flex; align-items: center; gap: 8px; color: var(--color-fg); font-weight: 500; }
.name-id { font-family: var(--font-mono); }
.ver { color: var(--color-fg-muted); }
.note { color: var(--color-fg-faint); margin-left: 4px; }
.used { display: flex; flex-wrap: wrap; gap: 4px; font-size: 11px; color: var(--color-fg-muted); }
.usedchip {
  padding: 1px 6px;
  border: 1px solid var(--color-border);
  border-radius: 3px;
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-size: 10.5px;
}
.dim { color: var(--color-fg-faint); }

.chip {
  font-family: var(--font-mono);
  font-size: 10.5px;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  background: var(--color-surface-2);
  white-space: nowrap;
}
.chip-ok {
  color: var(--color-success);
  border-color: color-mix(in srgb, var(--color-success) 30%, transparent);
  background: color-mix(in srgb, var(--color-success) 8%, transparent);
}
.chip-warn {
  color: var(--color-warning);
  border-color: color-mix(in srgb, var(--color-warning) 30%, transparent);
  background: color-mix(in srgb, var(--color-warning) 8%, transparent);
}
.chip-err {
  color: var(--color-danger);
  border-color: color-mix(in srgb, var(--color-danger) 30%, transparent);
  background: color-mix(in srgb, var(--color-danger) 8%, transparent);
}
.chip-amber {
  color: var(--hal0-accent);
  border-color: color-mix(in srgb, var(--hal0-accent) 35%, transparent);
  background: color-mix(in srgb, var(--hal0-accent) 12%, transparent);
}

.actions { display: flex; gap: 4px; justify-content: flex-end; }
.btn-ghost {
  padding: 4px 10px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 11.5px;
  cursor: pointer;
}
.btn-ghost:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }
.btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
