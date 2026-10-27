import fs from 'node:fs'
import path from 'node:path'
import { SITE_CONFIG } from '@/lib/site-config'

const root = path.resolve(__dirname, '..')
const read = (p: string) => fs.readFileSync(path.join(root, p), 'utf-8')

describe('community links configuration', () => {
  const originalEnv = process.env.NEXT_PUBLIC_DISCORD_INVITE_URL

  afterEach(() => {
    if (originalEnv !== undefined) {
      process.env.NEXT_PUBLIC_DISCORD_INVITE_URL = originalEnv
    } else {
      delete process.env.NEXT_PUBLIC_DISCORD_INVITE_URL
    }
    jest.resetModules()
  })

  it('provides default discord invite url and display label', () => {
    delete process.env.NEXT_PUBLIC_DISCORD_INVITE_URL
    const { DISCORD_URL, DISCORD_INVITE_LABEL } = require('@/lib/links')
    expect(DISCORD_URL).toBe('https://discord.gg/sWNKxh6jJX')
    expect(DISCORD_INVITE_LABEL).toBe('discord.gg/sWNKxh6jJX')
  })

  it('respects NEXT_PUBLIC_DISCORD_INVITE_URL when provided', () => {
    process.env.NEXT_PUBLIC_DISCORD_INVITE_URL = 'https://discord.gg/custom-invite'
    const { DISCORD_URL, DISCORD_INVITE_LABEL } = require('@/lib/links')
    expect(DISCORD_URL).toBe('https://discord.gg/custom-invite')
    expect(DISCORD_INVITE_LABEL).toBe('discord.gg/custom-invite')
  })

  it('SITE_CONFIG.discordInviteUrl references DISCORD_URL', () => {
    const { DISCORD_URL } = require('@/lib/links')
    expect(SITE_CONFIG.discordInviteUrl).toBe(DISCORD_URL)
  })

  it('OG card routes use DISCORD_INVITE_LABEL variable instead of hardcoded strings', () => {
    const routes = [
      'app/api/og/games-card/route.tsx',
      'app/api/og/picks-card/route.tsx',
      'app/api/og/props-card/route.tsx',
    ]
    for (const r of routes) {
      const content = read(r)
      expect(content).toContain('DISCORD_INVITE_LABEL')
      expect(content).not.toContain('discord.gg/HfMYCmbmE')
      expect(content).not.toContain('discord.gg/sWNKxh6jJX')
    }
  })
})
