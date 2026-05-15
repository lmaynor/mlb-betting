import { NextRequest, NextResponse } from 'next/server'
import { americanToImpliedProb, removeVig, probToAmerican, formatProb, formatOdds } from '@/lib/odds'

export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams
  const o1 = parseFloat(sp.get('odds1') ?? '')
  const o2 = parseFloat(sp.get('odds2') ?? '')

  if (isNaN(o1) || isNaN(o2)) {
    return NextResponse.json({ error: 'odds1 and odds2 are required' }, { status: 400 })
  }

  const prob1 = americanToImpliedProb(o1)
  const prob2 = americanToImpliedProb(o2)
  const { fair1, fair2, vig } = removeVig(prob1, prob2)

  return NextResponse.json({
    side1: {
      inputOdds:       o1,
      impliedProb:     formatProb(prob1),
      fairProb:        formatProb(fair1),
      fairOdds:        formatOdds(probToAmerican(fair1)),
    },
    side2: {
      inputOdds:       o2,
      impliedProb:     formatProb(prob2),
      fairProb:        formatProb(fair2),
      fairOdds:        formatOdds(probToAmerican(fair2)),
    },
    vig: `${vig.toFixed(2)}%`,
  })
}
