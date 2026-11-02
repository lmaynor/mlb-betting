import { bookDisplay } from '@/lib/tokens'
import { fmtMoney, kellySizing } from '@/lib/ev-math'
import { formatOdds } from '@/lib/odds'
import { PlayerHeadshot } from '@/components/ui/sport-assets'
import { type HeadshotSport } from '@/lib/sport-assets'
import { BookLogo } from '@/components/ui/book-logo'
import { LockGlyph } from '@/components/ui/lock-glyph'
import { type ArbRow, type BoardBook, type LongshotRow, type MiddleResult, type OutlierRow, type ScreenRow, type SgpProxyRow } from '@/lib/odds-screen'
import { HAIR, GREEN, SKY, VIOLET, MIN_REF, CELL_EDGE } from './odds-constants'
import { startLabel, signed, usd, fairText, sgpEvText, dec, underText } from './odds-format'
import { SgpBadges, PriceCell, TierPill, PropCells, BookTag } from './odds-cells'

export function EvRow({ r, columns, bankroll }: { r: ScreenRow; columns: { key: string; offshore: boolean }[]; bankroll: number }) {
  const d = r.implied > 0 ? 1 / r.implied : 1
  const size = r.pFair == null ? null : kellySizing(bankroll, r.pFair, d)
  const byBook = new Map(r.row.books.map(b => [b.book, b]))
  const evColor = r.ev != null && r.ev > 0 ? GREEN : 'var(--color-text-secondary)'
  return (
    <tr data-row="odds">
      <PropCells row={r.row} label={r.label} />
      <td className="odds-ev">
        {r.fairAmerican == null || r.pFair == null ? (
          <>
            <div className="mono text-cozy font-bold text-text-secondary">--</div>
            <div className="mono odds-sub" title={`Only ${r.nRef} reference book${r.nRef === 1 ? '' : 's'} priced this line both ways; ${MIN_REF} are needed for a fair value`}>{r.nRef} of {MIN_REF} bk</div>
          </>
        ) : (
          <>
            <div className="mono text-cozy font-bold text-text-primary">{r.fairAmerican > 0 ? `+${r.fairAmerican}` : r.fairAmerican}</div>
            <div className="mono odds-sub" title={`${r.nRef} reference books; the other side's fair price is ${fairText(1 - r.pFair)}`}>{r.nRef} BKS &middot; {fairText(1 - r.pFair)}</div>
          </>
        )}
      </td>
      <td className="odds-ev">
        <div className="flex items-center gap-2">
          <BookLogo book={r.bestBook} size={28} />
          <div>
            <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: evColor }}>{r.ev == null ? '--' : `${signed(r.ev * 100)}%`}</div>
            <div className="mono odds-sub">{r.side} {formatOdds(r.bestAmerican)}</div>
          </div>
        </div>
      </td>
      <td className="text-right">
        <div className="mono" style={{ fontSize: '12px', color: size && size.dollars > 0 ? 'var(--color-text-primary)' : 'var(--color-text-secondary)' }}>{size && size.dollars > 0 ? fmtMoney(size.dollars) : '--'}</div>
        <div className="mt-0.5"><TierPill alt={r.row.is_alt === true} modalPoint={r.row.modal_point} /></div>
      </td>
      {columns.map(c => {
        const q = byBook.get(c.key)
        const target = q ? (r.side === 'OVER' ? q.over : q.under) : null
        const other = q ? (r.side === 'OVER' ? q.under : q.over) : null
        const isBest = !c.offshore && r.bestTies.includes(c.key)
        const edge = !c.offshore && r.pFair != null && target != null && r.pFair * dec(target) - 1 >= CELL_EDGE
        return <PriceCell key={c.key} top={target} bottom={other} topState={isBest ? 'best' : undefined} best={isBest} tint={edge} />
      })}
    </tr>
  )
}

