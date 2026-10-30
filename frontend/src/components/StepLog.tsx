// The agent trace: one slim always-visible line under the headline. While the run is going it shows the
// plan's steps as a row that lights up as each one happens (the current one pulses), plus a count of
// findings that passed the checks; afterwards it becomes a summary ("Planned 3 steps → ran 2 analyses →
// 3 findings passed checks → report accepted · 38 s · $0.003"). It expands into the plan checklist, the
// live step log from the run's server-sent events, and the turn timeline.
import { useState } from 'react'
import type { Metadata, RunEvent } from '../api/client'
import { nextTurn, useInvestigation } from '../state/investigation'
import { PlanCard, stepText } from './PlanCard'
import { Timeline } from './Timeline'

const BOOKKEEPING = new Set(['update_checklist', 'record_finding', 'submit_report'])
const SHORT: Record<string, string> = {
  get_metadata: 'Check dataset', list_groups: 'Domains', calculate_contacts: 'Contact map', distance_over_time: 'Distance trace',
  compare_intervals: 'Compare windows', contact_statistics: 'Pair statistics', native_contacts: 'Native contacts',
  cutoff_sensitivity: 'Check cutoffs', reproduction_check: 'Check vs published code', submit_report: 'Write report',
  contact_difference_map: 'Difference map', residue_flexibility: 'Flexibility',
}

/** The plan's steps as a row that lights up while the run is going. */
function LiveSteps({ run, paused }: { run: NonNullable<ReturnType<typeof useInvestigation>['run']>; paused: boolean }) {
  const current = run.checklist.find((c) => c.status === 'in_progress') ?? run.checklist.find((c) => c.status === 'pending')
  const findings = run.turns.filter((t) => t.tool === 'record_finding' && t.status === 'ok').length
  return (
    <ol className="live-steps" aria-live="polite">
      {run.checklist.map((c) => {
        const state = c === current && !paused ? 'current' : c.status === 'done' ? 'done' : c.status === 'skipped' ? 'skipped' : 'pending'
        return (
          <li key={c.item} className={`lstep ${state}`} title={`${c.text}${c.note ? ` — ${c.note}` : ''}`}>
            <span className="ldot" />{SHORT[c.tool] ?? c.tool}
          </li>
        )
      })}
      {findings > 0 && <li className="lstep done findings-count"><span className="ldot" />{findings} {findings === 1 ? 'finding' : 'findings'} checked</li>}
    </ol>
  )
}
const cost = (c: number | null) => (c === null ? '' : ` · $${c.toFixed(4)}`)

function line(e: RunEvent): { text: string; cls: string } | null {
  switch (e.type) {
    case 'run_started':
      return { text: 'Run started', cls: 'muted' }
    case 'turn':
      return {
        text: `T${e.turn} ${e.tool ?? '(no tool call)'} — ${e.status}${e.provenance_id ? ` → ${e.provenance_id}` : ''} · model ${e.model_s.toFixed(1)}s (${e.effort}) · tool ${e.tool_s.toFixed(1)}s${cost(e.cost_usd)}`,
        cls: e.status === 'ok' || e.status === 'submitted' ? '' : 'warn',
      }
    case 'paused':
      return { text: 'Paused (step-through)', cls: 'muted' }
    case 'run_finished':
      return { text: `Finished: ${e.status} in ${e.wall_s.toFixed(1)}s${cost(e.cost_usd)}${e.error ? ` — ${e.error}` : ''}`, cls: e.status === 'awaiting_verification' ? 'ok' : 'warn' }
    default:
      return null
  }
}

function StepLog() {
  const { events, phase, paused } = useInvestigation()
  const working = phase === 'running' && !paused && events.at(-1)?.type !== 'run_finished'
  return (
    <div>
      <h3>Step log</h3>
      <ul className="steplog">
        {events.map((e) => {
          const l = line(e)
          return l && <li key={e.seq} className={l.cls}>{l.text}</li>
        })}
        {working && <li className="muted">Working…</li>}
      </ul>
    </div>
  )
}

/** The trace's one-line summary, counted from the run's turns. */
function summary(run: NonNullable<ReturnType<typeof useInvestigation>['run']>, finished: boolean): string[] {
  const turns = run.turns
  const analyses = turns.filter((t) => t.tool && !BOOKKEEPING.has(t.tool) && t.status === 'ok').length
  const findingsOk = turns.filter((t) => t.tool === 'record_finding' && t.status === 'ok').length
  const findingsRefused = turns.filter((t) => t.tool === 'record_finding' && t.status === 'refused').length
  const reportRefused = turns.filter((t) => t.tool === 'submit_report' && t.status === 'refused').length
  const parts = [`Planned ${run.checklist.length} steps`, `ran ${analyses} ${analyses === 1 ? 'analysis' : 'analyses'}`]
  if (findingsOk || findingsRefused) parts.push(`${findingsOk} ${findingsOk === 1 ? 'finding' : 'findings'} passed checks${findingsRefused ? ` (${findingsRefused} refused and redone)` : ''}`)
  if (finished) {
    parts.push(run.report
      ? `report accepted${reportRefused ? ` after ${reportRefused} ${reportRefused === 1 ? 'fix' : 'fixes'}` : ''}`
      : 'report not accepted')
  } else {
    const cur = run.checklist.find((c) => c.status === 'in_progress') ?? run.checklist.find((c) => c.status === 'pending')
    if (cur) parts.push(`now: ${stepText(cur.tool, null).toLowerCase()}…`)
  }
  return parts
}

export function AgentTrace({ meta }: { meta: Metadata | null }) {
  const { run, phase, paused } = useInvestigation()
  const [open, setOpen] = useState(false)
  if (!run || (phase !== 'running' && phase !== 'finished')) return null
  const finished = phase === 'finished'
  const parts = summary(run, finished)
  return (
    <div className={`trace ${open ? 'open' : ''}`}>
      <div className="trace-line">
        <button className="trace-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className="trace-caret">{open ? '▾' : '▸'}</span>
          <span className="trace-label">Agent</span>
          {finished
            ? parts.map((p, i) => <span key={i} className="trace-part">{i > 0 && <span className="arrow">→</span>}{p}</span>)
            : <LiveSteps run={run} paused={paused} />}
          {finished && <span className="trace-meta">· {run.wall_s.toFixed(0)} s{run.cost_usd !== null ? ` · $${run.cost_usd.toFixed(3)}` : ''}</span>}
        </button>
        {paused && <button className="primary small" onClick={() => void nextTurn()}>Next step</button>}
      </div>
      {open && (
        <div className="trace-body">
          {finished && <PlanCard meta={meta} />}
          <StepLog />
          <Timeline />
        </div>
      )}
    </div>
  )
}
