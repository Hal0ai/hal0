// Single source of truth for the FLM [npu] modality defaults.
//
// Backend contract (config/schema.py NpuConfig + providers/flm.py build_env):
// chat defaults ON when the key is absent; asr/embed default OFF unless
// explicitly true. The slot-drawer toggle seeds and the NPU occupancy card's
// pills both read through here so the two surfaces can never disagree on an
// absent key.
export const npuModalityOn = (npu, role) => {
  const t = npu || {}
  return role === 'chat' ? t.chat !== false : t[role] === true
}