export function ArbTableRow({ r, columns, outlay, copied, onCopy }: {
  r: ArbRow; columns: { key: string; offshore: boolean }[]; outlay: number; copied: boolean; onCopy: (r: ArbRow) => void
}) {
  const a = r.arb
  const k = outlay / 100
  const byBook = new Map(r.row.books.map(b => [b.book, b]))
  const live = a.netArbPct > 0
  const profit = a.guaranteedProfitPer100 * k
  return (
    <tr data-row="odds">
      <PropCells row={r.row} label={String(r.row.line)} />
      <td className="odds-ev">
        <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: live ? GREEN : SKY }}
          title={a.feeLeg ? `Gross ${signed(a.arbPct, 2)}%, net of Kalshi's taker fee ${signed(a.netArbPct, 2)}%` : `${signed(a.netArbPct, 2)}%, no fee on either leg`}>
          {signed(a.netArbPct, 2)}% {live ? 'ARB' : 'HOLD'}
        </div>
        <div className="mono odds-sub">{a.feeLeg ? 'NET OF KALSHI FEE' : '\u00a0'}</div>
      </td>
      <td className="odds-ev">
        <div className="flex items-center gap-2">
          <span className="mono text-[11.5px] font-bold text-text-primary min-w-[84px]">OVER {formatOdds(a.bestOverAm)}</span>
          <BookTag book={a.bestOverBook} />
        </div>
        <div className="flex items-center gap-2 mt-[3px]">
          <span className="mono text-[11.5px] font-bold text-text-primary min-w-[84px]">UNDER {formatOdds(a.bestUnderAm)}</span>
          <BookTag book={a.bestUnderBook} />
        </div>
      </td>
      <td className="odds-ev">
        <div className="mono text-compact text-text-primary">{usd(a.stakeOverPer100 * k)} Over / {usd(a.stakeUnderPer100 * k)} Under</div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="mono odds-sub" style={{ color: live ? GREEN : SKY }}>{profit >= 0 ? '+' : '-'}{usd(Math.abs(profit))} {live ? 'locked' : 'at this hold'}</span>
          <button className="btn btn-ghost" onClick={() => onCopy(r)} style={{ padding: '1px 8px', fontSize: '10.5px' }}>{copied ? 'Copied' : 'Copy hedge'}</button>
        </div>
      </td>
      {columns.map(c => {
        const q = byBook.get(c.key)
        return (
          <PriceCell key={c.key} top={q ? q.over : null} bottom={q ? q.under : null}
            topState={c.key === a.bestOverBook ? 'arb' : undefined} bottomState={c.key === a.bestUnderBook ? 'arb' : undefined} />
        )
      })}
    </tr>
  )
}

export function MiddleTableRow({ r, columns, outlay, copied, onCopy }: {
  r: MiddleResult; columns: { key: string; offshore: boolean }[]; outlay: number; copied: boolean; onCopy: (r: MiddleResult) => void
}) {
  const k = outlay / 100
  const low = new Map(r.low.books.map(b => [b.book, b]))
  const high = new Map(r.high.books.map(b => [b.book, b]))
  const free = r.missPct >= 0
  return (
    <tr data-row="odds">
      <PropCells row={r.low} label={`o${r.low.line} / u${r.high.line}`} />
      <td className="odds-ev">
        <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: free ? GREEN : SKY }}
          title={r.feeLeg ? "Net of Kalshi's taker fee" : 'Both legs win only if the result lands strictly between the two lines'}>
          {r.gap % 1 === 0 ? r.gap : r.gap.toFixed(1)} PT WINDOW
        </div>
        <div className="mono odds-sub" title="Profit on the outlay if one leg wins (a miss) and if both win (inside the window)">
          MISS {signed(r.missPct)}% &middot; HIT {signed(r.hitPct)}%
        </div>
      </td>
      <td className="odds-ev">
        <div className="flex items-center gap-2">
          <span className="mono text-[11.5px] font-bold text-text-primary min-w-[104px]">OVER {r.low.line} {formatOdds(r.overAm)}</span>
          <BookTag book={r.overBook} />
        </div>
        <div className="flex items-center gap-2 mt-[3px]">
          <span className="mono text-[11.5px] font-bold text-text-primary min-w-[104px]">UNDER {r.high.line} {formatOdds(r.underAm)}</span>
          <BookTag book={r.underBook} />
        </div>
      </td>
      <td className="odds-ev">
        <div className="mono text-compact text-text-primary">{usd(r.stakeOverPer100 * k)} Over / {usd(r.stakeUnderPer100 * k)} Under</div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="mono odds-sub" title="The chance the window must hit for the middle to break even; the estimate comes from the reference books' fair price at each line">
            BREAK-EVEN {(r.breakevenProb * 100).toFixed(1)}%{r.midProb != null ? ` \u00b7 P(WINDOW) ${(r.midProb * 100).toFixed(1)}%` : ''}
          </span>
          <button className="btn btn-ghost" onClick={() => onCopy(r)} style={{ padding: '1px 8px', fontSize: '10.5px' }}>{copied ? 'Copied' : 'Copy middle'}</button>
        </div>
      </td>
      {columns.map(c => {
        const o = low.get(c.key)
        const u = high.get(c.key)
        return (
          <PriceCell key={c.key} top={o ? o.over : null} bottom={u ? u.under : null}
            topState={c.key === r.overBook ? 'arb' : undefined} bottomState={c.key === r.underBook ? 'arb' : undefined} />
        )
      })}
    </tr>
  )
}

