/** lib/contest/names mirrors contest/names.py: the same inputs give the same answers, so the join form agrees with the server. */
import { cleanName, nameKey, suggestName, validateName } from '@/lib/contest/names'

const code = (raw: string) => { const v = validateName(raw); return v.ok ? 'ok' : v.code }

describe('validateName', () => {
  it('cleans and keys a good name', () => {
    expect(validateName('  Alex   Morgan ')).toEqual({ ok: true, name: 'Alex Morgan', key: 'alexmorgan' })
    expect(validateName("O'Brien")).toMatchObject({ ok: true, name: "O'Brien", key: 'obrien' })
    expect(validateName('Max_1').ok).toBe(true)
    expect(nameKey('Jose')).toBe(nameKey('JOSE'))
    expect(nameKey('j.o.s.e')).toBe(nameKey('Jose'))
    expect(nameKey('A.l-e x_1')).toBe('alex1')
    expect(cleanName('  a   b ')).toBe('a b')
  })
  it('length, characters and ends', () => {
    for (const bad of ['A', 'x'.repeat(21), '', 'bad@name', 'a<b>', 'hi\u200bthere', '.Alex', 'Alex-', '..', '-_']) expect(code(bad)).toBe('NAME_INVALID')
    expect(code('Al')).toBe('ok')
    expect(code('x'.repeat(20))).toBe('ok')
  })
  it('links and reserved names', () => {
    for (const bad of ['Tom.com', 'www tom', 'http tom']) expect(code(bad)).toBe('NAME_INVALID')
    for (const r of ['Admin', 'HOUSE', 'you', 'B e e z y', 'Mod.erator']) expect(code(r)).toBe('NAME_RESERVED')
  })
  it('other alphabets are fine', () => {
    expect(code('Jos\u00e9')).toBe('ok')
    expect(code('\u674e\u5a1c')).toBe('ok')
  })
})

describe('suggestName', () => {
  it('comes from the account and always validates', () => {
    expect(suggestName({ username: 'alexm' })).toBe('alexm')
    expect(suggestName({ firstName: 'Alex', lastName: 'Morgan' })).toBe('Alex M')
    expect(suggestName({ email: 'jordan.lee_77@gmail.com' })).toBe('Jordan Lee 77')
    expect(suggestName({ username: 'a', firstName: 'Sam' })).toBe('Sam')
    expect(suggestName({})).toBe('Player')
    expect(suggestName({ username: 'admin', firstName: 'Pat', lastName: 'Quinn' })).toBe('Pat Q')
  })
})
