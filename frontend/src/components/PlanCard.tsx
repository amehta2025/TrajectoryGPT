// The proposed plan with editable consequential parameters. One Run click approves it all; the backend
// re-validates the edits. Once running, the same card shows the checklist and "3 of 4 done".
// Primary text is plain language built by code from the plan's own parameters; advanced parameters
// sit under a collapsed "Settings"; tool names are small secondary tags.
import { useEffect, useState } from 'react'
import { api, type ChecklistItem, type Metadata, type Plan } from '../api/client'
import { approve, useInvestigation } from '../state/investigation'

const CONTACT_DEFINITION = 'two residues are in contact when their closest heavy atoms are within the cutoff'
const CONTACT_TOOLS = new Set(['calculate_contacts', 'distance_over_time', 'compare_intervals', 'contact_statistics',
  'cutoff_sensitivity', 'contact_difference_map'])
const STATUS_ICON: Record<ChecklistItem['status'], string> = { pending: '○', in_progress: '◐', done: '●', skipped: '⊘' }

/** An error in plain words; the backend's own wording (validation problems) is folded under Show the science. */
export function ErrorNote({ error, detail, className = 'error' }: { error: string; detail?: string[] | null; className?: string }) {
  return (
    <div className={className} role="alert">
      {error}
      {!!detail?.length && (
        <details className="error-detail">
          <summary>Show the science</summary>
          <ul>{detail.map((d) => <li key={d}>{d}</li>)}</ul>
        </details>
      )}
    </div>
  )
}

/** Plain sentence for a plan step, from the tool and the plan's groups. */
export function stepText(tool: string, p: Pick<Plan, 'group_a' | 'group_b'> | null): string {
  const pair = p ? `${p.group_a}–${p.group_b}` : ''
  switch (tool) {
    case 'get_metadata': return 'Check the dataset'
    case 'list_groups': return 'Look up the protein’s domains'
    case 'calculate_contacts': return `Measure how often each ${pair} contact forms`
    case 'distance_over_time': return 'Track how far apart two parts of the protein are over time'
    case 'compare_intervals': return `Compare ${pair} contacts between two parts of the run`
    case 'contact_statistics': return 'Look at one contact in detail'
    case 'native_contacts': return 'Reconstruct the molecular contacts'
    case 'cutoff_sensitivity': return 'Re-check each change at several distance thresholds'
    case 'contact_difference_map': return `Map which ${pair} contacts change`
    case 'residue_flexibility': return 'Measure how much each part of the protein moves'
    case 'domain_opening': return 'Track how open the domain is over time'
    case 'reproduction_check': return 'Validate against the publication'
    case 'submit_report': return 'Summarize the findings'
    default: return 'Run an analysis'
  }
}

/** One line on what a step does and why, under its plain sentence. */
export const STEP_DETAIL: Record<string, string> = {
  get_metadata: 'Size, length and time step of the simulation.',
  calculate_contacts: 'Which residue pairs stay close, and how often.',
  distance_over_time: 'The closest distance between two residues in every frame.',
  compare_intervals: 'Separates real changes from random variation, correcting for testing many pairs at once.',
  contact_statistics: 'How often it forms, how slowly it changes, and how sure we can be.',
  native_contacts: 'Which contacts of the starting and ending shapes stay close as the protein changes shape.',
  cutoff_sensitivity: 'A change counts as robust only if it holds at every threshold.',
  contact_difference_map: 'Which contacts were gained and which were lost.',
  residue_flexibility: 'How far each residue moves around its average position.',
  domain_opening: 'The distance between the centres of a domain and the core, frame by frame.',
  reproduction_check: 'Compares every computed value with the published code’s output.',
  submit_report: 'What matches, what differs, and which conclusions the numbers support.',
}

