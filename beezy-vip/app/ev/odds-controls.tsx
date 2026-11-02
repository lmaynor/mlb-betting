import { useEffect, useRef, useState } from 'react'
import { bookDisplay, SURFACE_CARD } from '@/lib/tokens'
import { cx } from '@/utils/cx'
import { BookLogo } from '@/components/ui/book-logo'
import { HAIR } from './odds-constants'

export function Field({ label, htmlFor, children }: { label: string; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <label htmlFor={htmlFor} className="ui-heading text-text-secondary">{label}</label>
      {children}
    </div>
  )
}

export function Select({ id, value, onChange, options }: { id: string; value: string; onChange: (v: string) => void; options: { v: string; l: string }[] }) {
  return (
    <select id={id} value={value} onChange={e => onChange(e.target.value)}
      className="h-[34px] max-w-[200px] rounded-lg border border-border-button-default bg-background-secondary-default px-2 text-caption-1-regular text-text-primary outline-none focus:border-accent-500">
      {options.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
    </select>
  )
}

export function Pill({ children, active, onClick, title }: { children: React.ReactNode; active: boolean; onClick: () => void; title?: string }) {
  return (
    <button onClick={onClick} title={title} aria-pressed={active}
      className={cx('button-press-motion cursor-pointer rounded-md border px-[11px] py-1.5 text-caption-1-semibold tracking-[0.03em]',
        active ? 'border-border-button-hover bg-background-tertiary-default text-text-primary' : 'border-transparent bg-transparent text-text-secondary hover:bg-background-primary-hover')}>
      {children}
    </button>
  )
}

export function PillGroup({ children, label }: { children: React.ReactNode; label?: string }) {
  return <div role={label ? 'group' : undefined} aria-label={label} className="inline-flex max-w-full flex-wrap gap-0.5 rounded-lg border border-separator-border bg-background-secondary-default p-[3px]">{children}</div>
}

/** A button that opens a checklist of books (a popover closed by outside click or Escape). `emptyMeansAll`: none ticked = every book. */
export function BookPicker({ label, books, selected, onChange, presets, note, emptyMeansAll, align, header }: {
  label: string
  books: { key: string; n: number; offshore: boolean }[]
  selected: string[]
  onChange: (next: string[]) => void
  presets: { label: string; keys: string[] }[]
  note: string
  emptyMeansAll?: boolean
  /** which edge the popover hangs from (the right-hand pickers open leftwards so they stay on screen) */
  align?: 'left' | 'right'
  /** extra controls above the presets */
  header?: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])
  const on = new Set(selected)
  const toggle = (k: string) => onChange(on.has(k) ? selected.filter(x => x !== k) : [...selected, k])
  const hasNonePreset = presets.some(p => p.label.toLowerCase() === 'none')
  const hasAllPreset = presets.some(p => p.label.toLowerCase() === 'all')

  return (
    <div ref={box} className="relative">
      <button onClick={() => setOpen(v => !v)} aria-expanded={open} className="btn btn-ghost" style={{ padding: '6px 11px', fontSize: '11.5px', height: '34px' }}>{label}</button>
      {open && (
        <div role="group" aria-label={label} style={{ position: 'absolute', zIndex: 30, top: '38px', ...(align === 'right' ? { right: 0 } : { left: 0 }), width: '290px', ...SURFACE_CARD, padding: '10px', display: 'grid', gap: '8px' }}>
          {header}
          <div className="flex gap-[5px] flex-wrap items-center">
            {presets.map(p => <button key={p.label} type="button" className="btn btn-ghost" style={{ padding: '3px 8px', fontSize: '11px' }} onClick={() => onChange(p.keys)}>{p.label}</button>)}
            {emptyMeansAll && !hasAllPreset && <button type="button" className="btn btn-ghost" style={{ padding: '3px 8px', fontSize: '11px' }} onClick={() => onChange([])}>All</button>}
            {!emptyMeansAll && !hasAllPreset && <button type="button" className="btn btn-ghost" style={{ padding: '3px 8px', fontSize: '11px' }} onClick={() => onChange(books.map(b => b.key))}>All</button>}
            {!hasNonePreset && <button type="button" className="btn btn-ghost" style={{ padding: '3px 8px', fontSize: '11px' }} onClick={() => onChange([])}>None</button>}
          </div>
          <div className="grid gap-0.5 max-h-[280px] overflow-y-auto">
            {books.map(b => {
              const meta = bookDisplay(b.key)
              const isOnly = on.has(b.key) && on.size === 1
              return (
                <div
                  key={b.key}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '6px',
                    padding: '3px 6px',
                    borderRadius: '4px',
                    background: isOnly ? 'rgba(87, 242, 135, 0.08)' : on.has(b.key) ? 'rgba(255, 255, 255, 0.03)' : 'transparent',
                  }}
                >
                  <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: on.has(b.key) ? 'var(--color-text-primary)' : 'var(--color-neutral-300)', cursor: 'pointer', flex: 1, minWidth: 0 }}>
                    <input type="checkbox" checked={on.has(b.key)} onChange={() => toggle(b.key)} style={{ cursor: 'pointer', accentColor: 'var(--signal)' }} />
                    <span className="min-w-6 inline-flex justify-center"><BookLogo book={b.key} size={22} /></span>
                    <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{meta.label}{b.offshore ? ' (offshore)' : ''}</span>
                    <span className="mono text-[10.5px] text-text-secondary mr-0.5">{b.n}</span>
                  </label>
                  <button
                    type="button"
                    title={`Select only ${meta.label}`}
                    aria-label={`Only ${meta.label}`}
                    onClick={(e) => {
                      e.preventDefault()
                      e.stopPropagation()
                      onChange([b.key])
                    }}
                    style={{
                      fontSize: '10px',
                      fontFamily: 'var(--font-mono)',
                      fontWeight: 600,
                      letterSpacing: '0.04em',
                      padding: '2px 6px',
                      borderRadius: '4px',
                      border: isOnly ? '1px solid var(--signal)' : `1px solid ${HAIR}`,
                      background: isOnly ? 'var(--win-wash)' : 'transparent',
                      color: isOnly ? 'var(--signal)' : 'var(--color-text-secondary)',
                      cursor: 'pointer',
                      flexShrink: 0,
                    }}
                  >
                    ONLY
                  </button>
                </div>
              )
            })}
          </div>
          <span className="text-mini text-text-secondary leading-[1.45]">{note}</span>
        </div>
      )}
    </div>
  )
}

