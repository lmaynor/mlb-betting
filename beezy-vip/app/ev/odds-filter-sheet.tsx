import { bookDisplay } from '@/lib/tokens'
import { MobileBottomSheet } from '@/components/ui/mobile-sheet'
import { STAT_LABEL, type SideFilter } from '@/lib/odds-screen'
import { SortKey, HAIR, SHARP_PRESET, MIN_EV, ODDS_MIN, PAGE, MID_COST, MID_GAP, OUTLIER_GAP_OPTIONS, OUTLIER_ODDS_OPTIONS, OUTLIER_MIN_BOOKS, MODES, SGP_ARCHETYPE_OPTIONS, SGP_MIN_EV_OPTIONS } from './odds-constants'
import { startLabel } from './odds-format'
import { Select, Pill, PillGroup, MobileBookChecklist } from './odds-controls'
import { OddsScreen } from './use-odds-screen'

export function MobileFilterSheet({ odds }: { odds: OddsScreen }) {
  const { mode, stat, setStat, game, setGame, side, setSide, lines, setLines, minEv, setMinEv, sgpArch, setSgpArch, sgpMinEv, setSgpMinEv, sgpBook, setSgpBook, setQ, method, setMethod, refs, setRefs, setShown, sort, setSort, setLimit, arbView, setArbView, exchanges, setExchanges, outlayText, setOutlayText, midCost, setMidCost, midGap, setMidGap, midPosEv, setMidPosEv, longSub, setLongSub, oddsMin, setOddsMin, useCurve, setUseCurve, outlierGap, setOutlierGap, outlierReqEv, setOutlierReqEv, outlierOddsBand, setOutlierOddsBand, outlierMinBooks, setOutlierMinBooks, outlierView, setOutlierView, myBooks, mobileFiltersOpen, setMobileFiltersOpen, bookList, defaultShown, shownSet, columns, statOptions, games, nRows, refsNote } = odds
  return (
    <MobileBottomSheet
      open={mobileFiltersOpen}
      onClose={() => setMobileFiltersOpen(false)}
      title="Terminal Filters"
      subtitle={`${MODES.find(m => m.key === mode)?.label} \u00b7 ${nRows} matching lines`}
      footer={
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => {
              setStat('all')
              setGame('all')
              setQ('')
              setMinEv(0)
              setLines('all')
              setSide('both')
              setSort('ev')
              setRefs([])
              setOutlierGap(8)
              setOutlierReqEv(false)
              setOutlierOddsBand('all')
              setOutlierMinBooks(3)
              setArbView('live')
              setExchanges(true)
              setOutlayText('100')
              setMidCost(5)
              setMidGap(0)
              setMidPosEv(false)
              setLongSub('all')
              setOddsMin(0)
              setUseCurve(true)
              setSgpArch('all')
              setSgpMinEv(0)
              setSgpBook('all')
            }}
            className="btn btn-ghost"
            style={{ flex: 1, padding: '9px 0', fontSize: '12px' }}
          >
            Reset
          </button>
          <button
            type="button"
            onClick={() => setMobileFiltersOpen(false)}
            className="btn btn-primary"
            style={{ flex: 2, padding: '9px 0', fontSize: '12px' }}
          >
            Show {nRows} Results
          </button>
        </div>
      }
    >
      <div className="flex flex-col gap-3.5">
        {mode === 'ev' && (
          <>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Lines</div>
              <PillGroup>
                <Pill active={lines === 'main'} onClick={() => setLines('main')}>Main Lines</Pill>
                <Pill active={lines === 'all'} onClick={() => setLines('all')}>Main + Alt Ladders</Pill>
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">O/U</div>
              <PillGroup>
                {(['both', 'over', 'under'] as SideFilter[]).map(s => <Pill key={s} active={side === s} onClick={() => setSide(s)}>{s.toUpperCase()}</Pill>)}
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Min EV</div>
              <PillGroup>
                {MIN_EV.map(m => <Pill key={m.v} active={minEv === m.v} onClick={() => setMinEv(m.v)}>{m.label}</Pill>)}
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">SORT</div>
              <Select id="mobile-ev-sort" value={sort} onChange={v => setSort(v as SortKey)} options={[{ v: 'ev', l: 'Best EV %' }, { v: 'time', l: 'Game time' }, { v: 'player', l: 'Player' }]} />
            </div>
          </>
        )}

        {mode === 'outliers' && (
          <>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Min gap</div>
              <PillGroup>
                {OUTLIER_GAP_OPTIONS.map(g => (
                  <Pill key={g.v} active={outlierGap === g.v} onClick={() => { setOutlierGap(g.v); setLimit(PAGE) }}>{g.label}</Pill>
                ))}
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Require +EV</div>
              <PillGroup>
                <Pill active={!outlierReqEv} onClick={() => { setOutlierReqEv(false); setLimit(PAGE) }}>All Outliers</Pill>
                <Pill active={outlierReqEv} onClick={() => { setOutlierReqEv(true); setLimit(PAGE) }}>Verified +EV Only</Pill>
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Odds band</div>
              <PillGroup>
                {OUTLIER_ODDS_OPTIONS.map(b => (
                  <Pill key={b.v} active={outlierOddsBand === b.v} onClick={() => { setOutlierOddsBand(b.v); setLimit(PAGE) }}>{b.label}</Pill>
                ))}
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Min books</div>
              <PillGroup>
                {OUTLIER_MIN_BOOKS.map(b => (
                  <Pill key={b.v} active={outlierMinBooks === b.v} onClick={() => { setOutlierMinBooks(b.v); setLimit(PAGE) }}>{b.label}</Pill>
                ))}
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">VIEW</div>
              <PillGroup>
                <Pill active={outlierView === 'cards'} onClick={() => setOutlierView('cards')}>Visual Cards</Pill>
                <Pill active={outlierView === 'table'} onClick={() => setOutlierView('table')}>Compact Table</Pill>
              </PillGroup>
            </div>
          </>
        )}

        {mode === 'arb' && (
          <>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">SHOW</div>
              <PillGroup>
                <Pill active={arbView === 'live'} onClick={() => { setArbView('live'); setLimit(PAGE) }}>LIVE ARBS (&gt; 0%)</Pill>
                <Pill active={arbView === 'near'} onClick={() => { setArbView('near'); setLimit(PAGE) }}>NEAR ARBS (&gt; -1.5%)</Pill>
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Exchanges</div>
              <PillGroup>
                <Pill active={exchanges} onClick={() => setExchanges(v => !v)}>Include Kalshi / Novig</Pill>
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">OUTLAY ($)</div>
              <input
                inputMode="decimal"
                value={outlayText}
                onChange={e => setOutlayText(e.target.value.replace(/[^0-9.]/g, ''))}
                style={{ background: 'var(--color-background-secondary-default)', color: 'var(--color-text-primary)', border: `1px solid ${HAIR}`, borderRadius: '6px', height: '34px', padding: '0 10px', fontSize: '12px', width: '120px', fontFamily: 'var(--font-mono), monospace' }}
              />
            </div>
          </>
        )}

        {mode === 'middles' && (
          <>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Max cost</div>
              <PillGroup>
                {MID_COST.map(c => <Pill key={c} active={midCost === c} onClick={() => { setMidCost(c); setLimit(PAGE) }}>{`${c}%`}</Pill>)}
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Value</div>
              <PillGroup>
                <Pill active={midPosEv} onClick={() => { setMidPosEv(v => !v); setLimit(PAGE) }}>+EV ONLY</Pill>
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Window</div>
              <PillGroup>
                {MID_GAP.map(g => <Pill key={g.v} active={midGap === g.v} onClick={() => { setMidGap(g.v); setLimit(PAGE) }}>{g.label}</Pill>)}
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Exchanges</div>
              <PillGroup>
                <Pill active={exchanges} onClick={() => setExchanges(v => !v)}>Include Kalshi / Novig</Pill>
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">OUTLAY ($)</div>
              <input
                inputMode="decimal"
                value={outlayText}
                onChange={e => setOutlayText(e.target.value.replace(/[^0-9.]/g, ''))}
                style={{ background: 'var(--color-background-secondary-default)', color: 'var(--color-text-primary)', border: `1px solid ${HAIR}`, borderRadius: '6px', height: '34px', padding: '0 10px', fontSize: '12px', width: '120px', fontFamily: 'var(--font-mono), monospace' }}
              />
            </div>
          </>
        )}

        {mode === 'longshots' && (
          <>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Strategy</div>
              <PillGroup>
                <Pill active={longSub === 'all'} onClick={() => { setLongSub('all'); setLimit(PAGE) }}>All</Pill>
                <Pill active={longSub === 'plus_ev_over'} onClick={() => { setLongSub('plus_ev_over'); setLimit(PAGE) }}>Buy Over</Pill>
                <Pill active={longSub === 'fade_under'} onClick={() => { setLongSub('fade_under'); setLimit(PAGE) }}>Fade Under</Pill>
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Min payout</div>
              <PillGroup>
                {ODDS_MIN.map(m => <Pill key={m.v} active={oddsMin === m.v} onClick={() => { setOddsMin(m.v); setLimit(PAGE) }}>{m.label}</Pill>)}
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Min EV</div>
              <PillGroup>
                {MIN_EV.map(m => <Pill key={m.v} active={minEv === m.v} onClick={() => setMinEv(m.v)}>{m.label}</Pill>)}
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Fair source</div>
              <PillGroup>
                <Pill active={useCurve} onClick={() => setUseCurve(true)}>Multi-Book + Curve</Pill>
                <Pill active={!useCurve} onClick={() => setUseCurve(false)}>Exact Books Only</Pill>
              </PillGroup>
            </div>
          </>
        )}

        {mode === 'sgp' && (
          <>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Archetype</div>
              <PillGroup>
                {SGP_ARCHETYPE_OPTIONS.map(a => (
                  <Pill key={a.v} active={sgpArch === a.v} onClick={() => { setSgpArch(a.v); setLimit(PAGE) }}>{a.label}</Pill>
                ))}
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Min EV</div>
              <PillGroup>
                {SGP_MIN_EV_OPTIONS.map(e => (
                  <Pill key={e.v} active={sgpMinEv === e.v} onClick={() => { setSgpMinEv(e.v); setLimit(PAGE) }}>{e.label}</Pill>
                ))}
              </PillGroup>
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">BOOK</div>
              <Select
                id="mobile-sgp-book"
                value={sgpBook}
                onChange={v => { setSgpBook(v); setLimit(PAGE) }}
                options={[
                  { v: 'all', l: 'All Books' },
                  ...columns.filter(c => !c.offshore).map(c => ({ v: c.key, l: bookDisplay(c.key).label })),
                ]}
              />
            </div>
            <div>
              <div className="ui-heading mb-1.5 text-micro text-text-secondary">Fair source</div>
              <PillGroup>
                <Pill active={useCurve} onClick={() => setUseCurve(true)}>Multi-Book + Curve</Pill>
                <Pill active={!useCurve} onClick={() => setUseCurve(false)}>Exact Books Only</Pill>
              </PillGroup>
            </div>
          </>
        )}

          <div style={{ borderTop: `1px solid ${HAIR}`, paddingTop: '12px' }}>
            <div className="ui-heading mb-1.5 text-micro text-text-secondary">Prop market</div>
            <select
              id="mobile-odds-stat"
              value={stat}
              onChange={e => { setStat(e.target.value); setLimit(PAGE) }}
              style={{
                width: '100%',
                background: 'var(--color-background-secondary-default)',
                color: 'var(--color-text-primary)',
                border: `1px solid ${HAIR}`,
                borderRadius: '8px',
                minHeight: '40px',
                padding: '0 12px',
                fontSize: '13px',
                cursor: 'pointer',
              }}
            >
              <option value="all">All props</option>
              {statOptions.map(s => <option key={s} value={s}>{STAT_LABEL[s] ?? s}</option>)}
            </select>
          </div>

          <div>
            <div className="ui-heading mb-1.5 text-micro text-text-secondary">GAME</div>
            <select
              id="mobile-odds-game"
              value={game}
              onChange={e => { setGame(e.target.value); setLimit(PAGE) }}
              style={{
                width: '100%',
                background: 'var(--color-background-secondary-default)',
                color: 'var(--color-text-primary)',
                border: `1px solid ${HAIR}`,
                borderRadius: '8px',
                minHeight: '40px',
                padding: '0 12px',
                fontSize: '13px',
                cursor: 'pointer',
              }}
            >
              <option value="all">All games</option>
              {games.map(g => <option key={g.key} value={g.key}>{`${g.label} ${startLabel(g.start)}`}</option>)}
            </select>
          </div>

        <MobileBookChecklist
          title="SPORTSBOOKS"
          subtitle="Columns shown and books you can bet at. Offshore books show price but never count as best."
          books={bookList}
          selected={[...shownSet]}
          onChange={next => setShown(next)}
          presets={[
            { label: 'Defaults', keys: defaultShown },
            ...(myBooks.enabled ? [{ label: 'My books', keys: myBooks.books }] : []),
            { label: 'All', keys: bookList.map(b => b.key) },
            { label: 'None', keys: [] },
          ]}
        />

        <MobileBookChecklist
          title="REFERENCE BOOKS"
          subtitle={`Fair value is the equal-weight average of these books' de-vigged lines. None selected uses every book. ${refsNote}`}
          books={bookList}
          selected={refs}
          onChange={setRefs}
          emptyMeansAll
          presets={[
            { label: 'Sharp', keys: SHARP_PRESET.filter(k => bookList.some(b => b.key === k)) },
            { label: 'All', keys: [] },
          ]}
          extraHeader={
            <div className="flex gap-2 items-center mb-1">
              <span className="ui-heading text-micro text-text-secondary">Devig method</span>
              <PillGroup>
                <Pill active={method === 'proportional'} onClick={() => setMethod('proportional')}>Proportional</Pill>
                <Pill active={method === 'power'} onClick={() => setMethod('power')}>Power</Pill>
              </PillGroup>
            </div>
          }
        />
      </div>
    </MobileBottomSheet>
  )
}
