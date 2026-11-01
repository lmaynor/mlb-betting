'use client'

import { useEffect, type RefObject } from 'react'

const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

/**
 * Modal focus handling: while `active`, focus moves into `ref`'s element, Tab / Shift+Tab wrap inside it, and on close
 * focus returns to whatever held it before (the control that opened the dialog).
 */
export function useFocusTrap(ref: RefObject<HTMLElement | null>, active: boolean) {
  useEffect(() => {
    if (!active) return
    const node = ref.current
    if (!node) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const items = () => Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(el => el.getAttribute('aria-hidden') !== 'true')

    const start = items()[0]
    if (start) start.focus()
    else node.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const list = items()
      if (list.length === 0) { e.preventDefault(); node.focus(); return }
      const first = list[0]
      const last = list[list.length - 1]
      const current = document.activeElement
      const inside = current instanceof Node && node.contains(current)
      if (e.shiftKey && (!inside || current === first || current === node)) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && (!inside || current === last)) { e.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (previous && previous.isConnected) previous.focus()
    }
  }, [ref, active])
}
