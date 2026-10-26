import Link from 'next/link'

export function SectionHeader({ title, href, link }: { title: string; href: string; link: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px', marginBottom: '12px' }}>
      <h2 style={{ fontSize: '16px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>{title}</h2>
      <Link href={href} style={{ fontSize: '12px', fontWeight: 600, color: 'var(--link)', textDecoration: 'none', whiteSpace: 'nowrap' }}>{link}</Link>
    </div>
  )
}
