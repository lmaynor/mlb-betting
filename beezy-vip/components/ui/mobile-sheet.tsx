'use client'

import React, { useCallback, useEffect, useRef, useSyncExternalStore, type ReactNode } from 'react'
import { useFocusTrap } from '@/components/ui/use-focus-trap'

/**
 * SSR-safe hook to detect mobile viewports (<= breakpoint px).
 * Defaults to false on the server to prevent hydration mismatch.
 */
export function useIsMobile(breakpoint = 768): boolean {
  // stable between renders (it only changes with the breakpoint), so useSyncExternalStore does not re-subscribe every render
  const subscribe = useCallback((onStoreChange: () => void) => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {}
    const mql = window.matchMedia(`(max-width: ${breakpoint}px)`)
    if (typeof mql.addEventListener === 'function') {
      mql.addEventListener('change', onStoreChange)
      return () => mql.removeEventListener('change', onStoreChange)
    } else if (typeof (mql as unknown as { addListener?: (cb: () => void) => void }).addListener === 'function') {
      (mql as unknown as { addListener: (cb: () => void) => void }).addListener(onStoreChange)
      return () => (mql as unknown as { removeListener: (cb: () => void) => void }).removeListener(onStoreChange)
    }
    return () => {}
  }, [breakpoint])

  const getSnapshot = useCallback(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
    const mql = window.matchMedia(`(max-width: ${breakpoint}px)`)
    return Boolean(mql && mql.matches)
  }, [breakpoint])

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}

const getServerSnapshot = () => false

/**
 * Body scroll lock shared by every open sheet. The ORIGINAL overflow value is saved when the first lock is taken and
 * restored only when the last one is released, so sheets that close out of order can never leave the body stuck hidden.
 */
let scrollLocks = 0
let savedOverflow = ''
function lockBodyScroll(): () => void {
  if (scrollLocks === 0) {
    savedOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  scrollLocks += 1
  let released = false
  return () => {
    if (released) return
    released = true
    scrollLocks = Math.max(0, scrollLocks - 1)
    if (scrollLocks === 0) document.body.style.overflow = savedOverflow
  }
}

/**
 * Horizontal swipeable pill strip with scrollbars hidden.
 * Allows overflowing pills/tabs to be smoothly scrolled horizontally on mobile.
 */
export function HorizontalPillStrip({
  children,
  className = '',
  style,
  gap = '8px',
  padding = '4px 0',
}: {
  children: ReactNode
  className?: string
  style?: React.CSSProperties
  gap?: string | number
  padding?: string
}) {
  return (
    <div
      className={`no-scrollbar ${className}`.trim()}
      style={{
        display: 'flex',
        overflowX: 'auto',
        WebkitOverflowScrolling: 'touch',
        scrollbarWidth: 'none',
        msOverflowStyle: 'none',
        gap,
        padding,
        alignItems: 'center',
        flexWrap: 'nowrap',
        width: '100%',
        maxWidth: '100%',
        boxSizing: 'border-box',
        ...style,
      }}
    >
      {children}
    </div>
  )
}

export interface MobileBottomSheetProps {
  open: boolean
  onClose: () => void
  title?: ReactNode
  subtitle?: ReactNode
  /** accessible name of the dialog; defaults to the title when it is plain text */
  ariaLabel?: string
  children: ReactNode
  footer?: ReactNode
  maxHeight?: string
  zIndex?: number
}

/**
 * Native-feeling dark-mode Bottom Sheet modal for mobile viewports.
 * Enforces NO_SLOP_CHECKLIST: solid var(--color-background-primary-default) graphite surface, 1px var(--color-separator-border) border,
 * no gradients, no glows, font sizes >= 10px, body scroll lock, ESC and backdrop dismiss.
 */
export function MobileBottomSheet({
  open,
  onClose,
  title,
  subtitle,
  ariaLabel,
  children,
  footer,
  maxHeight = '88vh',
  zIndex = 80,
}: MobileBottomSheetProps) {
  const dialogRef = useRef<HTMLDivElement>(null)

  // Body scroll lock (module-level counter)
  useEffect(() => {
    if (!open) return
    return lockBodyScroll()
  }, [open])

  // Focus moves into the sheet, Tab wraps inside it, and focus returns to the opener on close
  useFocusTrap(dialogRef, open)

  // ESC key dismissal
  useEffect(() => {
    if (!open) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel ?? (typeof title === 'string' ? title : 'Dialog')}
      tabIndex={-1}
      style={{
        outline: 'none',
        position: 'fixed',
        inset: 0,
        zIndex,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
      }}
    >
      {/* Backdrop */}
      <div
        data-testid="bottom-sheet-backdrop"
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0, 0, 0, 0.75)',
          backdropFilter: 'blur(4px)',
          WebkitBackdropFilter: 'blur(4px)',
          cursor: 'pointer',
        }}
      />

      {/* Sheet Surface */}
      <div
        data-testid="bottom-sheet-surface"
        style={{
          position: 'relative',
          background: 'var(--color-background-primary-default)',
          borderTop: '1px solid var(--color-separator-border)',
          borderLeft: '1px solid var(--color-separator-border)',
          borderRight: '1px solid var(--color-separator-border)',
          borderRadius: '16px 16px 0 0',
          maxHeight,
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 -4px 24px rgba(0, 0, 0, 0.6)',
          zIndex: zIndex + 1,
          paddingBottom: 'env(safe-area-inset-bottom, 16px)',
          boxSizing: 'border-box',
        }}
      >
        {/* Drag handle pill */}
        <div
          data-testid="bottom-sheet-drag-handle"
          style={{
            width: '36px',
            height: '4px',
            background: 'var(--color-border-button-hover)',
            borderRadius: '2px',
            margin: '8px auto 4px auto',
            flexShrink: 0,
          }}
        />

        {/* Header bar */}
        {(title || subtitle) && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '8px 16px 10px 16px',
              borderBottom: '1px solid var(--color-separator-border)',
              flexShrink: 0,
              gap: '12px',
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              {title && (
                <div
                  style={{
                    fontSize: '14px',
                    fontWeight: 700,
                    color: 'var(--color-text-primary)',
                    lineHeight: 1.3,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {title}
                </div>
              )}
              {subtitle && (
                <div
                  style={{
                    fontSize: '11px',
                    color: 'var(--color-text-secondary)',
                    lineHeight: 1.3,
                    marginTop: '2px',
                  }}
                >
                  {subtitle}
                </div>
              )}
            </div>

            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              style={{
                background: 'transparent',
                border: '1px solid var(--color-separator-border)',
                borderRadius: '6px',
                color: 'var(--color-text-secondary)',
                width: '28px',
                height: '28px',
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '14px',
                padding: 0,
                flexShrink: 0,
              }}
            >
              {'\u2715'}
            </button>
          </div>
        )}

        {/* Scrollable body */}
        <div
          style={{
            overflowY: 'auto',
            WebkitOverflowScrolling: 'touch',
            padding: '14px 16px',
            flex: 1,
            boxSizing: 'border-box',
          }}
        >
          {children}
        </div>

        {/* Optional Sticky Footer */}
        {footer && (
          <div
            style={{
              padding: '10px 16px',
              borderTop: '1px solid var(--color-separator-border)',
              background: 'var(--color-background-secondary-default)',
              flexShrink: 0,
              boxSizing: 'border-box',
            }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}
