<script setup>
/**
 * TierCard — single bundle tier card.
 *
 * Mirrors the inner `<div className="tier-card">` block from
 *   /tmp/hal0-design/hal0-v2/project/dash/firstrun.jsx (lines 64-99)
 *
 * State chips (one of):
 *   recommended  — amber ★ chip (RAM-aware best fit on a clean box)
 *   available    — neutral "fits" chip
 *   unfit        — greyed >=50% + [Pick] disabled
 *   installed    — green chip (re-entering picker post-install)
 *   gated-no-hf  — warn chip (bundle has gated models + no HF_TOKEN)
 */
defineProps({
  bundle: { type: Object, required: true },
  // 'recommended' | 'available' | 'unfit' | 'installed' | 'gated-no-hf'
  state:  { type: String, default: 'available' },
})

const emit = defineEmits(['pick'])

function pick(id) { emit('pick', id) }
</script>

<template>
  <div
    class="tier-card"
    :class="{
      recommended: state === 'recommended',
      unfit:       state === 'unfit',
      installed:   state === 'installed',
      gated:       state === 'gated-no-hf',
    }"
    :data-tier-id="bundle.id"
    :data-tier-state="state"
  >
    <div class="tier-card-h">
      <div class="tier-name mono">{{ bundle.name }}</div>
      <span v-if="state === 'recommended'" class="tier-tag rec">★ recommended</span>
      <span v-else-if="state === 'installed'" class="tier-tag installed">installed</span>
      <span v-else-if="state === 'unfit'" class="tier-tag unfit">needs ≥ {{ bundle.ram }} GB</span>
      <span v-else-if="state === 'gated-no-hf'" class="tier-tag gated">HF token required</span>
      <span v-else class="tier-tag fit">fits</span>
    </div>
    <div class="tier-spec">
      <b>{{ bundle.ram }} GB+</b> unified · <b>~{{ bundle.sizeGB }} GB</b> download
    </div>
    <div class="tier-stats">
      <div class="tier-stat">
        <div class="l">slots</div>
        <div class="v num">{{ bundle.includes.filter((i) => i.active).length }}<span class="u">/{{ bundle.includes.length }}</span></div>
      </div>
      <div class="tier-stat">
        <div class="l">size</div>
        <div class="v num">{{ bundle.sizeGB }}<span class="u">GB</span></div>
      </div>
    </div>
    <div class="tier-includes">
      <div
        v-for="(inc, i) in bundle.includes"
        :key="i"
        class="ln"
        :class="{ faint: !inc.active }"
      >
        <span class="ic">{{ inc.active ? '+' : '·' }}</span>
        <span>{{ inc.label }}</span>
      </div>
    </div>
    <div class="actions">
      <button
        type="button"
        class="btn"
        style="flex: 1"
        :disabled="state === 'unfit'"
        @click="pick(bundle.id)"
      >
        <template v-if="state === 'installed'">Re-install {{ bundle.name }}</template>
        <template v-else>Pick {{ bundle.name }}</template>
      </button>
    </div>
  </div>
</template>

<style scoped>
.tier-card {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--rad-lg);
  padding: 22px;
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow: hidden;
}
.tier-card.recommended { border-color: var(--accent-line); }
.tier-card.recommended::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: var(--accent);
}
.tier-card.installed { border-color: var(--ok-line); }
.tier-card.installed::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: var(--ok);
}
.tier-card.gated { border-color: var(--warn-line); }
.tier-card.unfit { opacity: 0.55; }

.tier-card-h { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.tier-name { font-family: var(--jbm); font-size: 18px; font-weight: 500; letter-spacing: -0.02em; }

.tier-tag {
  font-family: var(--jbm);
  font-size: 9px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  padding: 2px 6px;
  border-radius: 3px;
  white-space: nowrap;
}
.tier-tag.rec       { color: var(--accent); border: 1px solid var(--accent-line); background: var(--accent-soft); }
.tier-tag.fit       { color: var(--ok); border: 1px solid var(--ok-line); background: var(--ok-soft); }
.tier-tag.installed { color: var(--ok); border: 1px solid var(--ok-line); background: var(--ok-soft); }
.tier-tag.unfit     { color: var(--fg-4); border: 1px solid var(--line); }
.tier-tag.gated     { color: var(--warn); border: 1px solid var(--warn-line); background: var(--warn-soft); }

.tier-spec { font-family: var(--jbm); font-size: 11.5px; color: var(--fg-3); }
.tier-spec b { color: var(--fg); font-weight: 500; }

.tier-stats {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
  padding: 12px 0;
  border-top: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
}
.tier-stat .l { font-family: var(--jbm); font-size: 10px; color: var(--fg-4); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; }
.tier-stat .v { font-family: var(--jbm); font-size: 18px; color: var(--fg); letter-spacing: -0.02em; }
.tier-stat .v .u { color: var(--accent); font-size: 11px; margin-left: 2px; }

.tier-includes { display: flex; flex-direction: column; gap: 5px; }
.tier-includes .ln { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--fg-2); }
.tier-includes .ln .ic { color: var(--accent); font-family: var(--jbm); font-size: 10px; width: 12px; }
.tier-includes .ln.faint { color: var(--fg-4); }
.tier-includes .ln.faint .ic { color: var(--fg-5); }

.actions { margin-top: auto; display: flex; gap: 8px; }
</style>
