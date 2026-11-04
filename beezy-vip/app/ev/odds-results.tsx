import { bookDisplay, SURFACE_CARD } from '@/lib/tokens'
import { cx } from '@/utils/cx'
import { BookLogo } from '@/components/ui/book-logo'
import { SGP_ARTIFACT_EV_PCT, SGP_UNVALIDATED_LABEL } from '@/lib/odds-screen'
import { HAIR, GREEN, PAGE } from './odds-constants'
import { ageLabel, usd } from './odds-format'
import { EvRow, ArbTableRow, MiddleTableRow, LongshotTableRow, OutlierTableRow, SgpTableRow } from './odds-rows'
import { MobileEvRowCard, MobileArbCard, MobileMiddleCard, MobileLongshotCard, MobileSgpCard, OutlierCard } from './odds-cards'
import { OddsScreen } from './use-odds-screen'
import { LeagueNote } from '@/components/ui/league-filter'

/** One strip above the SGP view: every row is UNVALIDATED model output, and the flagged extreme rows are counted, not silently dropped. */
function SgpNotice({ extremeCount, includeExtreme, onToggle }: { extremeCount: number; includeExtreme: boolean; onToggle: () => void }) {
  return (
    <div role="note" className="mb-2.5 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-separator-border border-l-[3px] border-l-[#facc15] bg-background-primary-default px-3 py-2 shadow-card">
      <span className="mono text-[11px] font-semibold text-warn">Unvalidated</span>
      <span className="min-w-[240px] flex-1 text-caption-1-regular text-text-secondary">
        {SGP_UNVALIDATED_LABEL}. Correlation and book hold are assumed constants, so a gap here is a place to look, not an edge.
      </span>
      <span className={cx('mono text-mini', extremeCount > 0 ? 'text-[#ff5a4f]' : 'text-text-secondary')}>
        {extremeCount} EXTREME (EV OVER {SGP_ARTIFACT_EV_PCT}%){includeExtreme ? ' SHOWN' : ' HIDDEN'}
      </span>
      <button type="button" onClick={onToggle} aria-pressed={includeExtreme} className="btn btn-ghost px-2.5 py-[3px] text-mini">
        Include extreme
      </button>
    </div>
  )
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-xl border border-separator-border bg-background-primary-default px-5 py-9 text-center shadow-card">
      <div className="mb-1.5 text-body-semibold text-text-primary">{title}</div>
      <div className="mx-auto max-w-[60ch] text-body-2-regular text-text-secondary">{body}</div>
    </div>
  )
}

/** Cards | Spreadsheet switch above the mobile result views. */
function MobileViewBar({ active, nRows, onChange, trailing }: { active: 'cards' | 'table'; nRows: number; onChange: (v: 'cards' | 'table') => void; trailing?: React.ReactNode }) {
  const tab = (on: boolean): React.CSSProperties => ({
    fontFamily: 'var(--font-text), sans-serif',
    fontSize: '11px',
    fontWeight: 600,
    padding: '4px 10px',
    borderRadius: '4px',
    border: 0,
    cursor: 'pointer',
    background: on ? 'var(--color-background-tertiary-default)' : 'transparent',
    color: on ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
  })
  return (
    <div className="mb-2.5 flex items-center justify-between">
      <div style={{ display: 'inline-flex', padding: '2px', background: 'var(--color-background-secondary-default)', border: `1px solid ${HAIR}`, borderRadius: '6px' }}>
        <button type="button" onClick={() => onChange('cards')} style={tab(active === 'cards')}>Cards ({nRows})</button>
        <button type="button" onClick={() => onChange('table')} style={tab(active === 'table')}>Spreadsheet</button>
      </div>
      {trailing}
    </div>
  )
}

