<script setup>
/**
 * SlotOverflowMenu.vue — popover menu for the ⋯ button on a SlotCard.
 *
 * Mirrors slot-modals.jsx::SlotOverflowMenu (lines 351-366). Built on
 * the primitives/Menu shell so positioning + outside-click + Esc are
 * the shared behaviour. Each item fires an emit; the parent decides
 * what to do (route, toast, modal, etc).
 */
import { computed } from 'vue'
import Menu from '../primitives/Menu.vue'

const props = defineProps({
  open:   { type: Boolean, default: false },
  anchor: { type: [Object, null], default: null },
  slot:   { type: Object, required: true },
})

const emit = defineEmits(['close', 'view-logs', 'set-default', 'copy-curl', 'delete'])

const items = computed(() => [
  {
    label: 'View slot logs',
    onClick: () => emit('view-logs', props.slot),
  },
  {
    label: props.slot.isDefault || props.slot.is_default ? 'Already default' : 'Set as default',
    onClick: () => emit('set-default', props.slot),
  },
  {
    label: 'Copy curl example',
    onClick: () => emit('copy-curl', props.slot),
  },
  { divider: true },
  {
    label: 'Delete slot',
    danger: true,
    onClick: () => emit('delete', props.slot),
  },
])
</script>

<template>
  <Menu
    :open="open"
    :anchor="anchor"
    :items="items"
    :on-close="() => emit('close')"
    side="right"
  />
</template>
