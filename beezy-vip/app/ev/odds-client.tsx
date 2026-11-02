'use client'

import Link from 'next/link'
import { LockedNotice } from '@/components/picks/locked-notice'
import { PRE_LAUNCH } from '@/lib/launch'
import type { SportSlug } from '@/lib/sports'
import type { EvHeadline } from '@/lib/types'
import type { OddsBoard } from '@/lib/odds-screen'
import { LedgerStrip } from './ledger-strip'
import { cx } from '@/utils/cx'
import { PAGE } from './odds-constants'
import { ageLabel } from './odds-format'
import { useOddsScreen } from './use-odds-screen'
import { DesktopFilterBar, MobileFilterBar } from './odds-filters'
import { MobileFilterSheet } from './odds-filter-sheet'
import { OddsResults } from './odds-results'
import { OddsFootnote } from './odds-footnote'
import { MobileCompareSheet } from './odds-compare-sheet'

export function OddsClient({ initial, initialLocked, headline, fullAccess = false }: { initial: Partial<Record<SportSlug, OddsBoard>>; initialLocked: number; headline: EvHeadline | null; fullAccess?: boolean }) {
  const odds = useOddsScreen({ initial, initialLocked, fullAccess })
  const { isMobile, meta, nRows, loading, refresh, mode, limit, setLimit, locked, allRows, columns, inspectItem, setInspectItem } = odds

  return (
    <div style={{ maxWidth: '1680px', margin: '0 auto', padding: isMobile ? '12px 12px 64px' : '20px 24px 64px' }}>
      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-x-5 gap-y-3">
        <h1 className="text-title-2-bold text-text-primary">Odds Terminal</h1>
        <div className="flex flex-wrap items-center gap-3">
          <span className={cx('mono text-caption-1-regular', meta.stale ? 'text-warn' : 'text-text-secondary')}>
            {nRows} ROWS &middot; {meta.age != null ? `LINES ${ageLabel(meta.age).toUpperCase()} OLD` : 'OFFSEASON / NO GAMES'}{meta.sources.length ? ` \u00b7 ${meta.sources.join(' + ').toUpperCase()}` : ''}
          </span>
          <button onClick={refresh} disabled={loading} className="btn btn-ghost px-3 py-[5px] text-xs">{loading ? 'Refreshing...' : 'Refresh'}</button>
        </div>
      </div>

      {PRE_LAUNCH && (
        <div className="mb-2.5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[rgba(87,242,135,0.35)] bg-background-primary-default px-4 py-2.5 shadow-card">
          <div className="flex items-center gap-2.5">
            <span className="ui-heading rounded-chip bg-[var(--brand-dim)] px-2 py-[3px] tracking-caps text-signal">PRE-LAUNCH PREVIEW</span>
            <span className="text-body-2-regular text-text-secondary">The full Odds Terminal is unlocked for early visitors (included in All-Access Pro at launch).</span>
          </div>
          <Link href="/pricing" className="text-caption-1-semibold text-signal no-underline hover:underline">Lock pre-launch pricing &rarr;</Link>
        </div>
      )}

      <LedgerStrip h={headline} />

      {isMobile ? <MobileFilterBar odds={odds} /> : <DesktopFilterBar odds={odds} />}

      <MobileFilterSheet odds={odds} />

      <OddsResults odds={odds} />
      {nRows > limit && (
        <div className="flex justify-center mt-2.5">
          <button className="btn btn-ghost" style={{ padding: '6px 14px', fontSize: '12px' }} onClick={() => setLimit(l => l + PAGE)}>Show {Math.min(PAGE, nRows - limit)} more ({nRows - limit} remaining)</button>
        </div>
      )}
      <LockedNotice locked={locked} shown={allRows.length} what="prop lines" plan="The odds screen is part of All-Access Pro." />

      <OddsFootnote mode={mode} />

      <MobileCompareSheet
        item={inspectItem}
        columns={columns}
        onClose={() => setInspectItem(null)}
      />
    </div>
  )
}
