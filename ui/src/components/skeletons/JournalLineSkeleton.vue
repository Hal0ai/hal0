<script setup>
/**
 * skeletons/JournalLineSkeleton.vue — placeholder mono row for the
 * Logs view (slice #175).
 *
 * Mirrors LogLine's three-cell layout: ts, source, message. Each cell
 * uses the shared `.skel` shimmer.
 */
</script>

<template>
  <div class="jl-skel mono" data-testid="journal-line-skeleton" aria-hidden="true">
    <span class="skel ts" />
    <span class="skel src" />
    <span class="skel msg" />
  </div>
</template>

<style scoped>
.jl-skel {
  display: grid;
  grid-template-columns: 100px 90px 1fr;
  align-items: center;
  gap: 12px;
  padding: 5px 12px;
  font-family: var(--hal0-font-mono);
}
.jl-skel .skel { height: 10px; border-radius: 3px; }
.jl-skel .ts  { width: 100%; }
.jl-skel .src { width: 100%; }
.jl-skel .msg { width: 80%; }
</style>
