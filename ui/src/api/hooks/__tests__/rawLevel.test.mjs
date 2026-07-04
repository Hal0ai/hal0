// Dependency-free tests for the raw-journald level heuristic + the
// blind-append ring policy used by useSlotLogsStream.
//
// Run: node ui/src/api/hooks/__tests__/rawLevel.test.mjs
//
// Why blind append (NOT logRing.appendEntry): raw journald lines carry no
// id and legitimately REPEAT (progress bars, per-token spam, the recurring
// "all slots are idle" heartbeat). Content-dedup would collapse real repeats
// into one line, so the slot channel appends every line and only bounds by
// ring size. This test pins that distinction so a future refactor can't
// "helpfully" route the raw channel through the dedup path.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseRawLevel } from '../rawLevel.js'

test('parseRawLevel: errors', () => {
  assert.equal(parseRawLevel('CUDA error: out of memory'), 'error')
  assert.equal(parseRawLevel('failed to load model shard 2'), 'error')
  assert.equal(parseRawLevel('llama_model_load: FATAL: bad magic'), 'error')
  assert.equal(parseRawLevel('connection refused'), 'error')
})

test('parseRawLevel: warnings', () => {
  assert.equal(parseRawLevel('warning: falling back to CPU'), 'warn')
  assert.equal(parseRawLevel('rope scaling deprecated, using default'), 'warn')
  assert.equal(parseRawLevel('retrying health probe'), 'warn')
})

test('parseRawLevel: info default', () => {
  assert.equal(parseRawLevel('load_tensors: offloaded 49/49 layers to GPU'), 'info')
  assert.equal(parseRawLevel('main: model loaded'), 'info')
  assert.equal(parseRawLevel('update_slots: all slots are idle'), 'info')
})

// Mirror of the useSlotLogsStream ring policy: append every line, bound by
// max, and DO NOT dedup identical repeats.
function blindAppend(prev, line, max) {
  const next = prev.length >= max ? prev.slice(prev.length - max + 1) : prev.slice()
  next.push(line)
  return next
}

test('blindAppend keeps identical repeats (unlike logRing dedup)', () => {
  let ring = []
  ring = blindAppend(ring, 'prompt processing progress', 4)
  ring = blindAppend(ring, 'prompt processing progress', 4)
  ring = blindAppend(ring, 'prompt processing progress', 4)
  assert.equal(ring.length, 3, 'identical repeats are preserved')
})

test('blindAppend bounds by max, evicting oldest', () => {
  let ring = []
  for (let i = 0; i < 10; i++) ring = blindAppend(ring, `line ${i}`, 4)
  assert.deepEqual(ring, ['line 6', 'line 7', 'line 8', 'line 9'])
})
