import { Pool } from 'pg'

// learn_articles lives on Vercel Postgres (separate from the betting Cloud SQL DB).
// DSN comes from LEARN_DATABASE_URL (set in Vercel dashboard after provisioning
// a Vercel Postgres database). Falls back to DATABASE_URL for local dev if unset.
const globalLearnPool = global as typeof global & { _learnPool?: Pool }

function getLearnPool(): Pool {
  if (!globalLearnPool._learnPool) {
    globalLearnPool._learnPool = new Pool({
      connectionString: process.env.LEARN_DATABASE_URL ?? process.env.DATABASE_URL,
      ssl: process.env.NODE_ENV === 'production' ? { rejectUnauthorized: false } : false,
      max: 5,
      idleTimeoutMillis: 30_000,
    })
  }
  return globalLearnPool._learnPool
}

async function learnQuery<T = Record<string, unknown>>(
  sql: string,
  params?: unknown[]
): Promise<T[]> {
  const pool = getLearnPool()
  const result = await pool.query(sql, params)
  return result.rows as T[]
}

export interface LearnArticle {
  slug:           string
  title:          string
  body_mdx:       string
  meta_desc:      string
  keyword:        string
  generated_at:   string
  stats_snapshot: Record<string, unknown>
}

// DB setup -- run once via POST /api/db/migrate
export async function ensureLearnTable() {
  await learnQuery(`
    CREATE TABLE IF NOT EXISTS learn_articles (
      slug            TEXT PRIMARY KEY,
      title           TEXT NOT NULL,
      body_mdx        TEXT NOT NULL,
      meta_desc       TEXT NOT NULL,
      keyword         TEXT NOT NULL,
      generated_at    TIMESTAMPTZ DEFAULT NOW(),
      stats_snapshot  JSONB DEFAULT '{}'
    )
  `)
}

export async function getArticle(slug: string): Promise<LearnArticle | null> {
  const rows = await learnQuery<LearnArticle>(
    `SELECT * FROM learn_articles WHERE slug = $1`,
    [slug]
  )
  return rows[0] ?? null
}

export async function getAllArticles(): Promise<Pick<LearnArticle, 'slug' | 'title' | 'meta_desc' | 'keyword' | 'generated_at'>[]> {
  return learnQuery(
    `SELECT slug, title, meta_desc, keyword, generated_at
     FROM learn_articles
     ORDER BY generated_at DESC`
  )
}

export async function upsertArticle(article: Omit<LearnArticle, 'generated_at'>) {
  await learnQuery(`
    INSERT INTO learn_articles (slug, title, body_mdx, meta_desc, keyword, stats_snapshot, generated_at)
    VALUES ($1, $2, $3, $4, $5, $6, NOW())
    ON CONFLICT (slug) DO UPDATE SET
      title          = EXCLUDED.title,
      body_mdx       = EXCLUDED.body_mdx,
      meta_desc      = EXCLUDED.meta_desc,
      keyword        = EXCLUDED.keyword,
      stats_snapshot = EXCLUDED.stats_snapshot,
      generated_at   = NOW()
  `, [
    article.slug,
    article.title,
    article.body_mdx,
    article.meta_desc,
    article.keyword,
    JSON.stringify(article.stats_snapshot),
  ])
}
