/**
 * Small, dependency-free markdown to HTML for the static /learn articles.
 * Supports: headings, fenced code, tables, ordered/unordered/checklist lists, blockquotes, rules,
 * paragraphs, and inline code, bold, italic and links. All text is HTML-escaped first.
 */
const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

function inline(raw: string): string {
  let s = esc(raw)
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>')
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  s = s.replace(/(^|[^*\w])\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)/g, '$1<em>$2</em>')
  s = s.replace(/\[([^\]]+)\]\((\/[^)\s]*|https?:\/\/[^)\s]+)\)/g, '<a href="$2">$1</a>')
  return s
}

const cells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim())

export function renderMarkdown(src: string): string {
  const lines = src.replace(/\r\n/g, '\n').split('\n')
  const out: string[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (/^\s*$/.test(line)) { i++; continue }

    const fence = line.match(/^```/)
    if (fence) {
      const buf: string[] = []
      i++
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++])
      i++
      out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`)
      continue
    }

    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue }

    if (/^---+\s*$/.test(line)) { out.push('<hr />'); i++; continue }

    if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const head = cells(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(cells(lines[i++]))
      out.push(
        `<div class="table-wrap"><table><thead><tr>${head.map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>` +
        rows.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('') +
        '</tbody></table></div>',
      )
      continue
    }

    if (/^>\s?/.test(line)) {
      const buf: string[] = []
      while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ''))
      out.push(`<blockquote>${buf.map(inline).join('<br />')}</blockquote>`)
      continue
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*]\s+/, ''))
      out.push(`<ul>${items.map(t => `<li>${inline(t.replace(/^\[( |x)\]\s*/, m => (m.includes('x') ? '[x] ' : '[ ] ')))}</li>`).join('')}</ul>`)
      continue
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+\.\s+/, ''))
      out.push(`<ol>${items.map(t => `<li>${inline(t)}</li>`).join('')}</ol>`)
      continue
    }

    const buf: string[] = []
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^(#{1,4}\s|```|>\s?|\s*[-*]\s+|\s*\d+\.\s+|\s*\|)/.test(lines[i])) buf.push(lines[i++])
    if (buf.length === 0) { out.push(`<p>${inline(lines[i++])}</p>`); continue }
    out.push(`<p>${buf.map(inline).join(' ')}</p>`)
  }
  return out.join('\n')
}

/** Plain-text summary for cards and meta descriptions: strips markdown, cuts at a word boundary. */
export function plainSummary(md: string, max = 155): string {
  const t = md.replace(/`/g, '').replace(/\*\*/g, '').replace(/(^|\s)\*(\S[^*]*?)\*/g, '$1$2').replace(/\s+/g, ' ').trim()
  if (t.length <= max) return t
  const cut = t.slice(0, max)
  return cut.slice(0, cut.lastIndexOf(' ')).replace(/[,;:\-—]+$/, '') + '...'
}