/** Mobile-native SwiftUI style checklist for books with presets, Only buttons and full-width touch targets. */
export function MobileBookChecklist({
  title,
  subtitle,
  books,
  selected,
  onChange,
  presets,
  emptyMeansAll,
  extraHeader,
}: {
  title: string
  subtitle?: string
  books: { key: string; n: number; offshore: boolean }[]
  selected: string[]
  onChange: (next: string[]) => void
  presets: { label: string; keys: string[] }[]
  emptyMeansAll?: boolean
  extraHeader?: React.ReactNode
}) {
  const [collapsed, setCollapsed] = useState(false)
  const on = new Set(selected)
  const toggle = (k: string) => onChange(on.has(k) ? selected.filter(x => x !== k) : [...selected, k])
  const countLabel = emptyMeansAll && selected.length === 0 ? 'ALL' : `${selected.length}`
  const hasNonePreset = presets.some(p => p.label.toLowerCase() === 'none')
  const hasAllPreset = presets.some(p => p.label.toLowerCase() === 'all')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '10px', background: 'var(--color-background-secondary-default)', borderRadius: '8px', border: `1px solid ${HAIR}` }}>
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => setCollapsed(v => !v)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            background: 'transparent',
            border: 'none',
            padding: 0,
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <span className="ui-heading text-mini text-text-primary tracking-head">
            {title} ({countLabel})
          </span>
          <span className="mono text-micro text-text-secondary">
            {collapsed ? '[\u25bc Show]' : '[\u25b2 Hide]'}
          </span>
        </button>
      </div>

      {!collapsed && (
        <>
          {extraHeader}

          <div className="flex gap-1.5 flex-wrap items-center">
            {presets.map(p => (
              <button
                key={p.label}
                type="button"
                className="btn btn-ghost"
                style={{ padding: '4px 10px', fontSize: '11px', minHeight: '30px' }}
                onClick={() => onChange(p.keys)}
              >
                {p.label}
              </button>
            ))}
            {emptyMeansAll && !hasAllPreset && (
              <button
                type="button"
                className="btn btn-ghost"
                style={{ padding: '4px 10px', fontSize: '11px', minHeight: '30px' }}
                onClick={() => onChange([])}
              >
                All
              </button>
            )}
            {!emptyMeansAll && !hasAllPreset && (
              <button
                type="button"
                className="btn btn-ghost"
                style={{ padding: '4px 10px', fontSize: '11px', minHeight: '30px' }}
                onClick={() => onChange(books.map(b => b.key))}
              >
                All
              </button>
            )}
            {!hasNonePreset && (
              <button
                type="button"
                className="btn btn-ghost"
                style={{ padding: '4px 10px', fontSize: '11px', minHeight: '30px' }}
                onClick={() => onChange([])}
              >
                None
              </button>
            )}
          </div>

          <div style={{ display: 'grid', gap: '4px', maxHeight: '220px', overflowY: 'auto', WebkitOverflowScrolling: 'touch', paddingRight: '2px' }}>
            {books.map(b => {
              const meta = bookDisplay(b.key)
              const isOnly = on.has(b.key) && on.size === 1
              const checked = on.has(b.key)
              return (
                <div
                  key={b.key}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    minHeight: '42px',
                    padding: '6px 8px',
                    borderRadius: '6px',
                    background: isOnly ? 'rgba(87, 242, 135, 0.08)' : checked ? '#141824' : 'transparent',
                    border: isOnly ? '1px solid rgba(87, 242, 135, 0.3)' : checked ? '1px solid #1e2536' : '1px solid transparent',
                  }}
                >
                  <label
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '10px',
                      fontSize: '12.5px',
                      color: checked ? 'var(--color-text-primary)' : 'var(--color-neutral-300)',
                      cursor: 'pointer',
                      flex: 1,
                      minWidth: 0,
                      userSelect: 'none',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(b.key)}
                      style={{ cursor: 'pointer', width: '16px', height: '16px', accentColor: 'var(--signal)' }}
                    />
                    <span className="min-w-6 inline-flex justify-center">
                      <BookLogo book={b.key} size={24} />
                    </span>
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: checked ? 600 : 400 }}>
                      {meta.label}{b.offshore ? ' (offshore)' : ''}
                    </span>
                    <span className="mono text-mini text-text-secondary mr-1.5">
                      {b.n}
                    </span>
                  </label>

                  <button
                    type="button"
                    title={`Select only ${meta.label}`}
                    aria-label={`Only ${meta.label}`}
                    onClick={(e) => {
                      e.preventDefault()
                      e.stopPropagation()
                      onChange([b.key])
                    }}
                    style={{
                      fontSize: '10px',
                      fontFamily: 'var(--font-mono)',
                      fontWeight: 700,
                      letterSpacing: '0.04em',
                      padding: '4px 8px',
                      minHeight: '30px',
                      minWidth: '46px',
                      borderRadius: '4px',
                      border: isOnly ? '1px solid var(--signal)' : `1px solid ${HAIR}`,
                      background: isOnly ? 'var(--win-wash)' : 'transparent',
                      color: isOnly ? 'var(--signal)' : 'var(--color-text-secondary)',
                      cursor: 'pointer',
                      flexShrink: 0,
                    }}
                  >
                    ONLY
                  </button>
                </div>
              )
            })}
          </div>

          {subtitle && (
            <span className="text-mini text-text-secondary leading-[1.45]">{subtitle}</span>
          )}
        </>
      )}
    </div>
  )
}
