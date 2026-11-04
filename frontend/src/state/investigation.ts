// The current investigation: question -> proposed plan -> approved run with its live events.
// Like the selection store, one module-level store read with useSyncExternalStore.
import { useSyncExternalStore } from 'react'
import { api, getPasscode, PasscodeRequired, PlanExpired, ReplayOnly, setPasscode, UnsupportedFigure, type Declined, type Figure, type Plan, type Provider, type UploadFile, type PlanResponse, type RunEvent, type RunView } from '../api/client'
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
  kicker: string // the small label over the heading: "You asked", "Sample figure", "Your figure", "Recorded run"
  figureRead: Figure | null // what the AI read from the figure, shown while the plan is drafted
  unsupported: { message: string; figure: Figure } | null // figure read, but not a type we can run yet
  science: boolean // "Show the science": technical detail (statistics, units, residue codes, charts, logs) is visible
  declined: (Declined & { question: string; followUp: boolean }) | null // turned away before planning, or a residue to pick
  notice: string | null // a status line while planning, e.g. "This plan expired. Re-planning…"
  edits: Partial<Plan> // the user's changes to the proposed plan (figure rows and Settings); Run sends them
  rows: Record<string, 'confirmed' | 'edited'> // figure-setting rows the user confirmed ("Looks right") or edited
  editSeq: number // bumped by a row edit, so the Settings fold re-reads the edited values
}

const empty: Investigation = {
  phase: 'idle', question: '', dataset: '', proposal: null, runId: null, events: [], run: null, paused: false, error: null, needPasscode: false,
  kicker: 'You asked', figureRead: null, unsupported: null, science: false, declined: null, notice: null,
  edits: {}, rows: {}, editSeq: 0,
}

/** A figure-setting row's edit: merged into the plan Run sends (the backend validates it again). */
export function editPlan(patch: Partial<Plan>, row?: string) {
  set({
    edits: { ...state.edits, ...patch },
    ...(row ? { rows: { ...state.rows, [row]: 'edited' as const }, editSeq: state.editSeq + 1 } : {}),
    error: null,
  })
}

/** "Looks right" on an inferred row. */
export function confirmRow(row: string) {
  if (state.rows[row] !== 'edited') set({ rows: { ...state.rows, [row]: 'confirmed' } })
}
const EXPIRED_NOTICE = 'This plan expired. Re-planning…'
let replanning = false // set while a plan the server lost is planned again
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

/** True when live AI work needs a passcode this tab has not entered yet (then ask first, before planning). */
async function passcodeMissing(): Promise<boolean> {
  if (getPasscode()) return false
  try {
    return !!(await api.health()).passcode_required
  } catch {
    return false // let the request itself report the problem
  }
}

/** A question; a follow-up also sends the selected pair, and the backend adds this session's last run.
 *  skipScope: "Ask anyway" after the scope check turned the question away. */
export async function ask(question: string, dataset: string, followUp = false, skipScope = false, kicker = 'You asked') {
  closeStream?.()
  retry = () => ask(question, dataset, followUp, skipScope, kicker)
  if (await passcodeMissing()) {
    set({ ...empty, phase: 'idle', question, dataset, kicker, needPasscode: true })
    return
  }
  const { pair } = getSelection()
  const selectedPair = followUp && pair ? pairLabel(pair.keyA, pair.keyB) : null
  set({ ...empty, phase: 'planning', question, dataset, kicker, science: followUp ? state.science : false,
    notice: replanning ? EXPIRED_NOTICE : null })
  try {
    const proposal = await api.plan(question, dataset, { followUp, selectedPair, provider, skipScope })
    if (proposal.declined) set({ phase: 'idle', notice: null, declined: { ...proposal.declined, question, followUp } })
    else set({ phase: 'plan', proposal, notice: null })
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

/** Figure mode: figure image and/or methods PDF -> what the AI read (shown at once) -> plan.
 *  label: the heading (a sample's title, else the note or file name); never put in the user's mouth as "You asked". */
export async function askFigure(files: File[], dataset: string, note: string, opts: { label?: string; kicker?: string } = {}) {
  closeStream?.()
  retry = () => askFigure(files, dataset, note, opts)
  const question = opts.label || note.trim() || files.map((f) => f.name).join(' + ')
  const kicker = opts.kicker ?? 'Your figure'
  if (await passcodeMissing()) {
    set({ ...empty, phase: 'idle', question, dataset, kicker, needPasscode: true })
    return
  }
  set({ ...empty, phase: 'planning', question, dataset, kicker, notice: replanning ? EXPIRED_NOTICE : null })
  try {
    const uploads = await Promise.all(files.map(toUpload))
    const read = await api.figureRead(uploads, note, provider)
    if (read.proposal) {
      set({ phase: 'plan', proposal: read.proposal, notice: null }) // replay mode: the recorded run's plan
      return
    }
    set({ figureRead: read.figure ?? null })
    const proposal = await api.figureReadPlan(read.read_id!, dataset)
    set({ phase: 'plan', proposal, notice: null })
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
  edits = { ...state.edits, ...edits } // figure-row edits, then the Settings fold (which starts from them)
  const p = { ...proposal.plan, ...edits }
  try {
    // Figure plans: "Confirm & run" is the user's one review of every setting read from the figure, including
    // the ones the AI was unsure of; the backend refuses the run unless each of those is confirmed.
    const confirmed = (proposal.figure?.spec.fields ?? []).filter((f) => f.confidence === 'low').map((f) => f.name)
    const { run_id } = await api.startRun(proposal.plan_id, edits, stepThrough, confirmed, plantOverclaim)
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
    if (e instanceof PlanExpired) {
      // The server lost the plan (it should not, now that plans are saved; this is the fallback). Plan the same
      // question or figure again; the user reviews the new plan and presses Run, as before.
      if (!retry) return set({ error: 'This plan expired. Ask the question again.' })
      replanning = true
      try {
        await retry()
      } finally {
        replanning = false
      }
      return
    }
    set(e instanceof PasscodeRequired ? { needPasscode: true, error: 'Demo passcode required.' } : { error: `Could not start: ${(e as Error).message}` })
  }
}

/** Store the passcode for this tab, check it, and retry the question that needed it. */
export async function submitPasscode(code: string): Promise<boolean> {
  setPasscode(code.trim())
  try {
    await api.checkPasscode()
  } catch (e) {
    // After 10 wrong passcodes in an hour the server says how long to wait (red-team F2).
    const msg = (e as Error).message
    set({ needPasscode: true, error: /wrong passcodes/.test(msg) ? msg : 'That passcode was not accepted.' })
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

/** Start from a clean slate: no previous question, result, figure or error (page load and New question). */
export function reset() {
  closeStream?.()
  set(empty)
}

/** Drop start-screen notices from an earlier attempt (unsupported figure, planning error) once the user acts. */
export function clearNotices() {
  if (state.unsupported || state.error || state.declined) set({ unsupported: null, error: null, declined: null })
}
