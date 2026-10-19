import type { ReactNode } from 'react'
import { SITE_CONFIG } from '@/lib/site-config'

/** Shared frame for the four /legal pages: title row, operator statement, then the article body. */
export function LegalPage({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={{ maxWidth: '760px', margin: '0 auto', padding: '32px 20px 48px' }}>
      <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#f3f4f6', margin: '0 0 6px' }}>{title}</h1>
      <p style={{ color: '#7d8596', fontSize: '13px', margin: '0 0 28px' }}>
        Last updated: {SITE_CONFIG.legalUpdated}. {SITE_CONFIG.operatorStatement}
      </p>
      <div className="article-body">{children}</div>
    </div>
  )
}

/** Inline contact line used at the foot of every legal page. */
export function ContactLine() {
  return (
    <p>
      Email <a href={`mailto:${SITE_CONFIG.supportEmail}`}>{SITE_CONFIG.supportEmail}</a>, message{' '}
      <a href={SITE_CONFIG.xUrl} target="_blank" rel="noopener noreferrer">{SITE_CONFIG.xHandle}</a> on X, or use the
      support channel in the official <a href={SITE_CONFIG.discordInviteUrl} target="_blank" rel="noopener noreferrer">{SITE_CONFIG.displayName} Discord</a>.
    </p>
  )
}
