import { NextRequest, NextResponse } from 'next/server'
import { generateArticle, generateAllArticles, ARTICLE_SPECS } from '@/lib/article-generator'
import { revalidatePath } from 'next/cache'

// Protect with secret key
function isAuthorized(req: NextRequest): boolean {
  const key = req.headers.get('x-admin-key') ?? req.nextUrl.searchParams.get('key')
  return key === process.env.ADMIN_SECRET_KEY
}

export async function POST(req: NextRequest) {
  if (!isAuthorized(req)) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const body = await req.json().catch(() => ({}))
  const { slug, all } = body as { slug?: string; all?: boolean }

  try {
    if (all) {
      // Generate all articles
      const result = await generateAllArticles()

      // Revalidate all learn pages
      revalidatePath('/learn', 'layout')

      return NextResponse.json({
        message: `Generated ${result.success.length}/${ARTICLE_SPECS.length} articles`,
        success: result.success,
        failed:  result.failed,
      })
    }

    if (slug) {
      const spec = ARTICLE_SPECS.find(s => s.slug === slug)
      if (!spec) {
        return NextResponse.json({ error: `Unknown slug: ${slug}` }, { status: 400 })
      }

      await generateArticle(spec)
      revalidatePath(`/learn/${slug}`)
      revalidatePath('/learn')

      return NextResponse.json({ message: `Generated: ${slug}` })
    }

    return NextResponse.json(
      { error: 'Provide slug or all=true', available: ARTICLE_SPECS.map(s => s.slug) },
      { status: 400 }
    )
  } catch (err) {
    console.error('Article generation error:', err)
    return NextResponse.json({ error: String(err) }, { status: 500 })
  }
}

// GET: list available articles and specs
export async function GET(req: NextRequest) {
  if (!isAuthorized(req)) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  return NextResponse.json({
    articles: ARTICLE_SPECS.map(s => ({ slug: s.slug, title: s.title, keyword: s.keyword })),
  })
}
