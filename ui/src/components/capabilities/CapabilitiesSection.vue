<script setup>
/**
 * CapabilitiesSection
 *
 * Wrapper that lays out the embed / voice / img capability cards
 * alongside the NPU backend rollup. Selection state is owned by the
 * `useCapabilities()` composable (singleton at module scope), so this
 * component just hands each card the slice it needs and lets the cards
 * call `setSelection(...)` themselves.
 *
 * Shared CSS for `.cap-section*`, `.cap-select`, `.cap-meta`, `.cap-chip`,
 * and `.cap-metric*` lives in the non-scoped <style> block below so all
 * three capability cards stay visually aligned without duplicate-copy
 * drift. Card-local structural styles (header, card chrome) are kept
 * scoped in each child.
 */
import { computed } from 'vue'
import { useCapabilities } from '../../composables/useCapabilities.js'
import EmbedCard from './EmbedCard.vue'
import VoiceCard from './VoiceCard.vue'
import ImgCard from './ImgCard.vue'
// NPUBackendCard now lives in the Slots section — see Slots.vue /
// Dashboard.vue. It can serve regular chat-style models alongside the
// hal0-slot@* templates, so colocating it with those cards reads more
// honestly than treating it as a capability-only widget.
import LoadingSkeleton from '../LoadingSkeleton.vue'
import EmptyState from '../EmptyState.vue'

const cap = useCapabilities()

const embedSel = computed(() => cap.selections.value?.embed ?? null)
const voiceSel = computed(() => cap.selections.value?.voice ?? null)
const imgSel   = computed(() => cap.selections.value?.img   ?? null)

const hasData = computed(() => !!(embedSel.value || voiceSel.value || imgSel.value))
</script>

<template>
  <section class="cap-wrap" aria-labelledby="capabilities-heading">
    <header class="cap-wrap-head">
      <h2 id="capabilities-heading" class="section-title">Capability slots</h2>
      <p class="cap-wrap-subtitle">
        Embed, voice, and image capabilities. Models are picked per-capability
        across all available hardware backends.
      </p>
    </header>

    <!-- Initial load — skeletons stand in for the four cards while the
         first /api/capabilities round-trip resolves. -->
    <div v-if="cap.loading.value && !hasData" class="cap-grid">
      <div v-for="i in 3" :key="i" class="cap-card cap-card-skeleton">
        <LoadingSkeleton :lines="4" />
      </div>
    </div>

    <!-- Hard error and no cached data — surface a retry path rather than
         showing four empty cards. Transient errors with cached data
         fall through and render the cards normally. -->
    <EmptyState
      v-else-if="cap.error.value && !hasData"
      title="Dashboard couldn't reach /api/capabilities"
      :description="cap.error.value"
      cta-label="Retry"
      @cta="cap.refresh()"
    />

    <div v-else class="cap-grid">
      <EmbedCard v-if="embedSel" :selection="embedSel" />
      <VoiceCard v-if="voiceSel" :selection="voiceSel" />
      <ImgCard   v-if="imgSel"   :selection="imgSel" />
    </div>
  </section>
</template>

<style scoped>
.cap-wrap {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.cap-wrap-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* Visual weight matched to other Dashboard/Hardware section titles
 * (see Dashboard.vue / Hardware.vue: 16px / 600 / -0.01em). */
.section-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-fg);
  letter-spacing: -0.01em;
  margin: 0;
}

.cap-wrap-subtitle {
  margin: 0;
  font-size: 12px;
  color: var(--color-fg-muted);
  max-width: 640px;
}

.cap-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(440px, 1fr));
  gap: 14px;
  align-items: start;
}

.cap-card-skeleton {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 16px 18px;
  min-height: 200px;
}
</style>

<!--
  Shared CSS — intentionally NOT scoped.

  Every capability card (embed / voice / img) uses the same chip + meta +
  metrics + section + select styling. Scoping it into each child Vue file
  would mean editing three identical blocks every time we tweak a metric
  unit color. Living here as a single non-scoped block keeps it DRY and
  still co-located with the only component that mounts the cards.

  The `cap-*` prefix is unique to this section, so it does not collide
  with existing app styles. The NPU backend card uses its own `bc-*`
  prefix on purpose (it's a different visual treatment).
-->
<style>
/* Section header + dropdown — shared by EmbedCard / VoiceCard / ImgCard */
.cap-section { display: flex; flex-direction: column; gap: 6px; }
.cap-section-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.cap-section-head-l { display: flex; align-items: center; gap: 8px; min-width: 0; }
.cap-section-label {
  font-size: 12.5px; font-weight: 600;
  color: var(--color-fg-muted);
  text-transform: uppercase; letter-spacing: 0.04em;
}
.cap-section-sub {
  font-family: var(--font-mono); font-size: 10.5px;
  color: var(--color-fg-faint);
}
.cap-section-port {
  margin-left: 2px;
  color: var(--color-fg-faint);
  opacity: 0.7;
}

/* Status pill — sits on the LEFT of the section header to anchor the eye.
 * Drives off the selection.status returned by /api/capabilities. */
.cap-status {
  font-family: var(--font-mono); font-size: 9.5px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.06em;
  padding: 2px 6px; border-radius: 3px;
  border: 1px solid var(--color-border);
  background: var(--color-surface-3);
  color: var(--color-fg-faint);
  display: inline-flex; align-items: center; gap: 4px;
  line-height: 1;
}
.cap-status[data-state="serving"] {
  color: var(--color-success);
  border-color: color-mix(in oklch, var(--color-success) 40%, transparent);
  background: color-mix(in oklch, var(--color-success) 12%, transparent);
}
.cap-status[data-state="idle"],
.cap-status[data-state="ready"] {
  color: var(--color-warning);
  border-color: color-mix(in oklch, var(--color-warning) 40%, transparent);
  background: color-mix(in oklch, var(--color-warning) 12%, transparent);
}
.cap-status[data-state="loading"] {
  color: var(--color-info);
  border-color: color-mix(in oklch, var(--color-info) 40%, transparent);
  background: color-mix(in oklch, var(--color-info) 12%, transparent);
}
.cap-status[data-state="error"] {
  color: var(--color-danger);
  border-color: color-mix(in oklch, var(--color-danger) 40%, transparent);
  background: color-mix(in oklch, var(--color-danger) 12%, transparent);
}
.cap-status[data-state="offline"] {
  color: var(--color-fg-faint);
  border-color: var(--color-border);
  background: var(--color-surface-3);
}
.cap-status-dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: currentColor;
}
.cap-status[data-state="loading"] .cap-status-dot {
  animation: cap-pulse 1s ease-in-out infinite;
}
@keyframes cap-pulse { 50% { opacity: 0.35; } }

