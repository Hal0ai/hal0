// A model's type as a glyph on a catalog row (chat, embedding, rerank, speech in, speech out,
// image). The type's name is the tooltip and the accessible name. A type with no glyph keeps the
// plain text chip, so an unknown type is never hidden.
const svg = (children) => (
  <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {children}
  </svg>
)

const GLYPHS = {
  chat: { label: 'Chat model', icon: svg(<path d="M2.5 3.5h11v7.5H6.5l-4 3z" />) },
  embed: {
    label: 'Embedding model',
    icon: svg(<path d="M3 3h3.5v3.5H3zM9.5 3H13v3.5H9.5zM3 9.5h3.5V13H3zM9.5 9.5H13V13H9.5z" />),
  },
  rerank: { label: 'Reranker', icon: svg(<path d="M2.5 4.5h8M2.5 8h5.5M2.5 11.5h3M12 5v7M10 10l2 2 2-2" />) },
  stt: {
    label: 'Speech to text',
    icon: svg(<><rect x="6" y="2" width="4" height="7" rx="2" /><path d="M3.5 7.5a4.5 4.5 0 0 0 9 0M8 12v2" /></>),
  },
  tts: { label: 'Text to speech', icon: svg(<path d="M2.5 6.5h2.5l3.5-3v9l-3.5-3H2.5zM11 5.5a3.5 3.5 0 0 1 0 5M12.8 3.8a6 6 0 0 1 0 8.4" />) },
  image: {
    label: 'Image model',
    icon: svg(<><rect x="2" y="3" width="12" height="10" rx="1.5" /><path d="M2.5 11.5l3.5-3.5 2.5 2.5 2-2 3 3" /></>),
  },
}

const ALIASES = {
  llm: 'chat',
  chat: 'chat',
  embed: 'embed',
  embedding: 'embed',
  embeddings: 'embed',
  rerank: 'rerank',
  reranker: 'rerank',
  reranking: 'rerank',
  stt: 'stt',
  asr: 'stt',
  transcription: 'stt',
  tts: 'tts',
  image: 'image',
  img: 'image',
}

/** The glyph key for a model type, or null when the type has no glyph. */
export function typeGlyphKey(type) {
  return ALIASES[String(type || '').toLowerCase()] ?? null
}

export function TypeGlyph({ type }) {
  const key = typeGlyphKey(type)
  if (!key) return <span className="chip">{type}</span>
  const g = GLYPHS[key]
  return (
    <span className="type-glyph" role="img" aria-label={g.label} title={`${g.label} (${type})`} data-type={type}>
      {g.icon}
    </span>
  )
}
