import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        bg:           '#080C10',
        surface:      '#0D1117',
        accent:       '#00FF87',
        win:          '#00FF87',
        loss:         '#FF4757',
        text:         '#F0F6FC',
        muted:        '#7D8590',
        border:       'rgba(255,255,255,0.06)',
        'border-bright': 'rgba(255,255,255,0.14)',
      },
      fontFamily: {
        sans: ['Geist', 'sans-serif'],
        mono: ['Geist Mono', 'monospace'],
      },
      borderRadius: {
        DEFAULT: '0px',
        sm: '2px',
      },
    },
  },
  plugins: [],
}
export default config
