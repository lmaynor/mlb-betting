import { ARTICLES } from '@/lib/articles-static'
import { PICK_SYSTEMS } from '@/lib/pick-systems'
import { SPORT_SLUGS } from '@/lib/sports'

const BASE = 'https://beezy.fyi'

const STATIC_ROUTES = [
  { path: '/',               priority: '1.0', changefreq: 'daily'   },
  { path: '/picks',          priority: '0.9', changefreq: 'daily'   },
  { path: '/picks/games',   priority: '0.7', changefreq: 'daily'   },
  { path: '/picks/history',  priority: '0.6', changefreq: 'daily'   },
  { path: '/ev',             priority: '0.9', changefreq: 'hourly'  },
  { path: '/edge',           priority: '0.7', changefreq: 'hourly'  },
  { path: '/analytics',      priority: '0.7', changefreq: 'daily'   },
  { path: '/cheat-sheet',    priority: '0.5', changefreq: 'daily'   },
  { path: '/pricing',        priority: '0.5', changefreq: 'weekly'  },
  { path: '/tools/boost-ev', priority: '0.7', changefreq: 'monthly' },
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

  // Pick-system and model pages come from the registry so a new system is listed automatically.
  const sportRoutes = SPORT_SLUGS.flatMap(sport => [
    { path: `/picks/${sport}`, priority: '0.8', changefreq: 'daily' },
    { path: `/picks/${sport}/projections`, priority: '0.7', changefreq: 'daily' },
  ])
  const systemRoutes = PICK_SYSTEMS.map(s => ({ path: `/picks/${s.sport}/${s.slug}`, priority: '0.7', changefreq: 'daily' }))
  const allRoutes = [...STATIC_ROUTES, ...sportRoutes, ...systemRoutes, ...learnRoutes]
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
