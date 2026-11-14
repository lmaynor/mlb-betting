import '@testing-library/jest-dom'
import { render, screen, fireEvent, act } from '@testing-library/react'
import {
  InstallAppButton,
  isStandaloneMode,
  detectInstallPlatform,
} from '@/components/layout/install-app-button'
import manifest from '@/app/manifest'

describe('PWA manifest', () => {
  it('manifest carries rich metadata, display overrides, shortcuts, and screenshots', () => {
    const data = manifest()
    expect(data.id).toBe('/')
    expect(data.display).toBe('standalone')
    // window-controls-overlay needs titlebar-area-* layout in the nav, which does not exist: it must not be requested
    expect(data.display_override).toEqual(['standalone', 'minimal-ui'])
    expect(data.display_override).not.toContain('window-controls-overlay')
    expect(data.start_url).toBe('/ev?source=pwa')
    expect(data.categories).toEqual(['sports', 'finance', 'utilities'])
    expect(data.shortcuts).toBeDefined()
    expect(data.shortcuts?.length).toBe(4)

    const shortcutUrls = data.shortcuts?.map(s => s.url)
    expect(shortcutUrls).toContain('/ev')
    expect(shortcutUrls).toContain('/picks')
    expect(shortcutUrls).toContain('/edge')
    expect(shortcutUrls).toContain('/results')

    // only the wide 2000x1056 capture exists: a 'narrow' entry would reuse the same landscape image
    expect(data.screenshots?.map(s => s.form_factor)).toEqual(['wide'])
    expect(data.screenshots?.every(s => s.sizes === '2000x1056')).toBe(true)
  })

  it('offers maskable variants of the 192 and 512 icons alongside the regular ones', () => {
    const icons = manifest().icons ?? []
    expect(icons.filter(i => i.purpose === 'maskable').map(i => i.sizes).sort()).toEqual(['192x192', '512x512'])
    expect(icons.filter(i => !i.purpose && i.type === 'image/png')).toHaveLength(2)
  })
})

describe('isStandaloneMode helper', () => {
  it('returns false for undefined or null window', () => {
    expect(isStandaloneMode(undefined)).toBe(false)
  })

  it('returns true when matchMedia reports standalone', () => {
    const fakeWin = {
      matchMedia: jest.fn().mockImplementation((query: string) => ({
        matches: query === '(display-mode: standalone)',
      })),
      navigator: {},
    } as unknown as Window
    expect(isStandaloneMode(fakeWin)).toBe(true)
  })

  it('returns true when navigator.standalone is true (iOS Safari)', () => {
    const fakeWin = {
      matchMedia: jest.fn().mockImplementation(() => ({ matches: false })),
      navigator: { standalone: true },
    } as unknown as Window
    expect(isStandaloneMode(fakeWin)).toBe(true)
  })

  it('returns false when neither standalone matchMedia nor navigator.standalone is true', () => {
    const fakeWin = {
      matchMedia: jest.fn().mockImplementation(() => ({ matches: false })),
      navigator: { standalone: false },
    } as unknown as Window
    expect(isStandaloneMode(fakeWin)).toBe(false)
  })
})

describe('detectInstallPlatform helper', () => {
  it('returns chromium when deferred prompt is present', () => {
    expect(detectInstallPlatform('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)', true)).toBe('chromium')
    expect(detectInstallPlatform('Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120', true)).toBe('chromium')
  })

  it('returns ios for iOS devices without deferred prompt', () => {
    expect(detectInstallPlatform('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15', false)).toBe('ios')
    expect(detectInstallPlatform('Mozilla/5.0 (iPad; CPU OS 16_5 like Mac OS X) AppleWebKit/605.1.15', false)).toBe('ios')
  })

  it('classifies iPadOS (Macintosh UA with a touch screen) as ios, and a real Mac as macos_safari', () => {
    const ipadOs = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
    expect(detectInstallPlatform(ipadOs, false, 5)).toBe('ios')
    expect(detectInstallPlatform(ipadOs, false, 0)).toBe('macos_safari')
    expect(detectInstallPlatform(ipadOs, false)).toBe('macos_safari')
    // a deferred prompt (Chromium) still wins over everything
    expect(detectInstallPlatform(ipadOs, true, 5)).toBe('chromium')
  })

  it('returns macos_safari for macOS Safari without Chrome or Edge', () => {
    const safariUa = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
    expect(detectInstallPlatform(safariUa, false)).toBe('macos_safari')
  })

  it('returns other for desktop Chrome or unknown without deferred prompt', () => {
    const chromeMac = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    expect(detectInstallPlatform(chromeMac, false)).toBe('other')
  })
})

