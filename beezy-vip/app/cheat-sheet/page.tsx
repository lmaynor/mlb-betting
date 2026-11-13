import { permanentRedirect } from 'next/navigation'

/** /cheat-sheet is the Daily Card's original URL. It permanently aliases to /picks (Today's Card); the card's
 *  components, its stripped screenshot layout, and the POST TO X formatter stay where they are. */
export default function CheatSheetAliasPage() {
  permanentRedirect('/picks')
}
