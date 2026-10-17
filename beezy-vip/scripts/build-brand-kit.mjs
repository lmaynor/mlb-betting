// One command to regenerate every static brand asset from public/logo-vector.svg:  npm run brand:build
// Writes PNG/ICO/SVG files into public/, public/brand/ and app/favicon.ico. Needs `sharp` (a devDependency).
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import sharp from 'sharp'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const EMERALD = '#57F287'
const MONO = '#f3f4f6'
const CANVAS = '#090a0f'

const source = await readFile(path.join(root, 'public/logo-vector.svg'), 'utf8')
const vb = source.match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/)
if (!vb) throw new Error('public/logo-vector.svg has no "0 0 W H" viewBox')
const [VW, VH] = [Number(vb[1]), Number(vb[2])]
const paths = [...source.matchAll(/ d="([^"]+)"/g)].map(m => m[1])
if (paths.length !== 3) throw new Error(`expected 3 paths in logo-vector.svg, found ${paths.length}`)

/** The fin as a standalone SVG in `color`. */
const finSvg = (color, width) => {
  const height = Math.round((width * VH) / VW)
  const body = paths.map(d => `<path fill="${color}" d="${d}"/>`).join('')
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${VW} ${VH}">${body}</svg>`
}

/** A square canvas with the fin centred; `pad` is the fraction of the edge kept clear on each side. */
async function tile(edge, pad, { rounded = 0 } = {}) {
  const finW = Math.round(edge * (1 - 2 * pad))
  const fin = Buffer.from(finSvg(EMERALD, finW))
  const finH = Math.round((finW * VH) / VW)
  const base = rounded
    ? Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${edge}" height="${edge}"><rect width="${edge}" height="${edge}" rx="${rounded}" fill="${CANVAS}"/></svg>`)
    : { create: { width: edge, height: edge, channels: 4, background: CANVAS } }
  return sharp(base)
    .composite([{ input: fin, left: Math.round((edge - finW) / 2), top: Math.round((edge - finH) / 2) }])
    .png()
}

const out = (...p) => path.join(root, ...p)
await mkdir(out('public/brand'), { recursive: true })

await (await tile(192, 0.18)).toFile(out('public/icon-192.png'))
await (await tile(512, 0.18)).toFile(out('public/icon-512.png'))
await (await tile(180, 0.18)).toFile(out('public/apple-touch-icon.png'))
await (await tile(512, 0.22)).toFile(out('public/brand/discord-avatar-512.png'))
await (await tile(400, 0.18)).toFile(out('public/brand/x-avatar-400.png'))

// Standalone vector variants.
await writeFile(out('public/brand/logo-emerald.svg'), finSvg(EMERALD, VW) + '\n')
await writeFile(out('public/brand/logo-mono-white.svg'), finSvg(MONO, VW) + '\n')

// favicon.ico: a 32x32 PNG inside an ICO container (supported by every current browser).
const png32 = await (await tile(32, 0.1)).toBuffer()
const header = Buffer.alloc(22)
header.writeUInt16LE(0, 0)
header.writeUInt16LE(1, 2)
header.writeUInt16LE(1, 4)
header.writeUInt8(32, 6)
header.writeUInt8(32, 7)
header.writeUInt16LE(1, 10)
header.writeUInt16LE(32, 12)
header.writeUInt32LE(png32.length, 14)
header.writeUInt32LE(22, 18)
await writeFile(out('app/favicon.ico'), Buffer.concat([header, png32]))

// X banner: 1500x500, fin + wordmark + descriptor, centred.
{
  const W = 1500, H = 500
  const finW = 250
  const finH = Math.round((finW * VH) / VW)
  const gap = 36
  const lockupW = 990 // fin + gap + the "beezy.fyi" / descriptor block
  const finX = Math.round((W - lockupW) / 2)
  const textX = finX + finW + gap
  const text = Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}">` +
    `<text x="${textX}" y="${H / 2 + 8}" font-family="Helvetica, Arial, sans-serif" font-size="96" font-weight="800" letter-spacing="-3" fill="${MONO}">beezy<tspan fill="${EMERALD}" font-weight="700">.fyi</tspan></text>` +
    `<text x="${textX + 4}" y="${H / 2 + 62}" font-family="Helvetica, Arial, sans-serif" font-size="26" font-weight="700" letter-spacing="5" fill="#7d8596">QUANTITATIVE MODELS &amp; +EV TERMINAL</text>` +
    `</svg>`,
  )
  await sharp({ create: { width: W, height: H, channels: 4, background: CANVAS } })
    .composite([
      { input: Buffer.from(finSvg(EMERALD, finW)), left: finX, top: Math.round((H - finH) / 2) },
      { input: text, left: 0, top: 0 },
    ])
    .png()
    .toFile(out('public/brand/x-banner-1500x500.png'))
}

console.log('brand kit written: public/icon-192.png, icon-512.png, apple-touch-icon.png, brand/*, app/favicon.ico')
