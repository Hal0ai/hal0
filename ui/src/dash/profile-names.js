// profile-names.js — shared pretty-name map for seed profiles.
//
// Seed profile slugs are lowercase (rocm/rocm-mtp/vulkan/flm/tts/comfyui);
// the dashboard renders a pretty display label instead of the raw slug.
// Shared between profiles.jsx (intent card) and slots.jsx (identity chip)
// so the two surfaces never drift.

const PROFILE_DISPLAY = {
  'rocm':     'ROCm',
  'rocm-mtp': 'ROCm-MTP',
  'vulkan':   'Vulkan',
  'flm':      'FLM',
  'tts':      'TTS',
  'comfyui':  'ComfyUI',
};

// prettyProfile(slug) → display label. Falls back to a title-cased slug
// (hyphens/underscores → spaces, each word capitalised) for custom profiles
// not in the seed map.
function prettyProfile(slug) {
  if (!slug) return '';
  if (PROFILE_DISPLAY[slug]) return PROFILE_DISPLAY[slug];
  return String(slug)
    .split(/[-_]/)
    .map(w => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}

export { PROFILE_DISPLAY, prettyProfile };
