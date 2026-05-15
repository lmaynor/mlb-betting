import { SignIn } from '@clerk/nextjs'
import type { Metadata } from 'next'

export const metadata: Metadata = { title: 'Log In' }

export default function LoginPage() {
  return (
    <div className="min-h-[70vh] flex items-center justify-center px-4 py-16">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <p className="mono text-xs text-accent uppercase tracking-widest mb-2">Beezy.VIP</p>
          <h1 className="text-xl font-extrabold uppercase tracking-tight">Sign In</h1>
        </div>
        <SignIn
          appearance={{
            variables: {
              colorPrimary:    '#00FF87',
              colorBackground: '#0D1117',
              colorText:       '#F0F6FC',
              colorInputText:  '#F0F6FC',
              colorInputBackground: '#080C10',
              borderRadius:    '0px',
              fontFamily:      'Geist, sans-serif',
            },
          }}
        />
      </div>
    </div>
  )
}
