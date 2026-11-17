/**
 * Blackjack for the contest's hidden lifeline: a TypeScript mirror of contest/blackjack.py and the cap in contest/lifeline.py, used by
 * SAMPLE mode (the page plays the hand itself). The real table runs on the server, which keeps the deck and the hole card; the browser
 * only ever receives `viewOf(hand)`. Same house rules: one fresh deck per hand, dealer peeks, blackjack pays 3 to 2, dealer stands on all
 * 17s, hit / stand / double on two cards, no splits, ties push.
 */
export type Outcome = 'blackjack' | 'win' | 'push' | 'lose' | 'bust'
export interface Hand { deck: string[]; player: string[]; dealer: string[]; stake: number; doubled: boolean; done: boolean; outcome: Outcome | null }
const RANKS = '23456789TJQKA'
const SUITS = 'SHDC'

export function newDeck(rand: () => number = Math.random): string[] {
  const d = [...RANKS].flatMap(r => [...SUITS].map(s => r + s))
  for (let i = d.length - 1; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [d[i], d[j]] = [d[j], d[i]] }
  return d
}

export function handValue(cards: readonly string[]): { total: number; soft: boolean } {
  let total = 0
  for (const c of cards) total += 'TJQK'.includes(c[0]) ? 10 : c[0] === 'A' ? 1 : Number(c[0])
  return cards.some(c => c[0] === 'A') && total + 10 <= 21 ? { total: total + 10, soft: true } : { total, soft: false }
}
export const isBlackjack = (cards: readonly string[]) => cards.length === 2 && handValue(cards).total === 21
export const totalStake = (h: Hand) => h.stake * (h.doubled ? 2 : 1)
export const canDouble = (h: Hand) => !h.done && !h.doubled && h.player.length === 2

export function deal(stake: number, deck?: readonly string[], rand?: () => number): Hand {
  const d = deck ? [...deck] : newDeck(rand)
  const h: Hand = { deck: d, player: [], dealer: [], stake, doubled: false, done: false, outcome: null }
  h.player.push(d.shift()!); h.dealer.push(d.shift()!); h.player.push(d.shift()!); h.dealer.push(d.shift()!)
  const p = isBlackjack(h.player), x = isBlackjack(h.dealer)
  if (p || x) { h.done = true; h.outcome = p && x ? 'push' : p ? 'blackjack' : 'lose' }
  return h
}

function dealerPlays(h: Hand): void {
  while (handValue(h.dealer).total < 17) h.dealer.push(h.deck.shift()!)
  const p = handValue(h.player).total, d = handValue(h.dealer).total
  h.outcome = d > 21 || p > d ? 'win' : p === d ? 'push' : 'lose'
  h.done = true
}
const live = (h: Hand) => { if (h.done) throw new Error('HAND_OVER') }

export function hit(h: Hand): Hand {
  live(h)
  h.player.push(h.deck.shift()!)
  const t = handValue(h.player).total
  if (t > 21) { h.done = true; h.outcome = 'bust' } else if (t === 21) dealerPlays(h)
  return h
}
export function stand(h: Hand): Hand { live(h); dealerPlays(h); return h }
export function doubleDown(h: Hand): Hand {
  live(h)
  if (!canDouble(h)) throw new Error('CANNOT_DOUBLE')
  h.doubled = true
  h.player.push(h.deck.shift()!)
  if (handValue(h.player).total > 21) { h.done = true; h.outcome = 'bust' } else dealerPlays(h)
  return h
}

/** Total returned to the player (stake included) for a finished hand, before the lifeline cap. */
export function payout(h: Hand): number {
  if (!h.done || !h.outcome) throw new Error('HAND_OPEN')
  const s = totalStake(h)
  return { blackjack: s + Math.floor((s * 3) / 2), win: 2 * s, push: s, lose: 0, bust: 0 }[h.outcome]
}

/** What the lifeline credits: winnings may never lift the bankroll above the threshold (mirror of lifeline._settle). */
export function creditedAfterCap(h: Hand, bankrollBefore: number, thresholdCents: number): { payout: number; credited: number; capped: boolean } {
  const p = payout(h)
  const bankrollNow = bankrollBefore - totalStake(h)
  const cap = Math.max(0, Math.max(bankrollBefore, thresholdCents) - bankrollNow)
  const credited = Math.min(p, cap)
  return { payout: p, credited, capped: credited < p }
}

/** What the player may see: the dealer's UP card only until the hand is over, never the deck. */
export function viewOf(h: Hand) {
  const dealer = h.done ? h.dealer : [h.dealer[0], '??']
  return { player: [...h.player], dealer, playerTotal: handValue(h.player).total, dealerTotal: handValue(h.done ? h.dealer : h.dealer.slice(0, 1)).total,
    stake: totalStake(h), doubled: h.doubled, done: h.done, outcome: h.outcome, canDouble: canDouble(h) }
}

const RANK_WORDS: Record<string, string> = { T: '10', J: 'Jack', Q: 'Queen', K: 'King', A: 'Ace' }
const SUIT_WORDS: Record<string, string> = { S: 'spades', H: 'hearts', D: 'diamonds', C: 'clubs' }
export const cardLabel = (c: string): string => c === '??' ? 'Face-down card' : `${RANK_WORDS[c[0]] ?? c[0]} of ${SUIT_WORDS[c[1]]}`
