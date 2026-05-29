const BASE = process.env.NEXT_PUBLIC_BASE_URL ?? 'https://beezy.fyi'

export function ogUrl(params: {
  title: string
  sub?:  string
  stat1?: string   // format: "Label:Value"
  stat2?: string
  stat3?: string
}): string {
  const sp = new URLSearchParams({ title: params.title })
  if (params.sub)   sp.set('sub',   params.sub)
  if (params.stat1) sp.set('stat1', params.stat1)
  if (params.stat2) sp.set('stat2', params.stat2)
  if (params.stat3) sp.set('stat3', params.stat3)
  return `${BASE}/api/og?${sp.toString()}`
}
