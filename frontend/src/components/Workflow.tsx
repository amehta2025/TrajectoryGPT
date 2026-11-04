// The investigation as three stages: Interpret figure (or Plan analysis) → Run analysis → Results.
// Each stage is one screen built from the existing pieces (plan card, our figure, report cards, charts, 3D
// viewer), so nothing the backend computes is lost; this file only arranges them. Every number shown
// comes from stored tool results (stepResult, the report's filled placeholders), never from model text.
import { useEffect, useState } from 'react'
import { api, type Figure, type Metadata, type Plan } from '../api/client'
import { setSelection } from '../state/selection'
import { datasetName } from './DatasetBadge'
import { confirmRow, editPlan, nextTurn, toggleScience, useInvestigation } from '../state/investigation'
import { withUnits } from '../state/paperFigure'
import { isHalves, PlanCard, STEP_DETAIL, stepText, windowText } from './PlanCard'
import { AgentTrace, stepResult } from './StepLog'
import { Ours, PublishedFigure, ReproduceView } from './ReproduceView'
import { ReportView, ScienceReport } from './ReportView'
import { CompareChart } from './CompareChart'
import { DistancePlot } from './DistancePlot'
import { Heatmap } from './Heatmap'
import { AnalysisCards } from './AnalysisViews'
import { Viewer } from './Viewer'
import { VoiceHint } from './VoiceInput'
import { DOMAIN_COLORS } from '../theme'

/** 1 Interpret figure (or Plan analysis) — 2 Run analysis — 3 Results. */
export function Stepper() {
  const { phase, proposal, figureRead } = useInvestigation()
  const figure = !!(proposal?.figure || figureRead)
  const stage = phase === 'finished' ? 2 : phase === 'running' ? 1 : 0
  const names = [figure ? 'Interpret figure' : 'Plan analysis', 'Run analysis', 'Results']
  return (
    <ol className="stepper" aria-label="Progress">
      {names.map((n, i) => (
        <li key={n} className={i < stage ? 'done' : i === stage ? 'current' : ''} aria-current={i === stage ? 'step' : undefined}>
          <span className="sp-dot">{i < stage ? '✓' : i + 1}</span>{n}
        </li>
      ))}
    </ol>
  )
}

const GOAL: Record<string, string> = {
  native_contacts: 'Track how often key contacts stay close as the protein changes shape',
  contact_map: 'Map which parts of the protein touch',
  difference_contact_map: 'Map which contacts are gained or lost',
  distance_trace: 'Track the distance between two parts of the protein',
  rmsf: 'Measure how much each part of the protein moves',
}

type SpecField = Figure['spec']['fields'][number]
// paper: the value came from the paper (its methods, caption or axes), shown as "from the paper"; false: a default.
type Row = { id: string; label: string; value: string; raw?: string; spec?: SpecField; paper?: boolean; editor?: (close: () => void) => React.ReactNode }

const DEFAULT_CUTOFF = 4.5 // the planner's default contact cutoff (prompts.PLANNER_SYSTEM)
const ATOM_LABEL: Record<string, string> = { 'name CA': 'Cα atoms (backbone)', 'heavy atoms': 'heavy atoms' }
const REF_OPTIONS: [string, number[], string][] = [['first', [0], 'the first frame'], ['last', [-1], 'the last frame'],
  ['both', [0, -1], 'the first and the last frame']]
const refText = (refs: number[]) => REF_OPTIONS.find(([, v]) => v.join() === refs.join())?.[2] ?? `frames ${refs.join(', ')}`

/** A number input with bounds and a unit; Save keeps it only when it is inside the bounds. */
function NumberEditor({ value, min, max, step, unit, onSave, close }: {
  value: number; min: number; max: number; step: number; unit: string; onSave: (v: number) => void; close: () => void
}) {
  const [v, setV] = useState(String(value))
  const n = Number(v)
  const ok = v.trim() !== '' && Number.isFinite(n) && n >= min && n <= max
  return (
    <form className="u-edit" onSubmit={(e) => { e.preventDefault(); if (ok) { onSave(n); close() } }}>
      <input type="number" value={v} min={min} max={max} step={step} autoFocus onChange={(e) => setV(e.target.value)}
        aria-label={`Value in ${unit}`} /> <span>{unit}</span>
      <button type="submit" className="small primary" disabled={!ok}>Save</button>
      <button type="button" className="small link" onClick={close}>Cancel</button>
      {!ok && <span className="u-hint">{min}–{max} {unit}</span>}
    </form>
  )
}