/** "first half (0–502 ns)" when the windows are the halves, otherwise the frame range with its time. */
export function windowText(meta: Metadata | null, w: [number, number], which: 'A' | 'B', halves: boolean): string {
  const time = !meta
    ? ''
    : meta.dt_ns === null
      ? `path steps ${w[0]}–${w[1] - 1}`
      : `${Math.round(w[0] * meta.dt_ns)}–${Math.round(w[1] * meta.dt_ns)} ns`
  if (halves) return `${which === 'A' ? 'first half' : 'second half'} (${time})`
  if (meta && w[0] === 0 && w[1] === meta.n_frames) return meta.dt_ns === null ? 'the whole path' : `the whole run (${time})`
  return meta?.dt_ns === null ? time : time || `window ${which}`
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

/** compact (figure setup): steps and parameters fold into "Scientific settings"; only the Run button shows.
 *  settingsOpen lets the page's Edit button open that fold. */
export function PlanCard({ meta, compact = false, settingsOpen = false }: { meta: Metadata | null; compact?: boolean; settingsOpen?: boolean }) {
  const inv = useInvestigation()
  const { phase, proposal, run, error, edits, editSeq } = inv
  const [draft, setDraft] = useState<Draft | null>(null)
  const [stepThrough, setStepThrough] = useState(false)
  const [starting, setStarting] = useState(false)
  const [plant, setPlant] = useState(false)
  const [canPlant, setCanPlant] = useState(false)
  useEffect(() => {
    api.health().then((h) => setCanPlant(h.dev_overclaim_toggle), () => {})
  }, [])

  // The Settings fold starts from the plan with the user's figure-row edits (re-read after each row edit).
  useEffect(() => {
    if (proposal) setDraft(draftOf({ ...proposal.plan, ...edits }))
  }, [proposal, editSeq]) // eslint-disable-line react-hooks/exhaustive-deps

  if (phase === 'planning') {
    const status = inv.kicker === 'Recorded run'
      ? 'Opening the recorded run…'
      : inv.figureRead ? 'Figure read. Drafting the analysis plan…'
      : inv.kicker !== 'You asked' ? 'Reading the figure… (usually 10–25 s)' : 'Drafting a plan… (usually 10–20 s)'
    return <section className="card plan-card"><h2>Analysis plan</h2><p className="muted plan-status">{status}</p></section>
  }
  if (!proposal || !draft) return null
  const p = { ...proposal.plan, ...edits }
  // Residues named from another protein: Run waits for the AdK pair (the backend refuses without it too).
  const needsPair = !!proposal.figure?.spec.foreign_residues?.length && !p.pair
  const locked = phase !== 'plan'
  const fixed = locked || !!proposal.recorded // a recorded plan (replay mode) cannot be edited, only run
  const checklist = run?.checklist
  const done = checklist ? checklist.filter((c) => c.status === 'done' || c.status === 'skipped').length : 0
  const halves = isHalves(p, meta?.n_frames)
  const robustStep = p.steps.some((s) => s.tool === 'cutoff_sensitivity')
  // Groups and cutoff matter only to contact tools; other plans (native contacts, RMSF) leave them out of the summary.
  const usesContacts = p.steps.some((s) => CONTACT_TOOLS.has(s.tool))
  const field = (k: keyof Omit<Draft, 'drop'>, w = 56) => (
    <input value={draft[k]} disabled={fixed} style={{ width: w }} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
  )

  const body = (
    <>
      <header className="card-head">
        <h2 title={p.summary}>Analysis plan</h2>
        {checklist && <span className="pill">{done} of {checklist.length} done</span>}
      </header>
      {usesContacts && (
        <p className="plan-scope">
          <b>{p.group_a}</b> and <b>{p.group_b}</b>
          {p.window_b ? (halves ? ', first half vs second half' : ', two parts of the run') : ''}
          {robustStep ? ', double-checked at several distance thresholds' : ''}.
        </p>
      )}
      {(p.notes ?? []).map((n) => <p key={n} className="plan-note">Note: {n}</p>)}
      <ol className="steps">
        {checklist
          ? checklist.map((c) => (
              <li key={c.item} className={`st-${c.status}`} title={c.text}>
                <span className="step-icon">{STATUS_ICON[c.status]}</span>
                <span className="step-text">{stepText(c.tool, p)}</span>
              </li>
            ))
          : p.steps.map((s, i) => {
              const dropped = draft.drop.has(i)
              return (
                <li key={i} className={dropped ? 'dropped' : ''} title={s.rationale}>
                  <span className="step-icon">{i + 1}</span>
                  <span className="step-text">{stepText(s.tool, p)}{STEP_DETAIL[s.tool] && <span className="step-detail">{STEP_DETAIL[s.tool]}</span>}</span>
                  {!fixed && s.tool !== 'submit_report' && (
                    <button className="link small step-drop" title="The agent won’t run this step, and the report will say it was skipped" onClick={() => {
                      const drop = new Set(draft.drop)
                      if (dropped) drop.delete(i)
                      else drop.add(i)
                      setDraft({ ...draft, drop })
                    }}>{dropped ? 'Keep step' : 'Skip step'}</button>
                  )}
                </li>
              )
            })}
      </ol>
      <details className="settings">
        <summary>{proposal.figure ? 'Edit scientific assumptions' : 'Advanced settings'}</summary>
        <p className="muted small plan-def">
          {usesContacts && <>Contact: {CONTACT_DEFINITION}; cutoff {p.cutoff} Å{!proposal.figure && (p.cutoff === 4.5 ? ' (default)' : ' (from your question)')}.
            {' '}Pairs within {p.sequence_exclusion} residues in sequence are skipped{!proposal.figure && meta && (p.sequence_exclusion === meta.sequence_exclusion_default ? ' (default)' : '')}. </>}
          {p.window_b
            ? <>{windowText(meta, p.window_a, 'A', halves)} vs {windowText(meta, p.window_b, 'B', halves)}.</>
            : <>{windowText(meta, p.window_a, 'A', false)}.</>}
          {robustStep && <> Robustness checked at {p.robustness_cutoffs.map((c) => c.toFixed(1)).join(', ')} Å.</>}
          {' '}Steps: {p.steps.map((s) => s.tool).join(', ')}.
        </p>
        <div className="param-grid">
          <label>Cutoff (Å) {field('cutoff')}</label>
          {robustStep && <label title="A finding is robust only if supported at every one of these cutoffs (3.5–6.0 Å)">Robustness cutoffs (Å) {field('robust', 110)}</label>}
          <label>Window A (frames) {field('a0', 52)}–{field('a1', 52)}</label>
          {p.window_b && <label>Window B (frames) {field('b0', 52)}–{field('b1', 52)}</label>}
          <label title="Use every Nth frame">Stride {field('stride', 40)}</label>
          <label title="Pairs with |Δresid| ≤ N are skipped">Sequence exclusion {field('seq', 40)}</label>
        </div>
      </details>
    </>
  )
  return (
    <section className={`card plan-card ${compact ? 'compact' : ''}`}>
      {compact
        ? <details className="sci-settings" open={settingsOpen || undefined}><summary>Scientific settings: the plan and its parameters</summary>{body}</details>
        : body}
      {!locked && (
        <div className="plan-actions">
          <label className="small muted" title="The agent stops after each step until you click Next"><input type="checkbox" checked={stepThrough} onChange={(e) => setStepThrough(e.target.checked)} /> Pause after each step</label>
          {canPlant && !proposal.recorded && (
            <label className="small muted dev-toggle" title="Development only: after the report passes the automatic checks, add a finding that calls an unsupported change supported, to show the second model catching it">
              <input type="checkbox" checked={plant} onChange={(e) => setPlant(e.target.checked)} /> Plant a wrong finding (dev demo)
            </label>
          )}
          {proposal.recorded && <span className="small muted">Recorded plan: its settings are fixed.</span>}
          {needsPair && <span className="small muted">Pick the AdK residues above first.</span>}
          <button className="primary" disabled={starting || needsPair} onClick={async () => {
            setStarting(true)
            await approve(editsOf(p, draft), stepThrough, plant)
            setStarting(false)
          }}>{starting ? 'Starting…' : 'Run analysis →'}</button>
        </div>
      )}
      {error && <ErrorNote error={error} detail={inv.errorDetail} />}
    </section>
  )
}
