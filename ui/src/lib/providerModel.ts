// Small facts about a provider's model rows that both the API layer and the
// Providers tab need. They live here so `api/` never imports from `dash/`.

export type ModelType = 'chat' | 'embed' | 'rerank' | 'tts' | 'stt' | 'image'

/** Model ids contain `/`: keep it as a path separator, encode the rest. */
export function modelPath(modelId: string): string {
  return modelId.split('/').map(encodeURIComponent).join('/')
}