function SelectEditor({ value, options, onSave, close }: { value: string; options: [string, string][]; onSave: (v: string) => void; close: () => void }) {
  const [v, setV] = useState(value)
  return (
    <form className="u-edit" onSubmit={(e) => { e.preventDefault(); onSave(v); close() }}>
      <select value={v} autoFocus onChange={(e) => setV(e.target.value)}>{options.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      <button type="submit" className="small primary">Save</button>
      <button type="button" className="small link" onClick={close}>Cancel</button>
    </form>
  )
}

/** Two domains and, for each, an optional residue range (only those residues are tested; blank = the whole domain). */
function GroupEditor({ groups, plan, onSave, close }: { groups: string[]; plan: Plan; onSave: (p: Partial<Plan>) => void; close: () => void }) {
  const [a, setA] = useState(plan.group_a)
  const [b, setB] = useState(plan.group_b)
  const r0 = plan.residue_ranges ?? {}
  const [ra, setRa] = useState((r0[plan.group_a] ?? ['', '']).map(String))
  const [rb, setRb] = useState((r0[plan.group_b] ?? ['', '']).map(String))
  const range = (r: string[]): [number, number] | null | 'bad' => {
    if (!r[0].trim() && !r[1].trim()) return null
    const [lo, hi] = r.map(Number)
    return Number.isInteger(lo) && Number.isInteger(hi) && lo <= hi ? [lo, hi] : 'bad'
  }
  const [ga, gb] = [range(ra), range(rb)]
  const ok = a !== b && ga !== 'bad' && gb !== 'bad'
  const box = (r: string[], set: (x: string[]) => void, i: number) => (
    <input type="number" step={1} value={r[i]} placeholder={i ? 'last' : 'first'} aria-label={i ? 'Last residue' : 'First residue'}
      onChange={(e) => set(r.map((x, j) => (j === i ? e.target.value : x)))} />
  )
  return (
    <form className="u-edit" onSubmit={(e) => {
      e.preventDefault()
      if (!ok) return
      const ranges: Record<string, [number, number]> = {}
      if (Array.isArray(ga)) ranges[a] = ga
      if (Array.isArray(gb)) ranges[b] = gb
      onSave({ group_a: a, group_b: b, residue_ranges: Object.keys(ranges).length ? ranges : null })
      close()
    }}>
      <select value={a} onChange={(e) => setA(e.target.value)} autoFocus aria-label="First domain">{groups.map((g) => <option key={g}>{g}</option>)}</select>
      {box(ra, setRa, 0)}–{box(ra, setRa, 1)}
      <span>vs</span>
      <select value={b} onChange={(e) => setB(e.target.value)} aria-label="Second domain">{groups.map((g) => <option key={g}>{g}</option>)}</select>
      {box(rb, setRb, 0)}–{box(rb, setRb, 1)}
      <span className="u-hint">Leave a range blank for the whole domain.</span>
      <button type="submit" className="small primary" disabled={!ok}>Save</button>
      <button type="button" className="small link" onClick={close}>Cancel</button>
    </form>
  )
}

/** Domain of an AdK residue, for the picker's labels. */
function domainOf(meta: Metadata, resid: number): string {
  return Object.entries(meta.groups).find(([, g]) => g.ranges.some(([a, b]) => resid >= a && resid <= b))?.[0] ?? ''
}

/** Two AdK residues for a figure whose residues come from another protein. Saving puts the pair in the plan. */
function ResiduePicker({ meta, value, onSave }: { meta: Metadata; value?: string; onSave: (pair: string) => void }) {
  const res = meta.residues ?? []
  const label = ([id, name]: [number, string]) => `${name}${id}`
  const [initA, initB] = (value ?? '').split('-')
  const [a, setA] = useState(initA ?? '')
  const [b, setB] = useState(initB ?? '')
  const options = res.map((r) => <option key={r[0]} value={label(r)}>{label(r)} · {domainOf(meta, r[0])}</option>)
  return (
    <form className="u-edit picker" onSubmit={(e) => { e.preventDefault(); if (a && b && a !== b) onSave(`${a}-${b}`) }}>
      <select value={a} onChange={(e) => setA(e.target.value)} aria-label="First AdK residue"><option value="">First residue…</option>{options}</select>
      <span>–</span>
      <select value={b} onChange={(e) => setB(e.target.value)} aria-label="Second AdK residue"><option value="">Second residue…</option>{options}</select>
      <button type="submit" className="small primary" disabled={!a || !b || a === b}>Use this pair</button>
      {a && a === b && <span className="u-hint">Pick two different residues</span>}
    </form>
  )
}

/** Which simulation to run on. Switching scales the frame windows to the other run's length (the whole run stays
 *  the whole run, the last half stays the last half); the backend validates the result again at Run. */
function DataEditor({ plan, original, meta, onSave, close }: {
  plan: Plan; original: Plan; meta: Metadata; onSave: (p: Partial<Plan>) => void; close: () => void
}) {
  const [ids, setIds] = useState<string[]>([plan.dataset])
  const [ds, setDs] = useState(plan.dataset)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.datasets().then((d) => setIds(d.map((x) => x.id)), () => {}) }, [])
  return (
    <form className="u-edit" onSubmit={async (e) => {
      e.preventDefault()
      if (ds === plan.dataset) return close()
      setBusy(true)
      try {
        const n = (await api.metadata(ds)).n_frames
        const scale = (w: [number, number]): [number, number] => {
          const a = Math.min(n - 1, Math.round((w[0] * n) / meta.n_frames))
          return [a, Math.max(a + 1, Math.min(n, Math.round((w[1] * n) / meta.n_frames)))]
        }
        // Back on the proposed dataset: its own windows exactly, not a scaled round trip.
        onSave(ds === original.dataset
          ? { dataset: ds, window_a: original.window_a, window_b: original.window_b }
          : { dataset: ds, window_a: scale(plan.window_a), ...(plan.window_b ? { window_b: scale(plan.window_b) } : {}) })
        setSelection({ dataset: ds })
        close()
      } finally {
        setBusy(false)
      }
    }}>
      <select value={ds} autoFocus onChange={(e) => setDs(e.target.value)} aria-label="Simulation">
        {ids.map((id) => <option key={id} value={id}>{datasetName(id)}</option>)}
      </select>
      <button type="submit" className="small primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
      <button type="button" className="small link" onClick={close}>Cancel</button>
    </form>
  )
}