export function LongshotTableRow({ r, columns }: { r: LongshotRow; columns: { key: string; offshore: boolean }[] }) {
  const m = r.m
  const byBook = new Map<string, BoardBook>(r.row.books.map(b => [b.book, b]))
  const evOf = r.bestEv
  const hot = evOf != null && evOf > 0
  const over = r.side !== 'UNDER'
  const implied = over ? m.overImplied : m.underImplied
  const truth = over ? m.fairOverProb : m.fairUnderProb
  const book = over ? m.bestOverBook : m.bestUnderBook
  const am = over ? m.bestOverAm : m.bestUnderAm
  const color = !hot ? 'var(--color-text-secondary)' : over ? GREEN : SKY
  return (
    <tr data-row="odds">
      <PropCells row={r.row} label={String(r.row.line)} />
      <td className="odds-ev">
        {m.fairOverProb == null ? (
          <>
            <div className="mono text-cozy font-bold text-text-secondary">--</div>
            <div className="mono odds-sub" title="Fewer than two reference books price this exact line and no curve covers it">NO FAIR VALUE</div>
          </>
        ) : (
          <>
            <div className="mono text-cozy font-bold text-text-primary">{fairText(m.fairOverProb)} / {fairText(m.fairUnderProb)}</div>
            <div className="mono odds-sub flex gap-1.5 items-center">
              {m.fairSource === 'curve' ? (
                <span title={`Implied from player's main line (${r.row.modal_point ?? 'main'}) across reference books`}
                  style={{ fontSize: '10px', fontWeight: 700, letterSpacing: '0.04em', padding: '0 5px', borderRadius: '4px', border: `1px solid ${VIOLET}66`, color: VIOLET }}>EST. FAIR</span>
              ) : <span>{m.nRef} BKS</span>}
              <TierPill alt={m.isAltLine} modalPoint={r.row.modal_point} />
            </div>
          </>
        )}
      </td>
      <td className="odds-ev">
        {book == null || am == null ? (
          <div className="mono text-cozy font-bold text-text-secondary">--</div>
        ) : (
          <>
            <div className="mono" style={{ fontSize: '12.5px', fontWeight: 700, color }}>
              {over ? `SNIPE OVER ${formatOdds(am)}` : `FADE ${underText(book, am)}`}
            </div>
            <div className="flex items-center gap-1.5 mt-0.5">
              <BookLogo book={book} size={16} />
              <span className="mono odds-sub inline">{bookDisplay(book).label}{evOf == null ? '' : ` \u00b7 ${signed(evOf)}% EV`}</span>
            </div>
          </>
        )}
      </td>
      <td className="text-right">
        <div className="mono text-compact text-neutral-300">
          {implied == null ? '--' : `${(implied * 100).toFixed(1)}%`} / <span style={{ color: truth != null && implied != null && truth > implied ? GREEN : 'var(--color-neutral-300)' }}>{truth == null ? '--' : `${(truth * 100).toFixed(1)}%`}</span>
        </div>
        <div className="mono odds-sub">{over ? 'OVER' : 'UNDER'} IMPL / TRUE</div>
      </td>
      {columns.map(c => {
        const q = byBook.get(c.key)
        const o = q ? q.over : null
        const u = q ? q.under : null
        const edgeO = !c.offshore && m.fairOverProb != null && o != null && m.fairOverProb * dec(o) - 1 >= CELL_EDGE
        const edgeU = !c.offshore && m.fairUnderProb != null && u != null && m.fairUnderProb * dec(u) - 1 >= CELL_EDGE
        return (
          <PriceCell key={c.key} top={o} bottom={u} tint={edgeO || edgeU}
            topState={c.key === m.bestOverBook ? 'best' : undefined} bottomState={c.key === m.bestUnderBook ? 'best' : undefined} />
        )
      })}
    </tr>
  )
}

