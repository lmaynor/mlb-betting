import { getOverallStats, getSummaryStats } from '@/lib/db'
import { upsertArticle } from '@/lib/learn-db'

export interface ArticleSpec {
  slug:    string
  title:   string
  keyword: string
  outline: string[]
  relatedTools:    string[]
  relatedPicks:    string[]
  relatedArticles: string[]
}

export const ARTICLE_SPECS: ArticleSpec[] = [
  {
    slug:    'what-is-nrfi',
    title:   'What is NRFI Betting? Complete Guide',
    keyword: 'what is nrfi betting',
    outline: [
      'Definition: No Run First Inning',
      'Why NRFI bets exist (starting pitcher dominance)',
      'How books price NRFI lines',
      'Key factors: starter quality, umpire K-rate, park, weather',
      'NRFI vs YRFI strategy',
      'How Beezy models NRFI probability',
      'FAQ',
    ],
    relatedTools:    ['/tools/nrfi-conditions', '/tools/odds-calculator'],
    relatedPicks:    ['/picks/mlb/nrfi'],
    relatedArticles: ['kelly-criterion', 'implied-probability'],
  },
  {
    slug:    'kelly-criterion',
    title:   'Kelly Criterion for Sports Betting',
    keyword: 'kelly criterion sports betting',
    outline: [
      'What is the Kelly Criterion',
      'The Kelly formula explained',
      'Full Kelly vs Half Kelly vs fractional',
      'Why Kelly requires accurate probabilities',
      'Practical bankroll management with Kelly',
      'How Beezy uses Kelly for stake sizing',
      'FAQ',
    ],
    relatedTools:    ['/tools/kelly-calculator'],
    relatedPicks:    [],
    relatedArticles: ['implied-probability', 'remove-vig'],
  },
  {
    slug:    'implied-probability',
    title:   'How to Calculate Implied Probability from Odds',
    keyword: 'implied probability calculator',
    outline: [
      'What is implied probability',
      'Converting American odds to probability',
      'Converting decimal and fractional odds',
      'The problem with raw implied probability (vig)',
      'Using implied probability to find edges',
      'FAQ',
    ],
    relatedTools:    ['/tools/odds-calculator'],
    relatedPicks:    [],
    relatedArticles: ['remove-vig', 'kelly-criterion'],
  },
  {
    slug:    'remove-vig',
    title:   'How to Remove Vig from Sports Betting Odds',
    keyword: 'how to remove vig',
    outline: [
      'What is vig / juice / overround',
      'Why vig matters to long-run profitability',
      'Step-by-step vig removal formula',
      'Additive vs multiplicative vig removal',
      'Applying vig removal to find true lines',
      'FAQ',
    ],
    relatedTools:    ['/tools/odds-calculator'],
    relatedPicks:    [],
    relatedArticles: ['implied-probability', 'kelly-criterion'],
  },
  {
    slug:    'mlb-strikeout-props',
    title:   'MLB Strikeout Props Betting Guide',
    keyword: 'mlb strikeout props',
    outline: [
      'Introduction to K props',
      'Key metrics: SwStr%, Zone%, Opp K%',
      'Starter vs. reliever considerations',
      'Park and weather factors',
      'Line shopping for K props',
      'How Beezy K model works',
      'FAQ',
    ],
    relatedTools:    ['/tools/pitcher-matchups'],
    relatedPicks:    ['/picks/mlb/k'],
    relatedArticles: ['implied-probability', 'f5-betting'],
  },
  {
    slug:    'f5-betting',
    title:   'First 5 Innings (F5) Betting Explained',
    keyword: 'f5 betting mlb',
    outline: [
      'What is an F5 bet',
      'F5 vs full game betting differences',
      'Starter quality and F5 pricing',
      'Bullpen fatigue irrelevance for F5',
      'F5 totals vs F5 spreads',
      'How Beezy F5 model identifies edges',
      'FAQ',
    ],
    relatedTools:    ['/tools/odds-calculator'],
    relatedPicks:    ['/picks/mlb/f5'],
    relatedArticles: ['what-is-nrfi', 'implied-probability'],
  },
  {
    slug:    'home-run-props',
    title:   'How to Bet MLB Home Run Props',
    keyword: 'mlb home run props',
    outline: [
      'HR prop market overview',
      'Batter Statcast metrics: launch angle, exit velocity, barrel rate',
      'Pitcher HR/9 and fly ball rate',
      'Park factors for HR props',
      'Platoon splits and lineup protection',
      'How Beezy HR model works',
      'FAQ',
    ],
    relatedTools:    ['/tools/edge-finder'],
    relatedPicks:    ['/picks/mlb/hr'],
    relatedArticles: ['implied-probability', 'kelly-criterion'],
  },
  {
    slug:    'xgboost-sports-betting',
    title:   'How Machine Learning Models Beat the Books',
    keyword: 'machine learning sports betting',
    outline: [
      'Why traditional handicapping falls short',
      'How XGBoost works (non-technical)',
      'Feature engineering for sports models',
      'Walk-forward cross-validation and leakage',
      'Model calibration and Kelly sizing',
      'Beezy\'s approach to model building',
      'FAQ',
    ],
    relatedTools:    ['/tools/edge-finder'],
    relatedPicks:    ['/picks/mlb'],
    relatedArticles: ['kelly-criterion', 'nrfi-strategy'],
  },
  {
    slug:    'nrfi-strategy',
    title:   'NRFI Betting Strategy: A Data-Driven Approach',
    keyword: 'nrfi strategy',
    outline: [
      'NRFI strategy vs gut-feel picking',
      'Building a NRFI model input checklist',
      'Umpire data: K-rate and called strike%',
      'Weather and park factor impact on NRFI',
      'Bankroll management for NRFI',
      'Beezy NRFI system performance breakdown',
      'FAQ',
    ],
    relatedTools:    ['/tools/nrfi-conditions', '/tools/kelly-calculator'],
    relatedPicks:    ['/picks/mlb/nrfi'],
    relatedArticles: ['what-is-nrfi', 'kelly-criterion'],
  },
  {
    slug:    'line-movement',
    title:   'Understanding Line Movement in Sports Betting',
    keyword: 'line movement sports betting',
    outline: [
      'What causes line movement',
      'Sharp vs. public money',
      'Steam moves and reverse line movement',
      'Using line movement as a signal',
      'How Beezy monitors opening vs closing lines',
      'FAQ',
    ],
    relatedTools:    ['/tools/edge-finder'],
    relatedPicks:    ['/picks/mlb'],
    relatedArticles: ['implied-probability', 'remove-vig'],
  },
]

