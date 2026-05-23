<script setup>
defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  eyebrow: { type: String, default: '' },
})
</script>

<template>
  <div class="page-header">
    <div class="page-header-text">
      <p v-if="eyebrow" class="page-eyebrow">
        <span class="page-eyebrow-dot" aria-hidden="true"></span>
        {{ eyebrow }}
      </p>
      <h1 class="page-title">{{ title }}</h1>
      <p v-if="subtitle" class="page-subtitle">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.actions" class="page-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 24px 24px 0;
  flex-wrap: wrap;
}

.page-header-text { min-width: 0; }

.page-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin: 0 0 8px;
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--hal0-accent);
  font-weight: 500;
}
.page-eyebrow-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--hal0-accent);
  box-shadow: 0 0 8px var(--hal0-accent);
}

.page-title {
  font-size: 22px;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0;
  letter-spacing: -0.02em;
  line-height: 1.25;
}

.page-subtitle {
  font-size: 13px;
  color: var(--color-fg-muted);
  margin: 4px 0 0;
}

.page-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  padding-top: 2px;
}
</style>
