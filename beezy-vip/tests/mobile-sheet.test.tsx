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
})