/** Frame windows: [start, stop) within the run, shown with their time. */
function WindowEditor({ plan, meta, onSave, close }: { plan: Plan; meta: Metadata; onSave: (p: Partial<Plan>) => void; close: () => void }) {
  const [w, setW] = useState([...plan.window_a, ...(plan.window_b ?? [])].map(String))
  const nums = w.map(Number)
  const n = meta.n_frames
  const valid = (a: number, b: number) => Number.isInteger(a) && Number.isInteger(b) && a >= 0 && a < b && b <= n
  const ok = valid(nums[0], nums[1]) && (!plan.window_b || (valid(nums[2], nums[3]) && (nums[1] <= nums[2] || nums[3] <= nums[0])))
  const t = (f: number) => (meta.dt_ns === null ? `step ${f}` : `${Math.round(f * meta.dt_ns)} ns`)
  const box = (i: number) => (
    <input type="number" min={0} max={n} step={1} value={w[i]} aria-label="Frame" onChange={(e) => setW(w.map((x, j) => (j === i ? e.target.value : x)))} />
  )
  return (
    <form className="u-edit" onSubmit={(e) => {
      e.preventDefault()
      if (!ok) return
      onSave({ window_a: [nums[0], nums[1]], ...(plan.window_b ? { window_b: [nums[2], nums[3]] as [number, number] } : {}) })
      close()
    }}>
      <span>{plan.window_b ? 'A: frames' : 'Frames'}</span> {box(0)}–{box(1)}
      {plan.window_b && <><span>B:</span> {box(2)}–{box(3)}</>}
      <button type="submit" className="small primary" disabled={!ok}>Save</button>
      <button type="button" className="small link" onClick={close}>Cancel</button>
      <span className="u-hint">{ok ? `${t(nums[0])}–${t(nums[1])}${plan.window_b ? ` vs ${t(nums[2])}–${t(nums[3])}` : ''}` : `0–${n}, start before stop, no overlap`}</span>
    </form>
  )
}

/** What the agent understood, in plain words. Once the plan is drafted, each setting the backend runs with can be
 *  edited in place (Run sends the edits; the backend validates them again), and an inferred one confirmed. */