export function OutlierTableRow({
  r,
  columns,
  isLocked,
  onReveal,
}: {
  r: OutlierRow
  columns: { key: string; offshore: boolean }[]
  isLocked: boolean
  onReveal: () => void
}) {
  const byBook = new Map(r.row.books.map(b => [b.book, b]))
  const gapColor = r.trueEvPct != null && r.trueEvPct > 0 ? GREEN : SKY

  return (
    <tr data-row="odds">
      <PropCells row={r.row} label={r.label} />
      <td className="odds-ev">
        <div className="mono text-cozy font-bold text-text-primary">
          {r.marketAvgAmerican > 0 ? `+${r.marketAvgAmerican}` : `${r.marketAvgAmerican}`}
        </div>
        <div className="mono odds-sub">
          {r.nPeers} PEERS &middot; FAIR {r.fairAmerican != null ? (r.fairAmerican > 0 ? `+${r.fairAmerican}` : `${r.fairAmerican}`) : '--'}
        </div>
      </td>
      <td className="odds-ev">
        {isLocked ? (
          <div className="flex items-center gap-1.5">
            <LockGlyph size={12} color="var(--color-neutral-400)" />
            <button onClick={onReveal} className="btn btn-ghost" style={{ padding: '2px 8px', fontSize: '11px', color: GREEN }}>See Pro</button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <BookLogo book={r.outlierBook} size={28} />
            <div>
              <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: GREEN }}>
                {r.side} {r.outlierAmerican > 0 ? `+${r.outlierAmerican}` : `${r.outlierAmerican}`}
              </div>
              <div className="mono odds-sub">{bookDisplay(r.outlierBook).label}</div>
            </div>
          </div>
        )}
      </td>
      <td className="text-right">
        <div className="mono" style={{ fontSize: '12.5px', fontWeight: 700, color: gapColor }}>
          +{r.payoutPremiumPct.toFixed(1)}% MKT
        </div>
        <div className="mono odds-sub" style={{ color: r.trueEvPct != null && r.trueEvPct > 0 ? GREEN : 'var(--color-text-secondary)' }}>
          {r.trueEvPct != null ? `${signed(r.trueEvPct)}% EV` : 'NO FAIR EV'}
        </div>
      </td>
      {columns.map(c => {
        const q = byBook.get(c.key)
        const target = q ? (r.side === 'OVER' ? q.over : q.under) : null
        const other = q ? (r.side === 'OVER' ? q.under : q.over) : null
        const isOutlier = !isLocked && c.key === r.outlierBook
        return <PriceCell key={c.key} top={target} bottom={other} topState={isOutlier ? 'best' : undefined} best={isOutlier} tint={isOutlier} />
      })}
    </tr>
  )
}

