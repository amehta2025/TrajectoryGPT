// The proposed plan with editable consequential parameters. One Run click approves it all; the backend
// re-validates the edits. Once running, the same card shows the checklist and "3 of 4 done".
// Primary text is plain language built by code from the plan's own parameters; advanced parameters
// sit under a collapsed "Settings"; tool names are small secondary tags.
import { useEffect, useState } from 'react'
import type { ChecklistItem, Metadata, Plan } from '../api/client'
import { approve, pendingConfirmations, useInvestigation } from '../state/investigation'

const CONTACT_DEFINITION = 'two residues are in contact when their closest heavy atoms are within the cutoff'
const CONTACT_TOOLS = new Set(['calculate_contacts', 'distance_over_time', 'compare_intervals', 'contact_statistics',
  'cutoff_sensitivity', 'contact_difference_map'])
const STATUS_ICON: Record<ChecklistItem['status'], string> = { pending: '○', in_progress: '◐', done: '●', skipped: '⊘' }

/** Plain sentence for a plan step, from the tool and the plan's groups. */
export function stepText(tool: string, p: Pick<Plan, 'group_a' | 'group_b'> | null): string {
  const pair = p ? `${p.group_a}–${p.group_b}` : ''
  switch (tool) {
    case 'get_metadata': return 'Check the dataset: length, time step and domains'
    case 'list_groups': return 'Look up the domain definitions'
    case 'calculate_contacts': return `Measure how often each ${pair} residue pair is in contact`
    case 'distance_over_time': return 'Track one residue pair’s distance frame by frame'
    case 'compare_intervals': return `Compare ${pair} contacts between the two windows`
    case 'contact_statistics': return 'Estimate one pair’s contact frequency with its uncertainty'
    case 'native_contacts': return 'Compute the fraction of native contacts per frame'
    case 'cutoff_sensitivity': return 'Re-check each supported change at every robustness cutoff'
    case 'contact_difference_map': return `Map how each ${pair} contact changes between the two windows`
    case 'residue_flexibility': return 'Measure how much each residue moves (Cα RMSF)'
    case 'reproduction_check': return 'Check the result against the published code'
    case 'submit_report': return 'Write the report from the recorded findings'
    default: return tool
  }
}

/** "first half (0–502 ns)" when the windows are the halves, otherwise the frame range with its time. */
export function windowText(meta: Metadata | null, w: [number, number], which: 'A' | 'B', halves: boolean): string {
  const time = !meta
    ? ''
    : meta.dt_ns === null
      ? `path steps ${w[0]}–${w[1] - 1}`
      : `${Math.round(w[0] * meta.dt_ns)}–${Math.round(w[1] * meta.dt_ns)} ns`
  if (halves) return `${which === 'A' ? 'first half' : 'second half'} (${time})`
  if (meta?.dt_ns === null) return `window ${which}: ${time}`
  return `window ${which}: frames ${w[0]}–${w[1] - 1}${time ? ` (${time})` : ''}`
}

export function isHalves(p: Pick<Plan, 'window_a' | 'window_b'>, nFrames: number | undefined): boolean {
  if (!nFrames || !p.window_b) return false
  const mid = Math.floor(nFrames / 2)
  return p.window_a[0] === 0 && p.window_a[1] === mid && p.window_b[0] === mid && p.window_b[1] === nFrames
}

type Draft = { cutoff: string; robust: string; a0: string; a1: string; b0: string; b1: string; stride: string; seq: string; drop: Set<number> }

function draftOf(p: Plan): Draft {
  return {
    cutoff: String(p.cutoff), robust: p.robustness_cutoffs.join(', '),
    a0: String(p.window_a[0]), a1: String(p.window_a[1]),
    b0: p.window_b ? String(p.window_b[0]) : '', b1: p.window_b ? String(p.window_b[1]) : '',
    stride: String(p.stride), seq: String(p.sequence_exclusion), drop: new Set(),
  }
}

function editsOf(p: Plan, d: Draft): Partial<Plan> {
  const num = Number
  return {
    cutoff: num(d.cutoff),
    robustness_cutoffs: d.robust.split(/[,\s]+/).filter(Boolean).map(num),
    window_a: [num(d.a0), num(d.a1)],
    window_b: p.window_b ? [num(d.b0), num(d.b1)] : null,
    stride: num(d.stride),
    sequence_exclusion: num(d.seq),
    steps: p.steps.filter((_, i) => !d.drop.has(i)),
  }
}

