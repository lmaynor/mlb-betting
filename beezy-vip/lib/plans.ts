/** Plan copy lives in one object so the tests can pin prices and the annual-savings arithmetic. Kept out of the 'use client' pricing component so server code (JSON-LD, About) can import it. */
export const PLANS = {
  single: { price: 19, period: '/mo' },
  monthly: { price: 39, period: '/mo' },
  annual: { price: 299, period: '/yr' },
} as const
export const ANNUAL_PER_MONTH = PLANS.annual.price / 12                                   // 24.92
export const ANNUAL_SAVINGS_PCT = Math.round((1 - PLANS.annual.price / (PLANS.monthly.price * 12)) * 100)   // 36

