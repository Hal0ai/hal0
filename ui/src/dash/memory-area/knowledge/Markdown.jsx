// The text of a knowledge page or a mental model, drawn from its markdown.
//
// The text was written by the engine from stored facts, so it is not trusted. It is parsed
// into blocks (markdown-pure.ts) and each block becomes a React element here: no HTML is ever
// built from the text, a tag written in it shows as the characters it is, and only an http(s)
// address becomes a link (opened in a new tab, with no referrer).
//
// Headings start one level under the reader's own title (an `h3`), so the page keeps one
// outline: the text's `#` and `##` are `h4`, `###` is `h5`, anything deeper is `h6`.
import '../memory-area.css'
import { inlineText, parseBlocks } from './markdown-pure'

function Runs({ runs }) {
  return runs.map((run, at) => {
    if (run.t === 'text') return run.v
    if (run.t === 'code') {
      return (
        <code className="ma-kn-md-code" key={at}>
          {run.v}
        </code>
      )
    }
    if (run.t === 'strong') {
      return (
        <strong key={at}>
          <Runs runs={run.c} />
        </strong>
      )
    }
    if (run.t === 'em') {
      return (
        <em key={at}>
          <Runs runs={run.c} />
        </em>
      )
    }
    return (
      <a className="ma-link" href={run.href} target="_blank" rel="noreferrer noopener" key={at}>
        <Runs runs={run.c} />
      </a>
    )
  })
}

const HEADING_TAG = { 1: 'h4', 2: 'h4', 3: 'h5' }

function Blocks({ blocks }) {
  return blocks.map((block, at) => {
    switch (block.t) {
      case 'heading': {
        const Tag = HEADING_TAG[block.level] ?? 'h6'
        return (
          <Tag className={`ma-kn-md-h ma-kn-md-h${Math.min(block.level, 4)}`} key={at}>
            <Runs runs={block.c} />
          </Tag>
        )
      }
      case 'list': {
        const Tag = block.ordered ? 'ol' : 'ul'
        return (
          <Tag className="ma-kn-md-list" key={at}>
            {block.items.map((item, n) => (
              <li key={n}>
                <Runs runs={item.c} />
                {item.children.length > 0 && <Blocks blocks={item.children} />}
              </li>
            ))}
          </Tag>
        )
      }
      case 'code':
        return (
          // A long line scrolls inside the block, and the block can be reached to scroll it.
          <pre className="ma-kn-md-pre" tabIndex={0} key={at}>
            <code>{block.v}</code>
          </pre>
        )
      case 'quote':
        return (
          <blockquote className="ma-kn-md-quote" key={at}>
            <Blocks blocks={block.blocks} />
          </blockquote>
        )
      case 'table':
        return (
          <div
            className="ma-table-region ma-kn-md-table-region"
            role="region"
            aria-label={`Table: ${block.head.map(inlineText).join(', ')}`}
            tabIndex={0}
            key={at}
          >
            <table className="ma-kn-md-table">
              <thead>
                <tr>
                  {block.head.map((cell, n) => (
                    <th scope="col" key={n}>
                      <Runs runs={cell} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((cell, n) => (
                      <td key={n}>
                        <Runs runs={cell} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      case 'rule':
        return <hr className="ma-kn-md-rule" key={at} />
      default:
        return (
          <p className="ma-kn-md-p" key={at}>
            <Runs runs={block.c} />
          </p>
        )
    }
  })
}

/** @param {{ text: string, 'data-testid'?: string }} props `text` is markdown. */
export function Markdown({ text, 'data-testid': testId }) {
  return (
    <div className="ma-kn-md" data-testid={testId}>
      <Blocks blocks={parseBlocks(text)} />
    </div>
  )
}
