// The current investigation: question -> proposed plan -> approved run with its live events.
// Like the selection store, one module-level store read with useSyncExternalStore.
import { useSyncExternalStore } from 'react'
import { api, PasscodeRequired, setPasscode, type Plan, type PlanResponse, type RunEvent, type RunView } from '../api/client'
import { setSelection } from './selection'

export type Investigation = {
  phase: 'idle' | 'planning' | 'plan' | 'running' | 'finished'
  question: string
  dataset: string
  proposal: PlanResponse | null
  runId: string | null
  events: RunEvent[]
  run: RunView | null
  paused: boolean
  error: string | null
  needPasscode: boolean // set by a 401; the start screen asks for the passcode, then retries
}

const empty: Investigation = {
  phase: 'idle', question: '', dataset: '', proposal: null, runId: null, events: [], run: null, paused: false, error: null, needPasscode: false,
}
let state: Investigation = empty
let closeStream: (() => void) | null = null
const listeners = new Set<() => void>()

function set(patch: Partial<Investigation>) {
  state = { ...state, ...patch }
  listeners.forEach((l) => l())
}

export function useInvestigation(): Investigation {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => state,
  )
}

export async function ask(question: string, dataset: string) {
  closeStream?.()
  set({ ...empty, phase: 'planning', question, dataset })
  try {
    const proposal = await api.plan(question, dataset)
    set({ phase: 'plan', proposal })
  } catch (e) {
    if (e instanceof PasscodeRequired) set({ phase: 'idle', needPasscode: true })
    else set({ phase: 'idle', error: `Planning failed: ${(e as Error).message}` })
  }
}

async function refresh(runId: string) {
  try {
    const run = await api.run(runId)
    if (state.runId === runId) set({ run })
  } catch (e) {
    set({ error: (e as Error).message })
  }
}

/** Approve the plan with the user's edits and start the run; the charts follow the approved params. */
export async function approve(edits: Partial<Plan>, stepThrough: boolean) {
  const proposal = state.proposal
  if (!proposal) return
  const p = { ...proposal.plan, ...edits }
  try {
    const { run_id } = await api.startRun(proposal.plan_id, edits, stepThrough)
    setSelection({
      dataset: p.dataset, groupA: p.group_a, groupB: p.group_b, cutoff: p.cutoff,
      windows: p.window_b ? [p.window_a, p.window_b] : null,
    })
    set({ phase: 'running', runId: run_id, events: [], error: null })
    closeStream = api.runEvents(run_id, (e) => {
      set({ events: [...state.events, e], paused: e.type === 'paused' })
      if (e.type === 'turn' || e.type === 'run_finished' || e.type === 'run_started') void refresh(run_id)
      if (e.type === 'run_finished') set({ phase: 'finished' })
    })
  } catch (e) {
    set(e instanceof PasscodeRequired ? { needPasscode: true, error: 'Demo passcode required.' } : { error: `Could not start: ${(e as Error).message}` })
  }
}

/** Store the passcode for this tab, check it, and retry the question that needed it. */
export async function submitPasscode(code: string): Promise<boolean> {
  setPasscode(code.trim())
  try {
    await api.checkPasscode()
  } catch {
    set({ needPasscode: true, error: 'That passcode was not accepted.' })
    return false
  }
  set({ needPasscode: false, error: null })
  if (state.question) await ask(state.question, state.dataset)
  return true
}

export async function nextTurn(stepThrough?: boolean) {
  if (!state.runId) return
  set({ paused: false })
  await api.next(state.runId, stepThrough)
}

export function reset() {
  closeStream?.()
  set(empty)
}
