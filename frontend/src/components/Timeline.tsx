// Turn timeline: click a turn to see what the model saw (new input that turn) and what it called.
// The "Step through turns" toggle pauses the loop after each turn until Next.
import { useState } from 'react'
import type { Turn } from '../api/client'
import { nextTurn, useInvestigation } from '../state/investigation'

function pretty(s: string): string {
  try {
    return JSON.stringify(JSON.parse(s), null, 1)
  } catch {
    return s
  }
}

/** One input/output item as readable text (encrypted reasoning is never shown). */
function itemText(it: Record<string, unknown>): string {
  const type = (it.type as string | undefined) ?? 'message'
  if (type === 'function_call_output') return `tool result:\n${pretty(String(it.output))}`
  if (type === 'function_call') return `call ${it.name}(${it.arguments})`
  if (type === 'reasoning') {
    const summary = (it.summary as { text: string }[] | undefined) ?? []
    return summary.length ? `reasoning summary: ${summary.map((s) => s.text).join(' ')}` : '(private reasoning)'
  }
  const content = it.content
  const text = typeof content === 'string'
    ? content
    : Array.isArray(content) ? content.map((c) => (c as { text?: string }).text ?? '[image]').join('') : ''
  return `${it.role ?? 'assistant'}: ${text}`
}

function TurnDetail({ t }: { t: Turn }) {
  return (
    <div className="turn-detail">
      <h3>Model saw (new this turn)</h3>
      {t.saw.map((it, i) => <pre key={i}>{itemText(it)}</pre>)}
      <h3>Model called</h3>
      {t.output.map((it, i) => <pre key={i}>{itemText(it)}</pre>)}
      <h3>Result ({t.status})</h3>
      <pre>{pretty(t.summary)}</pre>
      <p className="muted small">
        {t.usage.model} · effort {t.usage.effort} · in {t.usage.input_tokens} (cached {t.usage.cached_tokens}) · out {t.usage.output_tokens} (reasoning {t.usage.reasoning_tokens})
      </p>
    </div>
  )
}

export function Timeline() {
  const { run, phase, paused } = useInvestigation()
  const [open, setOpen] = useState<number | null>(null)
  if (!run || (phase !== 'running' && phase !== 'finished')) return null
  return (
    <section className="card">
      <header className="card-head">
        <h2>Timeline</h2>
        <div className="controls">
          <label title="Pause after each turn until you click Next">
            <input type="checkbox" checked={run.step_through} disabled={phase === 'finished'} onChange={(e) => void nextTurn(e.target.checked)} />
            Step through turns
          </label>
          {paused && <button className="primary small" onClick={() => void nextTurn()}>Next</button>}
        </div>
      </header>
      <ol className="timeline">
        {run.turns.map((t) => (
          <li key={t.turn}>
            <button className={`turn-btn ${open === t.turn ? 'active' : ''}`} onClick={() => setOpen(open === t.turn ? null : t.turn)}>
              <b>T{t.turn}</b> {t.tool ?? '(no tool call)'} <span className="muted">{t.status}</span>
              <span className="muted small"> {t.model_s.toFixed(1)}s + {t.tool_s.toFixed(1)}s</span>
            </button>
            {open === t.turn && <TurnDetail t={t} />}
          </li>
        ))}
      </ol>
    </section>
  )
}
