import Link from 'next/link'
import { holdReasonFor, type TopPick } from '@/lib/top-picks'

/**
 * The near-miss zone (Decision 17): on a thin card, the tracked and confluence candidates that did NOT clear
 * the gate, each with its derived hold reason, below a hard divider. These are shadow monitoring rows, not
 * plays: nothing here is graded, and the full live ranking is one click away in the Scanner.
 */
export function NearMissZone({ picks }: { picks: TopPick[] }) {
  if (picks.length === 0) return null
  return (
    <div className="mx-auto max-w-page px-6 pb-8">
      <hr className="my-6 border-t-2 border-separator-border" />
      <h2 className="text-body-semibold text-text-primary">Shadow Monitoring &middot; Near-Miss Candidates (Not Official Plays)</h2>
      <p className="mt-1.5 max-w-[90ch] text-body-2-regular text-text-secondary">
        The closest candidates that did not clear the official gate today, and the reason each is held. Watching
        only: these rows are not plays and are not graded.
      </p>
      <ul className="mt-3 divide-y divide-separator-border rounded-xl border border-separator-border bg-background-primary-default shadow-card">
        {picks.map(p => (
          <li key={p.key} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-4 py-3">
            <span className="text-body-2-semibold text-text-primary">{p.who}</span>
            <span className="text-body-2-regular text-text-secondary">{p.what}</span>
            <span className="text-caption-1-regular text-text-secondary">{p.game}</span>
            <span className="mono text-caption-1-regular text-text-secondary">Score {Math.round(p.score)}</span>
            {p.odds != null && (
              <span className="mono text-caption-1-regular text-text-secondary">
                {p.odds > 0 ? `+${p.odds}` : p.odds}{p.book ? ` · ${p.book}` : ''}
              </span>
            )}
            <span className="mono ml-auto text-caption-1-semibold text-text-secondary">{holdReasonFor(p)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-body-2-regular">
        <Link href="/ev/candidates" className="text-accent-400 hover:underline">See the full live candidate ranking in the Scanner &rarr;</Link>
      </p>
    </div>
  )
}
