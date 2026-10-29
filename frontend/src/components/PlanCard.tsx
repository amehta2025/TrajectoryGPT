// The proposed plan with editable consequential parameters. One Run click approves it all; the backend
// re-validates the edits. Once running, the same card shows the checklist and "3/4 done".
import { useEffect, useState } from 'react'
import type { ChecklistItem, Plan } from '../api/client'
import { approve, useInvestigation } from '../state/investigation'

const CONTACT_DEFINITION = 'min heavy-atom distance ≤ cutoff (Å)'
const STATUS_ICON: Record<ChecklistItem['status'], string> = { pending: '○', in_progress: '◐', done: '●', skipped: '⊘' }

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

export function PlanCard() {
  const { phase, proposal, run, error } = useInvestigation()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [stepThrough, setStepThrough] = useState(false)
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    if (proposal) setDraft(draftOf(proposal.plan))
  }, [proposal])

  if (phase === 'planning') return <section className="card"><h2>Plan</h2><p className="muted">Planning… (medium reasoning, ~10 s)</p></section>
  if (!proposal || !draft) return null
  const p = proposal.plan
  const locked = phase !== 'plan'
  const checklist = run?.checklist
  const done = checklist ? checklist.filter((c) => c.status === 'done' || c.status === 'skipped').length : 0
  const field = (k: keyof Omit<Draft, 'drop'>, w = 56) => (
    <input value={draft[k]} disabled={locked} style={{ width: w }} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
  )

  return (
    <section className="card plan-card">
      <header className="card-head">
        <h2>Plan</h2>
        {checklist ? <span className="pill">{done}/{checklist.length} done</span> : <span className="pill">awaiting approval</span>}
      </header>
      <p className="plan-summary">{p.summary}</p>
      <div className="plan-params">
        <span className="pill">{p.dataset}</span>
        <span className="pill">{p.group_a}–{p.group_b}</span>
        <span className="muted small">Contact: {CONTACT_DEFINITION}</span>
      </div>
      <div className="param-grid">
        <label>Cutoff (Å) {field('cutoff')}</label>
        <label title="A finding is robust only if supported at every one of these cutoffs (3.5–6.0 Å)">Robustness cutoffs (Å) {field('robust', 110)}</label>
        <label>Window A (frames) {field('a0', 52)}–{field('a1', 52)}</label>
        {p.window_b && <label>Window B (frames) {field('b0', 52)}–{field('b1', 52)}</label>}
        <label>Stride {field('stride', 40)}</label>
        <label title="Pairs with |Δresid| ≤ N are skipped">Sequence exclusion {field('seq', 40)}</label>
      </div>
      <ol className="steps">
        {checklist
          ? checklist.map((c) => (
              <li key={c.item} className={`st-${c.status}`}>
                <span className="step-icon">{STATUS_ICON[c.status]}</span>
                <code>{c.tool}</code> <span className="muted">{c.text}</span>
                {c.note && <span className="muted small"> — {c.note}</span>}
              </li>
            ))
          : p.steps.map((s, i) => {
              const dropped = draft.drop.has(i)
              return (
                <li key={i} className={dropped ? 'dropped' : ''}>
                  <span className="step-icon">○</span>
                  <code>{s.tool}</code> <span className="muted">{s.rationale}</span>
                  {!locked && s.tool !== 'submit_report' && (
                    <button className="link small" onClick={() => {
                      const drop = new Set(draft.drop)
                      if (dropped) drop.delete(i)
                      else drop.add(i)
                      setDraft({ ...draft, drop })
                    }}>{dropped ? 'keep' : 'drop'}</button>
                  )}
                </li>
              )
            })}
      </ol>
      {!locked && (
        <div className="plan-actions">
          <label className="small muted"><input type="checkbox" checked={stepThrough} onChange={(e) => setStepThrough(e.target.checked)} /> Step through turns</label>
          <button className="primary" disabled={starting} onClick={async () => {
            setStarting(true)
            await approve(editsOf(p, draft), stepThrough)
            setStarting(false)
          }}>{starting ? 'Starting…' : 'Run'}</button>
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </section>
  )
}
