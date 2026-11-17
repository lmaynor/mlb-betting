/**
 * ESPN writes a live status as "<clock> - <period>" ("8:42 - 3rd Quarter", "6:40 - 2nd Half", "10:00 - 1st OT"). The board puts the
 * period first, because it is the part people scan for: "3rd Quarter 8:42". Anything without a clock ("Halftime", "End of 1st
 * Quarter", "Final", "Final/OT") is shown as ESPN wrote it.
 */
export function periodFirst(detail: string): string {
  const m = detail.trim().match(/^(\d{1,2}:\d{2}(?:\.\d+)?)\s+-\s+(.+)$/)
  return m ? `${m[2]} ${m[1]}` : detail.trim()
}
