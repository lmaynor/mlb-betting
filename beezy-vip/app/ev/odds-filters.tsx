import { bookDisplay } from '@/lib/tokens'
import { LeagueFilter } from '@/components/ui/league-filter'
import { STAT_LABEL, type SideFilter } from '@/lib/odds-screen'
import { SportFilter, SortKey, HAIR, GREEN, SHARP_PRESET, MIN_EV, ODDS_MIN, PAGE, MID_COST, MID_GAP, OUTLIER_GAP_OPTIONS, OUTLIER_ODDS_OPTIONS, OUTLIER_MIN_BOOKS, MODES, SGP_ARCHETYPE_OPTIONS, SGP_MIN_EV_OPTIONS } from './odds-constants'
import { startLabel } from './odds-format'
import { Field, Select, Pill, PillGroup, BookPicker } from './odds-controls'
import { OddsScreen } from './use-odds-screen'

export function MobileFilterBar({ odds }: { odds: OddsScreen }) {
  const { mode, sport, setSport, sportOptions, setStat, setGame, q, setQ, setLimit, setMobileFiltersOpen, activeFilterCount, switchMode, badgeFor } = odds
  return (
    <section className="mb-2.5 rounded-xl border border-separator-border bg-background-primary-default shadow-card" aria-label="Filters">
      <div className="grid grid-cols-2 gap-1.5 px-2.5 py-2 [&>button]:w-full [&>button]:whitespace-nowrap">
        {MODES.map(m => {
          const n = badgeFor(m.key)
          const hot = n != null && n > 0 && (m.key === 'arb' || m.key === 'outliers')
          return (
            <Pill key={m.key} active={mode === m.key} onClick={() => switchMode(m.key)}>
              {m.label}
              <span className="mono" style={{ marginLeft: '6px', fontSize: '11px', color: hot ? GREEN : 'var(--color-text-secondary)' }}>{n ?? '\u00b7'}</span>
            </Pill>
          )
        })}
      </div>

      <div style={{ padding: '8px 10px', borderTop: `1px solid ${HAIR}`, display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
        <LeagueFilter value={sport} live={sportOptions} onChange={v => { setSport(v); setStat('all'); setGame('all'); setLimit(PAGE) }} />

        <input
          aria-label="Search player or team"
          placeholder="Search player / team"
          value={q}
          onChange={e => { setQ(e.target.value); setLimit(PAGE) }}
          style={{
            background: 'var(--color-background-secondary-default)',
            color: 'var(--color-text-primary)',
            border: `1px solid ${HAIR}`,
            borderRadius: '6px',
            height: '32px',
            padding: '0 8px',
            fontSize: '12px',
            flex: '1 1 100%',
            order: 3,
          }}
        />

        <button
          type="button"
          onClick={() => setMobileFiltersOpen(true)}
          className="btn btn-ghost"
          style={{
            padding: '5px 10px',
            fontSize: '12px',
            height: '32px',
            flexShrink: 0,
            marginLeft: 'auto',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '5px',
            borderColor: activeFilterCount > 0 ? GREEN : HAIR,
            color: activeFilterCount > 0 ? GREEN : 'var(--color-text-primary)',
          }}
        >
          <span>Filters</span>
          {activeFilterCount > 0 && (
            <span
              className="mono"
              style={{
                fontSize: '10px',
                background: 'rgba(87, 242, 135, 0.15)',
                color: GREEN,
                padding: '1px 5px',
                borderRadius: '10px',
                fontWeight: 600,
              }}
            >
              {activeFilterCount}
            </span>
          )}
        </button>
      </div>
    </section>
  )
}

export function DesktopFilterBar({ odds }: { odds: OddsScreen }) {
  const { mode, sport, setSport, sportOptions, stat, setStat, game, setGame, side, setSide, lines, setLines, minEv, setMinEv, sgpArch, setSgpArch, sgpMinEv, setSgpMinEv, sgpBook, setSgpBook, q, setQ, method, setMethod, refs, setRefs, setShown, sort, setSort, setLimit, arbView, setArbView, exchanges, setExchanges, outlayText, setOutlayText, midCost, setMidCost, midGap, setMidGap, midPosEv, setMidPosEv, longSub, setLongSub, oddsMin, setOddsMin, useCurve, setUseCurve, outlierGap, setOutlierGap, outlierReqEv, setOutlierReqEv, outlierOddsBand, setOutlierOddsBand, outlierMinBooks, setOutlierMinBooks, outlierView, setOutlierView, myBooks, switchMode, bookList, defaultShown, shownSet, columns, statOptions, games, badgeFor, refsNote } = odds
  return (
    <section className="mb-2.5 rounded-xl border border-separator-border bg-background-primary-default shadow-card" aria-label="Filters">
      <div className="py-2.5 px-3.5 flex gap-y-2.5 gap-x-4.5 flex-wrap items-center">
        <PillGroup label="Mode">
          {MODES.map(m => {
            const n = badgeFor(m.key)
            const hot = n != null && n > 0 && (m.key === 'arb' || m.key === 'outliers')
            return (
              <Pill key={m.key} active={mode === m.key} onClick={() => switchMode(m.key)}>
                {m.label}
                <span className="mono" style={{ marginLeft: '7px', fontSize: '11px', color: hot ? GREEN : 'var(--color-text-secondary)' }}>{n ?? '\u00b7'}</span>
              </Pill>
            )
          })}
        </PillGroup>
          <Field label="Sport">
            <LeagueFilter value={sport} live={sportOptions} onChange={v => { setSport(v); setStat('all'); setGame('all'); setLimit(PAGE) }} />
          </Field>
          <Field label="Prop" htmlFor="odds-stat">
            <Select id="odds-stat" value={stat} onChange={v => { setStat(v); setLimit(PAGE) }}
              options={[{ v: 'all', l: 'All props' }, ...statOptions.map(s => ({ v: s, l: STAT_LABEL[s] ?? s }))]} />
          </Field>
          <Field label="Game" htmlFor="odds-game">
            <Select id="odds-game" value={game} onChange={v => { setGame(v); setLimit(PAGE) }}
              options={[{ v: 'all', l: 'All games' }, ...games.map(g => ({ v: g.key, l: `${g.label} ${startLabel(g.start)}` }))]} />
          </Field>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginLeft: 'auto', flexWrap: 'wrap' }}>
          <input
            aria-label="Search player or team" placeholder="Search player / team" value={q} onChange={e => { setQ(e.target.value); setLimit(PAGE) }}
            style={{ background: 'var(--color-background-secondary-default)', color: 'var(--color-text-primary)', border: `1px solid ${HAIR}`, borderRadius: '6px', height: '34px', padding: '0 10px', fontSize: '12px', width: '190px' }}
          />
          <BookPicker
            label={`BOOKS (${columns.length})`}
            books={bookList} selected={[...shownSet]} onChange={next => setShown(next)}
            presets={[
              { label: 'Defaults', keys: defaultShown },
              ...(myBooks.enabled ? [{ label: 'My books', keys: myBooks.books }] : []),
              { label: 'All', keys: bookList.map(b => b.key) },
              { label: 'None', keys: [] },
            ]}
            note="Columns shown and books you can bet at. Offshore books show their price but never count as the best price."
            align="right"
          />
          <BookPicker
            label={refs.length ? `REFERENCE BOOKS (${refs.length})` : 'REFERENCE BOOKS (ALL)'}
            books={bookList} selected={refs} onChange={setRefs} emptyMeansAll
            presets={[
              { label: 'Sharp', keys: SHARP_PRESET.filter(k => bookList.some(b => b.key === k)) },
              { label: 'All', keys: [] },
            ]}
            note={`Fair value is the equal-weight average of these books' de-vigged lines. None selected uses every book. ${refsNote}`}
            align="right"
            header={
              <div className="flex gap-2 items-center">
                <span className="ui-heading text-micro text-text-secondary">Devig</span>
                <PillGroup>
                  <Pill active={method === 'proportional'} onClick={() => setMethod('proportional')} title="Remove the vig in proportion to each side's implied probability">Proportional</Pill>
                  <Pill active={method === 'power'} onClick={() => setMethod('power')} title="Raise both implied probabilities to the power that sums them to 1 (shifts less to the favourite)">Power</Pill>
                </PillGroup>
              </div>
            }
          />
        </div>
      </div>

      <div style={{ padding: '8px 14px', borderTop: `1px solid ${HAIR}`, display: 'flex', gap: '10px 18px', flexWrap: 'wrap', alignItems: 'center' }}>
        {mode === 'ev' && (
          <>
            <Field label="Lines">
              <PillGroup>
                <Pill active={lines === 'main'} onClick={() => setLines('main')} title="Standard over/under line quoted by most sportsbooks">Main Lines</Pill>
                <Pill active={lines === 'all'} onClick={() => setLines('all')} title="Includes alternate milestone rungs and shifted lines">Main + Alt Ladders</Pill>
              </PillGroup>
            </Field>
            <Field label="O/U">
              <PillGroup>
                {(['both', 'over', 'under'] as SideFilter[]).map(s => <Pill key={s} active={side === s} onClick={() => setSide(s)}>{s === 'both' ? 'BOTH' : s === 'over' ? 'OVER' : 'UNDER'}</Pill>)}
              </PillGroup>
            </Field>
            <Field label="Min EV">
              <PillGroup>
                {MIN_EV.map(m => <Pill key={m.v} active={minEv === m.v} onClick={() => setMinEv(m.v)}>{m.label}</Pill>)}
              </PillGroup>
            </Field>
            <Field label="Sort" htmlFor="odds-sort">
              <Select id="odds-sort" value={sort} onChange={v => setSort(v as SortKey)} options={[{ v: 'ev', l: 'Best EV %' }, { v: 'time', l: 'Game time' }, { v: 'player', l: 'Player' }]} />
            </Field>
          </>
        )}
        {mode === 'outliers' && (
          <>
            <Field label="Min gap">
              <PillGroup>
                {OUTLIER_GAP_OPTIONS.map(g => (
                  <Pill key={g.v} active={outlierGap === g.v} onClick={() => { setOutlierGap(g.v); setLimit(PAGE) }}>
                    {g.label}
                  </Pill>
                ))}
              </PillGroup>
            </Field>
            <Field label="Require +EV">
              <PillGroup>
                <Pill active={!outlierReqEv} onClick={() => { setOutlierReqEv(false); setLimit(PAGE) }}>All Outliers</Pill>
                <Pill active={outlierReqEv} onClick={() => { setOutlierReqEv(true); setLimit(PAGE) }} title="Require verified positive EV against reference books or player curve">Verified +EV Only</Pill>
              </PillGroup>
            </Field>
            <Field label="Odds band">
              <PillGroup>
                {OUTLIER_ODDS_OPTIONS.map(b => (
                  <Pill key={b.v} active={outlierOddsBand === b.v} onClick={() => { setOutlierOddsBand(b.v); setLimit(PAGE) }}>
                    {b.label}
                  </Pill>
                ))}
              </PillGroup>
            </Field>
            <Field label="Min books">
              <PillGroup>
                {OUTLIER_MIN_BOOKS.map(b => (
                  <Pill key={b.v} active={outlierMinBooks === b.v} onClick={() => { setOutlierMinBooks(b.v); setLimit(PAGE) }}>
                    {b.label}
                  </Pill>
                ))}
              </PillGroup>
            </Field>
            <Field label="View">
              <PillGroup>
                <Pill active={outlierView === 'cards'} onClick={() => setOutlierView('cards')}>Visual Cards</Pill>
                <Pill active={outlierView === 'table'} onClick={() => setOutlierView('table')}>Compact Table</Pill>
              </PillGroup>
            </Field>
          </>
        )}
        {mode === 'arb' && (
          <>
            <Field label="Show">
              <PillGroup>
                <Pill active={arbView === 'live'} onClick={() => { setArbView('live'); setLimit(PAGE) }}>LIVE ARBS (&gt; 0%)</Pill>
                <Pill active={arbView === 'near'} onClick={() => { setArbView('near'); setLimit(PAGE) }} title="Within 1.5% of break-even: bonus-bet conversion and near zero-hold line shopping">NEAR ARBS (&gt; -1.5%)</Pill>
              </PillGroup>
            </Field>
            <Field label="Exchanges">
              <PillGroup>
                <Pill active={exchanges} onClick={() => setExchanges(v => !v)} title="Kalshi, Novig and ProphetX quotes. Kalshi's taker fee is taken off its leg; Novig charges none before the game.">INCLUDE KALSHI / NOVIG (FEE-ADJUSTED)</Pill>
              </PillGroup>
            </Field>
            <Field label="Outlay" htmlFor="odds-outlay">
              <span className="inline-flex items-center gap-1">
                <span className="mono text-text-secondary text-compact">$</span>
                <input
                  id="odds-outlay" inputMode="decimal" value={outlayText} onChange={e => setOutlayText(e.target.value.replace(/[^0-9.]/g, ''))}
                  style={{ background: 'var(--color-background-secondary-default)', color: 'var(--color-text-primary)', border: `1px solid ${HAIR}`, borderRadius: '6px', height: '34px', padding: '0 10px', fontSize: '12px', width: '90px', fontFamily: 'var(--font-mono), monospace' }}
                />
              </span>
            </Field>
          </>
        )}
        {mode === 'middles' && (
          <>
            <Field label="Max cost">
              <PillGroup>
                {MID_COST.map(c => <Pill key={c} active={midCost === c} onClick={() => { setMidCost(c); setLimit(PAGE) }} title="The most a miss may cost, as a share of the outlay">{`${c}%`}</Pill>)}
              </PillGroup>
            </Field>
            <Field label="Value">
              <PillGroup>
                <Pill active={midPosEv} onClick={() => { setMidPosEv(v => !v); setLimit(PAGE) }} title="Only middles whose window chance, estimated from the reference books' fair price at each line, beats the break-even chance">+EV ONLY</Pill>
              </PillGroup>
            </Field>
            <Field label="Window">
              <PillGroup>
                {MID_GAP.map(g => <Pill key={g.v} active={midGap === g.v} onClick={() => { setMidGap(g.v); setLimit(PAGE) }} title="Narrowest gap between the two lines, in points">{g.label}</Pill>)}
              </PillGroup>
            </Field>
            <Field label="Exchanges">
              <PillGroup>
                <Pill active={exchanges} onClick={() => setExchanges(v => !v)} title="Kalshi, Novig and ProphetX quotes. Kalshi's taker fee is taken off its leg; Novig charges none before the game.">INCLUDE KALSHI / NOVIG (FEE-ADJUSTED)</Pill>
              </PillGroup>
            </Field>
            <Field label="Outlay" htmlFor="odds-outlay-mid">
              <span className="inline-flex items-center gap-1">
                <span className="mono text-text-secondary text-compact">$</span>
                <input
                  id="odds-outlay-mid" inputMode="decimal" value={outlayText} onChange={e => setOutlayText(e.target.value.replace(/[^0-9.]/g, ''))}
                  style={{ background: 'var(--color-background-secondary-default)', color: 'var(--color-text-primary)', border: `1px solid ${HAIR}`, borderRadius: '6px', height: '34px', padding: '0 10px', fontSize: '12px', width: '90px', fontFamily: 'var(--font-mono), monospace' }}
                />
              </span>
            </Field>
          </>
        )}
        {mode === 'longshots' && (
          <>
            <Field label="Strategy">
              <PillGroup>
                <Pill active={longSub === 'all'} onClick={() => { setLongSub('all'); setLimit(PAGE) }} title="Every alt line and every over paying +200 or more, with both angles priced">All Tail Lines</Pill>
                <Pill active={longSub === 'plus_ev_over'} onClick={() => { setLongSub('plus_ev_over'); setLimit(PAGE) }} title="Longshot Over/Yes price pays more than its true fair probability">Buy Over (+EV Milestone)</Pill>
                <Pill active={longSub === 'fade_under'} onClick={() => { setLongSub('fade_under'); setLimit(PAGE) }} title="House-side play: fade an overpriced retail milestone by buying Under or Exchange NO">Lay the Longshot (Buy Under / NO)</Pill>
              </PillGroup>
            </Field>
            <Field label="Min payout">
              <PillGroup>
                {ODDS_MIN.map(m => <Pill key={m.v} active={oddsMin === m.v} onClick={() => { setOddsMin(m.v); setLimit(PAGE) }}>{m.label}</Pill>)}
              </PillGroup>
            </Field>
            <Field label="Min EV">
              <PillGroup>
                {MIN_EV.map(m => <Pill key={m.v} active={minEv === m.v} onClick={() => setMinEv(m.v)}>{m.label}</Pill>)}
              </PillGroup>
            </Field>
            <Field label="Fair source">
              <PillGroup>
                <Pill active={useCurve} onClick={() => setUseCurve(true)} title="Uses exact multi-book de-vigged fair odds when available, or interpolates from the player's main-line curve when fewer than 2 books price an alt rung">Multi-Book + Player Curve</Pill>
                <Pill active={!useCurve} onClick={() => setUseCurve(false)} title="Requires at least 2 reference books quoting this exact alternate line">Exact Books Only</Pill>
              </PillGroup>
            </Field>
          </>
        )}
        {mode === 'sgp' && (
          <>
            <Field label="Archetype">
              <PillGroup>
                {SGP_ARCHETYPE_OPTIONS.map(a => (
                  <Pill key={a.v} active={sgpArch === a.v} onClick={() => { setSgpArch(a.v); setLimit(PAGE) }}>
                    {a.label}
                  </Pill>
                ))}
              </PillGroup>
            </Field>
            <Field label="Min EV">
              <PillGroup>
                {SGP_MIN_EV_OPTIONS.map(e => (
                  <Pill key={e.v} active={sgpMinEv === e.v} onClick={() => { setSgpMinEv(e.v); setLimit(PAGE) }}>
                    {e.label}
                  </Pill>
                ))}
              </PillGroup>
            </Field>
            <Field label="Book" htmlFor="sgp-book">
              <Select
                id="sgp-book"
                value={sgpBook}
                onChange={v => { setSgpBook(v); setLimit(PAGE) }}
                options={[
                  { v: 'all', l: 'All Books' },
                  ...columns.filter(c => !c.offshore).map(c => ({ v: c.key, l: bookDisplay(c.key).label })),
                ]}
              />
            </Field>
            <Field label="Fair source">
              <PillGroup>
                <Pill active={useCurve} onClick={() => setUseCurve(true)} title="Uses multi-book de-vigged fair probabilities with monotone curve fallback">Multi-Book + Curve</Pill>
                <Pill active={!useCurve} onClick={() => setUseCurve(false)} title="Requires at least 2 reference books quoting each leg">Exact Books Only</Pill>
              </PillGroup>
            </Field>
          </>
        )}
      </div>
    </section>
  )
}