function Understood({ figure, meta }: { figure: Figure; meta: Metadata | null }) {
  const { proposal, phase, edits, rows: rowState } = useInvestigation()
  const [open, setOpen] = useState<string | null>(null)
  const field = (name: string) => figure.spec.fields.find((f) => f.name === name)
  const fieldLike = (re: RegExp) => figure.spec.fields.find((f) => re.test(f.name) && !/reference/.test(f.name))
  const kind = figure.spec.kind
  const plan: Plan | null = proposal ? { ...proposal.plan, ...edits } : null
  const editable = !!plan && !!meta && phase === 'plan' && !proposal?.recorded
  const native = kind === 'native_contacts'
  const usesCutoff = ['contact_map', 'difference_contact_map', 'distance_trace'].includes(kind)
  const atoms = field('atom selection')
  const save = (row: string) => (patch: Partial<Plan>) => editPlan(patch, row)

  const rows: Row[] = [
    { id: 'goal', label: 'Goal', value: GOAL[kind] ?? figure.spec.summary, raw: field('analysis')?.value },
    { id: 'protein', label: 'Protein', value: withUnits(field('protein')?.value ?? '—'), spec: field('protein') },
  ]
  if (plan && (kind === 'contact_map' || kind === 'difference_contact_map')) {
    const range = (g: string) => {
      const r = plan.residue_ranges?.[g]
      return r ? ` ${r[0]}–${r[1]}` : ' (whole domain)'
    }
    const groups = meta ? Object.keys(meta.groups) : [plan.group_a, plan.group_b]
    rows.push({ id: 'groups', label: 'Domains compared', value: `${plan.group_a}${range(plan.group_a)} vs ${plan.group_b}${range(plan.group_b)}`,
      raw: 'Only these residues are counted and tested, so the statistics cover the pairs the paper showed', spec: fieldLike(/x axis|y axis/),
      paper: !!proposal?.plan.residue_ranges,
      editor: (close) => <GroupEditor groups={groups} plan={plan} onSave={save('groups')} close={close} /> })
  }
  if (plan && meta) {
    const fromPaper = figure.spec.mode === 'exact' || figure.spec.closest_dataset === proposal!.plan.dataset
    rows.push({ id: 'data', label: 'Data', value: datasetName(plan.dataset), spec: field('data source'), paper: fromPaper || undefined,
      raw: field('data source')?.value,
      editor: figure.spec.mode === 'exact' ? undefined : (close) => <DataEditor plan={plan} original={proposal!.plan} meta={meta} onSave={save('data')} close={close} /> })
  }
  if ((figure.spec.foreign_residues ?? []).length && plan && meta) {
    rows.push({ id: 'pair', label: 'AdK residues', value: plan.pair ? plan.pair.replace('-', '–') : 'not chosen yet',
      raw: `The paper measured ${(figure.spec.foreign_residues ?? []).join('–')} in another protein`,
      editor: plan.pair ? (close) => <ResiduePicker meta={meta} value={plan.pair} onSave={(p) => { save('pair')({ pair: p }); close() }} /> : undefined })
  }
  if (native && plan) {
    const sel = plan.native_selection ?? 'name CA'
    rows.push({ id: 'atoms', label: 'Atoms', value: ATOM_LABEL[sel] ?? sel, raw: atoms?.value, spec: atoms,
      editor: (close) => <SelectEditor value={sel} options={Object.entries(ATOM_LABEL)} close={close}
        onSave={(v) => save('atoms')({ native_selection: v as Plan['native_selection'] })} /> })
  } else {
    rows.push({ id: 'atoms', label: 'Atoms', spec: atoms, raw: atoms?.value,
      value: kind === 'rmsf' ? 'Cα atoms (fixed)' : plan ? 'heavy atoms, closest pair (fixed)' : withUnits(atoms?.value ?? '—') })
  }
  if (native) {
    const r = plan?.native_radius ?? 8
    rows.push({ id: 'distance', label: 'Contact distance', value: plan ? `${r} Å` : withUnits(field('cutoff or radius')?.value ?? '—'),
      raw: 'Two atoms count as a native contact within this distance', spec: field('cutoff or radius'),
      editor: (close) => <NumberEditor value={r} min={2} max={15} step={0.5} unit="Å" close={close} onSave={(v) => save('distance')({ native_radius: v })} /> })
  } else if (usesCutoff) {
    rows.push({ id: 'distance', label: 'Contact distance', value: plan ? `${plan.cutoff} Å` : withUnits(field('cutoff or radius')?.value ?? '—'),
      raw: 'Two residues are in contact when their closest heavy atoms are within this distance', spec: field('cutoff or radius'),
      paper: !!proposal && proposal.plan.cutoff !== DEFAULT_CUTOFF,
      editor: (close) => <NumberEditor value={plan!.cutoff} min={2} max={10} step={0.5} unit="Å" close={close} onSave={(v) => save('distance')({ cutoff: v })} /> })
    if (plan && meta) {
      const dflt = meta.sequence_exclusion_default
      rows.push({ id: 'exclusion', label: 'Skip neighbours', value: `pairs within ${plan.sequence_exclusion} residues in sequence`,
        raw: 'Residue pairs this close in sequence touch trivially, so they are not counted', paper: proposal!.plan.sequence_exclusion !== dflt,
        editor: (close) => <NumberEditor value={plan.sequence_exclusion} min={0} max={10} step={1} unit="residues" close={close}
          onSave={(v) => save('exclusion')({ sequence_exclusion: v })} /> })
    }
  }
  if (plan && meta) {
    const halves = isHalves(plan, meta.n_frames)
    rows.push({ id: 'frames', label: 'Frames', spec: fieldLike(/frame|window|time/),
      value: plan.window_b ? `${windowText(meta, plan.window_a, 'A', halves)} vs ${windowText(meta, plan.window_b, 'B', halves)}`
        : windowText(meta, plan.window_a, 'A', false).replace(/^window A: /, ''),
      editor: (close) => <WindowEditor plan={plan} meta={meta} onSave={save('frames')} close={close} /> })
  }
  if (native) {
    const refs = plan?.ref_frames ?? [0, -1]
    rows.push({ id: 'refs', label: 'Reference states', value: plan ? refText(refs) : withUnits(field('reference frames or structures')?.value ?? '—'),
      spec: field('reference frames or structures'),
      editor: (close) => <SelectEditor value={REF_OPTIONS.find(([, v]) => v.join() === refs.join())?.[0] ?? 'both'} close={close}
        options={REF_OPTIONS.map(([k, , l]) => [k, l] as [string, string])}
        onSave={(k) => save('refs')({ ref_frames: REF_OPTIONS.find(([id]) => id === k)![1] })} /> })
  }

  return (
    <ul className="understood">
      {rows.map((r) => {
        const st = rowState[r.id]
        const inferred = !!r.spec && r.spec.confidence !== 'high' && !st
        const canEdit = editable && !!r.editor
        return (
          <li key={r.id} title={r.raw} className={open === r.id ? 'editing' : ''}>
            <span className={`u-mark ${inferred ? 'inferred' : 'read'}`} aria-label={inferred ? 'inferred' : 'read from the figure'}>{inferred ? '!' : '✓'}</span>
            <span className="u-label">{r.label}</span>
            {open === r.id && r.editor
              ? r.editor(() => setOpen(null))
              : (
                <span className="u-value">
                  {canEdit
                    ? <button type="button" className="u-click" onClick={() => setOpen(r.id)} title="Click to change">{r.value} <span className="u-pen" aria-hidden>✎</span></button>
                    : r.value}
                  {st === 'edited' && <span className="u-tag edited">edited by you</span>}
                  {st === 'confirmed' && <span className="u-tag confirmed">confirmed</span>}
                  {inferred && <span className="u-tag">inferred</span>}
                  {!st && !inferred && r.paper === true && <span className="u-tag paper">from the paper</span>}
                  {inferred && editable && <button type="button" className="small link u-ok" onClick={() => confirmRow(r.id)}>Looks right</button>}
                </span>
              )}
          </li>
        )
      })}
    </ul>
  )
}

