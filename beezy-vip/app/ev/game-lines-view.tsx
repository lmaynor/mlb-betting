'use client'

import { BookLogo } from '@/components/ui/book-logo'
import { formatOdds } from '@/lib/odds'
import { bookDisplay, SURFACE_CARD } from '@/lib/tokens'
import {
  expectedMarginLabel, highlightModelDiff,
  type GamePricedGameView, type GameRowView,
} from '@/lib/game-lines'

const HAIR = 'var(--color-separator-border)'
const GREEN = '#57F287'
const SKY = '#38bdf8'

const CT_TIME = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', weekday: 'short', hour: 'numeric', minute: '2-digit' })

function startLabel(iso: string): string {
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '' : CT_TIME.format(d).replace(' AM', 'a').replace(' PM', 'p')
}

function BookTag({ book, size = 16 }: { book: string; size?: number }) {
  return (
    <span className="inline-flex items-center gap-[6px]">
      <BookLogo book={book} size={size} />
      <span className="text-[11.5px] text-[var(--color-neutral-300)]">{bookDisplay(book).label}</span>
    </span>
  )
}

export function GameCard({ game }: { game: GamePricedGameView }) {
  const sport = (game.sport || '').toLowerCase()
  const isNba = sport === 'nba'
  const isNhl = sport === 'nhl'

  const expMargin = expectedMarginLabel(game)

  return (
    <div style={{ ...SURFACE_CARD, padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div className="flex justify-between items-center gap-[8px] flex-wrap">
        <div className="flex items-center gap-[8px]">
          <span className="mono" style={{
            fontSize: '11px',
            fontWeight: 700,
            padding: '2px 7px',
            borderRadius: '4px',
            background: isNba ? 'rgba(249,115,22,0.15)' : 'rgba(56,189,248,0.15)',
            color: isNba ? '#f97316' : '#38bdf8',
            border: `1px solid ${isNba ? 'rgba(249,115,22,0.35)' : 'rgba(56,189,248,0.35)'}`,
          }}>
            {sport.toUpperCase()}
          </span>
          <span className="text-[15px] font-bold text-text-primary">
            {game.away_team} @ {game.home_team}
          </span>
        </div>
        <div className="mono text-[11px] text-text-secondary">
          {game.game_date}
        </div>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
        gap: '8px',
        padding: '10px 12px',
        background: 'var(--color-background-secondary-default)',
        borderRadius: '6px',
        border: `1px solid ${HAIR}`,
      }}>
        {isNba && (
          <>
            <div>
              <div className="dell-heading text-text-secondary">MODEL MARGIN</div>
              <div className="mono text-[13px] font-bold text-text-primary mt-[2px]">
                {expMargin ?? '--'}
              </div>
            </div>
            <div>
              <div className="dell-heading text-text-secondary">MODEL TOTAL</div>
              <div className="mono text-[13px] font-bold text-text-primary mt-[2px]">
                {game.mu_total != null ? `${game.mu_total.toFixed(1)} pts` : '--'}
              </div>
            </div>
            <div>
              <div className="dell-heading text-text-secondary">EXPECTED PACE</div>
              <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: SKY, marginTop: '2px' }}>
                {game.exp_pace != null ? `${game.exp_pace.toFixed(1)} poss` : '--'}
              </div>
            </div>
          </>
        )}
        {isNhl && (
          <>
            <div>
              <div className="dell-heading text-text-secondary">EXPECTED GOALS</div>
              <div className="mono text-[13px] font-bold text-text-primary mt-[2px]">
                {game.lambda_a != null && game.lambda_h != null
                  ? `${game.away_team} ${game.lambda_a.toFixed(1)} - ${game.home_team} ${game.lambda_h.toFixed(1)}`
                  : '--'}
              </div>
            </div>
            <div>
              <div className="dell-heading text-text-secondary">MODEL TOTAL</div>
              <div className="mono text-[13px] font-bold text-text-primary mt-[2px]">
                {game.exp_total != null ? `${game.exp_total.toFixed(2)} goals` : '--'}
              </div>
            </div>
            <div>
              <div className="dell-heading text-text-secondary">DISPERSION</div>
              <div className="mono" style={{ fontSize: '13px', fontWeight: 700, color: SKY, marginTop: '2px' }}>
                {game.sigma_total != null ? `\u03c3 ${game.sigma_total.toFixed(1)}` : '--'}
              </div>
            </div>
          </>
        )}
      </div>

      <div className="overflow-x-auto">
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${HAIR}`, color: 'var(--color-text-secondary)', fontSize: '10px' }}>
              <th scope="col" style={{ textAlign: 'left', padding: '6px 8px', fontWeight: 600 }}>MARKET / SIDE</th>
              <th scope="col" style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 600 }}>LINE</th>
              <th scope="col" style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 600 }}>MODEL %</th>
              <th scope="col" style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 600 }}>MKT FAIR</th>
              <th scope="col" style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 600 }}>MODEL DIFF</th>
              <th scope="col" style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 600 }}>BEST PRICE</th>
            </tr>
          </thead>
          <tbody>
            {game.filteredMarkets.map((m, idx) => {
              // green only for a validated feed: an unvalidated model difference is shown, never highlighted
              const hasEdge = highlightModelDiff(m.model_edge, game.validated)
              const sideLabel = m.side === 'home' ? game.home_team : (m.side === 'away' ? game.away_team : m.side.toUpperCase())
              return (
                <tr key={`${m.system}-${m.side}-${idx}`} style={{ borderBottom: '1px solid var(--color-border-table)', background: hasEdge ? 'rgba(87,242,135,0.03)' : 'transparent' }}>
                  <td style={{ padding: '8px', color: 'var(--color-text-primary)' }}>
                    <div className="font-semibold">{m.market_label}</div>
                    <div className="mono text-[11px] text-[var(--color-neutral-300)]">{sideLabel}</div>
                  </td>
                  <td className="mono" style={{ textAlign: 'right', padding: '8px', color: 'var(--color-text-primary)', fontWeight: 600 }}>
                    {m.line != null ? (m.line > 0 ? `+${m.line}` : `${m.line}`) : 'ML'}
                  </td>
                  <td className="mono" style={{ textAlign: 'right', padding: '8px', color: 'var(--color-text-primary)' }}>
                    {(m.p_model * 100).toFixed(1)}%
                  </td>
                  <td className="mono" style={{ textAlign: 'right', padding: '8px', color: 'var(--color-text-secondary)' }}>
                    {m.p_fair != null ? `${(m.p_fair * 100).toFixed(1)}%` : '--'}
                  </td>
                  <td className="mono" style={{ textAlign: 'right', padding: '8px', fontWeight: 700, color: hasEdge ? GREEN : (m.model_edge != null && m.model_edge < 0 ? '#ff5a4f' : 'var(--color-neutral-300)') }}>
                    {m.model_edge != null ? `${m_edge(m.model_edge)}` : '--'}
                  </td>
                  <td style={{ textAlign: 'right', padding: '8px' }}>
                    {m.best_odds != null ? (
                      <div className="inline-flex items-center gap-[6px] justify-end">
                        <span className="mono" style={{ fontSize: '12px', fontWeight: 700, color: hasEdge ? GREEN : 'var(--color-text-primary)' }}>
                          {formatOdds(m.best_odds)}
                        </span>
                        {m.best_book && <BookTag book={m.best_book} size={15} />}
                      </div>
                    ) : (
                      <span className="mono text-text-secondary">--</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function GameLinesTable({ rows, limit }: { rows: GameRowView[]; limit: number }) {
  return (
    <div className="odds-table-wrap" style={{ ...SURFACE_CARD, padding: 0 }}>
      <table className="odds-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: `1px solid ${HAIR}`, background: '#0d0f15' }}>
            <th scope="col" style={{ textAlign: 'left', minWidth: '70px', padding: '10px 12px' }}>SPORT</th>
            <th scope="col" style={{ textAlign: 'left', minWidth: '180px', padding: '10px 12px' }}>MATCHUP</th>
            <th scope="col" style={{ textAlign: 'left', minWidth: '120px', padding: '10px 12px' }}>MARKET</th>
            <th scope="col" style={{ textAlign: 'left', minWidth: '140px', padding: '10px 12px' }}>SELECTION / LINE</th>
            <th scope="col" style={{ textAlign: 'right', minWidth: '100px', padding: '10px 12px' }}>MODEL PROB</th>
            <th scope="col" style={{ textAlign: 'right', minWidth: '100px', padding: '10px 12px' }}>MKT FAIR</th>
            <th scope="col" style={{ textAlign: 'right', minWidth: '100px', padding: '10px 12px' }}>MODEL DIFF</th>
            <th scope="col" style={{ textAlign: 'right', minWidth: '100px', padding: '10px 12px' }}>MKT EV</th>
            <th scope="col" style={{ textAlign: 'right', minWidth: '160px', padding: '10px 12px' }}>BEST ODDS &amp; BOOK</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, limit).map((r, idx) => {
            const isNba = r.sport.toLowerCase() === 'nba'
            const hasEdge = highlightModelDiff(r.model_edge, r.validated)
            const bestOdds = r.best_over_odds ?? r.best_under_odds
            const bestBook = r.best_over_book ?? r.best_under_book
            const sideDisplay = r.team ? `${r.team} ${r.side.toUpperCase()}` : r.side.toUpperCase()
            const lineDisplay = r.line != null ? (r.line > 0 ? `+${r.line}` : `${r.line}`) : ''

            return (
              <tr key={`${r.game_key}-${r.system}-${r.side}-${idx}`} data-row="odds">
                <td style={{ padding: '9px 12px' }}>
                  <span className="mono" style={{
                    fontSize: '10.5px',
                    fontWeight: 700,
                    padding: '2px 6px',
                    borderRadius: '4px',
                    background: isNba ? 'rgba(249,115,22,0.15)' : 'rgba(56,189,248,0.15)',
                    color: isNba ? '#f97316' : '#38bdf8',
                    border: `1px solid ${isNba ? 'rgba(249,115,22,0.35)' : 'rgba(56,189,248,0.35)'}`,
                  }}>
                    {r.sport.toUpperCase()}
                  </span>
                </td>
                <td style={{ padding: '9px 12px', fontWeight: 600, color: 'var(--color-text-primary)' }}>
                  {r.player}
                  <div className="mono" style={{ fontSize: '10.5px', color: 'var(--color-text-secondary)', fontWeight: 400 }}>
                    {startLabel(r.commence)}
                  </div>
                </td>
                <td style={{ padding: '9px 12px', color: 'var(--color-neutral-300)' }}>
                  {r.stat_label}
                </td>
                <td className="mono" style={{ padding: '9px 12px', fontWeight: 600, color: 'var(--color-text-primary)' }}>
                  {sideDisplay} {lineDisplay}
                </td>
                <td className="mono" style={{ textAlign: 'right', padding: '9px 12px', color: 'var(--color-text-primary)' }}>
                  {(r.p_model_side * 100).toFixed(1)}%
                </td>
                <td className="mono" style={{ textAlign: 'right', padding: '9px 12px', color: 'var(--color-text-secondary)' }}>
                  {r.p_mkt_side != null ? `${(r.p_mkt_side * 100).toFixed(1)}%` : '--'}
                </td>
                <td className="mono" style={{ textAlign: 'right', padding: '9px 12px', fontWeight: 700, color: hasEdge ? GREEN : (r.model_edge != null && r.model_edge < 0 ? '#ff5a4f' : 'var(--color-neutral-300)') }}>
                  {r.model_edge != null ? `${m_edge(r.model_edge)}` : '--'}
                </td>
                <td className="mono" style={{ textAlign: 'right', padding: '9px 12px', color: highlightModelDiff(r.market_ev, r.validated, 0.0000001) ? GREEN : 'var(--color-text-secondary)' }}>
                  {r.market_ev != null ? `${r.market_ev > 0 ? '+' : ''}${(r.market_ev * 100).toFixed(1)}%` : '--'}
                </td>
                <td style={{ textAlign: 'right', padding: '9px 12px' }}>
                  {bestOdds != null ? (
                    <div className="inline-flex items-center gap-[6px] justify-end">
                      <span className="mono" style={{ fontSize: '12px', fontWeight: 700, color: hasEdge ? GREEN : 'var(--color-text-primary)' }}>
                        {formatOdds(bestOdds)}
                      </span>
                      {bestBook && <BookTag book={bestBook} size={15} />}
                    </div>
                  ) : (
                    <span className="mono text-text-secondary">--</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function m_edge(edge: number): string {
  return `${edge > 0 ? '+' : ''}${(edge * 100).toFixed(1)}%`
}

/** The visible status badge for the Game Lines view: the feed's own label, or the default UNVALIDATED text. */
export function GameLinesNotice({ label, validated }: { label: string; validated: boolean }) {
  return (
    <div role="note" style={{ ...SURFACE_CARD, padding: '8px 12px', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '8px 12px', flexWrap: 'wrap', borderLeft: `3px solid ${validated ? GREEN : '#facc15'}` }}>
      <span className="mono" data-testid="game-lines-label" style={{ fontSize: '10.5px', fontWeight: 700, letterSpacing: '0.04em', color: validated ? GREEN : '#facc15' }}>{label.toUpperCase()}</span>
      <span className="text-[12px] text-[var(--color-neutral-300)]">
        MODEL DIFF is the model&apos;s probability minus the market&apos;s fair probability. It is not an edge until a backtest supports it.
      </span>
    </div>
  )
}

export function GameLinesView({
  pricedGames,
  rows,
  view,
  limit,
  onSwitchView,
  isMobile,
}: {
  pricedGames: GamePricedGameView[]
  rows: GameRowView[]
  view: 'cards' | 'table'
  limit: number
  onSwitchView: (v: 'cards' | 'table') => void
  isMobile: boolean
}) {
  return (
    <div>
      {isMobile && (
        <div className="flex justify-between items-center mb-[10px]">
          <div style={{ display: 'inline-flex', padding: '2px', background: 'var(--color-background-secondary-default)', border: `1px solid ${HAIR}`, borderRadius: '6px' }}>
            <button
              type="button"
              onClick={() => onSwitchView('cards')}
              style={{
                fontFamily: 'var(--font-text), sans-serif',
                fontSize: '11px',
                fontWeight: 600,
                padding: '4px 10px',
                borderRadius: '4px',
                border: 0,
                cursor: 'pointer',
                background: view === 'cards' ? 'var(--color-background-tertiary-default)' : 'transparent',
                color: view === 'cards' ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
              }}
            >
              Game Cards ({pricedGames.length})
            </button>
            <button
              type="button"
              onClick={() => onSwitchView('table')}
              style={{
                fontFamily: 'var(--font-text), sans-serif',
                fontSize: '11px',
                fontWeight: 600,
                padding: '4px 10px',
                borderRadius: '4px',
                border: 0,
                cursor: 'pointer',
                background: view === 'table' ? 'var(--color-background-tertiary-default)' : 'transparent',
                color: view === 'table' ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
              }}
            >
              Data Matrix ({rows.length})
            </button>
          </div>
        </div>
      )}

      {view === 'cards' ? (
        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(auto-fill, minmax(520px, 1fr))', gap: '16px' }}>
          {pricedGames.slice(0, limit).map((g, idx) => (
            <GameCard key={`${g.sport}-${g.game_key}-${idx}`} game={g} />
          ))}
        </div>
      ) : (
        <GameLinesTable rows={rows} limit={limit} />
      )}
    </div>
  )
}
