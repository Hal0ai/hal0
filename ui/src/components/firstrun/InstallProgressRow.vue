<script setup>
/**
 * InstallProgressRow — single per-model download row for the progress
 * state. Inline error form exposes [Retry] + [Skip this model].
 *
 * Mirrors the per-row markup inside `<FirstRunProgress>` in
 *   /tmp/hal0-design/hal0-v2/project/dash/firstrun.jsx (lines 246-263)
 * with the inline error treatment added (brief slice §State 3).
 */
defineProps({
  item: { type: Object, required: true },
})

const emit = defineEmits(['retry', 'skip'])
</script>

<template>
  <div
    class="dl-row"
    :class="{
      'dl-row-done':    item.state === 'done',
      'dl-row-err':     item.state === 'failed',
      'dl-row-paused':  item.state === 'paused',
    }"
    :data-row-key="item.key"
    :data-row-state="item.state"
  >
    <div class="dl-name mono">
      {{ item.slot }} · {{ item.model }}
      <span class="sub">
        {{ item.size }}
        <template v-if="item.state === 'pulling' && item.rate">· {{ item.rate }} · {{ item.eta }} remaining</template>
      </span>
    </div>
    <div class="dl-bar">
      <i
        :class="{ ok: item.state === 'done' }"
        :style="{ width: `${item.pct}%` }"
      />
    </div>
    <div
      class="dl-pct mono"
      :class="{
        ok:  item.state === 'done',
        dim: item.state === 'queued' || item.state === 'paused',
        err: item.state === 'failed',
      }"
    >
      <template v-if="item.state === 'done'">✓ 100%</template>
      <template v-else-if="item.state === 'queued'">queued</template>
      <template v-else-if="item.state === 'failed'">✗ failed</template>
      <template v-else-if="item.state === 'paused'">paused</template>
      <template v-else>{{ item.pct }}%</template>
    </div>
    <div class="dl-state mono">
      <template v-if="item.state === 'pulling'">pulling</template>
      <template v-else-if="item.state === 'queued'">waiting</template>
      <template v-else-if="item.state === 'verifying'">verifying</template>
      <template v-else-if="item.state === 'done'">complete</template>
      <template v-else-if="item.state === 'paused'">paused</template>
      <template v-else-if="item.state === 'failed'">error</template>
    </div>

    <!-- Inline error row — spans the grid -->
    <div v-if="item.state === 'failed'" class="dl-err">
      <span class="dl-err-msg mono">✗ failed · error: {{ item.error || 'unknown' }}</span>
      <span class="dl-err-actions">
        <button
          type="button"
          class="btn sm"
          :data-row-action="`retry:${item.key}`"
          @click="emit('retry', item.key)"
        >Retry</button>
        <button
          type="button"
          class="btn ghost sm"
          :data-row-action="`skip:${item.key}`"
          @click="emit('skip', item.key)"
        >Skip this model</button>
      </span>
    </div>
  </div>
</template>

<style scoped>
.dl-row {
  display: grid;
  grid-template-columns: 1fr 220px 110px 90px;
  gap: 16px;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid var(--line-soft);
}
.dl-row:last-child { border-bottom: none; }
.dl-row-err { background: color-mix(in srgb, var(--err-soft) 60%, transparent); }
.dl-row-paused { opacity: 0.7; }

.dl-name { font-family: var(--jbm); font-size: 12.5px; color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dl-name .sub { color: var(--fg-4); font-size: 11px; display: block; margin-top: 2px; }

.dl-bar {
  position: relative;
  height: 6px;
  background: var(--bg-3);
  border-radius: 1px;
  overflow: hidden;
}
.dl-bar i { display: block; height: 100%; background: var(--accent); border-radius: 1px; transition: width 0.3s ease; }
.dl-bar i.ok { background: var(--ok); }

.dl-pct { font-family: var(--jbm); font-size: 12px; color: var(--fg); text-align: right; }
.dl-pct.dim { color: var(--fg-4); }
.dl-pct.ok  { color: var(--ok); }
.dl-pct.err { color: var(--err); }
.dl-state   { font-family: var(--jbm); font-size: 11px; color: var(--fg-3); text-transform: lowercase; }

.dl-err {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
  padding: 8px 12px;
  border: 1px solid var(--err-line);
  border-radius: var(--rad-sm);
  background: var(--err-soft);
}
.dl-err-msg { color: var(--err); font-size: 11.5px; }
.dl-err-actions { display: inline-flex; gap: 8px; }
</style>
