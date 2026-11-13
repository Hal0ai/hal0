// Markdown, read into a small tree a component can draw without ever building HTML from text.
//
// A knowledge page and a mental model are markdown the engine wrote from stored facts. That
// text is not trusted: it is parsed here into blocks and inline runs, and Markdown.jsx turns
// those into React elements. Nothing is passed to `innerHTML`, raw HTML in the text stays
// text, and only an `http://` or `https://` address becomes a link.
//
// It reads the subset the engine writes: headings, paragraphs, bulleted and numbered lists
// (nested by indent), fenced code, block quotes, tables and rules; and inline code, bold,
// italic and links. Anything else falls through as plain text.

export type Inline =
  | { t: 'text'; v: string }
  | { t: 'code'; v: string }
  | { t: 'strong'; c: Inline[] }
  | { t: 'em'; c: Inline[] }
  | { t: 'link'; href: string; c: Inline[] }

export interface ListItem {
  c: Inline[]
  /** Lists nested under this item. */
  children: Block[]
}

export type Block =
  | { t: 'heading'; level: number; c: Inline[] }
  | { t: 'para'; c: Inline[] }
  | { t: 'list'; ordered: boolean; items: ListItem[] }
  | { t: 'code'; lang: string | null; v: string }
  | { t: 'quote'; blocks: Block[] }
  | { t: 'table'; head: Inline[][]; rows: Inline[][][] }
  | { t: 'rule' }

// ── Inline ────────────────────────────────────────────────────────────────────

// In order of precedence: an escaped character, a code span, a link, bold, italic. A pattern
// only: each call of `parseInline` runs a matcher of its own built from it.
const INLINE =
  /\\([\\`*_[\]()#+\-.!|>~])|(`+)([^`]|[^`][\s\S]*?[^`])\2(?!`)|\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))+)(?:\s+"[^"]*")?\)|(\*\*|__)(?=\S)([\s\S]*?\S)\6|(\*|_)(?=\S)([\s\S]*?\S)\8/

const SAFE_LINK = /^https?:\/\//i
const WORD = /[A-Za-z0-9]/

function pushText(out: Inline[], value: string) {
  if (!value) return
  const last = out[out.length - 1]
  if (last && last.t === 'text') last.v += value
  else out.push({ t: 'text', v: value })
}

export function parseInline(source: string): Inline[] {
  const out: Inline[] = []
  let at = 0
  // A matcher of its own: this function calls itself for the text inside a mark, and a shared
  // one would lose its place.
  const matcher = new RegExp(INLINE.source, 'g')
  for (let match = matcher.exec(source); match; match = matcher.exec(source)) {
    const [whole, escaped, , code, label, href, , strong, emMark, em] = match
    // `snake_case_name` is a name, not emphasis: an underscore inside a word marks nothing.
    if (emMark === '_' || match[6] === '__') {
      const before = source[match.index - 1] ?? ''
      const after = source[match.index + whole.length] ?? ''
      if (WORD.test(before) || WORD.test(after)) {
        pushText(out, source.slice(at, match.index + 1))
        at = match.index + 1
        matcher.lastIndex = at
        continue
      }
    }
    pushText(out, source.slice(at, match.index))
    at = match.index + whole.length
    if (escaped !== undefined) pushText(out, escaped)
    else if (code !== undefined) out.push({ t: 'code', v: code.trim() })
    else if (label !== undefined) {
      // An address that is not plain http(s) is not followed: the label stays, as text.
      if (SAFE_LINK.test(href)) out.push({ t: 'link', href, c: parseInline(label) })
      else for (const part of parseInline(label)) out.push(part)
    } else if (strong !== undefined) out.push({ t: 'strong', c: parseInline(strong) })
    else if (em !== undefined) out.push({ t: 'em', c: parseInline(em) })
  }
  pushText(out, source.slice(at))
  return out
}

// ── Blocks ────────────────────────────────────────────────────────────────────

const FENCE = /^\s{0,3}(```+|~~~+)\s*([^\s`]*)/
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/
const QUOTE = /^\s{0,3}>\s?(.*)$/
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/
const TABLE_RULE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/

const blank = (line: string | undefined) => line === undefined || line.trim() === ''

function cells(line: string): string[] {
  const inner = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  // A pipe inside a code span, or escaped, does not split the row.
  const out: string[] = []
  let current = ''
  let inCode = false
  for (let i = 0; i < inner.length; i += 1) {
    const ch = inner[i]
    if (ch === '\\' && inner[i + 1] === '|') {
      current += '|'
      i += 1
    } else if (ch === '`') {
      inCode = !inCode
      current += ch
    } else if (ch === '|' && !inCode) {
      out.push(current.trim())
      current = ''
    } else current += ch
  }
  out.push(current.trim())
  return out
}