function RevealNotice({ msg, onDismiss }: { msg: string; onDismiss: () => void }) {
  return (
    <div role="status" style={{ ...SURFACE_CARD, padding: '10px 14px', marginBottom: '12px', borderColor: 'rgba(87,242,135,0.4)', color: GREEN, fontSize: '12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      <span>{msg}</span>
      <button onClick={onDismiss} className="btn btn-ghost" style={{ padding: '2px 8px', fontSize: '11px' }}>Dismiss</button>
    </div>
  )
}

function MobileCardsView({ odds }: { odds: OddsScreen }) {
  const { mode, limit, copied, revealed, revealMsg, setRevealMsg, bankroll, setMobileView, setInspectItem, handleReveal, screen, outliers, arbs, middles, longshots, sgpProxies, nRows, meta, outlay, copyHedge, copyMiddle, fullAccess } = odds
  return (
    <div>
      <MobileViewBar active="cards" nRows={nRows} onChange={setMobileView} trailing={<span className="mono text-mini text-text-secondary">{meta.age != null ? ageLabel(meta.age) : ''}</span>} />

      {mode === 'outliers' ? (
        <div>
          {revealMsg && <RevealNotice msg={revealMsg} onDismiss={() => setRevealMsg(null)} />}
          <div className="grid grid-cols-1 gap-3">
            {outliers.slice(0, limit).map((item, idx) => (
              <OutlierCard
                key={item.key}
                item={item}
                isLocked={!revealed.has(item.key) && idx >= 3 && !fullAccess}
                onReveal={() => handleReveal(item.key)}
              />
            ))}
          </div>
        </div>
      ) : mode === 'ev' ? (
        <div>
          {screen.slice(0, limit).map(r => (
            <MobileEvRowCard
              key={r.key}
              r={r}
              bankroll={bankroll}
              onInspect={() => setInspectItem({ type: 'ev', row: r })}
            />
          ))}
        </div>
      ) : mode === 'arb' ? (
        <div>
          {arbs.slice(0, limit).map(r => (
            <MobileArbCard
              key={r.key}
              r={r}
              outlay={outlay}
              copied={copied === r.key}
              onCopy={copyHedge}
              onInspect={() => setInspectItem({ type: 'arb', row: r })}
            />
          ))}
        </div>
      ) : mode === 'middles' ? (
        <div>
          {middles.slice(0, limit).map(r => (
            <MobileMiddleCard
              key={r.key}
              r={r}
              outlay={outlay}
              copied={copied === r.key}
              onCopy={copyMiddle}
              onInspect={() => setInspectItem({ type: 'middles', row: r })}
            />
          ))}
        </div>
      ) : mode === 'longshots' ? (
        <div>
          {longshots.slice(0, limit).map(r => (
            <MobileLongshotCard
              key={r.key}
              r={r}
              onInspect={() => setInspectItem({ type: 'longshots', row: r })}
            />
          ))}
        </div>
      ) : (
        <div>
          {sgpProxies.slice(0, limit).map(r => (
            <MobileSgpCard key={r.key} r={r} />
          ))}
        </div>
      )}
    </div>
  )
}

function OutlierCardsView({ odds }: { odds: OddsScreen }) {
  const { limit, revealed, revealMsg, setRevealMsg, isMobile, setMobileView, handleReveal, outliers, nRows, fullAccess } = odds
  return (
    <div>
      {isMobile && <MobileViewBar active="table" nRows={nRows} onChange={setMobileView} />}
      {revealMsg && <RevealNotice msg={revealMsg} onDismiss={() => setRevealMsg(null)} />}
      <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(auto-fill, minmax(420px, 1fr))', gap: '14px' }}>
        {outliers.slice(0, limit).map((item, idx) => (
          <OutlierCard
            key={item.key}
            item={item}
            isLocked={!revealed.has(item.key) && idx >= 3 && !fullAccess}
            onReveal={() => handleReveal(item.key)}
          />
        ))}
      </div>
    </div>
  )
}

function SgpTableView({ odds }: { odds: OddsScreen }) {
  const { limit, isMobile, setMobileView, sgpProxies, nRows } = odds
  return (
    <div>
      {isMobile && <MobileViewBar active="table" nRows={nRows} onChange={setMobileView} />}
      <div className="odds-table-wrap rounded-xl border border-separator-border bg-background-primary-default shadow-card">
        <table className="odds-table w-full border-collapse">
          <thead>
            <tr style={{ borderBottom: `1px solid ${HAIR}`, background: '#0d0f15' }}>
              <th scope="col" className="text-left min-w-[220px] py-2.5 px-3.5">PLAYER &amp; MATCHUP</th>
              <th scope="col" className="text-left min-w-[280px] py-2.5 px-3.5">SGP LEGS (SAME BOOK)</th>
              <th scope="col" className="text-left min-w-[150px] py-2.5 px-3.5">Assumed-correlation fair</th>
              <th scope="col" className="text-left min-w-[220px] py-2.5 px-3.5">MODELLED SGP PRICE (NOT QUOTED)</th>
              <th scope="col" className="text-right min-w-[170px] py-2.5 px-3.5">MODEL EV &amp; ACTION</th>
            </tr>
          </thead>
          <tbody>
            {sgpProxies.slice(0, limit).map(r => (
              <SgpTableRow key={r.key} r={r} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function PropTableView({ odds }: { odds: OddsScreen }) {
  const { mode, limit, copied, revealed, bankroll, isMobile, setMobileView, handleReveal, columns, screen, outliers, arbs, middles, longshots, nRows, refWeight, refSet, outlay, copyHedge, copyMiddle, fullAccess } = odds
  return (
    <div>
      {isMobile && <MobileViewBar active="table" nRows={nRows} onChange={setMobileView} />}
      <div className="odds-table-wrap rounded-xl border border-separator-border bg-background-primary-default shadow-card">
        <table className="odds-table w-full border-collapse">
        <thead>
          <tr>
            <th scope="col" className="odds-prop text-left min-w-[210px]">PROP</th>
            <th scope="col" className="text-left">Game</th>
            {mode === 'ev' && (
              <>
                <th scope="col" className="odds-ev" title="Fair price of this side from the reference books">Fair odds</th>
                <th scope="col" className="odds-ev" title="EV of the best bettable price against the fair value">Best edge</th>
                <th scope="col" title="Quarter Kelly at your bankroll, edge shrunk the way the +EV Terminal sizes" className="text-right">Kelly / line</th>
              </>
            )}
            {mode === 'outliers' && (
              <>
                <th scope="col" className="odds-ev" title="Average price across all peer books quoting this side">Market avg</th>
                <th scope="col" className="odds-ev" title="The single book hanging a number off the market">Outlier book</th>
                <th scope="col" title="Payout premium above peer average, and de-vigged EV" className="text-right">Dislocation</th>
              </>
            )}
            {mode === 'middles' && (
              <>
                <th scope="col" className="odds-ev" title="Width of the window between the two lines, and what a miss and a hit pay">Middle</th>
                <th scope="col" className="odds-ev">LEGS</th>
                <th scope="col" className="odds-ev">STAKE SPLIT ({usd(outlay).replace('.00', '')})</th>
              </>
            )}
            {mode === 'arb' && (
              <>
                <th scope="col" className="odds-ev" title="Margin on the outlay with the best over and under at two different books, net of Kalshi's fee">Arb / hold</th>
                <th scope="col" className="odds-ev">Hedge legs</th>
                <th scope="col" className="odds-ev">STAKE SPLIT ({usd(outlay).replace('.00', '')})</th>
              </>
            )}
            {mode === 'longshots' && (
              <>
                <th scope="col" className="odds-ev" title="Fair over / under price: the reference books' consensus, or the player's curve when they are too few">Fair anchor</th>
                <th scope="col" className="odds-ev" title="The side with the higher EV">Best action</th>
                <th scope="col" title="Implied probability of the best price against the fair probability, for the best action's side" className="text-right">Implied / true</th>
              </>
            )}
            {columns.map(c => {
              const b = bookDisplay(c.key)
              const isRef = refSet.has(c.key)
              return (
                <th key={c.key} scope="col" className="odds-book" title={`${b.label}${c.offshore ? ' (offshore: reference only)' : ''}`}>
                  <BookLogo book={c.key} size={34} style={{ display: 'block', margin: '0 auto 2px' }} />
                  <span className="mono odds-sub">{isRef && refWeight != null && mode !== 'arb' && mode !== 'middles' ? `${refWeight}%` : c.offshore ? 'ref' : '\u00a0'}</span>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {mode === 'ev' && screen.slice(0, limit).map(r => <EvRow key={r.key} r={r} columns={columns} bankroll={bankroll} />)}
          {mode === 'outliers' && outliers.slice(0, limit).map((r, idx) => (
            <OutlierTableRow
              key={r.key}
              r={r}
              columns={columns}
              isLocked={!revealed.has(r.key) && idx >= 3 && !fullAccess}
              onReveal={() => handleReveal(r.key)}
            />
          ))}
          {mode === 'middles' && middles.slice(0, limit).map(r => <MiddleTableRow key={r.key} r={r} columns={columns} outlay={outlay} copied={copied === r.key} onCopy={copyMiddle} />)}
          {mode === 'arb' && arbs.slice(0, limit).map(r => <ArbTableRow key={r.key} r={r} columns={columns} outlay={outlay} copied={copied === r.key} onCopy={copyHedge} />)}
          {mode === 'longshots' && longshots.slice(0, limit).map(r => <LongshotTableRow key={r.key} r={r} columns={columns} />)}
        </tbody>
      </table>
    </div>
    </div>
  )
}

export function OddsResults({ odds }: { odds: OddsScreen }) {
  const { mode, sport, setSport, sportOptions, sgpExtreme, setSgpExtreme, setLimit, outlierView, isMobile, mobileView, allRows, sgpExtremeCount, nRows, unpublished, emptyBody } = odds
  return (
    <>
      {mode === 'sgp' && !unpublished && (
        <SgpNotice extremeCount={sgpExtremeCount} includeExtreme={sgpExtreme} onToggle={() => { setSgpExtreme(v => !v); setLimit(PAGE) }} />
      )}

      {unpublished ? (
        <Empty title="Odds board not published yet" body="The price feed behind this screen is not available right now. It is rewritten every 30 minutes while games are on the board; check back shortly." />
      ) : sport !== 'all' && !sportOptions.includes(sport) ? (
        <LeagueNote value={sport} live={sportOptions} onPick={v => { setSport(v); setLimit(PAGE) }} />
      ) : allRows.length === 0 ? (
        <Empty title="No prop prices posted" body="No book has posted player props for the games on the board yet." />
      ) : nRows === 0 ? (
        <Empty title={emptyBody.title} body={emptyBody.body} />
      ) : isMobile && mobileView === 'cards' ? (
        <MobileCardsView odds={odds} />
      ) : mode === 'outliers' && outlierView === 'cards' ? (
        <OutlierCardsView odds={odds} />
      ) : mode === 'sgp' ? (
        <SgpTableView odds={odds} />
      ) : (
        <PropTableView odds={odds} />
      )}
    </>
  )
}
