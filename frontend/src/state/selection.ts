// One shared selection store. Heatmap, distance plot, and viewer all read and write it.
import { useSyncExternalStore } from 'react'

export type Pair = { keyA: string; keyB: string; excluded: boolean }

export type Selection = {
  dataset: string
  groupA: string
  groupB: string
  pair: Pair | null
  frame: number // trajectory frame index (always kept, whatever the time unit)
  cutoff: number // Å; the approved plan's cutoff once an investigation runs
  windows: [[number, number], [number, number]] | null // approved comparison windows; null = halves
  focus: { frame: number; text: string } | null // why the viewer is showing this frame (set when a report opens)
}

let state: Selection = { dataset: 'adk_equilibrium', groupA: 'NMP', groupB: 'CORE', pair: null, frame: 0, cutoff: 4.5, windows: null, focus: null }
const listeners = new Set<() => void>()

export function setSelection(patch: Partial<Selection>) {
  const next = { ...state, ...patch }
  // A new dataset or group pair invalidates the selected pair; a new dataset also resets the frame.
  if (patch.dataset !== undefined && patch.dataset !== state.dataset) {
    next.pair = patch.pair ?? null
    next.frame = patch.frame ?? 0
    next.focus = patch.focus ?? null
  } else if ((patch.groupA ?? state.groupA) !== state.groupA || (patch.groupB ?? state.groupB) !== state.groupB) {
    next.pair = patch.pair ?? null
  }
  state = next
  listeners.forEach((l) => l())
}

export function getSelection(): Selection {
  return state
}

export function useSelection(): Selection {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => state,
  )
}
