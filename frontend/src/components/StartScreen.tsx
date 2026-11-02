// What the user sees before any investigation. Figure mode leads: drop in a figure from a paper and the
// agent reads its method and runs it on our preloaded AdK simulations. Asking a question is the second
// entry point. The dataset picker is a small detail. Users never upload trajectories (README: Known
// limitations), and the copy never implies they can.
import { useEffect, useState } from 'react'
import { api, type DatasetInfo, type Metadata, type Recorded } from '../api/client'
import { ask, askFigure, clearNotices, MAX_UPLOAD_BYTES, submitPasscode, useInvestigation } from '../state/investigation'
import { setSelection } from '../state/selection'
import { datasetName } from './DatasetBadge'
import { ModelToggle } from './ModelToggle'
import { MicButton, VoiceHint } from './VoiceInput'
import { onTranscript } from '../state/voice'
import { SpinningStructure } from './Viewer'
import { UnsupportedNotice } from './ReproduceView'

const FIGURE_EXAMPLES = [
  { label: 'A figure from the MDAnalysis guide', dataset: 'adk_transition',
    figure: '/figures/userguide_q1q2_replot.png', question: 'Reproduce this figure.',
    note: 'Attaches the MDAnalysis User Guide Q1 vs Q2 figure, regenerated from its published code (see assets/figures/ATTRIBUTION.md).' },
  { label: 'A figure from Iyer et al. 2020', dataset: 'adk_transition',
    figure: '/figures/iyer2020_fig5c_adk_difference_contact_map.png', caption: '/figures/iyer2020_fig5c_caption.txt',
    question: 'Apply this figure’s method to our data.',
    note: 'Iyer M et al. (2020) PLOS ONE 15(3): e0226702, Fig 5C, CC BY (see assets/figures/ATTRIBUTION.md).' },
]
const QUESTION_EXAMPLES = [
  { label: 'Which LID–CORE contacts change between the first and second half?', dataset: 'adk_equilibrium',
    question: 'Which LID–CORE contacts change between the first and second half of the run?' },
  { label: 'Is a big drop in one contact real?', dataset: 'adk_equilibrium',
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

/** Replay mode (AI off): say so, and offer the recorded real investigations. Charts and 3D stay live. */
function ReplayBanner({ busy, onQuestion, onFigure }: {
  busy: boolean; onQuestion: (q: string, ds: string) => void; onFigure: (f: File, ds: string, label: string) => void
}) {
  const [info, setInfo] = useState<{ mode: string; recorded: Recorded[]; reason: string | null } | null>(null)
  useEffect(() => {
    Promise.all([api.replays(), api.health()]).then(([r, h]) => setInfo({ ...r, reason: h.mode_reason }), () => {})
  }, [])
  if (!info || info.mode !== 'replay') return null
  const openFigure = async (r: Recorded) => {
    const blob = await (await fetch(api.figureUrl(r.figure_id!))).blob()
    onFigure(new File([blob], 'figure.png', { type: blob.type || 'image/png' }), r.dataset, r.label)
  }
  return (
    <div className="replay-banner">
      <p><strong>The AI is off right now</strong>{info.reason ? ` (${info.reason})` : ''}. You can watch recorded real
        investigations, replayed step by step; the charts and the 3D viewer are live.</p>
      <div className="example-row">
        {info.recorded.map((r) => (
          <button key={r.run_id} className="example-chip" disabled={busy}
            onClick={() => (r.figure ? void openFigure(r) : onQuestion(r.question, r.dataset))}>{r.label}</button>
        ))}
      </div>
    </div>
  )
}

export function StartScreen({ datasets, dataset, meta }: { datasets: DatasetInfo[]; dataset: string; meta: Metadata | null }) {
  const { phase, needPasscode, unsupported, declined } = useInvestigation()
  const [question, setQuestion] = useState('')
  const [note, setNote] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [fileError, setFileError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const busy = phase === 'planning'
  useEffect(() => onTranscript((text) => { clearNotices(); setQuestion(text) }), []) // spoken question, to review

  const addFiles = (list: FileList | File[]) => {
    clearNotices()
    const picked = [...files, ...Array.from(list)].slice(-2)
    const bad = picked.find((f) => !['image/png', 'image/jpeg', 'application/pdf'].includes(f.type) || f.size > MAX_UPLOAD_BYTES)
    setFileError(bad ? `${bad.name}: use a PNG, JPEG or PDF under 10 MB` : null)
    if (!bad) setFiles(picked)
  }

  const startFigure = (attached: File[], ds = dataset, text = note, label?: string) => {
    if (busy || !attached.length) return
    if (ds !== dataset) setSelection({ dataset: ds })
    void askFigure(attached, ds, text.trim(), label)
    setFiles([])
    setNote('')
  }

  const startQuestion = (q: string, ds = dataset) => {
    if (busy || !q.trim()) return
    if (ds !== dataset) setSelection({ dataset: ds })
    void ask(q.trim(), ds)
    setQuestion('')
  }

  // A sample ships with its caption and methods text when the image alone does not carry them.
  const runFigureExample = async (ex: (typeof FIGURE_EXAMPLES)[number]) => {
    const blob = await (await fetch(ex.figure)).blob()
    const file = new File([blob], ex.figure.split('/').pop() ?? 'figure.png', { type: 'image/png' })
    const caption = 'caption' in ex && ex.caption ? await (await fetch(ex.caption)).text() : ''
    startFigure([file], ex.dataset, caption ? `${ex.question} ${caption}` : ex.question, ex.question)
  }

  return (
    <section className="start">
      <div className="start-main">
        <ReplayBanner busy={busy} onQuestion={startQuestion} onFigure={(f, ds, label) => startFigure([f], ds, '', label)} />
        <h1 className="start-title">Ask what a protein is doing</h1>
        <p className="start-lede">
          Drop in a figure from any paper, or ask a question. TrajectoryGPT runs the analysis on real simulations of
          the enzyme adenylate kinase.
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
                <span className="muted small">PNG, JPEG or PDF</span>
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
        {unsupported && <UnsupportedNotice message={unsupported.message} figure={unsupported.figure} onDismiss={clearNotices} />}
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
              {ex.label}
            </button>
          ))}
        </div>

        <div className="or-ask">
          <h2 className="or-title">Or ask a question about the simulations</h2>
          {declined && (
            <div className="declined" role="status">
              <p><span className="muted">You asked “{declined.question}”.</span> {declined.message}</p>
              <div className="example-row">
                {declined.examples.map((q) => (
                  <button key={q} className="example-chip" disabled={busy} onClick={() => startQuestion(q, 'adk_equilibrium')}>{q}</button>
                ))}
              </div>
            </div>
          )}
          <form className="ask" onSubmit={(e) => { e.preventDefault(); startQuestion(question) }}>
            <input
              className="ask-input" value={question} onChange={(e) => { clearNotices(); setQuestion(e.target.value) }} disabled={busy}
              placeholder="Which contacts change between the first and second half?" aria-label="Question"
            />
            <MicButton />
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
          <select id="ds" value={dataset} onChange={(e) => { clearNotices(); setSelection({ dataset: e.target.value }) }} disabled={busy}>
            {datasets.map((d) => <option key={d.id} value={d.id}>AdK · {datasetName(d.id)}</option>)}
          </select>
          <ModelToggle disabled={busy} />
        </div>
      </div>
      <div className="start-art">{meta && <SpinningStructure meta={meta} />}<VoiceHint /></div>
    </section>
  )
}
