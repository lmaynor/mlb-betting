import { extendTailwindMerge } from 'tailwind-merge'

/** BoardUI text-style families registered in styles/typography.css. */
const TEXT_FAMILIES = [
  'large-title', 'display-1', 'display-2', 'display-3', 'display-4',
  'title-1', 'title-2', 'title-3', 'headline', 'body', 'body-2',
  'caption-1', 'caption-2',
] as const

const TEXT_WEIGHTS = ['regular', 'medium', 'semibold', 'bold'] as const

const TEXT_STYLE_SUFFIXES = TEXT_FAMILIES.flatMap(family =>
  TEXT_WEIGHTS.map(weight => `${family}-${weight}`)
)

const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: TEXT_STYLE_SUFFIXES }],
    },
  },
})

/** Merge Tailwind classes safely; last-write-wins on conflicts. */
export const cx = twMerge

/** Identity helper for Tailwind IntelliSense class sorting in style objects. */
export function sortCx<
  T extends Record<string, string | number | Record<string, string | number | Record<string, string | number>>>,
>(classes: T): T {
  return classes
}