// Plain names for plan settings, for the "can no longer match" warning; defaults for plans made without them.
const SETTING_NAME: Partial<Record<keyof Plan, string>> = {
  native_radius: 'contact distance', native_selection: 'atoms', ref_frames: 'reference states', cutoff: 'contact distance',
  window_a: 'frames', window_b: 'frames', group_a: 'domains', group_b: 'domains', stride: 'stride', sequence_exclusion: 'sequence exclusion',
}
const NATIVE_DEFAULT: Partial<Record<keyof Plan, unknown>> = { native_radius: 8, native_selection: 'name CA', ref_frames: [0, -1] }

/** Stage 1 for a figure: their figure beside what the agent understood, one Run button, settings folded away. */
export function FigureSetup({ meta }: { meta: Metadata | null }) {
  const { proposal, figureRead, phase, edits, rows } = useInvestigation()
  const figure = proposal?.figure ?? figureRead
  if (!figure) return null
  const inferred = figure.spec.fields.some((f) => f.confidence !== 'high')
  const foreign = figure.spec.foreign_residues ?? []
  // An exact reproduction only matches with the published settings: say so as soon as one is changed.
  const changed = proposal && figure.spec.mode === 'exact'
    ? [...new Set((Object.keys(edits) as (keyof Plan)[])
        .filter((k) => SETTING_NAME[k] && JSON.stringify(edits[k]) !== JSON.stringify(proposal.plan[k] ?? NATIVE_DEFAULT[k]))
        .map((k) => SETTING_NAME[k]!))]
    : []
  return (
    <div className="setup">
      <section className="card setup-figure">
        <h2>Your figure</h2>
        <PublishedFigure figure={figure} />
        <p className="muted small">{figure.spec.mode === 'exact'
          ? 'Made from the same simulation we have, so the numbers should match exactly.'
          : 'Made from other data: the method is applied to our simulation, and numbers are not expected to match.'}</p>
      </section>
      <section className="card setup-understood">
        <header className="card-head">
          <h2>What I understood from your figure</h2>
        </header>
        {foreign.length > 0 && proposal && meta && !edits.pair && (
          <div className="pick-residues" role="status">
            <p><b>This paper’s residues (e.g. {foreign.slice(0, 2).join('–')}) are from a different protein.</b> Pick the AdK residues to use.</p>
            <ResiduePicker meta={meta} onSave={(p) => editPlan({ pair: p }, 'pair')} />
          </div>
        )}
        <Understood figure={figure} meta={meta} />
        {changed.length > 0 && (
          <p className="inferred-note warn" role="status">⚠ You changed the {changed.join(' and ')} the published figure used, so the result can no longer match it.</p>
        )}
        {inferred && proposal && Object.keys(rows).length === 0 && (
          <p className="inferred-note">Settings marked “inferred” are my best guess. Click a value to change it, or “Looks right” to confirm it.</p>
        )}
        {phase === 'planning' && <p className="muted plan-status">Drafting the analysis plan…</p>}
        {proposal && <PlanCard meta={meta} compact />}
        <details className="fold">
          <summary>Everything read from the figure ({figure.spec.fields.length} settings)</summary>
          <ul className="plain small">{figure.spec.fields.map((f) => <li key={f.name}><b>{f.name}:</b> {withUnits(f.value)} <span className="muted">({f.confidence}, from {f.source})</span></li>)}</ul>
        </details>
      </section>
    </div>
  )
}

