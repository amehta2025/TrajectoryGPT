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
  get_metadata: 'Check the data', list_groups: 'Domains', calculate_contacts: 'Measure contacts', distance_over_time: 'Track a distance',
  compare_intervals: 'Compare', contact_statistics: 'Look closer', native_contacts: 'Reconstruct contacts',
  cutoff_sensitivity: 'Double-check', reproduction_check: 'Validate against the publication', submit_report: 'Summarize findings',
  contact_difference_map: 'Map changes', residue_flexibility: 'Measure movement', domain_opening: 'Track opening',
}

type Run = NonNullable<ReturnType<typeof useInvestigation>['run']>
type Data = Record<string, unknown> & { n_native?: number[]; n_values?: number; match?: boolean; n_supported?: number; n_tested?: number
  n_robust?: number; keys_a?: string[]; keys_b?: string[]; supported_pairs?: unknown[] | null }

/** What a finished step found, in a few words, from its stored result (never the model's own text). */
export function stepResult(run: Run, tool: string): string | null {
  const hits = Object.values(run.results).filter((r) => r.tool === tool && r.status === 'ok' && r.data_for_ui)
  const d = hits.at(-1)?.data_for_ui as Data | undefined
  if (!d) return null
  switch (tool) {
    case 'native_contacts': return d.n_native ? `${d.n_native[0].toLocaleString()} reference contacts` : null
    case 'reproduction_check': return d.n_values ? (d.match ? `${d.n_values}/${d.n_values} values match` : 'values differ') : null
    case 'compare_intervals': return d.n_tested !== undefined ? `${d.n_supported} of ${d.n_tested} changed` : null
    case 'cutoff_sensitivity': return d.n_robust !== undefined ? `${d.n_robust} robust` : null
    case 'contact_difference_map': return d.keys_a && d.keys_b ? `${(d.keys_a.length * d.keys_b.length).toLocaleString()} pairs mapped` : null
    default: return null
  }
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
            <span className="ldot" />{SHORT[c.tool] ?? 'Analysis'}
            {c.status === 'done' && stepResult(run, c.tool) && <span className="lresult">· {stepResult(run, c.tool)}</span>}
          </li>
        )
      })}
      {findings > 0 && <li className="lstep done findings-count"><span className="ldot" />{findings} {findings === 1 ? 'finding' : 'findings'} checked</li>}
    </ol>
  )
}
function line(e: RunEvent): { text: string; cls: string } | null {
  switch (e.type) {
    case 'run_started':
      return { text: 'Run started', cls: 'muted' }
    case 'turn':
      return {
        text: `T${e.turn} ${e.tool ?? '(no tool call)'} — ${e.status}${e.provenance_id ? ` → ${e.provenance_id}` : ''} · model ${e.model_s.toFixed(1)}s (${e.effort}) · tool ${e.tool_s.toFixed(1)}s`,
        cls: e.status === 'ok' || e.status === 'submitted' ? '' : 'warn',
      }
    case 'paused':
      return { text: 'Paused (step-through)', cls: 'muted' }
    case 'run_finished':
      return { text: `Finished: ${e.status} in ${e.wall_s.toFixed(1)}s${e.error ? ` — ${e.error}` : ''}`, cls: e.status === 'awaiting_verification' ? 'ok' : 'warn' }
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
  const found = run.checklist.map((c) => stepResult(run, c.tool)).filter(Boolean) as string[]
  if (found.length) parts.push(found.join(', '))
  if (findingsOk || findingsRefused) parts.push(`checked ${findingsOk} ${findingsOk === 1 ? 'finding' : 'findings'}`)
  void reportRefused // refusals stay in the step log; the summary line stays plain
  if (finished) {
    parts.push(run.report ? 'done' : 'stopped for review')
  } else {
    const cur = run.checklist.find((c) => c.status === 'in_progress') ?? run.checklist.find((c) => c.status === 'pending')
    if (cur) parts.push(`now: ${stepText(cur.tool, null).toLowerCase()}…`)
  }
  return parts
}

export function AgentTrace({ meta, open: startOpen = false }: { meta: Metadata | null; open?: boolean }) {
  const { run, phase, paused } = useInvestigation()
  const [open, setOpen] = useState(startOpen)
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
          {finished && <span className="trace-meta">· {run.wall_s.toFixed(0)} s</span>}
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
