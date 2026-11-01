import '@testing-library/jest-dom'
import { render, screen, fireEvent, renderHook, act } from '@testing-library/react'
import {
  useIsMobile,
  HorizontalPillStrip,
  MobileBottomSheet,
} from '@/components/ui/mobile-sheet'

describe('useIsMobile hook', () => {
  const originalMatchMedia = window.matchMedia

  afterEach(() => {
    window.matchMedia = originalMatchMedia
  })

  it('evaluates matchMedia against the default breakpoint (768px)', () => {
    let listener: (() => void) | null = null
    window.matchMedia = jest.fn().mockImplementation((query: string) => ({
      matches: query === '(max-width: 768px)',
      addEventListener: jest.fn((_, l) => { listener = l }),
      removeEventListener: jest.fn(),
    }))

    const { result } = renderHook(() => useIsMobile())
    expect(result.current).toBe(true)
    expect(window.matchMedia).toHaveBeenCalledWith('(max-width: 768px)')
  })

  it('subscribes once, not on every render', () => {
    const add = jest.fn()
    window.matchMedia = jest.fn().mockImplementation(() => ({
      matches: false,
      addEventListener: add,
      removeEventListener: jest.fn(),
    }))
    const { rerender } = renderHook(() => useIsMobile(700))
    rerender()
    rerender()
    rerender()
    expect(add).toHaveBeenCalledTimes(1)
  })

  it('updates reactively when media query changes', () => {
    let listener: (() => void) | null = null
    let matches = false

    window.matchMedia = jest.fn().mockImplementation((query: string) => ({
      get matches() { return matches },
      addEventListener: jest.fn((_, l) => { listener = l }),
      removeEventListener: jest.fn(),
    }))

    const { result } = renderHook(() => useIsMobile(1024))
    expect(result.current).toBe(false)

    act(() => {
      matches = true
      if (listener) listener()
    })

    expect(result.current).toBe(true)
  })
})

describe('<HorizontalPillStrip />', () => {
  it('renders children with no-scrollbar class and swipeable styles', () => {
    render(
      <HorizontalPillStrip data-testid="strip">
        <button>Pill 1</button>
        <button>Pill 2</button>
      </HorizontalPillStrip>
    )

    const strip = screen.getByText('Pill 1').parentElement
    expect(strip).toHaveClass('no-scrollbar')
    expect(strip).toHaveStyle({
      display: 'flex',
      overflowX: 'auto',
      scrollbarWidth: 'none',
    })
    expect(screen.getByText('Pill 1')).toBeInTheDocument()
    expect(screen.getByText('Pill 2')).toBeInTheDocument()
  })
})