type StepState = 'done' | 'running' | 'pending' | 'skipped' | 'failed'
const STATE_ICON: Record<StepState, string> = { done: '✓', running: '', pending: '', skipped: '–', failed: '✕' }

/** Stage 2: the plan's steps with what each one found, the live result, and a progress bar by steps done. */
export function RunningView({ meta }: { meta: Metadata | null }) {
  const { run, proposal, paused } = useInvestigation()
  const plan = proposal?.plan
  const items = run?.checklist ?? plan?.steps.map((s, i) => ({ item: i, tool: s.tool, status: 'pending' as const, text: '', note: '' })) ?? []
  const current = items.find((c) => c.status === 'in_progress') ?? items.find((c) => c.status === 'pending')
  const state = (c: (typeof items)[number]): StepState => {
    const results = run ? Object.values(run.results).filter((r) => r.tool === c.tool) : []
    if (c.status === 'done') return 'done'
    if (c.status === 'skipped') return 'skipped'
    if (results.length && !results.some((r) => r.status === 'ok')) return 'failed'
    return c === current && !paused ? 'running' : 'pending'
  }
  const done = items.filter((c) => c.status === 'done' || c.status === 'skipped').length
  const figure = !!(run?.figure ?? proposal?.figure)
  return (
    <div className="running">
      <h2 className="stage-title">Running analysis…</h2>
      <p className="stage-sub">{figure ? 'Recreating the figure from simulation data.' : 'Working through the approved plan on the simulation data.'}</p>
      <div className="running-grid">
        <section className="card run-steps">
          <ol>
            {items.map((c) => {
              const st = state(c)
              const found = run && st === 'done' ? stepResult(run, c.tool) : null
              return (
                <li key={c.item} className={`rs ${st}`}>
                  <span className="rs-icon" aria-label={st}>{STATE_ICON[st]}</span>
                  <span className="rs-body">
                    <span className="rs-title">{stepText(c.tool, plan ?? null)}
                      {st === 'skipped' && <span className="rs-tag">Skipped</span>}
                      {st === 'failed' && <span className="rs-tag failed">Couldn’t run</span>}</span>
                    <span className="rs-detail">{found ? `Found: ${found}` : STEP_DETAIL[c.tool] ?? ''}</span>
                  </span>
                </li>
              )
            })}
          </ol>
          {paused && <button className="primary violet" onClick={() => void nextTurn()}>Next step →</button>}
        </section>
        <section className="card run-live">
          {figure ? <Ours run={run} /> : meta && run && Object.values(run.results).some((r) => r.tool === 'compare_intervals' && r.status === 'ok')
            ? <CompareChart meta={meta} />
            : <div className="muted placeholder">Results appear here as each step finishes.</div>}
        </section>
      </div>
      <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={items.length} aria-valuenow={done}>
        <span className="progress-label">{current ? `${stepText(current.tool, null)}…` : 'Finishing the report…'}</span>
        <span className="progress-track"><span className="progress-fill" style={{ width: `${items.length ? (100 * done) / items.length : 0}%` }} /></span>
        <span className="progress-count">Step {Math.min(done + 1, items.length)} of {items.length}</span>
      </div>
    </div>
  )
}

