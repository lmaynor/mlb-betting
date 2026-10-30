'use client'

import React, { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react'

/**
 * SSR-safe hook to detect mobile viewports (<= breakpoint px).
 * Defaults to false on the server to prevent hydration mismatch.
 */
export function useIsMobile(breakpoint = 768): boolean {
  const subscribe = (onStoreChange: () => void) => {
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
  }

  const getSnapshot = () => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
    const mql = window.matchMedia(`(max-width: ${breakpoint}px)`)
    return Boolean(mql && mql.matches)
  }

  const getServerSnapshot = () => false

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
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
  children: ReactNode
  footer?: ReactNode
  maxHeight?: string
  zIndex?: number
}

/**
 * Native-feeling dark-mode Bottom Sheet modal for mobile viewports.
 * Enforces NO_SLOP_CHECKLIST: solid #11131a graphite surface, 1px #1e2230 border,
 * no gradients, no glows, font sizes >= 10px, body scroll lock, ESC and backdrop dismiss.
 */
export function MobileBottomSheet({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  maxHeight = '88vh',
  zIndex = 80,
}: MobileBottomSheetProps) {
  // Body scroll lock
  useEffect(() => {
    if (!open) return
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = originalOverflow
    }
  }, [open])

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
      role="dialog"
      aria-modal="true"
      style={{
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
          background: '#11131a',
          borderTop: '1px solid #1e2230',
          borderLeft: '1px solid #1e2230',
          borderRight: '1px solid #1e2230',
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
            background: '#3a4156',
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
              borderBottom: '1px solid #1e2230',
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
                    color: 'var(--chalk)',
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
                    color: 'var(--fog)',
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
                border: '1px solid #1e2230',
                borderRadius: '6px',
                color: 'var(--fog)',
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
              borderTop: '1px solid #1e2230',
              background: '#090a0f',
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
