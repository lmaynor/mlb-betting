const mockUserId = jest.fn()
const mockCurrent = jest.fn()
jest.mock('@/lib/auth', () => ({ getUserId: () => mockUserId(), getCurrentUser: () => mockCurrent() }))
jest.mock('@/lib/auth-mode', () => ({ AUTH_DISABLED: false }))

import { contestUser } from '@/lib/contest/identity'

beforeEach(() => { mockUserId.mockReset(); mockCurrent.mockReset() })

describe('contestUser', () => {
  it('is null for an anonymous visitor', async () => {
    mockUserId.mockResolvedValue(null)
    expect(await contestUser()).toBeNull()
  })
  it('carries a suggested name and whether the primary email is verified', async () => {
    mockUserId.mockResolvedValue('user_1')
    mockCurrent.mockResolvedValue({ username: null, firstName: 'Alexandria-the-very-long-name', primaryEmailAddress: { verification: { status: 'verified' } } })
    expect(await contestUser()).toEqual({ id: 'user_1', suggestedName: 'Alexandria-the-very-', emailVerified: true })
    mockCurrent.mockResolvedValue({ username: 'alex', primaryEmailAddress: { verification: { status: 'unverified' } } })
    expect(await contestUser()).toMatchObject({ suggestedName: 'alex', emailVerified: false })
    mockCurrent.mockResolvedValue(null)
    expect(await contestUser()).toMatchObject({ suggestedName: '', emailVerified: false })
  })
})