.cap-select {
  width: 100%;
  padding: 8px 10px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-family: var(--font-mono);
  font-size: 12px;
  cursor: pointer;
}
.cap-select:focus { outline: none; border-color: var(--color-border-hi); }
.cap-select:disabled { opacity: 0.5; cursor: not-allowed; }

/* Two-dropdown picker — model (75%) on the left, backend (25%) on the
 * right. Explicit grid columns avoid the flex-basis ambiguity that
 * width:100% on .cap-select otherwise causes (a flex item with
 * flex:0 0 auto picks up width:100% as its basis and steals the row).
 * Stacks below 480px so the cards remain usable in narrow layouts. */
.cap-pickers {
  display: grid;
  grid-template-columns: 3fr 1fr;
  gap: 8px;
  align-items: stretch;
}
.cap-select-model,
.cap-select-backend { min-width: 0; }
@media (max-width: 480px) {
  .cap-pickers { grid-template-columns: 1fr; }
}

/* Meta line: chip + supplementary mono-font fragments */
.cap-meta {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  font-size: 11px; color: var(--color-fg-faint);
}
.cap-meta-item { font-family: var(--font-mono); }

/* Hardware chip — colors keyed off `data-backend` */
.cap-chip {
  font-family: var(--font-mono); font-size: 10.5px;
  padding: 2px 7px; border-radius: 4px;
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg-muted);
}
.cap-chip[data-backend="npu"] {
  border-color: color-mix(in oklch, var(--hal0-accent) 50%, transparent);
  background: color-mix(in oklch, var(--hal0-accent) 14%, transparent);
  color: var(--hal0-accent);
}
.cap-chip[data-backend="gpu-vulkan"],
.cap-chip[data-backend="gpu-rocm"] {
  border-color: color-mix(in oklch, var(--color-info) 35%, transparent);
  background: color-mix(in oklch, var(--color-info) 12%, transparent);
  color: var(--color-info);
}
.cap-chip[data-backend="cpu"] {
  border-color: var(--color-border-hi);
  background: var(--color-surface-3);
  color: var(--color-fg-muted);
}

/* Per-child metrics strip */
.cap-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(72px, 1fr));
  gap: 8px;
  padding: 6px 8px;
  border-top: 1px solid color-mix(in oklch, var(--color-border) 60%, transparent);
  margin-top: 4px;
}
.cap-metric {
  display: flex; flex-direction: column; gap: 1px; min-width: 0;
  align-items: center; text-align: center;
}
.cap-metric-v {
  font-family: var(--font-mono); font-size: 12.5px; font-weight: 600;
  color: var(--color-fg); line-height: 1.2;
}
.cap-metric-u {
  font-family: var(--font-mono); font-size: 9.5px;
  color: var(--color-fg-faint);
  text-transform: uppercase; letter-spacing: 0.04em;
  line-height: 1.1;
}
.cap-metric-headline .cap-metric-v { color: var(--hal0-accent); }
.cap-metric-mem .cap-metric-v { color: var(--color-fg-muted); }
.cap-metric-na .cap-metric-v { color: var(--color-fg-faint); opacity: 0.7; }

/* Pull progress strip — appears under the select while a `⬇` option is
 * downloading. Mirrors the bar styling on NPUBackendCard's .bc-bar so
 * the visual language stays consistent across capability surfaces. */
.cap-pull {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  padding: 6px 8px;
  border: 1px solid color-mix(in oklch, var(--hal0-accent) 30%, var(--color-border));
  border-radius: var(--radius);
  background: color-mix(in oklch, var(--hal0-accent) 6%, transparent);
}
.cap-pull-bar {
  flex: 1;
  height: 4px;
  border-radius: 2px;
  background: var(--hal0-bg-sunken);
  overflow: hidden;
}
.cap-pull-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--hal0-accent), var(--hal0-accent-hover));
  transition: width 0.2s ease-out;
}
.cap-pull-label {
  font-size: 10.5px;
  color: var(--color-fg-muted);
  white-space: nowrap;
  flex-shrink: 0;
}
.cap-pull-cancel {
  background: transparent;
  border: 1px solid var(--color-border);
  color: var(--color-fg-faint);
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 2px 8px;
  border-radius: var(--radius);
  cursor: pointer;
}
.cap-pull-cancel:hover {
  color: var(--color-danger);
  border-color: color-mix(in oklch, var(--color-danger) 40%, var(--color-border));
}
</style>
