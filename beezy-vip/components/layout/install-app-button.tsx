'use client'

import React, { useEffect, useState } from 'react'

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

export function detectInstallPlatform(
  ua: string,
  hasDeferredPrompt: boolean,
): 'chromium' | 'ios' | 'macos_safari' | 'other' {
  if (hasDeferredPrompt) return 'chromium'
  if (/iphone|ipad|ipod/i.test(ua)) return 'ios'
  if (/macintosh/i.test(ua) && /safari/i.test(ua) && !/chrome|chromium|edg/i.test(ua)) return 'macos_safari'
  return 'other'
}

export function InstallAppButton({ compact = false, style, className }: InstallAppButtonProps) {
  const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(null)
  const [isInstalled, setIsInstalled] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)

  useEffect(() => {
    if (typeof window === 'undefined') return
    if (isStandaloneMode(window)) {
      setIsInstalled(true)
      return
    }

    const handleBeforeInstall = (e: Event) => {
      e.preventDefault()
      setDeferredPrompt(e as BeforeInstallPromptEvent)
    }

    const handleAppInstalled = () => {
      setIsInstalled(true)
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
    const platform = detectInstallPlatform(ua, Boolean(deferredPrompt))

    if (platform === 'chromium' && deferredPrompt) {
      try {
        await deferredPrompt.prompt()
        const choice = await deferredPrompt.userChoice
        if (choice.outcome === 'accepted') {
          setIsInstalled(true)
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
  const platform = detectInstallPlatform(ua, Boolean(deferredPrompt))

  const btnStyle: React.CSSProperties = {
    height: '30px',
    padding: compact ? '0 8px' : '0 10px',
    borderRadius: '6px',
    border: '1px solid #2e3446',
    background: '#171a24',
    color: '#f3f4f6',
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
          role="dialog"
          aria-modal="true"
          aria-labelledby="install-modal-title"
          onClick={() => setModalOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0, 0, 0, 0.78)',
            backdropFilter: 'blur(4px)',
            WebkitBackdropFilter: 'blur(4px)',
            zIndex: 9999,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '16px',
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{
              background: '#11131a',
              border: '1px solid #1e2230',
              borderRadius: '8px',
              maxWidth: '440px',
              width: '100%',
              padding: '20px 22px',
              boxShadow: '0 20px 40px rgba(0, 0, 0, 0.6)',
              color: '#f3f4f6',
              position: 'relative',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px', marginBottom: '10px' }}>
              <div>
                <h3 id="install-modal-title" style={{ fontSize: '16px', fontWeight: 700, margin: 0, color: '#f3f4f6' }}>
                  Install beezy.fyi Terminal
                </h3>
                <p style={{ fontSize: '12px', color: '#7d8596', margin: '4px 0 0', lineHeight: 1.5 }}>
                  Add beezy.fyi to your home screen or dock for fast, full-screen odds scanning with zero browser chrome.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                aria-label="Close install modal"
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: '#7d8596',
                  cursor: 'pointer',
                  fontSize: '18px',
                  lineHeight: 1,
                  padding: '2px 6px',
                }}
              >
                &times;
              </button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginTop: '16px', marginBottom: '20px' }}>
              {platform === 'ios' ? (
                <>
                  <div style={{ background: '#171a24', border: '1px solid #2e3446', borderRadius: '6px', padding: '10px 12px', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ width: '22px', height: '22px', borderRadius: '4px', background: '#252a38', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '11px', flexShrink: 0 }}>
                      1
                    </span>
                    <span style={{ flex: 1, color: '#c9cdd8' }}>
                      Tap the <strong>Share</strong> button in your Safari toolbar.
                    </span>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#57F287" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />
                      <polyline points="16 6 12 2 8 6" />
                      <line x1="12" y1="2" x2="12" y2="15" />
                    </svg>
                  </div>
                  <div style={{ background: '#171a24', border: '1px solid #2e3446', borderRadius: '6px', padding: '10px 12px', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ width: '22px', height: '22px', borderRadius: '4px', background: '#252a38', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '11px', flexShrink: 0 }}>
                      2
                    </span>
                    <span style={{ flex: 1, color: '#c9cdd8' }}>
                      Scroll down and tap <strong>&ldquo;Add to Home Screen&rdquo;</strong>.
                    </span>
                  </div>
                  <div style={{ background: '#171a24', border: '1px solid #2e3446', borderRadius: '6px', padding: '10px 12px', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ width: '22px', height: '22px', borderRadius: '4px', background: '#252a38', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '11px', flexShrink: 0 }}>
                      3
                    </span>
                    <span style={{ flex: 1, color: '#c9cdd8' }}>
                      Launch <strong>beezy.fyi</strong> from your Home Screen for full-screen standalone access.
                    </span>
                  </div>
                </>
              ) : platform === 'macos_safari' ? (
                <>
                  <div style={{ background: '#171a24', border: '1px solid #2e3446', borderRadius: '6px', padding: '10px 12px', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ width: '22px', height: '22px', borderRadius: '4px', background: '#252a38', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '11px', flexShrink: 0 }}>
                      1
                    </span>
                    <span style={{ flex: 1, color: '#c9cdd8' }}>
                      Click <strong>File</strong> (or the Share button) in the Safari menu bar.
                    </span>
                  </div>
                  <div style={{ background: '#171a24', border: '1px solid #2e3446', borderRadius: '6px', padding: '10px 12px', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ width: '22px', height: '22px', borderRadius: '4px', background: '#252a38', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '11px', flexShrink: 0 }}>
                      2
                    </span>
                    <span style={{ flex: 1, color: '#c9cdd8' }}>
                      Select <strong>&ldquo;Add to Dock...&rdquo;</strong>.
                    </span>
                  </div>
                  <div style={{ background: '#171a24', border: '1px solid #2e3446', borderRadius: '6px', padding: '10px 12px', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ width: '22px', height: '22px', borderRadius: '4px', background: '#252a38', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '11px', flexShrink: 0 }}>
                      3
                    </span>
                    <span style={{ flex: 1, color: '#c9cdd8' }}>
                      Open <strong>beezy.fyi</strong> directly from your Dock for a dedicated window experience.
                    </span>
                  </div>
                </>
              ) : (
                <>
                  <div style={{ background: '#171a24', border: '1px solid #2e3446', borderRadius: '6px', padding: '10px 12px', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ width: '22px', height: '22px', borderRadius: '4px', background: '#252a38', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '11px', flexShrink: 0 }}>
                      1
                    </span>
                    <span style={{ flex: 1, color: '#c9cdd8' }}>
                      Click the <strong>Install</strong> icon in your browser address bar (or browser menu).
                    </span>
                  </div>
                  <div style={{ background: '#171a24', border: '1px solid #2e3446', borderRadius: '6px', padding: '10px 12px', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ width: '22px', height: '22px', borderRadius: '4px', background: '#252a38', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '11px', flexShrink: 0 }}>
                      2
                    </span>
                    <span style={{ flex: 1, color: '#c9cdd8' }}>
                      Select <strong>&ldquo;Install beezy.fyi&rdquo;</strong> or <strong>&ldquo;Add to Home Screen&rdquo;</strong>.
                    </span>
                  </div>
                  <div style={{ background: '#171a24', border: '1px solid #2e3446', borderRadius: '6px', padding: '10px 12px', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ width: '22px', height: '22px', borderRadius: '4px', background: '#252a38', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '11px', flexShrink: 0 }}>
                      3
                    </span>
                    <span style={{ flex: 1, color: '#c9cdd8' }}>
                      Launch from your desktop or app drawer for instant, full-screen access.
                    </span>
                  </div>
                </>
              )}
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                style={{
                  padding: '7px 16px',
                  borderRadius: '6px',
                  border: '1px solid #2e3446',
                  background: '#171a24',
                  color: '#f3f4f6',
                  fontSize: '12px',
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
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
