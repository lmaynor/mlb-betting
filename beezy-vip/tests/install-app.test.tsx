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
    expect(data.display_override).toEqual(['window-controls-overlay', 'standalone', 'minimal-ui'])
    expect(data.start_url).toBe('/ev?source=pwa')
    expect(data.categories).toEqual(['sports', 'finance', 'utilities'])
    expect(data.shortcuts).toBeDefined()
    expect(data.shortcuts?.length).toBe(4)

    const shortcutUrls = data.shortcuts?.map(s => s.url)
    expect(shortcutUrls).toContain('/ev')
    expect(shortcutUrls).toContain('/cheat-sheet')
    expect(shortcutUrls).toContain('/edge')
    expect(shortcutUrls).toContain('/results')

    expect(data.screenshots).toBeDefined()
    expect(data.screenshots?.length).toBe(2)
    const formFactors = data.screenshots?.map(s => s.form_factor)
    expect(formFactors).toContain('wide')
    expect(formFactors).toContain('narrow')
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
})
