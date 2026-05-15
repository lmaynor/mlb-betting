import { NextRequest, NextResponse } from 'next/server'
import { apiGetPicks } from '@/lib/betting-api'

export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams

  try {
    const picks = await apiGetPicks({
      system: sp.get('system') ?? undefined,
      date:   sp.get('date')   ?? undefined,
      status: sp.get('status') ?? undefined,
      limit:  sp.get('limit')  ? parseInt(sp.get('limit')!) : 50,
      offset: sp.get('offset') ? parseInt(sp.get('offset')!) : 0,
    })
    return NextResponse.json({ picks, count: picks.length })
  } catch (err) {
    console.error('/api/picks error:', err)
    return NextResponse.json({ error: 'Failed to fetch picks' }, { status: 500 })
  }
}