function buildPrompt(spec: ArticleSpec, stats: {
  overall:  { total_bets: string; win_rate: string; roi: string }
  bySystem: Array<{ system: string; win_rate: number; roi: number; total_bets: number }>
}): string {
  const systemLines = stats.bySystem
    .map(s => `- ${s.system}: ${s.total_bets} bets, ${s.win_rate}% win rate, ${s.roi > 0 ? '+' : ''}${s.roi}% ROI`)
    .join('\n')

  const toolLinks = spec.relatedTools.map(t => `[${t.split('/').pop()}](${t})`).join(', ')
  const pickLinks = spec.relatedPicks.map(t => `[${t.split('/').pop()}](${t})`).join(', ')
  const articleLinks = spec.relatedArticles.map(s => `[/learn/${s}](/learn/${s})`).join(', ')

  return `You are writing an article for Beezy.VIP, a sports betting analytics site backed by machine learning models.

BRAND VOICE:
- Confident, data-forward, transparent
- Short sentences. No fluff. No superlatives.
- Show real numbers. Acknowledge uncertainty.
- Never say "premium" or "exclusive"
- Tone: Bloomberg Terminal meets sharp bettor

ARTICLE SPEC:
Title: ${spec.title}
Primary Keyword: "${spec.keyword}"
Target Length: 1,200–1,600 words

OUTLINE (cover all sections):
${spec.outline.map((s, i) => `${i + 1}. ${s}`).join('\n')}

LIVE BEEZY MODEL STATS (cite these where relevant):
Overall: ${stats.overall.total_bets} settled bets, ${stats.overall.win_rate}% win rate, ${stats.overall.roi}% ROI
${systemLines}

INTERNAL LINKS TO INCLUDE:
- Tools: ${toolLinks || 'none'}
- Picks: ${pickLinks || 'none'}
- Related articles: ${articleLinks}

FORMAT RULES:
- Return clean Markdown only
- H1 = article title (include primary keyword)
- H2 for each main section
- Use ## FAQ at the end with 3–5 Q&A pairs
- Bold key terms on first use
- Include a data table where it adds value
- End with a CTA block linking to the most relevant tool or picks page
- Do NOT include the word "premium", "exclusive", or excessive hype
- Monospace code blocks for any formulas (e.g. \`Kelly% = (bp - q) / b\`)

Return ONLY the markdown article. No preamble, no commentary.`
}

