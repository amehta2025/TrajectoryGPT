// All backend calls live here. The UI never calls an LLM or any other service directly.
const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export type Units = { distance: string; time: string; dt_ns: number | null }

export type Metadata = {
  dataset: string
  n_atoms: number
  n_viewer_atoms: number
  n_residues: number
  n_frames: number
  dt_ns: number | null
  time_label: string
  stats_valid: boolean
  playback_stride: number
  pbc: string
  groups: Record<string, { ranges: [number, number][]; selection: string }>
  groups_source: string
  sequence_exclusion_default: number
  contact_definition: string
  license: string
  citation: string
}

export type DatasetInfo = { id: string; description: string; stats_valid: boolean; time_label: string }

export type Contacts = {
  dataset: string
  group_a: string
  group_b: string
  keys_a: string[]
  keys_b: string[]
  frequency: number[][]
  excluded: boolean[][]
  sequence_exclusion: number
  n_frames: number
  cutoff: number
  contact_definition: string
  units: Units
}

export type Distances = {
  dataset: string
  key_a: string
  key_b: string
  frames: number[]
  time_ns: number[] | null
  distance: number[]
  units: Units
}

export type CompareRow = {
  key_a: string
  key_b: string
  freq_a: number
  freq_b: number
  delta: number
  ci_low: number
  ci_high: number
  p: number
  q: number
  supported: boolean
  too_few_events: boolean
  too_few_blocks: boolean
  provisional: boolean
  boot_ci_low: number
  boot_ci_high: number
  transitions: number
  tau_a: number | null
  tau_b: number | null
  tau_ns_a: number | null
  tau_ns_b: number | null
  n_eff_a: number | null
  n_eff_b: number | null
  n_blocks_a: number | null
  n_blocks_b: number | null
}

export type Compare =
  | { status: 'refused'; reason: string }
  | {
      status: 'ok'
      dataset: string
      group_a: string
      group_b: string
      window_a: [number, number]
      window_b: [number, number]
      cutoff: number
      rows: CompareRow[]
      n_pairs: number
      n_tested: number
      n_filtered: number
      n_supported: number
      n_supported_firm: number
      n_supported_provisional: number
      n_provisional: number
      min_blocks_firm: number
      min_pooled_freq: number
      fdr: number
      ci_level: number
      units: Units
    }

export type Frames = { frames: number[]; nAtoms: number; coords: Float32Array }

// ---------- agent (SPEC §9) ----------

export type PlanStep = { tool: string; rationale: string }

export type Plan = {
  summary: string
  dataset: string
  group_a: string
  group_b: string
  cutoff: number
  window_a: [number, number]
  window_b: [number, number] | null
  stride: number
  sequence_exclusion: number
  robustness_cutoffs: number[]
  steps: PlanStep[]
}

export type Usage = {
  provider: string
  model: string
  effort: string
  input_tokens: number
  cached_tokens: number
  output_tokens: number
  reasoning_tokens: number
  latency_s: number
  cost_usd: number | null
}

export type SpecField = { name: string; value: string; confidence: 'high' | 'medium' | 'low'; source: 'caption' | 'methods' | 'axis' | 'assumed' }

export type FigureSpec = {
  summary: string
  kind: 'native_contacts' | 'contact_map' | 'distance_trace' | 'other'
  mode: 'exact' | 'transfer'
  dataset: string
  mode_reason: string
  fields: SpecField[]
  question: string
  pdf_fallback: boolean
}

export type Figure = { figure_id: string; filename: string; mime: string; spec: FigureSpec }

/** A file for figure mode: base64 content plus its type (PNG, JPEG or PDF). */
export type UploadFile = { filename: string; mime: string; data: string }

export type PlanResponse = { plan_id: string; plan: Plan; usage: Usage[]; model: string; figure?: Figure; question?: string }

export type ChecklistItem = { item: number; tool: string; text: string; status: 'pending' | 'in_progress' | 'done' | 'skipped'; note: string }

/** A filled placeholder value (has pid) or plain text. */
export type Segment = { text: string; pid?: string; path?: string; kind?: string }

export type Robustness = { pair: string; verdict: 'robust' | 'cutoff-sensitive' | 'not checked'; pid: string | null; label: string }

export type Finding = {
  id: string
  text: string
  claim: 'supported' | 'not_supported' | 'descriptive'
  evidence_ids: string[]
  rendered: string
  segments: Segment[]
  badge: 'supported' | 'proven' | 'unsupported' | 'null' | 'descriptive' // 'proven' = runs saved before 2026-10-30
  badge_label: string
  pairs: string[]
  robustness: Robustness[]
}

export type StatRow = {
  finding: string
  pair: string
  pid: string
  delta: string
  ci: string
  q: string
  n_blocks_a: string
  n_blocks_b: string
  tau_ns_a: string
  tau_ns_b: string
  supported: boolean
  provisional: boolean
  robustness: string
}

export type TextItem = { text: string; segments: Segment[] }

export type Report = {
  findings: Finding[]
  statistical_support: StatRow[]
  background: TextItem[]
  background_label: string
  hypotheses: TextItem[]
  caveats: TextItem[]
  gaps: TextItem[]
  follow_ups: { tool: string; pair?: string; rationale: string }[]
}

export type ResultPair = { key_a: string; key_b: string; supported?: boolean; provisional?: boolean; verdict?: string }

export type ToolResult = {
  status: 'ok' | 'refused' | 'error'
  tool: string
  provenance_id: string
  params: Record<string, unknown>
  units: Record<string, string>
  summary_for_model: string
  data_for_ui?: unknown
  pairs: Record<string, ResultPair>
}

