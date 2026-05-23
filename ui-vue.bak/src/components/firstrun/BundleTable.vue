<script setup>
/**
 * BundleTable — capability-matrix layout (tweak variant of BundleGrid).
 *
 * Mirrors `<BundleTable>` in
 *   /tmp/hal0-design/hal0-v2/project/dash/firstrun.jsx (lines 104-157).
 *
 * Switched on via useTweaksStore.firstrunLayout === 'wizard' (the
 * existing enum keeps `tiers | wizard` — `wizard` reads as "matrix"
 * here so we don't break stored localStorage prefs).
 */
defineProps({
  bundles: { type: Array, required: true },
  ramGb:   { type: Number, default: 0 },
})

const emit = defineEmits(['pick'])
function onPick(b) {
  if (b._state === 'unfit') return
  emit('pick', b.id)
}

const ROWS = [
  {
    id: 'chat', label: 'chat',
    each: (b) => (
      b.id === 'lite'    ? '1.2B' :
      b.id === 'default' ? '9B' :
      b.id === 'pro'     ? '27B + 30B coder' :
                           '35B + 30B coder + NPU 1B'
    ),
  },
  {
    id: 'embed', label: 'embed + rerank',
    each: (b) => (
      b.id === 'lite'    ? '—' :
      b.id === 'default' ? 'nomic-v1.5' :
      b.id === 'pro'     ? 'nomic + bge-rerank' :
                           'nomic + bge-rerank + embed-gemma'
    ),
  },
  {
    id: 'voice', label: 'voice (stt+tts)',
    each: (b) => (
      b.id === 'lite'    ? '—' :
      b.id === 'default' ? 'whisper-base + kokoro' :
      b.id === 'pro'     ? 'whisper-large + kokoro' :
                           'whisper-large + kokoro + npu-stt'
    ),
  },
  {
    id: 'image', label: 'image',
    each: (b) => (
      b.id === 'pro' ? 'sd-turbo' :
      b.id === 'max' ? 'flux-2-klein-9b' :
                       '—'
    ),
  },
  {
    id: 'npu', label: 'NPU trio',
    each: (b) => (b.id === 'max' ? 'agent + stt-npu + embed-npu' : '—'),
  },
]
</script>

<template>
  <div class="bt-card" data-firstrun-layout="matrix">
    <!-- Tier-header row -->
    <div class="bt-grid bt-header">
      <div class="bt-cap-head mono">capability</div>
      <div
        v-for="b in bundles"
        :key="b.id"
        class="bt-tier-head"
        :class="{ unfit: !b._fits, recommended: b._recommended }"
      >
        <div class="bt-tier-name mono">{{ b.name }}</div>
        <div class="bt-tier-spec mono">{{ b.ram }} GB+ · ~{{ b.sizeGB }} GB</div>
      </div>
    </div>

    <!-- Capability rows -->
    <div
      v-for="r in ROWS"
      :key="r.id"
      class="bt-grid bt-row"
    >
      <div class="bt-cap-label mono">{{ r.label }}</div>
      <div
        v-for="b in bundles"
        :key="b.id"
        class="bt-cell mono"
        :class="{ off: r.each(b) === '—' }"
      >{{ r.each(b) }}</div>
    </div>

    <!-- Pick row -->
    <div class="bt-grid bt-pick">
      <div></div>
      <div v-for="b in bundles" :key="b.id" class="bt-pick-cell">
        <button
          type="button"
          class="btn sm"
          style="width: 92%; justify-content: center"
          :disabled="!b._fits"
          :data-tier-id="b.id"
          @click="onPick(b)"
        >Pick {{ b.name }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bt-card {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--rad-lg);
  overflow: hidden;
  margin-bottom: 40px;
}
.bt-grid {
  display: grid;
  grid-template-columns: 180px repeat(4, 1fr);
}
.bt-header {
  background: var(--bg);
  border-bottom: 1px solid var(--line);
}
.bt-cap-head {
  padding: 14px;
  font-size: 10px;
  color: var(--fg-4);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}
.bt-tier-head {
  padding: 14px;
  border-left: 1px solid var(--line);
  text-align: center;
  position: relative;
}
.bt-tier-head.unfit { opacity: 0.5; }
.bt-tier-head.recommended::after {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: var(--accent);
}
.bt-tier-name { font-size: 17px; font-weight: 500; letter-spacing: -0.02em; color: var(--fg); }
.bt-tier-head.recommended .bt-tier-name { color: var(--accent); }
.bt-tier-spec { font-size: 10px; color: var(--fg-4); margin-top: 2px; }

.bt-row { border-bottom: 1px solid var(--line-soft); }
.bt-cap-label { padding: 12px; font-size: 12px; color: var(--fg-2); }
.bt-cell {
  padding: 12px;
  border-left: 1px solid var(--line-soft);
  font-size: 11.5px;
  text-align: center;
  color: var(--fg-2);
}
.bt-cell.off { color: var(--fg-5); }

.bt-pick {
  border-top: 1px solid var(--line);
  background: var(--bg);
}
.bt-pick-cell {
  padding: 12px;
  border-left: 1px solid var(--line-soft);
  text-align: center;
}
</style>
