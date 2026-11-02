// The current investigation: question -> proposed plan -> approved run with its live events.
// Like the selection store, one module-level store read with useSyncExternalStore.
import { useSyncExternalStore } from 'react'
import { api, PasscodeRequired, ReplayOnly, setPasscode, UnsupportedFigure, type Declined, type Figure, type Plan, type Provider, type UploadFile, type PlanResponse, type RunEvent, type RunView } from '../api/client'
import { getSelection, setSelection } from './selection'
import { parseResidueKey } from './residueKey'

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
  science: boolean // "Show the science": technical detail (statistics, units, residue codes, charts, logs) is visible
  declined: (Declined & { question: string }) | null // the question was turned away before planning
}

const empty: Investigation = {
  phase: 'idle', question: '', dataset: '', proposal: null, runId: null, events: [], run: null, paused: false, error: null, needPasscode: false,
  confirmed: [], unsupported: null, science: false, declined: null,
}
let state: Investigation = empty
let closeStream: (() => void) | null = null
let retry: (() => Promise<void>) | null = null // the last question or figure, re-sent after the passcode
const listeners = new Set<() => void>()

function set(patch: Partial<Investigation>) {
  state = { ...state, ...patch }
  listeners.forEach((l) => l())
}

/** Current state outside React (tests, event handlers). */
export function getState(): Investigation {
  return state
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

// The model chosen on the start screen; kept across questions (follow-ups use it too). Undefined = server default.
let provider: Provider | undefined
export const getProvider = () => provider
export function setProvider(p: Provider) {
  provider = p
}

/** A pair's backend label ("HSE134-ASP118") from residue keys. */
function pairLabel(keyA: string, keyB: string): string {
  const [a, b] = [parseResidueKey(keyA), parseResidueKey(keyB)]
  return `${a.resname}${a.resid}-${b.resname}${b.resid}`
}

/** A question; a follow-up also sends the selected pair, and the backend adds this session's last run. */
export async function ask(question: string, dataset: string, followUp = false) {
  closeStream?.()
  retry = () => ask(question, dataset, followUp)
  const { pair } = getSelection()
  const selectedPair = followUp && pair ? pairLabel(pair.keyA, pair.keyB) : null
  set({ ...empty, phase: 'planning', question, dataset, science: followUp ? state.science : false })
  try {
    const proposal = await api.plan(question, dataset, { followUp, selectedPair, provider })
    if (proposal.declined) set({ phase: 'idle', declined: { ...proposal.declined, question } })
    else set({ phase: 'plan', proposal })
  } catch (e) {
    if (e instanceof PasscodeRequired) set({ phase: 'idle', needPasscode: true })
    else if (e instanceof ReplayOnly) set({ phase: 'idle', error: e.message })
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
export async function askFigure(files: File[], dataset: string, note: string, label?: string) {
  closeStream?.()
  retry = () => askFigure(files, dataset, note, label)
  label = label || note.trim() || `Reproduce ${files.map((f) => f.name).join(' + ')}`
  set({ ...empty, phase: 'planning', question: label, dataset })
  try {
    const uploads = await Promise.all(files.map(toUpload))
    const proposal = await api.figurePlan(uploads, dataset, note, provider)
    set({ phase: 'plan', proposal })
  } catch (e) {
    if (e instanceof PasscodeRequired) set({ phase: 'idle', needPasscode: true })
    else if (e instanceof UnsupportedFigure) set({ phase: 'idle', unsupported: { message: e.message, figure: e.figure } })
    else if (e instanceof ReplayOnly) set({ phase: 'idle', error: e.message })
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
export async function approve(edits: Partial<Plan>, stepThrough: boolean, plantOverclaim = false) {
  const proposal = state.proposal
  if (!proposal) return
  const p = { ...proposal.plan, ...edits }
  try {
    const { run_id } = await api.startRun(proposal.plan_id, edits, stepThrough, state.confirmed, plantOverclaim)
    setSelection({
      dataset: p.dataset, groupA: p.group_a, groupB: p.group_b, cutoff: p.cutoff,
      windows: p.window_b ? [p.window_a, p.window_b] : null,
    })
    set({ phase: 'running', runId: run_id, events: [], error: null })
    closeStream = api.runEvents(run_id, (e) => {
      set({ events: [...state.events, e], paused: e.type === 'paused' })
      if (['turn', 'run_finished', 'run_started', 'verification_started', 'verification_finished'].includes(e.type)) void refresh(run_id)
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

/** Show or hide all technical detail at once. */
export function toggleScience() {
  set({ science: !state.science })
}

/** Show the passcode prompt without re-sending a question afterwards (voice input asks this way). */
export function askForPasscode() {
  retry = null
  set({ needPasscode: true })
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

/** Start from a clean slate: no previous question, result, figure or error (page load and New question). */
export function reset() {
  closeStream?.()
  set(empty)
}

/** Drop start-screen notices from an earlier attempt (unsupported figure, planning error) once the user acts. */
export function clearNotices() {
  if (state.unsupported || state.error || state.declined) set({ unsupported: null, error: null, declined: null })
}