export type Turn = {
  turn: number
  tool: string | null
  arguments?: string
  status: string
  provenance_id?: string | null
  summary: string
  saw: Record<string, unknown>[]
  output: Record<string, unknown>[]
  usage: Usage
  model_s: number
  tool_s: number
}

export type RunStatus = 'running' | 'needs_review' | 'awaiting_verification' | 'aborted_budget' | 'error'

export type RunView = {
  run_id: string
  question: string
  status: RunStatus
  checklist: ChecklistItem[]
  turns: Turn[]
  plan_usage: Usage[]
  results: Record<string, ToolResult>
  report: Report | null
  problems: string[]
  wall_s: number
  cost_usd: number | null
  error: string | null
  step_through: boolean
  figure: Figure | null
}

export type RunEvent =
  | { seq: number; type: 'run_started'; run_id: string; checklist: ChecklistItem[] }
  | { seq: number; type: 'turn_started'; turn: number }
  | { seq: number; type: 'turn'; turn: number; tool: string | null; status: string; model_s: number; tool_s: number; cost_usd: number | null; effort: string; provenance_id: string | null; checklist: ChecklistItem[]; progress: string }
  | { seq: number; type: 'paused' }
  | { seq: number; type: 'run_finished'; status: RunStatus; problems: string[]; cost_usd: number | null; wall_s: number; error: string | null }

// Demo passcode for the AI endpoints (SPEC §12): asked once, kept for this browser tab only.
const PASSCODE_KEY = 'trajectorygpt.passcode'

export function getPasscode(): string {
  try {
    return sessionStorage.getItem(PASSCODE_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setPasscode(code: string) {
  try {
    sessionStorage.setItem(PASSCODE_KEY, code)
  } catch {
    /* storage blocked: the code is asked again next time */
  }
}

/** Thrown on HTTP 401 so the UI can ask for the passcode and retry. */
export class PasscodeRequired extends Error {}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(new URL(path, BASE), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Demo-Passcode': getPasscode() },
    body: JSON.stringify(body),
  })
  if (r.status === 401) throw new PasscodeRequired('Demo passcode required')
  if (!r.ok) {
    let detail: unknown = r.statusText
    try {
      detail = (await r.json()).detail
    } catch {
      /* not JSON */
    }
    const errors = (detail as { errors?: string[] } | null)?.errors
    throw new Error(errors ? errors.join('; ') : `${r.status} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`)
  }
  return r.json() as Promise<T>
}

async function get(path: string, params: Record<string, string | number | undefined> = {}): Promise<Response> {
  const url = new URL(path, BASE)
  for (const [k, v] of Object.entries(params)) if (v !== undefined) url.searchParams.set(k, String(v))
  const r = await fetch(url)
  if (!r.ok) {
    let detail = r.statusText
    try {
      detail = JSON.stringify((await r.json()).detail)
    } catch {
      /* not JSON */
    }
    throw new Error(`${r.status} ${detail}`)
  }
  return r
}

export const api = {
  datasets: async (): Promise<DatasetInfo[]> => (await get('/datasets')).json(),
  metadata: async (ds: string): Promise<Metadata> => (await get(`/metadata/${ds}`)).json(),
  contacts: async (ds: string, groupA: string, groupB: string, cutoff = 4.5): Promise<Contacts> =>
    (await get(`/contacts/${ds}`, { group_a: groupA, group_b: groupB, cutoff })).json(),
  distances: async (ds: string, keyA: string, keyB: string): Promise<Distances> =>
    (await get(`/distances/${ds}`, { key_a: keyA, key_b: keyB })).json(),
  compare: async (ds: string, groupA: string, groupB: string, a: [number, number], b: [number, number], cutoff = 4.5): Promise<Compare> =>
    (await get(`/compare/${ds}`, { group_a: groupA, group_b: groupB, a_start: a[0], a_stop: a[1], b_start: b[0], b_stop: b[1], cutoff })).json(),
  checkPasscode: () => post<{ ok: boolean }>('/auth/check', {}),
  figurePlan: (files: UploadFile[], dataset: string, note: string) =>
    post<PlanResponse>('/figure/plan', { files, dataset, note }),
  figureUrl: (figureId: string) => new URL(`/figures/${figureId}`, BASE).toString(),
  plan: (question: string, dataset: string) => post<PlanResponse>('/plan', { question, dataset }),
  startRun: (planId: string, edits: Partial<Plan>, stepThrough: boolean) =>
    post<{ run_id: string }>('/runs', { plan_id: planId, edits, step_through: stepThrough }),
  run: async (runId: string): Promise<RunView> => (await get(`/runs/${runId}`)).json(),
  next: (runId: string, stepThrough?: boolean) => post<{ ok: boolean }>(`/runs/${runId}/next`, { step_through: stepThrough ?? null }),
  /** Live step log (server-sent events). Returns a function that closes the stream. */
  runEvents: (runId: string, onEvent: (e: RunEvent) => void): (() => void) => {
    const es = new EventSource(new URL(`/runs/${runId}/events`, BASE))
    es.onmessage = (m) => {
      const e = JSON.parse(m.data) as RunEvent
      onEvent(e)
      if (e.type === 'run_finished') es.close()
    }
    return () => es.close()
  },
  structure: async (ds: string): Promise<string> => (await get(`/structure/${ds}`)).text(),
  frames: async (ds: string, start: number, stop: number, stride: number): Promise<Frames> => {
    const r = await get(`/frames/${ds}`, { start, stop, stride })
    const frames = (r.headers.get('X-Frames') ?? '').split(',').filter(Boolean).map(Number)
    const nAtoms = Number(r.headers.get('X-N-Atoms'))
    return { frames, nAtoms, coords: new Float32Array(await r.arrayBuffer()) }
  },
}