export function PlanCard({ meta }: { meta: Metadata | null }) {
  const inv = useInvestigation()
  const { phase, proposal, run, error } = inv
  const pending = pendingConfirmations(inv)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [stepThrough, setStepThrough] = useState(false)
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    if (proposal) setDraft(draftOf(proposal.plan))
  }, [proposal])

  if (phase === 'planning') return <section className="card plan-card"><h2>Plan</h2><p className="muted">Drafting a plan… (about 10 s)</p></section>
  if (!proposal || !draft) return null
  const p = proposal.plan
  const locked = phase !== 'plan'
  const checklist = run?.checklist
  const done = checklist ? checklist.filter((c) => c.status === 'done' || c.status === 'skipped').length : 0
  const halves = isHalves(p, meta?.n_frames)
  const robustStep = p.steps.some((s) => s.tool === 'cutoff_sensitivity')
  // Groups and cutoff matter only to contact tools; other plans (native contacts, RMSF) leave them out of the summary.
  const usesContacts = p.steps.some((s) => CONTACT_TOOLS.has(s.tool))
  const field = (k: keyof Omit<Draft, 'drop'>, w = 56) => (
    <input value={draft[k]} disabled={locked} style={{ width: w }} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
  )

  return (
    <section className="card plan-card">
      <header className="card-head">
        <h2 title={p.summary}>Plan</h2>
        <span className="pill">{checklist ? `${done} of ${checklist.length} done` : 'Waiting for your approval'}</span>
      </header>
      {usesContacts
        ? (
          <>
            <p className="plan-scope">
              <b>{p.group_a}</b> vs <b>{p.group_b}</b> at a <b>{p.cutoff} Å</b> cutoff
              {p.window_b
                ? <>, {windowText(meta, p.window_a, 'A', halves)} vs {windowText(meta, p.window_b, 'B', halves)}</>
                : <>, {windowText(meta, p.window_a, 'A', false)}</>}
              {robustStep && <>, robustness checked at {p.robustness_cutoffs.map((c) => c.toFixed(1)).join(", ")} Å</>}.
            </p>
            <p className="muted small plan-def">Contact: {CONTACT_DEFINITION}.</p>
          </>
        )
        : <p className="plan-scope">Frames: {windowText(meta, p.window_a, 'A', false).replace('window A: ', '')}.</p>}
      <ol className="steps">
        {checklist
          ? checklist.map((c) => (
              <li key={c.item} className={`st-${c.status}`} title={c.text}>
                <span className="step-icon">{STATUS_ICON[c.status]}</span>
                <span className="step-text">{stepText(c.tool, p)}{c.note && <span className="muted small"> — {c.note}</span>}</span>
                <span className="tool-tag">{c.tool}</span>
              </li>
            ))
          : p.steps.map((s, i) => {
              const dropped = draft.drop.has(i)
              return (
                <li key={i} className={dropped ? 'dropped' : ''} title={s.rationale}>
                  <span className="step-icon">{i + 1}</span>
                  <span className="step-text">{stepText(s.tool, p)}</span>
                  <span className="tool-tag">{s.tool}</span>
                  {!locked && s.tool !== 'submit_report' && (
                    <button className="link small step-drop" onClick={() => {
                      const drop = new Set(draft.drop)
                      if (dropped) drop.delete(i)
                      else drop.add(i)
                      setDraft({ ...draft, drop })
                    }}>{dropped ? 'Keep' : 'Skip'}</button>
                  )}
                </li>
              )
            })}
      </ol>
      <details className="settings">
        <summary>Settings</summary>
        <div className="param-grid">
          <label>Cutoff (Å) {field('cutoff')}</label>
          {robustStep && <label title="A finding is robust only if supported at every one of these cutoffs (3.5–6.0 Å)">Robustness cutoffs (Å) {field('robust', 110)}</label>}
          <label>Window A (frames) {field('a0', 52)}–{field('a1', 52)}</label>
          {p.window_b && <label>Window B (frames) {field('b0', 52)}–{field('b1', 52)}</label>}
          <label title="Use every Nth frame">Stride {field('stride', 40)}</label>
          <label title="Pairs with |Δresid| ≤ N are skipped">Sequence exclusion {field('seq', 40)}</label>
        </div>
      </details>
      {!locked && (
        <div className="plan-actions">
          <label className="small muted" title="The agent stops after each step until you click Next"><input type="checkbox" checked={stepThrough} onChange={(e) => setStepThrough(e.target.checked)} /> Pause after each step</label>
          {pending.length > 0 && <span className="small pending-note">Tick {pending.length === 1 ? 'the unsure setting' : `the ${pending.length} unsure settings`} read from the figure first</span>}
          <button className="primary" disabled={starting || pending.length > 0} onClick={async () => {
            setStarting(true)
            await approve(editsOf(p, draft), stepThrough)
            setStarting(false)
          }}>{starting ? 'Starting…' : 'Run plan'}</button>
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </section>
  )
}
