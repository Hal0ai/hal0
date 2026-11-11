// The markdown reader behind a knowledge page's text: what each block and inline mark becomes,
// and what is refused (HTML, links that are not plain http or https).
import { describe, expect, it } from 'vitest'
import { inlineText, parseBlocks, parseInline, type Block, type Inline } from './markdown-pure'

const kinds = (blocks: Block[]) => blocks.map((b) => b.t)
const one = <T extends Block['t']>(source: string, t: T) => {
  const blocks = parseBlocks(source)
  expect(kinds(blocks)).toEqual([t])
  return blocks[0] as Extract<Block, { t: T }>
}

describe('inline', () => {
  it('reads code, bold, italic and plain text, in order', () => {
    expect(parseInline('a `b` **c** *d* e')).toEqual<Inline[]>([
      { t: 'text', v: 'a ' },
      { t: 'code', v: 'b' },
      { t: 'text', v: ' ' },
      { t: 'strong', c: [{ t: 'text', v: 'c' }] },
      { t: 'text', v: ' ' },
      { t: 'em', c: [{ t: 'text', v: 'd' }] },
      { t: 'text', v: ' e' },
    ])
  })

  it('reads a mark inside a mark, and carries on after it', () => {
    // The text inside a mark is read by the same function; it must not lose its place in the
    // text around it.
    const runs = parseInline('**bold with `code`** then **more** and *last*')
    expect(runs.map((r) => r.t)).toEqual(['strong', 'text', 'strong', 'text', 'em'])
    expect((runs[0] as { c: Inline[] }).c).toEqual([
      { t: 'text', v: 'bold with ' },
      { t: 'code', v: 'code' },
    ])
    expect(inlineText(runs)).toBe('bold with code then more and last')
  })

  it('leaves a mark inside a code span as it is written', () => {
    expect(parseInline('`**not bold**`')).toEqual([{ t: 'code', v: '**not bold**' }])
  })

  it('does not read an underscore inside a name as emphasis', () => {
    expect(inlineText(parseInline('set mental_model_min_refresh in the_config'))).toBe(
      'set mental_model_min_refresh in the_config',
    )
    expect(parseInline('mental_model_id').every((r) => r.t === 'text')).toBe(true)
    // Between words it still is one.
    expect(parseInline('an _emphasised_ word').map((r) => r.t)).toEqual(['text', 'em', 'text'])
  })

  it('leaves an unclosed mark and a lone asterisk as text', () => {
    expect(inlineText(parseInline('2 * 3 is **six'))).toBe('2 * 3 is **six')
    expect(parseInline('2 * 3 is **six').every((r) => r.t === 'text')).toBe(true)
  })

  it('reads an escaped mark as the character', () => {
    expect(parseInline('\\*not italic\\*')).toEqual([{ t: 'text', v: '*not italic*' }])
  })

  it('links only http and https', () => {
    expect(parseInline('[docs](https://hal0.dev/docs)')).toEqual([
      { t: 'link', href: 'https://hal0.dev/docs', c: [{ t: 'text', v: 'docs' }] },
    ])
    for (const href of ['javascript:alert(1)', 'data:text/html,x', '//evil.test', './page.md', 'mailto:a@b.c']) {
      const runs = parseInline(`[label](${href})`)
      expect(runs, href).toEqual([{ t: 'text', v: 'label' }])
    }
  })

  it('keeps HTML as the characters it is', () => {
    const source = '<img src=x onerror=alert(1)> and <script>alert(1)</script>'
    expect(parseInline(source)).toEqual([{ t: 'text', v: source }])
  })
})

