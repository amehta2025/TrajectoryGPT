// What the user sees before any investigation: one prominent input, the dataset picker, three examples.
import { useState } from 'react'
import type { DatasetInfo } from '../api/client'
import { ask, askFigure, MAX_UPLOAD_BYTES, submitPasscode, useInvestigation } from '../state/investigation'
import { setSelection } from '../state/selection'

const EXAMPLES = [
  { label: 'Which LID–CORE contacts change between the first and second half?', dataset: 'adk_equilibrium',
    question: 'Which LID–CORE contacts change between the first and second half of the run?' },
  { label: 'Reproduce the native contacts figure', dataset: 'adk_transition', figure: '/figures/userguide_q1q2_replot.png',
    question: 'Reproduce this figure.',
    note: 'Attaches the MDAnalysis User Guide Q1 vs Q2 figure, regenerated from its published code (see assets/figures/ATTRIBUTION.md).' },
  { label: 'Is the SER30–ASP84 drop real?', dataset: 'adk_equilibrium',
    question: 'Is the drop in the SER30–ASP84 contact between the first and second half of the run real?' },
]

/** Shown after a 401: AI investigations need the demo passcode; charts and 3D stay open. */
function PasscodePrompt() {
  const { error } = useInvestigation()
  const [code, setCode] = useState('')
  const [checking, setChecking] = useState(false)
  return (
    <form className="passcode" onSubmit={async (e) => {
      e.preventDefault()
      setChecking(true)
      await submitPasscode(code)
      setChecking(false)
    }}>
      <span className="small">AI investigations need the demo passcode.</span>
      <input type="password" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Demo passcode" aria-label="Demo passcode" autoFocus />
      <button type="submit" className="primary" disabled={!code.trim() || checking}>{checking ? 'Checking…' : 'Unlock'}</button>
      {error && <span className="warn small">{error}</span>}
    </form>
  )
}

export function StartScreen({ datasets, dataset, compact = false }: { datasets: DatasetInfo[]; dataset: string; compact?: boolean }) {
  const { phase, needPasscode } = useInvestigation()
  const [text, setText] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [fileError, setFileError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const busy = phase === 'planning'
  const info = datasets.find((d) => d.id === dataset)

  const addFiles = (list: FileList | File[]) => {
    const picked = [...files, ...Array.from(list)].slice(-2)
    const bad = picked.find((f) => !['image/png', 'image/jpeg', 'application/pdf'].includes(f.type) || f.size > MAX_UPLOAD_BYTES)
    setFileError(bad ? `${bad.name}: use a PNG, JPEG or PDF under 10 MB` : null)
    if (!bad) setFiles(picked)
  }

  const submit = (q: string, ds = dataset, attached = files) => {
    if (busy || (!q.trim() && !attached.length)) return
    if (ds !== dataset) setSelection({ dataset: ds })
    if (attached.length) void askFigure(attached, ds, q.trim())
    else void ask(q.trim(), ds)
    setText('')
    setFiles([])
  }

  const runExample = async (ex: (typeof EXAMPLES)[number]) => {
    if (!('figure' in ex) || !ex.figure) return submit(ex.question, ex.dataset, [])
    const blob = await (await fetch(ex.figure)).blob()
    const file = new File([blob], ex.figure.split('/').pop() ?? 'figure.png', { type: 'image/png' })
    submit(ex.question, ex.dataset, [file])
  }

  return (
    <section className={compact ? 'start compact' : 'start'}>
      {!compact && <h1 className="start-title">What do you want to know about this simulation?</h1>}
      <form
        className={dragging ? 'ask dragging' : 'ask'}
        onSubmit={(e) => { e.preventDefault(); submit(text) }}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files) }}
      >
        <input
          className="ask-input" value={text} onChange={(e) => setText(e.target.value)} disabled={busy}
          placeholder="Ask a question about this simulation, or drop in a figure to reproduce."
          aria-label="Question"
        />
        <select value={dataset} onChange={(e) => setSelection({ dataset: e.target.value })} aria-label="Dataset" disabled={busy}>
          {datasets.map((d) => <option key={d.id} value={d.id}>{d.id}</option>)}
        </select>
        <label className="attach" title="Attach a figure (PNG/JPEG) and/or its methods (PDF), up to 10 MB each">
          📎
          <input type="file" accept="image/png,image/jpeg,application/pdf" multiple hidden disabled={busy}
            onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = '' }} />
        </label>
        <button type="submit" className="primary" disabled={busy || (!text.trim() && !files.length)}>{busy ? 'Planning…' : 'Plan'}</button>
      </form>
      {(files.length > 0 || fileError) && (
        <div className="attached small">
          {files.map((f) => (
            <span key={f.name} className="pill">{f.name} <button type="button" className="link" onClick={() => setFiles(files.filter((x) => x !== f))} aria-label={`Remove ${f.name}`}>×</button></span>
          ))}
          {fileError && <span className="warn">{fileError}</span>}
        </div>
      )}
      {needPasscode && <PasscodePrompt />}
      {info && <p className="muted small dataset-line">{info.description}{info.stats_valid ? '' : ' Statistics are refused on this dataset.'}</p>}
      {!compact && (
        <div className="examples">
          {EXAMPLES.map((ex) => (
            <button key={ex.label} className="example" disabled={busy} onClick={() => void runExample(ex)} title={ex.note ?? ex.question}>
              {ex.label}
              <span className="muted small">{ex.dataset}{'figure' in ex ? ' · sample figure attached' : ''}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  )
}