const startsTable = (lines: string[], i: number) =>
  lines[i].includes('|') && lines[i + 1] !== undefined && lines[i + 1].includes('-') && TABLE_RULE.test(lines[i + 1])

const startsBlock = (lines: string[], i: number) =>
  FENCE.test(lines[i]) ||
  HEADING.test(lines[i]) ||
  RULE.test(lines[i]) ||
  QUOTE.test(lines[i]) ||
  ITEM.test(lines[i]) ||
  startsTable(lines, i)

interface RawItem {
  indent: number
  ordered: boolean
  text: string
}

function buildList(items: RawItem[]): Block {
  const base = items[0].indent
  const list: Block & { t: 'list' } = { t: 'list', ordered: items[0].ordered, items: [] }
  let i = 0
  while (i < items.length) {
    const item = items[i]
    const nested: RawItem[] = []
    let j = i + 1
    while (j < items.length && items[j].indent > base) nested.push(items[j++])
    list.items.push({ c: parseInline(item.text), children: nested.length > 0 ? [buildList(nested)] : [] })
    i = j
  }
  return list
}

export function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, '\n').replace(/\t/g, '    ').split('\n')
  const out: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (blank(line)) {
      i += 1
      continue
    }
    const fence = FENCE.exec(line)
    if (fence) {
      const marker = fence[1]
      const body: string[] = []
      i += 1
      while (i < lines.length && !lines[i].trim().startsWith(marker)) body.push(lines[i++])
      i += 1 // the closing fence, or the end of the text
      out.push({ t: 'code', lang: fence[2] || null, v: body.join('\n') })
      continue
    }
    const heading = HEADING.exec(line)
    if (heading) {
      out.push({ t: 'heading', level: heading[1].length, c: parseInline(heading[2]) })
      i += 1
      continue
    }
    if (RULE.test(line)) {
      out.push({ t: 'rule' })
      i += 1
      continue
    }
    if (startsTable(lines, i)) {
      const head = cells(line).map(parseInline)
      const rows: Inline[][][] = []
      i += 2
      while (i < lines.length && !blank(lines[i]) && lines[i].includes('|')) {
        const row = cells(lines[i++]).map(parseInline)
        // Every row has the header's number of cells: short rows are padded, long ones cut.
        rows.push(head.map((_, at) => row[at] ?? []))
      }
      out.push({ t: 'table', head, rows })
      continue
    }
    if (QUOTE.test(line)) {
      const body: string[] = []
      while (i < lines.length && QUOTE.test(lines[i])) body.push(QUOTE.exec(lines[i++])![1])
      out.push({ t: 'quote', blocks: parseBlocks(body.join('\n')) })
      continue
    }
    if (ITEM.test(line)) {
      const items: RawItem[] = []
      while (i < lines.length) {
        const item = ITEM.exec(lines[i])
        if (item) {
          items.push({ indent: item[1].length, ordered: /\d/.test(item[2]), text: item[3] })
          i += 1
        } else if (!blank(lines[i]) && /^\s{2,}\S/.test(lines[i]) && !FENCE.test(lines[i])) {
          // An indented line carries on the item above it.
          items[items.length - 1].text += ` ${lines[i].trim()}`
          i += 1
        } else if (blank(lines[i]) && lines[i + 1] !== undefined && ITEM.test(lines[i + 1])) {
          i += 1 // a blank line between two items of one list
        } else break
      }
      // A change of bullet kind at the top level starts a new list.
      let from = 0
      for (let k = 1; k <= items.length; k += 1) {
        const boundary =
          k === items.length || (items[k].indent <= items[from].indent && items[k].ordered !== items[from].ordered)
        if (boundary) {
          out.push(buildList(items.slice(from, k)))
          from = k
        }
      }
      continue
    }
    const para: string[] = [line.trim()]
    i += 1
    while (i < lines.length && !blank(lines[i]) && !startsBlock(lines, i)) para.push(lines[i++].trim())
    out.push({ t: 'para', c: parseInline(para.join(' ')) })
  }
  return out
}

/** The text of inline runs, with the marks dropped. For a test, or an accessible name. */
export function inlineText(runs: Inline[]): string {
  return runs.map((run) => ('v' in run ? run.v : inlineText(run.c))).join('')
}
