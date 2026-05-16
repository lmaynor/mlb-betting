import { getArticle, ARTICLES } from '@/lib/articles-static'
import { notFound } from 'next/navigation'
import type { Metadata } from 'next'

const B = '0.5px solid #1f1f24'

export function generateStaticParams() {
  return ARTICLES.map(a => ({ slug: a.slug }))
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params
  const article = getArticle(slug)
  if (!article) return {}
  return { title: `${article.title} — Beezy.VIP`, description: article.description }
}

export default async function ArticlePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params
  const article = getArticle(slug)
  if (!article) notFound()

  // Simple markdown to HTML: headers, bold, code blocks, paragraphs
  const html = article.content
    .replace(/```[\w]*\n([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2">$1</a>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/^(?!<[hupla])/gm, '')

  return (
    <div style={{ maxWidth: '780px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '32px', paddingBottom: '24px', borderBottom: B }}>
        <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#10b981', marginBottom: '10px' }}>
          {article.category} · {article.keyword}
        </div>
        <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#f5f5f7', lineHeight: 1.3 }}>{article.title}</h1>
      </div>
      <div
        className="article-body"
        dangerouslySetInnerHTML={{ __html: `<p>${html}</p>` }}
      />
      <div style={{ marginTop: '48px', paddingTop: '24px', borderTop: B }}>
        <a href="/learn" style={{ fontSize: '12px', color: '#10b981', textDecoration: 'none', fontFamily: 'monospace' }}>← Back to Learn</a>
      </div>
    </div>
  )
}