describe('<MobileBottomSheet />', () => {
  it('does not render when open is false', () => {
    render(
      <MobileBottomSheet open={false} onClose={() => {}}>
        <div>Sheet Content</div>
      </MobileBottomSheet>
    )
    expect(screen.queryByText('Sheet Content')).not.toBeInTheDocument()
  })

  it('renders title, subtitle, children, footer, and drag-handle when open', () => {
    render(
      <MobileBottomSheet
        open={true}
        onClose={() => {}}
        title="Test Sheet Title"
        subtitle="Test Subtitle"
        footer={<button>Action</button>}
      >
        <div>Sheet Content</div>
      </MobileBottomSheet>
    )

    expect(screen.getByText('Test Sheet Title')).toBeInTheDocument()
    expect(screen.getByText('Test Subtitle')).toBeInTheDocument()
    expect(screen.getByText('Sheet Content')).toBeInTheDocument()
    expect(screen.getByText('Action')).toBeInTheDocument()
    expect(screen.getByTestId('bottom-sheet-drag-handle')).toBeInTheDocument()
    expect(screen.getByTestId('bottom-sheet-backdrop')).toBeInTheDocument()
  })

  it('calls onClose when backdrop or close button is clicked', () => {
    const handleClose = jest.fn()
    render(
      <MobileBottomSheet
        open={true}
        onClose={handleClose}
        title="Closeable Sheet"
      >
        <div>Body</div>
      </MobileBottomSheet>
    )

    // Click backdrop
    fireEvent.click(screen.getByTestId('bottom-sheet-backdrop'))
    expect(handleClose).toHaveBeenCalledTimes(1)

    // Click close button
    const closeBtn = screen.getByRole('button', { name: 'Close' })
    fireEvent.click(closeBtn)
    expect(handleClose).toHaveBeenCalledTimes(2)
  })

  it('calls onClose when Escape key is pressed', () => {
    const handleClose = jest.fn()
    render(
      <MobileBottomSheet
        open={true}
        onClose={handleClose}
        title="Escape Sheet"
      >
        <div>Body</div>
      </MobileBottomSheet>
    )

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(handleClose).toHaveBeenCalledTimes(1)
  })

  it('locks body scroll when open and restores when closed/unmounted', () => {
    expect(document.body.style.overflow).toBe('')

    const { rerender, unmount } = render(
      <MobileBottomSheet open={true} onClose={() => {}}>
        <div>Body</div>
      </MobileBottomSheet>
    )
    expect(document.body.style.overflow).toBe('hidden')

    rerender(
      <MobileBottomSheet open={false} onClose={() => {}}>
        <div>Body</div>
      </MobileBottomSheet>
    )
    expect(document.body.style.overflow).toBe('')

    rerender(
      <MobileBottomSheet open={true} onClose={() => {}}>
        <div>Body</div>
      </MobileBottomSheet>
    )
    expect(document.body.style.overflow).toBe('hidden')

    unmount()
    expect(document.body.style.overflow).toBe('')
  })

  it('names the dialog (title by default, or an explicit ariaLabel)', () => {
    const { rerender } = render(
      <MobileBottomSheet open={true} onClose={() => {}} title="Filter Picks"><div>Body</div></MobileBottomSheet>
    )
    expect(screen.getByRole('dialog', { name: 'Filter Picks' })).toBeInTheDocument()
    rerender(
      <MobileBottomSheet open={true} onClose={() => {}} title={<span>Rich</span>} ariaLabel="Terminal filters"><div>Body</div></MobileBottomSheet>
    )
    expect(screen.getByRole('dialog', { name: 'Terminal filters' })).toBeInTheDocument()
  })

  it('moves focus in, traps Tab inside the sheet and restores focus to the opener on close', () => {
    const opener = document.createElement('button')
    document.body.appendChild(opener)
    opener.focus()
    const { rerender } = render(
      <MobileBottomSheet open={true} onClose={() => {}} title="Trap" footer={<button>Last</button>}>
        <button>Middle</button>
      </MobileBottomSheet>
    )
    const closeBtn = screen.getByRole('button', { name: 'Close' })
    const last = screen.getByRole('button', { name: 'Last' })
    expect(document.activeElement).toBe(closeBtn)

    last.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(closeBtn)
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)

    rerender(<MobileBottomSheet open={false} onClose={() => {}} title="Trap"><button>Middle</button></MobileBottomSheet>)
    expect(document.activeElement).toBe(opener)
    opener.remove()
  })

  it('stacked sheets that close out of order restore the ORIGINAL body overflow only when the last one closes', () => {
    document.body.style.overflow = 'scroll'
    const a = render(<MobileBottomSheet open={true} onClose={() => {}}><div>A</div></MobileBottomSheet>)
    const b = render(<MobileBottomSheet open={true} onClose={() => {}}><div>B</div></MobileBottomSheet>)
    expect(document.body.style.overflow).toBe('hidden')
    a.unmount() // the first-opened sheet closes first
    expect(document.body.style.overflow).toBe('hidden')
    b.unmount()
    expect(document.body.style.overflow).toBe('scroll')
    document.body.style.overflow = ''
  })
})

