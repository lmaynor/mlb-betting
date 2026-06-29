import { ImageResponse } from 'next/og'
import { NextRequest }   from 'next/server'

export const runtime = 'edge'

export async function GET(req: NextRequest) {
  const sp    = req.nextUrl.searchParams
  const title = sp.get('title') ?? 'Beezy.FYI'
  const sub   = sp.get('sub')   ?? 'MLB Picks · Backed by Machine Learning'
  const stat1 = sp.get('stat1') ?? ''
  const stat2 = sp.get('stat2') ?? ''
  const stat3 = sp.get('stat3') ?? ''

  return new ImageResponse(
    (
      <div
        style={{
          width:      '1200px',
          height:     '630px',
          background: '#04040b',
          display:    'flex',
          flexDirection: 'column',
          padding:    '60px',
          fontFamily: 'monospace',
          position:   'relative',
          // Grid lines
          backgroundImage: 'linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px)',
          backgroundSize:  '44px 44px',
        }}
      >
        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px', marginBottom: '48px' }}>
          <span style={{ width: '12px', height: '12px', borderRadius: '50%', background: '#71d083' }} />
          <span style={{ fontSize: '22px', fontWeight: 800, color: '#f3f2f5', letterSpacing: '-0.5px' }}>
            BEEZY<span style={{ color: '#71d083' }}>.FYI</span>
          </span>
          <span style={{ display: 'flex', width: '1px', height: '18px', background: '#323035' }} />
          <span style={{ fontSize: '12px', color: '#8a8893', letterSpacing: '3px', textTransform: 'uppercase' }}>
            Paper Mode
          </span>
        </div>

        {/* Title */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
          <p style={{ fontSize: '14px', color: '#8a8893', letterSpacing: '3px', textTransform: 'uppercase', marginBottom: '18px' }}>
            {sub}
          </p>
          <h1 style={{ fontSize: '60px', fontWeight: 800, color: '#f3f2f5', lineHeight: '1.04', letterSpacing: '-2px', margin: 0 }}>
            {title}
          </h1>
        </div>

        {/* Stats row */}
        {(stat1 || stat2 || stat3) && (
          <div style={{ display: 'flex', gap: '0px', marginTop: '48px', border: '1px solid #2b292d', borderRadius: '12px', overflow: 'hidden' }}>
            {[stat1, stat2, stat3].filter(Boolean).map((s, i) => {
              const [label, value] = s.split(':')
              return (
                <div
                  key={i}
                  style={{
                    flex: 1,
                    padding: '22px 26px',
                    borderRight: i < 2 ? '1px solid #2b292d' : 'none',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px',
                  }}
                >
                  <span style={{ fontSize: '11px', color: '#8a8893', letterSpacing: '2px', textTransform: 'uppercase' }}>
                    {label}
                  </span>
                  <span style={{ fontSize: '28px', fontWeight: 700, color: '#71d083' }}>{value}</span>
                </div>
              )
            })}
          </div>
        )}

        {/* Bottom border accent */}
        <div style={{
          position:   'absolute',
          bottom:     0,
          left:       0,
          right:      0,
          height:     '3px',
          background: '#71d083',
        }} />
      </div>
    ),
    {
      width:  1200,
      height: 630,
    }
  )
}
