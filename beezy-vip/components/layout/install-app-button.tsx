'use client'

import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useFocusTrap } from '@/components/ui/use-focus-trap'

export interface InstallAppButtonProps {
  compact?: boolean
  style?: React.CSSProperties
  className?: string
}

export interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>
}

export function isStandaloneMode(win?: Window): boolean {
  if (!win || typeof win === 'undefined') return false
  if (win.matchMedia?.('(display-mode: standalone)')?.matches) return true
  if ((win.navigator as { standalone?: boolean })?.standalone === true) return true
  return false
}

/**
 * `maxTouchPoints` is navigator.maxTouchPoints. iPadOS 13+ reports a Macintosh user agent, so a Mac UA with a touch
 * screen (a real Mac reports 0 touch points) is an iPad and gets the iOS Share-sheet instructions.
 */
export function detectInstallPlatform(
  ua: string,
  hasDeferredPrompt: boolean,
  maxTouchPoints = 0,
): 'chromium' | 'ios' | 'macos_safari' | 'other' {
  if (hasDeferredPrompt) return 'chromium'
  if (/iphone|ipad|ipod/i.test(ua)) return 'ios'
  if (/macintosh/i.test(ua) && maxTouchPoints > 1) return 'ios'
  if (/macintosh/i.test(ua) && /safari/i.test(ua) && !/chrome|chromium|edg/i.test(ua)) return 'macos_safari'
  return 'other'
}

