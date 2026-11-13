/**
 * The Daily Card gate receipt (Decision 17): shown in every card state, fat or thin, so a quiet day reads as
 * the gate working rather than as missing content. Counts come from the same loader the card itself uses.
 */
export function GateReceipt({ officials, candidatesScanned, sportsScanned }: { officials: number; candidatesScanned: number; sportsScanned: number }) {
  return (
    <div className="mx-auto max-w-page px-6 pt-5">
      <div className="mono flex flex-wrap gap-x-4 gap-y-1.5 rounded-control border border-separator-border bg-background-primary-default px-3.5 py-[9px] text-[13px] text-neutral-300">
        <span className="text-text-primary">Gate receipt</span>
        <span>{officials} Official {officials === 1 ? 'Play' : 'Plays'} today</span>
        <span>{officials} of {candidatesScanned.toLocaleString('en-US')} candidates cleared our minimum edge and consensus gates</span>
        <span>{sportsScanned} {sportsScanned === 1 ? 'sport' : 'sports'} scanned</span>
      </div>
    </div>
  )
}
