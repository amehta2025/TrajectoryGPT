// What the user sees before any investigation: one prominent input, the dataset picker, three examples.
import { useState } from 'react'
import type { DatasetInfo } from '../api/client'
import { ask, useInvestigation } from '../state/investigation'
import { setSelection } from '../state/selection'

const EXAMPLES = [
  { label: 'Which LID–CORE contacts change between the first and second half?', dataset: 'adk_equilibrium',
    question: 'Which LID–CORE contacts change between the first and second half of the run?' },
  { label: 'Reproduce the native contacts figure', dataset: 'adk_transition',
    question: 'Reproduce the MDAnalysis User Guide native contacts figure (fraction of native contacts Q1 vs Q2) on the transition path.',
    note: 'The sample figure is attached once figure mode lands (Day 5); for now this asks in words.' },
  { label: 'Is the SER30–ASP84 drop real?', dataset: 'adk_equilibrium',
    question: 'Is the drop in the SER30–ASP84 contact between the first and second half of the run real?' },
]

export function StartScreen({ datasets, dataset, compact = false }: { datasets: DatasetInfo[]; dataset: string; compact?: boolean }) {
  const { phase } = useInvestigation()
  const [text, setText] = useState('')
  const busy = phase === 'planning'
  const info = datasets.find((d) => d.id === dataset)

  const submit = (q: string, ds = dataset) => {
    if (!q.trim() || busy) return
    if (ds !== dataset) setSelection({ dataset: ds })
    void ask(q.trim(), ds)
    setText('')
  }

  return (
    <section className={compact ? 'start compact' : 'start'}>
      {!compact && <h1 className="start-title">What do you want to know about this simulation?</h1>}
      <form className="ask" onSubmit={(e) => { e.preventDefault(); submit(text) }}>
        <input
          className="ask-input" value={text} onChange={(e) => setText(e.target.value)} disabled={busy}
          placeholder="Ask a question about this simulation, or drop in a figure to reproduce."
          aria-label="Question"
        />
        <select value={dataset} onChange={(e) => setSelection({ dataset: e.target.value })} aria-label="Dataset" disabled={busy}>
          {datasets.map((d) => <option key={d.id} value={d.id}>{d.id}</option>)}
        </select>
        <button type="submit" className="primary" disabled={busy || !text.trim()}>{busy ? 'Planning…' : 'Plan'}</button>
      </form>
      {info && <p className="muted small dataset-line">{info.description}{info.stats_valid ? '' : ' Statistics are refused on this dataset.'}</p>}
      {!compact && (
        <div className="examples">
          {EXAMPLES.map((ex) => (
            <button key={ex.label} className="example" disabled={busy} onClick={() => submit(ex.question, ex.dataset)} title={ex.note ?? ex.question}>
              {ex.label}
              <span className="muted small">{ex.dataset}{ex.note ? ' · figure attaches on Day 5' : ''}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  )
}
