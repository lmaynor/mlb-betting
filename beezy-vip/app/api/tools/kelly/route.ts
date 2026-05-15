import { NextRequest, NextResponse } from 'next/server'
import { kellyStake } from '@/lib/odds'

export async function GET(req: NextRequest) {
  const sp       = req.nextUrl.searchParams
  const bankroll = parseFloat(sp.get('bankroll') ?? '')
  const odds     = parseFloat(sp.get('odds')     ?? '')
  const prob     = parseFloat(sp.get('prob')     ?? '')

  if (isNaN(bankroll) || isNaN(odds) || isNaN(prob)) {
    return NextResponse.json(
      { error: 'bankroll, odds, and prob are required' },
      { status: 400 }
    )
  }

  if (prob <= 0 || prob >= 1) {
    return NextResponse.json({ error: 'prob must be between 0 and 1' }, { status: 400 })
  }

  const result = kellyStake(bankroll, odds, prob)
  return NextResponse.json(result)
}