export function SgpTableRow({ r }: { r: SgpProxyRow }) {
  return (
    <tr style={{ borderBottom: `1px solid ${HAIR}` }}>
      <td className="py-2.5 px-3.5 min-w-[220px]">
        <div className="flex items-center gap-2.5">
          <PlayerHeadshot id={r.headshot_id} name={r.player} sport={r.sport as HeadshotSport} size={36} />
          <div>
            <div className="font-semibold text-text-primary text-cozy">{r.player}</div>
            <div className="flex items-center gap-1.5 flex-wrap mt-[3px]">
              <SgpBadges r={r} />
            </div>
            <div className="mono text-mini text-text-secondary mt-[3px]">
              {r.team ? `${r.team} \u00b7 ` : ''}{r.away} @ {r.home} &middot; {startLabel(r.commence)}
            </div>
          </div>
        </div>
      </td>
      <td className="py-2.5 px-3.5">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            {r.legs.map((leg, idx) => (
              <span
                key={idx}
                className="mono"
                style={{ fontSize: '11px', padding: '2px 6px', borderRadius: '4px', background: 'rgba(255, 255, 255, 0.04)', border: `1px solid ${HAIR}`, color: 'var(--color-text-primary)' }}
              >
                {leg.label} ({formatOdds(leg.bookAm)})
              </span>
            ))}
            <span className="text-mini text-text-secondary">
              @ <span className="capitalize text-text-primary font-medium">{bookDisplay(r.book).label}</span>
            </span>
          </div>
          <div className="mono text-[10.5px] text-text-secondary">
            Uncorrelated parlay: {formatOdds(r.naiveSingleProductAm)}
          </div>
        </div>
      </td>
      <td className="py-2.5 px-3.5">
        <div className="mono text-cozy font-semibold text-text-primary">
          {formatOdds(r.fairJointAm)}
        </div>
        <div className="flex items-center gap-1 mt-0.5">
          <span className="mono text-mini text-text-secondary">
            {(r.fairJointProb * 100).toFixed(1)}% model
          </span>
          <span
            className="mono"
            style={{ fontSize: '10px', padding: '1px 4px', borderRadius: '3px', background: 'rgba(255, 255, 255, 0.05)', color: 'var(--color-text-secondary)' }}
            title={`Assumed correlation rho = ${r.rhoTrue} (unvalidated: a constant per archetype, not fitted to data)`}
          >
            &rho;={r.rhoTrue > 0 ? `+${r.rhoTrue}` : r.rhoTrue}
          </span>
        </div>
      </td>
      <td className="py-2.5 px-3.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          <BookLogo book={r.book} size={22} />
          <span className="mono text-cozy font-bold text-text-primary">
            Modelled: {formatOdds(r.estSgpAm)}
          </span>
        </div>
        <div className="mono mt-0.5 text-[10.5px] text-text-secondary">
          Assumed book copula (&rho;={r.rhoBook}, hold={Math.round(r.holdBook * 100)}%)
        </div>
        {r.runnerUpBooks && r.runnerUpBooks.length > 0 && (
          <div className="text-[10.5px] text-text-secondary mt-[3px]">
            Also at: {r.runnerUpBooks.map(b => `${bookDisplay(b.book).label} (${b.evPct > 0 ? `+${b.evPct.toFixed(1)}%` : `${b.evPct.toFixed(1)}%`})`).join(', ')}
          </div>
        )}
      </td>
      <td className="py-2.5 px-3.5 text-right">
        <div className="flex items-center justify-end gap-2.5">
          <span className="mono" style={{ fontSize: '12px', fontWeight: 700, padding: '3px 8px', borderRadius: '4px', background: 'rgba(255, 255, 255, 0.04)', color: 'var(--color-neutral-300)', border: `1px solid ${HAIR}` }}>
            {sgpEvText(r)}
          </span>
          {r.deeplink && (
            <a
              href={r.deeplink}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-ghost"
              style={{ padding: '4px 8px', fontSize: '11px', fontWeight: 600, color: 'var(--color-text-primary)', border: `1px solid ${HAIR}`, borderRadius: '4px', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '4px' }}
            >
              Check price &rarr;
            </a>
          )}
        </div>
      </td>
    </tr>
  )
}
