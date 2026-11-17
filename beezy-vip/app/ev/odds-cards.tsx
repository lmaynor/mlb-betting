import { bookDisplay, SURFACE_CARD } from '@/lib/tokens'
import { fmtMoney, kellySizing } from '@/lib/ev-math'
import { formatOdds } from '@/lib/odds'
import { PlayerHeadshot } from '@/components/ui/sport-assets'
import { type HeadshotSport } from '@/lib/sport-assets'
import { BookLogo } from '@/components/ui/book-logo'
import { LockGlyph } from '@/components/ui/lock-glyph'
import { CardShell, CardHeader, CardPlayerRow } from '@/components/picks/card-shell'
import { BookComparisonBars } from '@/components/picks/book-comparison-bars'
import { STAT_LABEL, outlierQuoteState, type ArbRow, type LongshotRow, type MiddleResult, type OutlierRow, type ScreenRow } from '@/lib/odds-screen'
import { HAIR, GREEN, SKY } from './odds-constants'
import { startLabel, signed, usd, fairText } from './odds-format'
import { TierPill } from './odds-cells'

export function MobileEvRowCard({
  r,
  bankroll,
  onInspect,
}: {
  r: ScreenRow
  bankroll: number
  onInspect: () => void
}) {
  const d = r.implied > 0 ? 1 / r.implied : 1
  const size = r.pFair == null ? null : kellySizing(bankroll, r.pFair, d)
  const evColor = r.ev != null && r.ev > 0 ? GREEN : 'var(--color-text-secondary)'
  const sportSlug = r.row.game_key.split(':')[0] as HeadshotSport

  return (
    <div className="mb-2.5 flex flex-col gap-2.5 rounded-xl border border-separator-border bg-background-primary-default p-3 shadow-card">
      <div className="flex items-center gap-2.5">
        <span className="inline-flex size-[34px] shrink-0">
          <PlayerHeadshot sport={sportSlug} id={r.row.headshot_id} name={r.row.player} size={34} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold text-text-primary leading-[1.25] overflow-hidden text-ellipsis whitespace-nowrap">
            {r.row.player}
          </div>
          <div className="mono mt-0.5 text-mini text-text-secondary">
            {r.row.away} @ {r.row.home} &middot; {startLabel(r.row.commence)}
          </div>
        </div>
        <TierPill alt={r.row.is_alt === true} modalPoint={r.row.modal_point} />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 10px', background: 'var(--color-background-secondary-default)', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
        <div>
          <span className="text-[13px] font-semibold text-text-primary">
            {r.label} {STAT_LABEL[r.row.stat] ?? r.row.stat}
          </span>
          <div className="mono mt-0.5 text-[11px] text-text-secondary">
            Fair: {r.fairAmerican != null ? (r.fairAmerican > 0 ? `+${r.fairAmerican}` : `${r.fairAmerican}`) : '--'} ({r.nRef} bks)
          </div>
        </div>
        <div className="text-right">
          <span className="mono" style={{ fontSize: '13px', fontWeight: 600, color: evColor }}>
            {r.ev == null ? '--' : `${signed(r.ev * 100)}% EV`}
          </span>
          {size && size.dollars > 0 && (
            <div className="mono text-[11px] text-neutral-300 mt-0.5">
              Kelly {fmtMoney(size.dollars)}
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-[7px]">
          <BookLogo book={r.bestBook} size={22} />
          <div>
            <div className="text-compact font-semibold text-text-primary">
              {bookDisplay(r.bestBook).label}
            </div>
            <div className="mono" style={{ fontSize: '11px', color: GREEN, fontWeight: 600 }}>
              {r.side} {formatOdds(r.bestAmerican)}
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={onInspect}
          className="btn btn-ghost py-[5px] px-2.5 text-mini font-semibold"
         
        >
          Compare All Books ({r.row.books.length}) &rarr;
        </button>
      </div>
    </div>
  )
}

export function MobileArbCard({
  r,
  outlay,
  copied,
  onCopy,
  onInspect,
}: {
  r: ArbRow
  outlay: number
  copied: boolean
  onCopy: (r: ArbRow) => void
  onInspect: () => void
}) {
  const a = r.arb
  const k = outlay / 100
  const live = a.netArbPct > 0
  const profit = a.guaranteedProfitPer100 * k
  const sportSlug = r.row.game_key.split(':')[0] as HeadshotSport

  return (
    <div className="mb-2.5 flex flex-col gap-2.5 rounded-xl border border-separator-border bg-background-primary-default p-3 shadow-card">
      <div className="flex items-center gap-2.5">
        <span className="inline-flex size-[34px] shrink-0">
          <PlayerHeadshot sport={sportSlug} id={r.row.headshot_id} name={r.row.player} size={34} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold text-text-primary leading-[1.25] overflow-hidden text-ellipsis whitespace-nowrap">
            {r.row.player}
          </div>
          <div className="mono mt-0.5 text-mini text-text-secondary">
            {r.row.line} {STAT_LABEL[r.row.stat] ?? r.row.stat} &middot; {r.row.away} @ {r.row.home}
          </div>
        </div>
        <span
          className="mono"
          style={{
            fontSize: '11px',
            fontWeight: 600,
            padding: '2px 7px',
            borderRadius: '4px',
            background: live ? 'rgba(87, 242, 135, 0.12)' : 'rgba(56, 189, 248, 0.12)',
            color: live ? GREEN : SKY,
            border: `1px solid ${live ? 'rgba(87, 242, 135, 0.3)' : 'rgba(56, 189, 248, 0.3)'}`,
          }}
        >
          {signed(a.netArbPct, 2)}% {live ? 'Arb' : 'Hold'}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div style={{ padding: '8px', background: 'var(--color-background-secondary-default)', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
          <div className="flex items-center gap-1.5">
            <BookLogo book={a.bestOverBook} size={18} />
            <span className="text-mini font-semibold text-neutral-300">{bookDisplay(a.bestOverBook).label}</span>
          </div>
          <div className="mono mt-1 text-xs font-semibold text-text-primary">
            OVER {formatOdds(a.bestOverAm)}
          </div>
          <div className="mono mt-0.5 text-[11px] text-text-secondary">
            Stake: {usd(a.stakeOverPer100 * k)}
          </div>
        </div>

        <div style={{ padding: '8px', background: 'var(--color-background-secondary-default)', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
          <div className="flex items-center gap-1.5">
            <BookLogo book={a.bestUnderBook} size={18} />
            <span className="text-mini font-semibold text-neutral-300">{bookDisplay(a.bestUnderBook).label}</span>
          </div>
          <div className="mono mt-1 text-xs font-semibold text-text-primary">
            UNDER {formatOdds(a.bestUnderAm)}
          </div>
          <div className="mono mt-0.5 text-[11px] text-text-secondary">
            Stake: {usd(a.stakeUnderPer100 * k)}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', borderTop: `1px solid var(--color-border-table)`, paddingTop: '8px' }}>
        <span className="mono" style={{ fontSize: '11px', color: live ? GREEN : SKY, fontWeight: 600 }}>
          {profit >= 0 ? '+' : '-'}{usd(Math.abs(profit))} {live ? 'locked' : 'hold'} on {usd(outlay)}
        </span>
        <div className="flex gap-1.5">
          <button type="button" onClick={() => onCopy(r)} className="btn btn-ghost" style={{ padding: '4px 8px', fontSize: '11px' }}>
            {copied ? 'Copied!' : 'Copy'}
          </button>
          <button type="button" onClick={onInspect} className="btn btn-ghost py-1 px-2 text-[11px]">
            Books &rarr;
          </button>
        </div>
      </div>
    </div>
  )
}

export function MobileMiddleCard({
  r,
  outlay,
  copied,
  onCopy,
  onInspect,
}: {
  r: MiddleResult
  outlay: number
  copied: boolean
  onCopy: (r: MiddleResult) => void
  onInspect: () => void
}) {
  const k = outlay / 100
  const free = r.missPct >= 0
  const sportSlug = r.low.game_key.split(':')[0] as HeadshotSport

  return (
    <div className="mb-2.5 flex flex-col gap-2.5 rounded-xl border border-separator-border bg-background-primary-default p-3 shadow-card">
      <div className="flex items-center gap-2.5">
        <span className="inline-flex size-[34px] shrink-0">
          <PlayerHeadshot sport={sportSlug} id={r.low.headshot_id} name={r.low.player} size={34} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold text-text-primary leading-[1.25] overflow-hidden text-ellipsis whitespace-nowrap">
            {r.low.player}
          </div>
          <div className="mono mt-0.5 text-mini text-text-secondary">
            {STAT_LABEL[r.low.stat] ?? r.low.stat} &middot; {r.low.away} @ {r.low.home}
          </div>
        </div>
        <span
          className="mono"
          style={{
            fontSize: '11px',
            fontWeight: 600,
            padding: '2px 7px',
            borderRadius: '4px',
            background: free ? 'rgba(87, 242, 135, 0.12)' : 'rgba(56, 189, 248, 0.12)',
            color: free ? GREEN : SKY,
            border: `1px solid ${free ? 'rgba(87, 242, 135, 0.3)' : 'rgba(56, 189, 248, 0.3)'}`,
          }}
        >
          {r.gap % 1 === 0 ? r.gap : r.gap.toFixed(1)} PT WINDOW
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div style={{ padding: '8px', background: 'var(--color-background-secondary-default)', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
          <div className="flex items-center gap-1.5">
            <BookLogo book={r.overBook} size={18} />
            <span className="text-mini font-semibold text-neutral-300">{bookDisplay(r.overBook).label}</span>
          </div>
          <div className="mono mt-1 text-xs font-semibold text-text-primary">
            OVER {r.low.line} ({formatOdds(r.overAm)})
          </div>
          <div className="mono mt-0.5 text-[11px] text-text-secondary">
            Stake: {usd(r.stakeOverPer100 * k)}
          </div>
        </div>

        <div style={{ padding: '8px', background: 'var(--color-background-secondary-default)', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
          <div className="flex items-center gap-1.5">
            <BookLogo book={r.underBook} size={18} />
            <span className="text-mini font-semibold text-neutral-300">{bookDisplay(r.underBook).label}</span>
          </div>
          <div className="mono mt-1 text-xs font-semibold text-text-primary">
            UNDER {r.high.line} ({formatOdds(r.underAm)})
          </div>
          <div className="mono mt-0.5 text-[11px] text-text-secondary">
            Stake: {usd(r.stakeUnderPer100 * k)}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', borderTop: `1px solid var(--color-border-table)`, paddingTop: '8px' }}>
        <div className="mono text-[11px] text-text-secondary">
          Miss: {signed(r.missPct)}% &middot; Hit: {signed(r.hitPct)}%
        </div>
        <div className="flex gap-1.5">
          <button type="button" onClick={() => onCopy(r)} className="btn btn-ghost" style={{ padding: '4px 8px', fontSize: '11px' }}>
            {copied ? 'Copied!' : 'Copy'}
          </button>
          <button type="button" onClick={onInspect} className="btn btn-ghost py-1 px-2 text-[11px]">
            Books &rarr;
          </button>
        </div>
      </div>
    </div>
  )
}

export function MobileLongshotCard({
  r,
  onInspect,
}: {
  r: LongshotRow
  onInspect: () => void
}) {
  const m = r.m
  const sportSlug = r.row.game_key.split(':')[0] as HeadshotSport
  const over = r.side !== 'UNDER'
  const actionBook = over ? m.bestOverBook : m.bestUnderBook
  const actionAm = over ? m.bestOverAm : m.bestUnderAm
  const actionEv = r.bestEv
  const fairProb = over ? m.fairOverProb : m.fairUnderProb
  const sideLabel = over ? 'Over' : 'Under'

  return (
    <div className="mb-2.5 flex flex-col gap-2.5 rounded-xl border border-separator-border bg-background-primary-default p-3 shadow-card">
      <div className="flex items-center gap-2.5">
        <span className="inline-flex size-[34px] shrink-0">
          <PlayerHeadshot sport={sportSlug} id={r.row.headshot_id} name={r.row.player} size={34} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold text-text-primary leading-[1.25] overflow-hidden text-ellipsis whitespace-nowrap">
            {r.row.player}
          </div>
          <div className="mono mt-0.5 text-mini text-text-secondary">
            {r.row.line} {STAT_LABEL[r.row.stat] ?? r.row.stat} &middot; {r.row.away} @ {r.row.home}
          </div>
        </div>
        <TierPill alt={r.row.is_alt === true} modalPoint={r.row.modal_point} />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 10px', background: 'var(--color-background-secondary-default)', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
        <div>
          <span className="text-compact font-semibold text-text-primary">
            Best Play: {sideLabel} {actionAm != null ? formatOdds(actionAm) : '--'}
          </span>
          <div className="mono mt-0.5 text-[11px] text-text-secondary">
            Fair: {fairText(fairProb)} {m.fairSource === 'curve' ? '(EST. CURVE)' : `(${m.nRef} BKS)`}
          </div>
        </div>
        <div className="text-right">
          <span className="mono" style={{ fontSize: '13px', fontWeight: 600, color: actionEv != null && actionEv > 0 ? GREEN : 'var(--color-text-secondary)' }}>
            {actionEv != null ? `${signed(actionEv)}% EV` : '--'}
          </span>
          {actionBook && (
            <div className="text-[11px] text-neutral-300 mt-0.5">
              @ {bookDisplay(actionBook).label}
            </div>
          )}
        </div>
      </div>

      <div className="flex justify-end">
        <button
          type="button"
          onClick={onInspect}
          className="btn btn-ghost py-[5px] px-2.5 text-mini font-semibold"
         
        >
          Compare All Books ({r.row.books.length}) &rarr;
        </button>
      </div>
    </div>
  )
}
export function OutlierCard({
  item,
  isLocked,
  onReveal,
}: {
  item: OutlierRow
  isLocked: boolean
  onReveal: () => void
}) {
  const sport = item.row.game_key.split(':')[0]
  const evBadge = item.trueEvPct != null
    ? `${signed(item.trueEvPct)}% EV \u00b7 +${item.payoutPremiumPct.toFixed(1)}% vs Mkt`
    : `+${item.payoutPremiumPct.toFixed(1)}% vs Mkt`

  const { lagMin, unmoved } = outlierQuoteState(item)
  const signedFair = item.fairAmerican != null
    ? (item.fairAmerican > 0 ? `+${item.fairAmerican}` : `${item.fairAmerican}`)
    : null

  return (
    <CardShell style={{ padding: '16px', gap: '12px' }}>
      {/* Row 1: Matchup & Dislocation Pill */}
      <CardHeader
        left={
          <div className="flex items-center gap-1.5">
            <span className="mono" style={{ fontSize: '11px', fontWeight: 600, padding: '2px 6px', borderRadius: '4px', background: 'var(--color-background-tertiary-default)', color: 'var(--color-neutral-400)', border: `1px solid ${HAIR}` }}>
              {sport.toUpperCase()}
            </span>
            <span className="mono text-[12px] text-neutral-300">
              {item.row.away} @ {item.row.home} &middot; {startLabel(item.row.commence)}
            </span>
          </div>
        }
        right={
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span
              className="mono"
              style={{
                fontSize: '11px',
                fontWeight: 600,
                color: '#f59e0b',
                background: 'rgba(245,158,11,0.08)',
                border: '1px solid rgba(245,158,11,0.25)',
                padding: '3px 8px',
                borderRadius: '4px',
              }}
            >
              {'FORWARD SHADOW \u00b7 UNGRADED'}
            </span>
            <div className="mono" style={{ fontSize: '11px', fontWeight: 600, color: GREEN, background: 'rgba(87,242,135,0.08)', padding: '3px 8px', borderRadius: '4px', border: '1px solid rgba(87,242,135,0.25)' }}>
              {evBadge}
            </div>
          </div>
        }
      />

      {/* Row 2: Player, Prop Line & Giant Outlier Price */}
      <CardPlayerRow
        sport={sport}
        headshotId={item.row.headshot_id}
        name={item.row.player}
        headshotSize={36}
        sub={
          <div className="mono text-compact text-neutral-400 flex items-center gap-1.5 mt-[3px]">
            <span>{item.side === 'OVER' ? 'O' : 'U'} {item.row.line} {STAT_LABEL[item.row.stat] ?? item.row.stat}</span>
            {item.row.is_alt === true && (
              <span className="text-micro font-semibold py-0 px-1 rounded-[3px] border border-border-button-hover text-neutral-400">[ALT]</span>
            )}
          </div>
        }
        right={
          <>
            <div className="mono" style={{ fontSize: '24px', fontWeight: 600, color: GREEN, letterSpacing: '-0.02em', lineHeight: 1 }}>
              {item.outlierAmerican > 0 ? `+${item.outlierAmerican}` : `${item.outlierAmerican}`}
            </div>
            <div className="mono text-micro text-text-secondary mt-[3px]">Outlier price</div>
            {signedFair != null && (
              <div className="mono text-micro text-text-secondary mt-[2px]">
                {item.fairSource === 'consensus' ? `Cons fair ${signedFair}` : `Curve fair ${signedFair}`}
              </div>
            )}
          </>
        }
      />

      {/* Row 3: Target Book / Reveal Control */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', background: 'var(--color-background-secondary-default)', borderRadius: '6px', border: `1px solid ${HAIR}` }}>
        {isLocked ? (
          <>
            <div className="flex items-center gap-[7px]">
              <LockGlyph size={13} color="var(--color-neutral-400)" />
              <span className="text-compact font-semibold text-neutral-400">Book hidden (Pro)</span>
              <span className="mono text-[11px] text-text-secondary">({item.nPeers} peer books)</span>
            </div>
            <button onClick={onReveal} className="btn btn-ghost" style={{ padding: '3px 9px', fontSize: '11px', color: GREEN, borderColor: 'rgba(87,242,135,0.3)' }}>
              See Pro &rarr;
            </button>
          </>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <BookLogo book={item.outlierBook} size={22} />
              <span className="text-[13px] font-semibold text-text-primary">{bookDisplay(item.outlierBook).label}</span>
              <span className="mono text-mini text-text-secondary ml-1">Implied: {(item.outlierImplied * 100).toFixed(1)}%</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {lagMin != null && lagMin >= 5 && (
                <span
                  className="mono text-[10px]"
                  style={{
                    color: '#f59e0b',
                    background: 'rgba(245,158,11,0.08)',
                    border: '1px solid rgba(245,158,11,0.25)',
                    padding: '1px 5px',
                    borderRadius: '3px',
                  }}
                >
                  +{Math.round(lagMin)}m behind mkt
                </span>
              )}
              {unmoved && (
                <span
                  className="mono text-[10px] text-text-secondary"
                  style={{
                    padding: '1px 5px',
                    borderRadius: '3px',
                    border: `1px solid ${HAIR}`,
                  }}
                >
                  Unmoved since open
                </span>
              )}
              <span className="mono" style={{ fontSize: '11px', color: GREEN, fontWeight: 600 }}>Best price</span>
            </div>
          </>
        )}
      </div>

      {/* Row 4: Horizontal Price Comparison Bar Chart */}
      <BookComparisonBars
        heroAmerican={item.outlierAmerican}
        heroBook={item.outlierBook}
        locked={isLocked}
        peers={item.peers}
        footerLeft={
          <span>Market avg &middot; {item.nPeers} books{item.disp != null ? ` \u00b7 Disp ${item.disp.toFixed(3)}` : ''}</span>
        }
        footerRight={item.marketAvgAmerican > 0 ? `+${item.marketAvgAmerican}` : `${item.marketAvgAmerican}`}
      />
    </CardShell>
  )
}
