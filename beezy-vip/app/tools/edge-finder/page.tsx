import { redirect } from 'next/navigation'

// Edge Finder was the Kelly calculator's edge line on its own; it now lives there.
export default function EdgeFinderPage() {
  redirect('/tools/kelly-calculator')
}
