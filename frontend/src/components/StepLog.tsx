// Live step log from the run's server-sent events: one line per turn, as it happens.
import type { RunEvent } from '../api/client'
import { nextTurn, useInvestigation } from '../state/investigation'

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

export function StepLog() {
  const { events, phase, paused } = useInvestigation()
  if (phase !== 'running' && phase !== 'finished') return null
  const working = phase === 'running' && !paused && events.at(-1)?.type !== 'run_finished'
  return (
    <section className="card">
      <header className="card-head">
        <h2>Step log</h2>
        {paused && <button className="primary small" onClick={() => void nextTurn()}>Next turn</button>}
      </header>
      <ul className="steplog">
        {events.map((e) => {
          const l = line(e)
          return l && <li key={e.seq} className={l.cls}>{l.text}</li>
        })}
        {working && <li className="muted">Working…</li>}
      </ul>
    </section>
  )
}
