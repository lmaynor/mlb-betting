import { NextRequest, NextResponse } from 'next/server'
import { ensureLearnTable }          from '@/lib/learn-db'

function isAuthorized(req: NextRequest): boolean {
  const key = req.headers.get('x-admin-key') ?? req.nextUrl.searchParams.get('key')
  return key === process.env.ADMIN_SECRET_KEY
}

// This route only manages tables the site owns (learn_articles).
// The bets table is owned by lmaynor/mlb-betting -- never alter it here.
export async function POST(req: NextRequest) {
  if (!isAuthorized(req)) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  try {
    await ensureLearnTable()
    return NextResponse.json({ applied: ['create_learn_articles'], skipped: [], failed: [] })
  } catch (err) {
    console.error('[migrate] error:', err)
    return NextResponse.json({ error: String(err) }, { status: 500 })
  }
}

export async function GET(req: NextRequest) {
  if (!isAuthorized(req)) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }
  return NextResponse.json({ migrations: ['create_learn_articles'] })
}
