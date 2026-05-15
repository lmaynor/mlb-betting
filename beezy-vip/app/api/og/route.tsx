import { ImageResponse } from 'next/og'
import { NextRequest }   from 'next/server'

export const runtime = 'edge'

export async function GET(req: NextRequest) {
  const sp    = req.nextUrl.searchParams
  const title = sp.get('title') ?? 'Beezy.VIP'
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
          background: '#080C10',
          display:    'flex',
          flexDirection: 'column',
          padding:    '60px',
          fontFamily: 'monospace',
          position:   'relative',
          // Grid lines
          backgroundImage: 'linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px)',
          backgroundSize:  '40px 40px',
        }}
      >
        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '48px' }}>
          <span style={{ fontSize: '20px', fontWeight: 700, color: '#00FF87', letterSpacing: '-0.5px' }}>
            BEEZY<span style={{ color: '#F0F6FC' }}>.VIP</span>
          </span>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#00FF87' }} />
          <span style={{ fontSize: '12px', color: '#7D8590', letterSpacing: '3px', textTransform: 'uppercase' }}>
            Paper Mode
          </span>
        </div>

        {/* Title */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
          <p style={{ fontSize: '14px', color: '#00FF87', letterSpacing: '3px', textTransform: 'uppercase', marginBottom: '16px' }}>
            {sub}
          </p>
          <h1 style={{ fontSize: '56px', fontWeight: 800, color: '#F0F6FC', lineHeight: '1', textTransform: 'uppercase', letterSpacing: '-2px', margin: 0 }}>
            {title}
          </h1>
        </div>

        {/* Stats row */}
        {(stat1 || stat2 || stat3) && (
          <div style={{ display: 'flex', gap: '0px', marginTop: '48px', border: '1px solid rgba(255,255,255,0.06)' }}>
            {[stat1, stat2, stat3].filter(Boolean).map((s, i) => {
              const [label, value] = s.split(':')
              return (
                <div
                  key={i}
                  style={{
                    flex: 1,
                    padding: '20px 24px',
                    borderRight: i < 2 ? '1px solid rgba(255,255,255,0.06)' : 'none',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '6px',
                  }}
                >
                  <span style={{ fontSize: '11px', color: '#7D8590', letterSpacing: '2px', textTransform: 'uppercase' }}>
                    {label}
                  </span>
                  <span style={{ fontSize: '28px', fontWeight: 700, color: '#00FF87' }}>{value}</span>
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
          height:     '2px',
          background: '#00FF87',
        }} />
      </div>
    ),
    {
      width:  1200,
      height: 630,
    }
  )
}