export async function generateArticle(spec: ArticleSpec): Promise<void> {
  // Fetch live stats
  let statsData = {
    overall:  { total_bets: '87', win_rate: '58.2', roi: '+11.4' },
    bySystem: [] as Array<{ system: string; win_rate: number; roi: number; total_bets: number }>,
  }

  try {
    const [overall, bySystem] = await Promise.all([
      getOverallStats(),
      getSummaryStats(),
    ])
    statsData = {
      overall: {
        total_bets: overall.total_bets,
        win_rate:   overall.win_rate,
        roi:        overall.roi,
      },
      bySystem: bySystem.map(s => ({
        system:     s.system,
        win_rate:   parseFloat(String(s.win_rate)),
        roi:        parseFloat(String(s.roi)),
        total_bets: parseInt(String(s.total_bets)),
      })),
    }
  } catch {
    // Use seed data
  }

  const prompt = buildPrompt(spec, statsData)

  // Call Anthropic API
  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method:  'POST',
    headers: {
      'Content-Type':      'application/json',
      'x-api-key':         process.env.ANTHROPIC_API_KEY!,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model:      'claude-sonnet-4-20250514',
      max_tokens: 4096,
      messages:   [{ role: 'user', content: prompt }],
    }),
  })

  if (!response.ok) {
    throw new Error(`Anthropic API error: ${response.status} ${await response.text()}`)
  }

  const data = await response.json()
  const body = data.content
    .filter((b: { type: string }) => b.type === 'text')
    .map((b: { text: string }) => b.text)
    .join('')

  // Extract meta description (first non-heading paragraph, truncated)
  const metaMatch = body.match(/^(?!#)[^\n]{60,}$/m)
  const metaDesc  = metaMatch
    ? metaMatch[0].substring(0, 155) + (metaMatch[0].length > 155 ? '…' : '')
    : `${spec.title} — Beezy.VIP`

  await upsertArticle({
    slug:           spec.slug,
    title:          spec.title,
    body_mdx:       body,
    meta_desc:      metaDesc,
    keyword:        spec.keyword,
    stats_snapshot: statsData as unknown as Record<string, unknown>,
  })
}

export async function generateAllArticles(): Promise<{
  success: string[]
  failed:  Array<{ slug: string; error: string }>
}> {
  const success: string[] = []
  const failed:  Array<{ slug: string; error: string }> = []

  for (const spec of ARTICLE_SPECS) {
    try {
      await generateArticle(spec)
      success.push(spec.slug)
      // Rate limit: 1 article per 2 seconds
      await new Promise(r => setTimeout(r, 2000))
    } catch (err) {
      failed.push({ slug: spec.slug, error: String(err) })
    }
  }

  return { success, failed }
}
