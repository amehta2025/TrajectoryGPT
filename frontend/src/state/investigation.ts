// The current investigation: question -> proposed plan -> approved run with its live events.
// Like the selection store, one module-level store read with useSyncExternalStore.
import { useSyncExternalStore } from 'react'
import { api, PasscodeRequired, setPasscode, UnsupportedFigure, type Figure, type Plan, type UploadFile, type PlanResponse, type RunEvent, type RunView } from '../api/client'
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
  confirmed: string[] // low-confidence figure settings the user has checked (the backend requires all of them)
  unsupported: { message: string; figure: Figure } | null // figure read, but not a type we can run yet
}

const empty: Investigation = {
  phase: 'idle', question: '', dataset: '', proposal: null, runId: null, events: [], run: null, paused: false, error: null, needPasscode: false,
  confirmed: [], unsupported: null,
}
let state: Investigation = empty
let closeStream: (() => void) | null = null
let retry: (() => Promise<void>) | null = null // the last question or figure, re-sent after the passcode
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
  retry = () => ask(question, dataset)
  set({ ...empty, phase: 'planning', question, dataset })
  try {
    const proposal = await api.plan(question, dataset)
    set({ phase: 'plan', proposal })
  } catch (e) {
    if (e instanceof PasscodeRequired) set({ phase: 'idle', needPasscode: true })
    else set({ phase: 'idle', error: `Planning failed: ${(e as Error).message}` })
  }
}

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024
const UPLOAD_TYPES = ['image/png', 'image/jpeg', 'application/pdf']

/** Read a browser File as base64; rejects types and sizes the backend would refuse. */
export async function toUpload(file: File): Promise<UploadFile> {
  if (!UPLOAD_TYPES.includes(file.type)) throw new Error(`${file.name}: only PNG, JPEG or PDF files are accepted`)
  if (file.size > MAX_UPLOAD_BYTES) throw new Error(`${file.name}: larger than 10 MB`)
  const buf = new Uint8Array(await file.arrayBuffer())
  let bin = ''
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000))
  return { filename: file.name, mime: file.type, data: btoa(bin) }
}

/** Figure mode: figure image and/or methods PDF -> extracted spec -> plan. */
export async function askFigure(files: File[], dataset: string, note: string) {
  closeStream?.()
  retry = () => askFigure(files, dataset, note)
  const label = note.trim() || `Reproduce ${files.map((f) => f.name).join(' + ')}`
  set({ ...empty, phase: 'planning', question: label, dataset })
  try {
    const uploads = await Promise.all(files.map(toUpload))
    const proposal = await api.figurePlan(uploads, dataset, note)
    set({ phase: 'plan', proposal })
  } catch (e) {
    if (e instanceof PasscodeRequired) set({ phase: 'idle', needPasscode: true })
    else if (e instanceof UnsupportedFigure) set({ phase: 'idle', unsupported: { message: e.message, figure: e.figure } })
    else set({ phase: 'idle', error: `Figure planning failed: ${(e as Error).message}` })
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
    const { run_id } = await api.startRun(proposal.plan_id, edits, stepThrough, state.confirmed)
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
  if (retry) await retry()
  return true
}

export async function nextTurn(stepThrough?: boolean) {
  if (!state.runId) return
  set({ paused: false })
  await api.next(state.runId, stepThrough)
}

/** Tick or untick one low-confidence figure setting. */
export function toggleConfirmed(name: string) {
  set({ confirmed: state.confirmed.includes(name) ? state.confirmed.filter((n) => n !== name) : [...state.confirmed, name] })
}

/** Low-confidence settings of the proposed figure plan that are not confirmed yet. */
export function pendingConfirmations(s: Investigation): string[] {
  const f = s.proposal?.figure
  return f ? f.spec.fields.filter((x) => x.confidence === 'low' && !s.confirmed.includes(x.name)).map((x) => x.name) : []
}

export function reset() {
  closeStream?.()
  set(empty)
}
