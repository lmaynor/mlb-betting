import Link from 'next/link'

export function SectionHeader({ title, href, link }: { title: string; href: string; link: string }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-4">
      <h2 className="text-headline-bold text-text-primary">{title}</h2>
      <Link href={href} className="whitespace-nowrap text-caption-1-semibold text-[var(--link)] no-underline hover:underline">{link}</Link>
    </div>
  )
}
