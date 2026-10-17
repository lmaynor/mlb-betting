import { ImageResponse } from 'next/og'
import { NextRequest }   from 'next/server'
import { OgBrandBadge } from '@/lib/og-brand'

export const runtime = 'edge'

export async function GET(req: NextRequest) {
  const sp    = req.nextUrl.searchParams
  const title = sp.get('title') ?? 'beezy.fyi'
  const sub   = sp.get('sub')   ?? 'Quantitative models \u00b7 +EV terminal'
  const stat1 = sp.get('stat1') ?? ''
  const stat2 = sp.get('stat2') ?? ''
  const stat3 = sp.get('stat3') ?? ''

  return new ImageResponse(
    (
      <div
        style={{
          width:      '1200px',
          height:     '630px',
          background: '#090a0f',
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
          <OgBrandBadge size={40} />
          <span style={{ display: 'flex', width: '1px', height: '18px', background: '#2e3446' }} />
          <span style={{ fontSize: '12px', color: '#7d8596', letterSpacing: '3px', textTransform: 'uppercase' }}>
            Paper Mode
          </span>
        </div>

        {/* Title */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
          <p style={{ fontSize: '14px', color: '#7d8596', letterSpacing: '3px', textTransform: 'uppercase', marginBottom: '18px' }}>
            {sub}
          </p>
          <h1 style={{ fontSize: '60px', fontWeight: 800, color: '#ffffff', lineHeight: '1.04', letterSpacing: '-2px', margin: 0 }}>
            {title}
          </h1>
        </div>

        {/* Stats row */}
        {(stat1 || stat2 || stat3) && (
          <div style={{ display: 'flex', gap: '0px', marginTop: '48px', border: '1px solid #1e2230', borderRadius: '12px', overflow: 'hidden' }}>
            {[stat1, stat2, stat3].filter(Boolean).map((s, i) => {
              const [label, value] = s.split(':')
              return (
                <div
                  key={i}
                  style={{
                    flex: 1,
                    padding: '22px 26px',
                    borderRight: i < 2 ? '1px solid #1e2230' : 'none',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px',
                  }}
                >
                  <span style={{ fontSize: '11px', color: '#7d8596', letterSpacing: '2px', textTransform: 'uppercase' }}>
                    {label}
                  </span>
                  <span style={{ fontSize: '28px', fontWeight: 700, color: '#57f287' }}>{value}</span>
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
          background: '#57f287',
        }} />
      </div>
    ),
    {
      width:  1200,
      height: 630,
    }
  )
}
