<script setup>
/**
 * dashboard/ChatEmpty.vue — slice #169.
 *
 * Placeholder shown above the composer when no conversation exists.
 * Renders a glyph, prompt, and 3 example chips. Picking a chip emits
 * ``pick`` with the prompt text so the host can seed the composer.
 */
const props = defineProps({
  prompts: {
    type: Array,
    default: () => [
      'Refactor this Python file…',
      'Summarize the docs in /docs',
      'Generate an image of a chip',
    ],
  },
})

const emit = defineEmits(['pick'])
</script>

<template>
  <div class="empty-chat" data-testid="chat-empty">
    <div class="glyph" aria-hidden="true">
      <span>hal<span class="zero">0</span></span>
    </div>
    <h3>What do you need from hal0?</h3>
    <p>Pick a persona below and start a conversation. Tool calls render inline.</p>
    <div class="prompts">
      <button
        v-for="(p, i) in prompts"
        :key="i"
        type="button"
        class="prompt"
        :data-testid="`empty-prompt-${i}`"
        @click="emit('pick', p)"
      >{{ p }}</button>
    </div>
  </div>
</template>

<style scoped>
.empty-chat {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 40px;
  text-align: center;
}
.empty-chat .glyph {
  width: 64px;
  height: 64px;
  border: 1px solid color-mix(in oklab, var(--hal0-accent, #feaf00) 30%, transparent);
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in oklab, var(--hal0-accent, #feaf00) 12%, transparent);
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 22px;
  font-weight: 600;
  color: var(--hal0-accent, var(--accent, #feaf00));
}
.empty-chat .glyph .zero { color: var(--hal0-accent, var(--accent, #feaf00)); }
.empty-chat h3 {
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 16px;
  font-weight: 500;
  margin: 0;
  letter-spacing: -0.01em;
  color: var(--color-fg, var(--fg, #e5e5e5));
}
.empty-chat p {
  color: var(--color-fg-muted, var(--fg-3, #888));
  font-size: 13px;
  margin: 0;
  max-width: 360px;
  line-height: 1.55;
}
.empty-chat .prompts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  margin-top: 8px;
  max-width: 520px;
}
.empty-chat .prompt {
  padding: 6px 12px;
  border: 1px solid var(--color-border, var(--line, #2a2a2a));
  border-radius: 999px;
  font-family: var(--font-mono, var(--jbm, monospace));
  font-size: 11.5px;
  color: var(--color-fg-muted, var(--fg-2, #bbb));
  cursor: pointer;
  background: var(--color-surface, var(--bg, #0a0a0a));
}
.empty-chat .prompt:hover {
  border-color: var(--hal0-accent, var(--accent, #feaf00));
  color: var(--hal0-accent, var(--accent, #feaf00));
}
</style>
