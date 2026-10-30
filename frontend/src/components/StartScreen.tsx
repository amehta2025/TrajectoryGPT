// What the user sees before any investigation. Figure mode leads: drop in a figure from a paper and the
// agent reads its method and runs it on our preloaded AdK simulations. Asking a question is the second
// entry point. The dataset picker is a small detail. Users never upload trajectories (README: Known
// limitations), and the copy never implies they can.
import { useState } from 'react'
import type { DatasetInfo, Metadata } from '../api/client'
import { ask, askFigure, MAX_UPLOAD_BYTES, submitPasscode, useInvestigation } from '../state/investigation'
import { setSelection } from '../state/selection'
import { datasetLine, datasetName } from './DatasetBadge'
import { SpinningStructure } from './Viewer'

const FIGURE_EXAMPLES = [
  { label: 'Native contacts (MDAnalysis User Guide)', sub: 'exact reproduction on the same data', dataset: 'adk_transition',
    figure: '/figures/userguide_q1q2_replot.png', question: 'Reproduce this figure.',
    note: 'Attaches the MDAnalysis User Guide Q1 vs Q2 figure, regenerated from its published code (see assets/figures/ATTRIBUTION.md).' },
]
const QUESTION_EXAMPLES = [
  { label: 'Which LID–CORE contacts change between the first and second half?', dataset: 'adk_equilibrium',
    question: 'Which LID–CORE contacts change between the first and second half of the run?' },
  { label: 'Is the SER30–ASP84 drop real?', dataset: 'adk_equilibrium',
    question: 'Is the drop in the SER30–ASP84 contact between the first and second half of the run real?' },
]

/** Shown after a 401: AI investigations need the demo passcode; charts and 3D stay open. */
export function PasscodePrompt() {
  const { error, question } = useInvestigation()
  const [code, setCode] = useState('')
  const [checking, setChecking] = useState(false)
  return (
    <form className="passcode" onSubmit={async (e) => {
      e.preventDefault()
      setChecking(true)
      await submitPasscode(code)
      setChecking(false)
    }}>
      <div className="passcode-text">
        <b>Enter the demo passcode to run AI investigations.</b>
        <span className="muted small">{question ? <>Your question will run right after: “{question}”</> : 'Charts and the 3D view stay open without it.'}</span>
      </div>
      <input type="password" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Passcode" aria-label="Demo passcode" autoFocus />
      <button type="submit" className="primary" disabled={!code.trim() || checking}>{checking ? 'Checking…' : 'Unlock'}</button>
      {error && <span className="error-text small">{error}</span>}
    </form>
  )
}

export function StartScreen({ datasets, dataset, meta }: { datasets: DatasetInfo[]; dataset: string; meta: Metadata | null }) {
  const { phase, needPasscode } = useInvestigation()
  const [question, setQuestion] = useState('')
  const [note, setNote] = useState('')
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

  const startFigure = (attached: File[], ds = dataset, text = note) => {
    if (busy || !attached.length) return
    if (ds !== dataset) setSelection({ dataset: ds })
    void askFigure(attached, ds, text.trim())
    setFiles([])
    setNote('')
  }

  const startQuestion = (q: string, ds = dataset) => {
    if (busy || !q.trim()) return
    if (ds !== dataset) setSelection({ dataset: ds })
    void ask(q.trim(), ds)
    setQuestion('')
  }

  const runFigureExample = async (ex: (typeof FIGURE_EXAMPLES)[number]) => {
    const blob = await (await fetch(ex.figure)).blob()
    const file = new File([blob], ex.figure.split('/').pop() ?? 'figure.png', { type: 'image/png' })
    startFigure([file], ex.dataset, ex.question)
  }

  return (
    <section className="start">
      <div className="start-main">
        <h1 className="start-title">Drop in a figure from any paper.</h1>
        <p className="start-lede">
          TrajectoryGPT reads its method and runs it on real simulation data: two preloaded simulations of the enzyme
          adenylate kinase (AdK).
        </p>

        <label
          className={`dropzone ${dragging ? 'dragging' : ''} ${files.length ? 'has-files' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files) }}
        >
          <input type="file" accept="image/png,image/jpeg,application/pdf" multiple hidden disabled={busy}
            onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = '' }} />
          {files.length === 0
            ? <>
                <span className="dz-main">Drop a figure here, or <u>choose a file</u></span>
                <span className="muted small">
                  PNG or JPEG, plus the methods as a PDF if you have them · up to 10 MB each ·
                  works with contact maps, distance traces and native contacts
                </span>
              </>
            : <span className="attached">
                {files.map((f) => (
                  <span key={f.name} className="pill">
                    {f.name}{' '}
                    <button type="button" className="link" onClick={(e) => { e.preventDefault(); setFiles(files.filter((x) => x !== f)) }} aria-label={`Remove ${f.name}`}>×</button>
                  </span>
                ))}
                <span className="muted small">Add the methods PDF too, if you have it</span>
              </span>}
        </label>
        {fileError && <span className="error-text small">{fileError}</span>}
        {files.length > 0 && (
          <div className="figure-go">
            <input className="ask-input" value={note} onChange={(e) => setNote(e.target.value)} disabled={busy}
              placeholder="Optional: what should we take from it?" aria-label="Note about the figure" />
            <button className="primary" disabled={busy} onClick={() => startFigure(files)}>{busy ? 'Reading…' : 'Read the figure'}</button>
          </div>
        )}
        <div className="example-row">
          <span className="muted small">Try a sample:</span>
          {FIGURE_EXAMPLES.map((ex) => (
            <button key={ex.label} className="example-chip" disabled={busy} onClick={() => void runFigureExample(ex)} title={ex.note}>
              {ex.label} <span className="muted small">· {ex.sub}</span>
            </button>
          ))}
        </div>

        <div className="or-ask">
          <h2 className="or-title">Or ask a question about the simulations</h2>
          <form className="ask" onSubmit={(e) => { e.preventDefault(); startQuestion(question) }}>
            <input
              className="ask-input" value={question} onChange={(e) => setQuestion(e.target.value)} disabled={busy}
              placeholder="Which contacts change between the first and second half?" aria-label="Question"
            />
            <button type="submit" disabled={busy || !question.trim()}>{busy ? 'Planning…' : 'Plan'}</button>
          </form>
          <div className="example-row">
            {QUESTION_EXAMPLES.map((ex) => (
              <button key={ex.label} className="example-chip" disabled={busy} onClick={() => startQuestion(ex.question, ex.dataset)} title={ex.question}>
                {ex.label}
              </button>
            ))}
          </div>
        </div>

        {needPasscode && <PasscodePrompt />}

        <div className="ds-picker">
          <label className="muted small" htmlFor="ds">Data</label>
          <select id="ds" value={dataset} onChange={(e) => setSelection({ dataset: e.target.value })} disabled={busy}>
            {datasets.map((d) => <option key={d.id} value={d.id}>AdK · {datasetName(d.id)}</option>)}
          </select>
          {info && <p className="muted small dataset-line">{datasetLine(info)} Analyses use these preloaded public simulations only.</p>}
        </div>
      </div>
      <div className="start-art">{meta && <SpinningStructure meta={meta} />}</div>
    </section>
  )
}
