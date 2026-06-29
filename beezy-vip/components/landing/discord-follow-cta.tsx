import Link from 'next/link'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'

const DISCORD_URL = 'https://discord.gg/HfMYCmbmE'
const X_URL = 'https://x.com/beezy_fyi'

export function DiscordFollowCTA() {
  return (
    <section style={{ padding: '56px 0 8px' }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '20px',
        flexWrap: 'wrap',
        padding: '24px',
        borderRadius: 'var(--radius-xl)',
        border: '1px solid color-mix(in oklab, #5865f2 32%, var(--basalt))',
        background: 'linear-gradient(135deg, color-mix(in oklab, #5865f2 14%, var(--graphite)), var(--graphite))',
        boxShadow: 'var(--shadow-md)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', minWidth: 0 }}>
          <div style={{
            width: '48px',
            height: '48px',
            borderRadius: 'var(--radius-lg)',
            background: '#5865f2',
            color: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
            boxShadow: '0 0 24px -4px color-mix(in oklab, #5865f2 60%, transparent)',
          }}>
            <DiscordMark size={26} color="currentColor" />
          </div>
          <div>
            <div className="dell-display" style={{ fontSize: '19px', color: 'var(--chalk)', marginBottom: '4px', letterSpacing: '-0.01em' }}>
              Get the card the moment it drops
            </div>
            <p className="times" style={{ fontSize: '14px', color: 'var(--silver)', lineHeight: 1.5, maxWidth: '52ch' }}>
              Join the Discord for the daily card, model notes, and nightly results &mdash; pushed to you, no refresh required.
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <a
            href={DISCORD_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="btn"
            style={{ background: '#5865f2', color: '#fff', border: '1px solid color-mix(in oklab, #5865f2 70%, white)' }}
          >
            <DiscordMark size={15} color="currentColor" />
            Join free
          </a>
          <a
            href={X_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-ghost"
          >
            <XMark size={14} color="currentColor" />
            Follow
          </a>
        </div>
      </div>
    </section>
  )
}