describe('blocks', () => {
  it('reads headings with their level', () => {
    expect(parseBlocks('# One\n## Two\n###### Six').map((b) => (b.t === 'heading' ? b.level : null))).toEqual([
      1, 2, 6,
    ])
    expect(inlineText(one('## Limits ##', 'heading').c)).toBe('Limits')
    // A `#` with no space after it is not a heading.
    expect(kinds(parseBlocks('#hashtag'))).toEqual(['para'])
  })

  it('joins the lines of a paragraph, and splits paragraphs at a blank line', () => {
    const blocks = parseBlocks('one\ntwo\n\nthree')
    expect(kinds(blocks)).toEqual(['para', 'para'])
    expect(inlineText((blocks[0] as { c: Inline[] }).c)).toBe('one two')
  })

  it('reads a bulleted and a numbered list as separate lists', () => {
    const blocks = parseBlocks('- a\n- b\n1. c\n2. d')
    expect(blocks).toHaveLength(2)
    expect(blocks.map((b) => (b.t === 'list' ? [b.ordered, b.items.length] : null))).toEqual([
      [false, 2],
      [true, 2],
    ])
  })

  it('nests a list by its indent, and carries an indented line on', () => {
    const list = one('- a\n  - a1\n  - a2\n- b\n  carried on', 'list')
    expect(list.items).toHaveLength(2)
    const nested = list.items[0].children[0] as Extract<Block, { t: 'list' }>
    expect(nested.items.map((i) => inlineText(i.c))).toEqual(['a1', 'a2'])
    expect(inlineText(list.items[1].c)).toBe('b carried on')
    expect(list.items[1].children).toEqual([])
  })

  it('keeps a list together across a blank line between items', () => {
    expect(one('- a\n\n- b', 'list').items).toHaveLength(2)
  })

  it('reads fenced code verbatim, marks and all', () => {
    const code = one('```sh\nryzenadj --set **x**\n  indented\n```', 'code')
    expect(code.lang).toBe('sh')
    expect(code.v).toBe('ryzenadj --set **x**\n  indented')
    // An unclosed fence runs to the end of the text.
    expect(one('```\nleft open', 'code').v).toBe('left open')
  })

  it('reads a table, padding short rows to the header', () => {
    const blocks = parseBlocks('| Setting | Peak |\n| --- | ---: |\n| Stock | 95 C |\n| `-30 mV` |\n\nafter')
    expect(kinds(blocks)).toEqual(['table', 'para'])
    const t = blocks[0] as Extract<Block, { t: 'table' }>
    expect(t.head.map(inlineText)).toEqual(['Setting', 'Peak'])
    expect(t.rows.map((row) => row.map(inlineText))).toEqual([
      ['Stock', '95 C'],
      ['-30 mV', ''],
    ])
  })

  it('does not split a table row at a pipe inside code', () => {
    const blocks = parseBlocks('| a | b |\n|---|---|\n| `x | y` | z |')
    const t = blocks[0] as Extract<Block, { t: 'table' }>
    expect(t.rows[0].map(inlineText)).toEqual(['x | y', 'z'])
  })

  it('reads a block quote and a rule', () => {
    const quote = one('> said\n> twice', 'quote')
    expect(kinds(quote.blocks)).toEqual(['para'])
    expect(kinds(parseBlocks('above\n\n---\n\nbelow'))).toEqual(['para', 'rule', 'para'])
  })

  it('reads a whole page', () => {
    const page = [
      '## Limits',
      '',
      'Load pushed it to **95 C**.',
      '',
      '| Setting | Peak |',
      '| --- | --- |',
      '| Stock | 95 C |',
      '',
      '1. An undervolt.',
      '2. The fan curve.',
      '',
      '```',
      'ryzenadj --set-coall=0xFFFE2',
      '```',
    ].join('\n')
    expect(kinds(parseBlocks(page))).toEqual(['heading', 'para', 'table', 'list', 'code'])
  })

  it('reads empty text as nothing', () => {
    expect(parseBlocks('')).toEqual([])
    expect(parseBlocks('\n\n  \n')).toEqual([])
  })

  it('reads Windows line ends', () => {
    expect(kinds(parseBlocks('# One\r\n\r\ntext\r\n'))).toEqual(['heading', 'para'])
  })
})