function ProteinPanel({ meta, tall = false }: { meta: Metadata; tall?: boolean }) {
  return (
    <section className={`card viewer-card ${tall ? 'tall' : ''}`}>
      <header className="card-head">
        <h2>Protein structure</h2>
        <VoiceHint />
        <div className="legend">
          {Object.entries(meta.groups).map(([g, { ranges }]) => (
            <span key={g} title={ranges.map(([a, b]) => `${a}–${b}`).join(', ')}><i style={{ background: DOMAIN_COLORS[g] }} /> {g}</span>
          ))}
        </div>
      </header>
      <Viewer meta={meta} />
    </section>
  )
}

type Tab = 'figure' | 'interactions' | 'protein' | 'methods'

/** Stage 3 below the result banner: the evidence in four tabs. */
export function ResultTabs({ meta }: { meta: Metadata }) {
  const { run, science } = useInvestigation()
  const figure = !!run?.figure
  const [tab, setTab] = useState<Tab>(figure ? 'figure' : 'interactions')
  useEffect(() => setTab(figure ? 'figure' : 'interactions'), [run?.run_id, figure])
  const tabs: [Tab, string, string][] = [
    ...(figure ? [['figure', '◩', 'Figure comparison'] as [Tab, string, string]] : []),
    ['interactions', '⚯', 'Changing interactions'], ['protein', '⬡', 'Protein structure'], ['methods', '▤', 'Details & methods'],
  ]
  return (
    <div className="result-tabs">
      <div className="tabs" role="tablist">
        {tabs.map(([id, icon, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>
            <span aria-hidden>{icon}</span> {label}
          </button>
        ))}
      </div>
      {tab === 'figure' && <ReproduceView />}
      {tab === 'interactions' && (
        <div className="tab-body">
          <div className="inv-body">
            <ProteinPanel meta={meta} />
            <div className="inv-col"><ReportView meta={meta} /></div>
          </div>
          <button className="science-toggle" onClick={toggleScience} aria-expanded={science}>
            {science ? 'Hide statistics ▴' : 'Show statistics on each finding ▾'}
          </button>
          <div className="evidence-more">
            <CompareChart meta={meta} />
            <div className="inv-col">
              <DistancePlot meta={meta} />
              <Heatmap meta={meta} />
            </div>
          </div>
        </div>
      )}
      {tab === 'protein' && (
        <div className="tab-body protein-tab">
          <ProteinPanel meta={meta} tall />
          <div className="inv-col"><AnalysisCards run={run} /></div>
        </div>
      )}
      {tab === 'methods' && (
        <div className="tab-body">
          <ScienceReport meta={meta} />
          <AgentTrace meta={meta} open />
        </div>
      )}
    </div>
  )
}
