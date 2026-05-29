import { ARTICLES } from '@/lib/articles-static'

const BASE = 'https://beezy.fyi'

const STATIC_ROUTES = [
  { path: '/',               priority: '1.0', changefreq: 'daily'   },
  { path: '/picks',          priority: '0.9', changefreq: 'daily'   },
  { path: '/picks/mlb',      priority: '0.9', changefreq: 'daily'   },
  { path: '/picks/mlb/nrfi', priority: '0.8', changefreq: 'daily'   },
  { path: '/picks/mlb/hr',   priority: '0.8', changefreq: 'daily'   },
  { path: '/picks/mlb/f5',   priority: '0.8', changefreq: 'daily'   },
  { path: '/picks/mlb/k',    priority: '0.8', changefreq: 'daily'   },
  { path: '/picks/mlb/outs', priority: '0.8', changefreq: 'daily'   },
  { path: '/results',        priority: '0.8', changefreq: 'daily'   },
  { path: '/tools',          priority: '0.7', changefreq: 'weekly'  },
  { path: '/tools/odds-calculator',  priority: '0.7', changefreq: 'monthly' },
  { path: '/tools/kelly-calculator', priority: '0.7', changefreq: 'monthly' },
  { path: '/models',         priority: '0.6', changefreq: 'weekly'  },
  { path: '/learn',          priority: '0.7', changefreq: 'weekly'  },
]

export async function GET() {
  const learnRoutes = ARTICLES.map(a => ({
    path:       `/learn/${a.slug}`,
    priority:   '0.6',
    changefreq: 'monthly',
  }))

  const allRoutes = [...STATIC_ROUTES, ...learnRoutes]
  const now       = new Date().toISOString()

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${allRoutes.map(r => `  <url>
    <loc>${BASE}${r.path}</loc>
    <lastmod>${now}</lastmod>
    <changefreq>${r.changefreq}</changefreq>
    <priority>${r.priority}</priority>
  </url>`).join('\n')}
</urlset>`

  return new Response(xml, {
    headers: {
      'Content-Type': 'application/xml',
      'Cache-Control': 's-maxage=3600',
    },
  })
}
