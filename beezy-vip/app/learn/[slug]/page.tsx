import { notFound }    from 'next/navigation'
import { getArticle }  from '@/lib/learn-db'
import { ARTICLE_SPECS } from '@/lib/article-generator'
import type { Metadata, ResolvingMetadata } from 'next'

export const revalidate = 3600

type Props = { params: { slug: string } }

export async function generateMetadata(
  { params }: Props,
  _parent: ResolvingMetadata
): Promise<Metadata> {
  const article = await getArticle(params.slug).catch(() => null)
  if (!article) {
    const spec = ARTICLE_SPECS.find(s => s.slug === params.slug)
    return { title: spec?.title ?? 'Article Not Found' }
  }
  return {
    title:       article.title,
    description: article.meta_desc,
    keywords:    article.keyword,
  }
}

export async function generateStaticParams() {
  return ARTICLE_SPECS.map(s => ({ slug: s.slug }))
}

// Minimal markdown → HTML renderer (no heavy deps)
function renderMarkdown(md: string): string {
  return md
    // Headings
    .replace(/^### (.+)$/gm, '<h3 class="text-base font-bold mt-8 mb-3 uppercase tracking-wide">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="text-lg font-extrabold mt-10 mb-4 uppercase tracking-wide">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="text-2xl font-extrabold mb-6 uppercase tracking-tight">$1</h1>')
    // Bold
    .replace(/\*\*(.+?)\*\*/g, '<strong class="text-text font-semibold">$1</strong>')
    // Inline code
    .replace(/`([^`]+)`/g, '<code class="mono text-accent bg-accent/5 px-1.5 py-0.5 text-sm">$1</code>')
    // Code blocks
    .replace(/```[\w]*\n([\s\S]*?)```/g, '<pre class="mono text-sm bg-[var(--surface)] border border-[var(--border)] p-4 my-4 overflow-x-auto"><code>$1</code></pre>')
    // Links
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" class="text-accent hover:underline">$1</a>')
    // Table rows (simple)
    .replace(/^\|(.+)\|$/gm, (_m, cells) => {
      const tds = cells.split('|').map((c: string) => `<td class="px-4 py-2 border-b border-[var(--border)] mono text-xs">${c.trim()}</td>`).join('')
      return `<tr>${tds}</tr>`
    })
    .replace(/(<tr>[\s\S]*?<\/tr>)/gm, '<table class="w-full border border-[var(--border)] my-6">$1</table>')
    // Separator rows (---)
    .replace(/^\|[-| :]+\|$/gm, '')
    // Blockquote
    .replace(/^> (.+)$/gm, '<blockquote class="border-l-2 border-accent pl-4 text-muted italic my-4">$1</blockquote>')
    // Unordered list
    .replace(/^- (.+)$/gm, '<li class="text-muted text-sm mb-1 pl-2">$1</li>')
    .replace(/(<li[^>]*>[\s\S]*?<\/li>)/gm, '<ul class="list-none space-y-1 my-4 border-l border-[var(--border)] pl-4">$1</ul>')
    // Paragraphs
    .replace(/^(?!<[huptbcali])(.+)$/gm, '<p class="text-muted text-sm leading-relaxed mb-4">$1</p>')
    // Clean up blank lines
    .replace(/\n{3,}/g, '\n\n')
}

export default async function ArticlePage({ params }: Props) {
  const article = await getArticle(params.slug).catch(() => null)

  if (!article) {
    // Check if it's a known but ungenerated article
    const spec = ARTICLE_SPECS.find(s => s.slug === params.slug)
    if (!spec) notFound()

    return (
      <div className="max-w-3xl mx-auto px-4 py-24 text-center">
        <p className="mono text-xs text-accent uppercase tracking-widest mb-4">Coming Soon</p>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-4">{spec.title}</h1>
        <p className="text-muted text-sm mb-8">
          This article is being generated. Check back shortly.
        </p>
        <a href="/learn" className="mono text-xs uppercase tracking-widest text-muted hover:text-accent transition-colors">
          ← Back to Learn
        </a>
      </div>
    )
  }

  const html = renderMarkdown(article.body_mdx)

  return (
    <div className="max-w-3xl mx-auto px-4 py-12">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 mono text-xs text-muted mb-8">
        <a href="/learn" className="hover:text-accent transition-colors">Learn</a>
        <span>·</span>
        <span className="text-accent">{article.keyword}</span>
      </div>

      {/* Article */}
      <article
        className="prose-beezy"
        dangerouslySetInnerHTML={{ __html: html }}
      />

      {/* Footer */}
      <div className="mt-16 pt-8 border-t border-[var(--border)]">
        <p className="mono text-xs text-muted">
          Last updated {new Date(article.generated_at).toLocaleDateString('en-US', {
            month: 'long', day: 'numeric', year: 'numeric'
          })} · Beezy.VIP
        </p>
        <p className="mono text-xs text-muted mt-1">
          Stats cited reflect live paper-mode results. Past performance is not indicative of future results.
        </p>
      </div>
    </div>
  )
}