export function InstallAppButton({ compact = false, style, className }: InstallAppButtonProps) {
  const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(null)
  // standalone (already installed) is read from the browser, not synced through an effect; appinstalled flips the state
  const standalone = useSyncExternalStore(() => () => {}, () => isStandaloneMode(window), () => false)
  const [installedEvent, setInstalledEvent] = useState(false)
  const isInstalled = standalone || installedEvent
  const [modalOpen, setModalOpen] = useState(false)
  const dialogRef = useRef<HTMLDivElement>(null)
  // focus moves into the dialog, Tab wraps inside it, and focus returns to the Install button on close
  useFocusTrap(dialogRef, modalOpen)

  useEffect(() => {
    if (typeof window === 'undefined') return
    if (isStandaloneMode(window)) return

    const handleBeforeInstall = (e: Event) => {
      e.preventDefault()
      setDeferredPrompt(e as BeforeInstallPromptEvent)
    }

    const handleAppInstalled = () => {
      setInstalledEvent(true)
      setDeferredPrompt(null)
      setModalOpen(false)
    }

    window.addEventListener('beforeinstallprompt', handleBeforeInstall)
    window.addEventListener('appinstalled', handleAppInstalled)

    return () => {
      window.removeEventListener('beforeinstallprompt', handleBeforeInstall)
      window.removeEventListener('appinstalled', handleAppInstalled)
    }
  }, [])

  useEffect(() => {
    if (!modalOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setModalOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [modalOpen])

  if (isInstalled) return null

  const handleInstallClick = async () => {
    const ua = typeof navigator !== 'undefined' ? navigator.userAgent : ''
    const platform = detectInstallPlatform(ua, Boolean(deferredPrompt), typeof navigator !== 'undefined' ? navigator.maxTouchPoints : 0)

    if (platform === 'chromium' && deferredPrompt) {
      try {
        await deferredPrompt.prompt()
        const choice = await deferredPrompt.userChoice
        if (choice.outcome === 'accepted') {
          setInstalledEvent(true)
          setDeferredPrompt(null)
        }
      } catch {
        setModalOpen(true)
      }
    } else {
      setModalOpen(true)
    }
  }

  const ua = typeof navigator !== 'undefined' ? navigator.userAgent : ''
  const platform = detectInstallPlatform(ua, Boolean(deferredPrompt), typeof navigator !== 'undefined' ? navigator.maxTouchPoints : 0)

  const btnStyle: React.CSSProperties = {
    height: '30px',
    padding: compact ? '0 8px' : '0 10px',
    borderRadius: '6px',
    border: '1px solid #2e3446',
    background: 'var(--color-background-tertiary-default)',
    color: 'var(--color-text-primary)',
    fontSize: '12px',
    fontWeight: 500,
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    cursor: 'pointer',
    textDecoration: 'none',
    lineHeight: 1,
    userSelect: 'none',
    whiteSpace: 'nowrap',
    ...style,
  }

  return (
    <>
      <button
        type="button"
        onClick={handleInstallClick}
        style={btnStyle}
        className={className}
        aria-label="Install beezy.fyi web app"
        title="Install beezy.fyi to your home screen or dock"
      >
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="7 10 12 15 17 10" />
          <line x1="12" y1="15" x2="12" y2="3" />
        </svg>
        <span>{compact ? 'Install' : 'Install App'}</span>
      </button>

      {modalOpen && (
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="install-modal-title"
          aria-label="Install beezy.fyi"
          tabIndex={-1}
          onClick={() => setModalOpen(false)}
          className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/80 p-4 outline-none backdrop-blur-[4px]"
        >
          <div
            onClick={e => e.stopPropagation()}
            className="relative w-full max-w-[440px] rounded-xl border border-separator-border bg-background-primary-default px-5.5 py-5 text-text-primary shadow-xl"
          >
            <div className="mb-2.5 flex items-start justify-between gap-3">
              <div>
                <h3 id="install-modal-title" className="text-headline-bold text-text-primary">
                  Install beezy.fyi Terminal
                </h3>
                <p className="mt-1 text-caption-1-regular text-text-secondary">
                  Add beezy.fyi to your home screen or dock for fast, full-screen odds scanning with zero browser chrome.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                aria-label="Close install modal"
                className="cursor-pointer border-none bg-transparent px-1.5 py-0.5 text-[18px] leading-none text-text-secondary hover:text-text-primary"
              >
                &times;
              </button>
            </div>

            <div className="mb-5 mt-4 flex flex-col gap-2.5">
              {platform === 'ios' ? (
                <>
                  <div className="flex items-center gap-2.5 rounded-lg border border-border-button-default bg-background-secondary-default px-3 py-2.5 text-caption-1-regular">
                    <span className="inline-flex size-[22px] shrink-0 items-center justify-center rounded-chip bg-background-tertiary-default text-mini font-semibold">
                      1
                    </span>
                    <span className="flex-1 text-text-secondary">
                      Tap the <strong>Share</strong> button in your Safari toolbar.
                    </span>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#57F287" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />
                      <polyline points="16 6 12 2 8 6" />
                      <line x1="12" y1="2" x2="12" y2="15" />
                    </svg>
                  </div>
                  <div className="flex items-center gap-2.5 rounded-lg border border-border-button-default bg-background-secondary-default px-3 py-2.5 text-caption-1-regular">
                    <span className="inline-flex size-[22px] shrink-0 items-center justify-center rounded-chip bg-background-tertiary-default text-mini font-semibold">
                      2
                    </span>
                    <span className="flex-1 text-text-secondary">
                      Scroll down and tap <strong>&ldquo;Add to Home Screen&rdquo;</strong>.
                    </span>
                  </div>
                  <div className="flex items-center gap-2.5 rounded-lg border border-border-button-default bg-background-secondary-default px-3 py-2.5 text-caption-1-regular">
                    <span className="inline-flex size-[22px] shrink-0 items-center justify-center rounded-chip bg-background-tertiary-default text-mini font-semibold">
                      3
                    </span>
                    <span className="flex-1 text-text-secondary">
                      Launch <strong>beezy.fyi</strong> from your Home Screen for full-screen standalone access.
                    </span>
                  </div>
                </>
              ) : platform === 'macos_safari' ? (
                <>
                  <div className="flex items-center gap-2.5 rounded-lg border border-border-button-default bg-background-secondary-default px-3 py-2.5 text-caption-1-regular">
                    <span className="inline-flex size-[22px] shrink-0 items-center justify-center rounded-chip bg-background-tertiary-default text-mini font-semibold">
                      1
                    </span>
                    <span className="flex-1 text-text-secondary">
                      Click <strong>File</strong> (or the Share button) in the Safari menu bar.
                    </span>
                  </div>
                  <div className="flex items-center gap-2.5 rounded-lg border border-border-button-default bg-background-secondary-default px-3 py-2.5 text-caption-1-regular">
                    <span className="inline-flex size-[22px] shrink-0 items-center justify-center rounded-chip bg-background-tertiary-default text-mini font-semibold">
                      2
                    </span>
                    <span className="flex-1 text-text-secondary">
                      Select <strong>&ldquo;Add to Dock...&rdquo;</strong>.
                    </span>
                  </div>
                  <div className="flex items-center gap-2.5 rounded-lg border border-border-button-default bg-background-secondary-default px-3 py-2.5 text-caption-1-regular">
                    <span className="inline-flex size-[22px] shrink-0 items-center justify-center rounded-chip bg-background-tertiary-default text-mini font-semibold">
                      3
                    </span>
                    <span className="flex-1 text-text-secondary">
                      Open <strong>beezy.fyi</strong> directly from your Dock for a dedicated window experience.
                    </span>
                  </div>
                </>
              ) : (
                <>
                  <div className="flex items-center gap-2.5 rounded-lg border border-border-button-default bg-background-secondary-default px-3 py-2.5 text-caption-1-regular">
                    <span className="inline-flex size-[22px] shrink-0 items-center justify-center rounded-chip bg-background-tertiary-default text-mini font-semibold">
                      1
                    </span>
                    <span className="flex-1 text-text-secondary">
                      Click the <strong>Install</strong> icon in your browser address bar (or browser menu).
                    </span>
                  </div>
                  <div className="flex items-center gap-2.5 rounded-lg border border-border-button-default bg-background-secondary-default px-3 py-2.5 text-caption-1-regular">
                    <span className="inline-flex size-[22px] shrink-0 items-center justify-center rounded-chip bg-background-tertiary-default text-mini font-semibold">
                      2
                    </span>
                    <span className="flex-1 text-text-secondary">
                      Select <strong>&ldquo;Install beezy.fyi&rdquo;</strong> or <strong>&ldquo;Add to Home Screen&rdquo;</strong>.
                    </span>
                  </div>
                  <div className="flex items-center gap-2.5 rounded-lg border border-border-button-default bg-background-secondary-default px-3 py-2.5 text-caption-1-regular">
                    <span className="inline-flex size-[22px] shrink-0 items-center justify-center rounded-chip bg-background-tertiary-default text-mini font-semibold">
                      3
                    </span>
                    <span className="flex-1 text-text-secondary">
                      Launch from your desktop or app drawer for instant, full-screen access.
                    </span>
                  </div>
                </>
              )}
            </div>

            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                className="button-press-motion cursor-pointer rounded-lg border border-border-button-default bg-background-secondary-default px-4 py-[7px] text-caption-1-semibold text-text-primary hover:bg-background-primary-hover"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
