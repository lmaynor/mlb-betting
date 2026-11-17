import '@testing-library/jest-dom'
import { render } from '@testing-library/react'
import { DistributionStrip } from '@/components/picks/distribution-strip'

describe('DistributionStrip', () => {
  it('renders correctly with mean, p10, p90, and line', () => {
    const { container } = render(
      <DistributionStrip mean={4.52} p10={1} p90={8} line={3.5} side="over" />
    )

    expect(container.textContent).toContain('P10: 1')
    expect(container.textContent).toContain('Proj 4.52')
    expect(container.textContent).toContain('Line: 3.5')
    expect(container.textContent).toContain('P90: 8')
    expect(container.textContent).toContain('80% range')
  })

  it('renders nothing when p10 is null', () => {
    const { container } = render(
      <DistributionStrip mean={4.52} p10={null} p90={8} line={3.5} side="over" />
    )

    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when mean or p90 is null', () => {
    const { container: c1 } = render(
      <DistributionStrip mean={null} p10={1} p90={8} line={3.5} side="over" />
    )
    expect(c1.firstChild).toBeNull()

    const { container: c2 } = render(
      <DistributionStrip mean={4.52} p10={1} p90={null} line={3.5} side="over" />
    )
    expect(c2.firstChild).toBeNull()
  })
})
