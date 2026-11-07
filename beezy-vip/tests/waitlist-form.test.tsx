import '@testing-library/jest-dom'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WaitlistForm } from '@/components/ui/waitlist-form'

function fillAndSubmit(email: string) {
  fireEvent.change(screen.getByLabelText('Email address'), { target: { value: email } })
  const form = document.querySelector('form')
  if (!form) throw new Error('form not rendered')
  fireEvent.submit(form)
}

describe('WaitlistForm', () => {
  let fetchMock: jest.Mock
  beforeEach(() => {
    fetchMock = jest.fn()
    global.fetch = fetchMock as unknown as typeof fetch
  })

  it('shows the consent copy before signup', () => {
    render(<WaitlistForm source="pricing" />)
    expect(screen.getByText(/Launch updates only\. No spam/)).toBeInTheDocument()
    expect(screen.getByText(/unsubscribe at any time/)).toBeInTheDocument()
  })

  it('submits the email with its source and shows the success state', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ok: true, status: 'created' }) })
    render(<WaitlistForm source="pricing" />)
    fillAndSubmit('fan@example.com')

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent("You're on the list"))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/waitlist')
    expect(JSON.parse(init.body)).toEqual({ email: 'fan@example.com', source: 'pricing' })
  })

  it('tells a repeat signup they are already on the list', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ok: true, status: 'existing' }) })
    render(<WaitlistForm source="footer" label="Notify me" />)
    fillAndSubmit('fan@example.com')

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent("You're already on the list"))
  })

  it('renders the server error inline and keeps the form usable', async () => {
    fetchMock.mockResolvedValue({ ok: false, json: async () => ({ error: 'Enter a valid email address' }) })
    render(<WaitlistForm source="signup" />)
    fillAndSubmit('nope')

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Enter a valid email address'))
    expect(screen.getByLabelText('Email address')).toBeInTheDocument()
  })
})
