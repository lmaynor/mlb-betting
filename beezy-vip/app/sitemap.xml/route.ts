import { buildSitemapXml } from '@/lib/sitemap'

export async function GET() {
  return new Response(buildSitemapXml(), {
    headers: {
      'Content-Type': 'application/xml',
      'Cache-Control': 's-maxage=3600',
    },
  })
}
