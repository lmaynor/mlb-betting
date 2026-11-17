import { contestRoles, grantContestRole, isAdmin, isParticipant, revokeContestRole, roleIn, type ClerkUsers } from '@/lib/contest/access'

/** An in-memory stand-in for Clerk's backend users API. */
function fakeClerk(initial: Record<string, unknown>): ClerkUsers & { meta: Record<string, unknown>; writes: number } {
  const state = { meta: JSON.parse(JSON.stringify(initial)) as Record<string, unknown>, writes: 0 }
  return {
    get meta() { return state.meta }, get writes() { return state.writes },
    users: {
      async getUser() { return { privateMetadata: state.meta } },
      async updateUserMetadata(_id, { privateMetadata }) { state.meta = { ...state.meta, ...privateMetadata }; state.writes += 1 },
    },
  }
}

describe('reading roles from private metadata', () => {
  it('returns only well-formed roles and never throws', () => {
    expect(contestRoles({ contest: { roles: { bowls: 'participant', march: 'admin', bad: 'owner', worse: 5 } } })).toEqual({ bowls: 'participant', march: 'admin' })
    for (const junk of [null, undefined, {}, { contest: null }, { contest: { roles: 'x' } }, 'str', 7]) expect(contestRoles(junk)).toEqual({})
  })
  it('an admin is a participant of the same contest, and nobody is one elsewhere', () => {
    const m = { contest: { roles: { bowls: 'admin', march: 'participant' } } }
    expect(roleIn(m, 'bowls')).toBe('admin')
    expect(isParticipant(m, 'bowls') && isParticipant(m, 'march')).toBe(true)
    expect(isAdmin(m, 'bowls') && !isAdmin(m, 'march')).toBe(true)
    expect(isParticipant(m, 'other')).toBe(false)
  })
})

describe('granting and revoking', () => {
  it('writes the participant role and keeps the Stripe tier and every other key', async () => {
    const c = fakeClerk({ tier: 'pro_monthly', sportChoice: 'nba' })
    expect(await grantContestRole(c, 'u1', 'bowls')).toEqual({ bowls: 'participant' })
    expect(c.meta).toMatchObject({ tier: 'pro_monthly', sportChoice: 'nba', contest: { roles: { bowls: 'participant' } } })
  })
  it('keeps the player\'s other contests when it adds one', async () => {
    const c = fakeClerk({ contest: { roles: { march: 'participant' } } })
    expect(await grantContestRole(c, 'u1', 'bowls')).toEqual({ march: 'participant', bowls: 'participant' })
  })
  it('a repeat join never downgrades an admin, and an admin grant upgrades', async () => {
    const c = fakeClerk({ contest: { roles: { bowls: 'admin' } } })
    await grantContestRole(c, 'u1', 'bowls', 'participant')
    expect(c.writes).toBe(0)
    const d = fakeClerk({ contest: { roles: { bowls: 'participant' } } })
    expect(await grantContestRole(d, 'u1', 'bowls', 'admin')).toEqual({ bowls: 'admin' })
  })
  it('revoke removes just that contest', async () => {
    const c = fakeClerk({ tier: 'pro', contest: { roles: { bowls: 'participant', march: 'participant' } } })
    expect(await revokeContestRole(c, 'u1', 'bowls')).toEqual({ march: 'participant' })
    expect(c.meta).toMatchObject({ tier: 'pro', contest: { roles: { march: 'participant' } } })
  })
})
