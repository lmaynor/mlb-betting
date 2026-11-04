import { permanentRedirect } from 'next/navigation'

/** Projections moved to the Scanner's own tab (/ev/projections). `?league=` and every other query parameter carry over. */
export default async function EdgeProjectionsRedirect({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = new URLSearchParams(await searchParams)
  const qs = sp.toString()
  permanentRedirect(qs ? `/ev/projections?${qs}` : '/ev/projections')
}
