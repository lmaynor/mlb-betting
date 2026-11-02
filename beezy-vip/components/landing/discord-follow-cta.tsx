import Link from 'next/link'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'

import { DISCORD_URL } from '@/lib/links'
import { X_URL } from '@/lib/links'

export function DiscordFollowCTA() {
  return (
    <section className="pb-2 pt-14">
      <div className="flex flex-wrap items-center justify-between gap-5 rounded-2xl p-6 shadow-md" style={{
        border: '1px solid color-mix(in oklab, #5865f2 32%, var(--color-separator-border))',
        background: 'linear-gradient(135deg, color-mix(in oklab, #5865f2 14%, var(--color-background-primary-default)), var(--color-background-primary-default))',
      }}>
        <div className="flex min-w-0 items-center gap-4">
          <div className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-[#5865f2] text-white" style={{ boxShadow: '0 0 24px -4px color-mix(in oklab, #5865f2 60%, transparent)' }}>
            <DiscordMark size={26} color="currentColor" />
          </div>
          <div>
            <div className="dell-display mb-1 text-[19px] tracking-[-0.01em]">
              Get the card the moment it drops
            </div>
            <p className="times max-w-[52ch] text-body-regular text-text-secondary">
              Join the Discord for the daily card, model notes, and nightly results &mdash; pushed to you, no refresh required.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
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
