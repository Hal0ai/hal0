<script setup>
/**
 * DownloadsPane.vue — right-bottom pane on the v2 Models view.
 *
 * Stacks DownloadRow children + empty-state + header summary.
 * The parent owns the download array + state mutations so this stays
 * presentational.
 */
import { computed } from 'vue'
import DownloadRow from './DownloadRow.vue'

const props = defineProps({
  downloads: { type: Array, default: () => [] },
})

const emit = defineEmits(['pause', 'resume', 'cancel', 'retry', 'remove'])

const active = computed(() =>
  props.downloads.filter((d) =>
    ['pulling', 'queued', 'paused', 'error', 'cancelled', 'verifying', 'completed'].includes(d.state)
  )
)

const inFlightCount = computed(() =>
  props.downloads.filter((d) => d.state === 'pulling' || d.state === 'queued').length
)
</script>

<template>
  <div class="mdl-dl" data-test="downloads-pane">
    <div class="mdl-dl-h">
      <span>Downloads</span>
      <span class="ct mono">{{ inFlightCount }}</span>
    </div>

    <div v-if="active.length === 0" class="dl-empty mono" data-test="dl-empty">
      <div class="primary">No active downloads.</div>
      <div class="sub">Add a model above.</div>
    </div>

    <DownloadRow
      v-for="d in active"
      :key="d.id"
      :dl="d"
      @pause="emit('pause', $event)"
      @resume="emit('resume', $event)"
      @cancel="emit('cancel', $event)"
      @retry="emit('retry', $event)"
      @remove="emit('remove', $event)"
    />
  </div>
</template>

<style scoped>
.mdl-dl {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--rad-lg);
  overflow: hidden;
}
.mdl-dl-h {
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--bg);
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: var(--jbm);
  font-size: 11px;
  color: var(--fg-3);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}
.mdl-dl-h .ct { color: var(--accent); margin-left: 4px; }

.dl-empty {
  padding: 32px 16px;
  text-align: center;
  color: var(--fg-4);
  font-size: 12px;
}
.dl-empty .primary { margin-bottom: 6px; color: var(--fg-3); }
.dl-empty .sub { font-size: 11px; color: var(--fg-5); }
</style>