describe('<InstallAppButton /> component', () => {
  const originalMatchMedia = window.matchMedia
  const originalUserAgent = navigator.userAgent

  afterEach(() => {
    window.matchMedia = originalMatchMedia
    Object.defineProperty(navigator, 'userAgent', {
      value: originalUserAgent,
      configurable: true,
    })
  })

  it('does not render when the app is already running in standalone mode', () => {
    window.matchMedia = jest.fn().mockImplementation((query: string) => ({
      matches: query === '(display-mode: standalone)',
      media: query,
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }))

    const { container } = render(<InstallAppButton />)
    expect(container.firstChild).toBeNull()
  })

  it('renders install button with standard label by default', () => {
    window.matchMedia = jest.fn().mockImplementation(() => ({
      matches: false,
      media: '',
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }))

    render(<InstallAppButton />)
    const btn = screen.getByRole('button', { name: /install beezy\.fyi web app/i })
    expect(btn).toBeInTheDocument()
    expect(btn).toHaveTextContent('Install App')
  })

  it('renders compact label when compact prop is true', () => {
    window.matchMedia = jest.fn().mockImplementation(() => ({
      matches: false,
      media: '',
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }))

    render(<InstallAppButton compact />)
    const btn = screen.getByRole('button', { name: /install beezy\.fyi web app/i })
    expect(btn).toHaveTextContent('Install')
  })

  it('triggers native prompt on Chromium when beforeinstallprompt was fired', async () => {
    window.matchMedia = jest.fn().mockImplementation(() => ({
      matches: false,
      media: '',
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }))

    const promptMock = jest.fn().mockResolvedValue(undefined)
    const userChoicePromise = Promise.resolve({ outcome: 'accepted' as const, platform: 'web' })

    render(<InstallAppButton />)

    const beforeInstallEvent = new Event('beforeinstallprompt')
    Object.assign(beforeInstallEvent, {
      prompt: promptMock,
      userChoice: userChoicePromise,
    })

    act(() => {
      window.dispatchEvent(beforeInstallEvent)
    })

    const btn = screen.getByRole('button', { name: /install beezy\.fyi web app/i })
    await act(async () => {
      fireEvent.click(btn)
    })

    expect(promptMock).toHaveBeenCalledTimes(1)
  })

  it('opens instruction modal on iOS Safari and closes it on Close click or Escape', () => {
    window.matchMedia = jest.fn().mockImplementation(() => ({
      matches: false,
      media: '',
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }))

    Object.defineProperty(navigator, 'userAgent', {
      value: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
      configurable: true,
    })

    render(<InstallAppButton />)
    const btn = screen.getByRole('button', { name: /install beezy\.fyi web app/i })

    fireEvent.click(btn)

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('Install beezy.fyi Terminal')).toBeInTheDocument()
    expect(screen.getByText(/Tap the/i)).toBeInTheDocument()
    expect(screen.getByText(/Add to Home Screen/i)).toBeInTheDocument()

    // Test close button
    const closeBtn = screen.getByRole('button', { name: 'Close' })
    fireEvent.click(closeBtn)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    // Open again and test escape key
    fireEvent.click(btn)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Escape', code: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('the dialog has an accessible name, moves focus in, traps Tab and returns focus to the button on close', () => {
    window.matchMedia = jest.fn().mockImplementation(() => ({
      matches: false, media: '', onchange: null, addListener: jest.fn(), removeListener: jest.fn(),
      addEventListener: jest.fn(), removeEventListener: jest.fn(), dispatchEvent: jest.fn(),
    }))
    Object.defineProperty(navigator, 'userAgent', {
      value: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1',
      configurable: true,
    })

    render(<InstallAppButton />)
    const opener = screen.getByRole('button', { name: /install beezy\.fyi web app/i })
    opener.focus()
    fireEvent.click(opener)

    const dialog = screen.getByRole('dialog', { name: /install beezy\.fyi/i })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    const closeX = screen.getByRole('button', { name: 'Close install modal' })
    const closeBtn = screen.getByRole('button', { name: 'Close' })
    expect(dialog).toContainElement(document.activeElement as HTMLElement) // focus moved into the dialog
    expect(document.activeElement).toBe(closeX)

    // Tab from the last control wraps to the first; Shift+Tab from the first wraps to the last
    closeBtn.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(closeX)
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(closeBtn)

    fireEvent.click(closeBtn)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(document.activeElement).toBe(opener) // focus restored
  })

  it('an iPad reporting a Macintosh UA gets the Share-sheet (iOS) instructions', () => {
    window.matchMedia = jest.fn().mockImplementation(() => ({
      matches: false, media: '', onchange: null, addListener: jest.fn(), removeListener: jest.fn(),
      addEventListener: jest.fn(), removeEventListener: jest.fn(), dispatchEvent: jest.fn(),
    }))
    Object.defineProperty(navigator, 'userAgent', {
      value: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
      configurable: true,
    })
    Object.defineProperty(navigator, 'maxTouchPoints', { value: 5, configurable: true })
    try {
      render(<InstallAppButton />)
      fireEvent.click(screen.getByRole('button', { name: /install beezy\.fyi web app/i }))
      expect(screen.getByText(/Add to Home Screen/i)).toBeInTheDocument()
      expect(screen.queryByText(/Add to Dock/i)).not.toBeInTheDocument()
    } finally {
      Object.defineProperty(navigator, 'maxTouchPoints', { value: 0, configurable: true })
    }
  })
})
