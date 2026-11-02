import { useSyncExternalStore } from 'react'

const subscribe = () => () => {}

/** False during SSR and the hydration render, true afterwards. Replaces the `useEffect(() => setMounted(true), [])` pattern. */
export function useMounted(): boolean {
  return useSyncExternalStore(subscribe, () => true, () => false)
}
