import { ImageResponse } from 'next/og'
import { OgBrandMark } from '@/lib/og-brand'

export const size = { width: 180, height: 180 }
export const contentType = 'image/png'

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div style={{ width: '180px', height: '180px', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#090a0f' }}>
        <OgBrandMark width={116} />
      </div>
    ),
    { ...size },
  )
}
