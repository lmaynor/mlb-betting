import Link from 'next/link'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'

const DISCORD_URL = 'https://discord.gg/HfMYCmbmE'
const X_URL = 'https://x.com/beezy_fyi'

export function DiscordFollowCTA() {
  return (
    <section style={{ padding: '18px 20px', borderBottom: '1px solid #1f1f24' }}>
      <div style={{
        maxWidth: '900px',
        margin: '0 auto',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '16px',
        flexWrap: 'wrap',
        padding: '14px 16px',
        border: '1px solid #000',
        background: '#0a0c1e',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', minWidth: 0 }}>
          {/* Discord icon block -- Dell accent tile */}
          <div style={{
            width: '40px',
            height: '40px',
            background: '#5865f2',
            color: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
            border: '1px solid #000',
          }}>
            <DiscordMark size={22} color="currentColor" />
          </div>
          <div>
            <div
              className="dell-heading"
              style={{ fontSize: '12px', color: '#f5f5f7', marginBottom: '4px', letterSpacing: '0.04em' }}
            >
              Follow the daily card in Discord
            </div>
            <p className="times" style={{ fontSize: '13px', color: '#a1a1aa', lineHeight: 1.45 }}>
              Card drops, model notes, and result updates without refreshing the site.
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <a
            href={DISCORD_URL}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '7px',
              fontSize: '11px',
              fontFamily: 'Arial, Helvetica, sans-serif',
              fontWeight: 700,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              padding: '7px 14px',
              background: '#fcc20f',
              color: '#000',
              border: '1px solid #000',
              textDecoration: 'none',
            }}
          >
            <DiscordMark size={14} color="currentColor" />
            Join free
          </a>
          <a
            href={X_URL}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '7px',
              fontSize: '11px',
              fontFamily: 'Arial, Helvetica, sans-serif',
              fontWeight: 700,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              padding: '6px 12px',
              background: '#0a0a0c',
              color: '#f5f5f7',
              border: '1px solid #333',
              textDecoration: 'none',
            }}
          >
            <XMark size={14} color="currentColor" />
            Follow
          </a>
          <Link
            href="/results"
            style={{
              fontSize: '12px',
              fontFamily: 'Arial, Helvetica, sans-serif',
              fontWeight: 700,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              color: '#0000ee',
              textDecoration: 'underline',
            }}
          >
            View results
          </Link>
        </div>
      </div>
    </section>
  )
}
