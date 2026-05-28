import Link from 'next/link'
import { DiscordMark } from '@/components/ui/discord-mark'

const DISCORD_URL = 'https://discord.gg/HfMYCmbmE'

export function DiscordFollowCTA() {
  return (
    <section style={{ padding: '18px 20px', borderBottom: '0.5px solid #1f1f24' }}>
      <div style={{
        maxWidth: '900px',
        margin: '0 auto',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '16px',
        flexWrap: 'wrap',
        padding: '14px 16px',
        border: '0.5px solid rgba(88,101,242,.35)',
        borderRadius: 'var(--radius)',
        background: 'rgba(88,101,242,.08)',
        boxShadow: 'var(--shadow-card)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', minWidth: 0 }}>
          <div style={{
            width: '36px',
            height: '36px',
            borderRadius: 'var(--radius)',
            background: '#5865f2',
            color: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}>
            <DiscordMark size={22} color="currentColor" />
          </div>
          <div>
            <div style={{ fontSize: '13px', fontWeight: 700, color: '#f5f5f7', marginBottom: '2px' }}>
              Follow the daily card in Discord
            </div>
            <p style={{ fontSize: '12px', color: '#a1a1aa', lineHeight: 1.45 }}>
              Card drops, model notes, and result updates without refreshing the site.
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <a
            href={DISCORD_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="mono"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '7px',
              fontSize: '11px',
              fontWeight: 800,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              padding: '8px 12px',
              background: '#5865f2',
              color: '#fff',
              borderRadius: 'var(--radius-sm)',
              textDecoration: 'none',
            }}
          >
            <DiscordMark size={16} color="currentColor" />
            Join free
          </a>
          <Link
            href="/results"
            className="mono"
            style={{
              fontSize: '11px',
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              color: '#a1a1aa',
              textDecoration: 'none',
            }}
          >
            View results
          </Link>
        </div>
      </div>
    </section>
  )
}
