// What the user sees before any investigation: one headline, one box (drop a figure or type a question,
// then Analyze), two example cards. Dataset and model choice sit under "Advanced settings". An example card
// only fills the box; nothing reaches the AI until the user presses Analyze. Users never upload trajectories
// (README: Known limitations), and the copy never implies they can. A sample never puts words in the
// user's mouth: its card title is the heading.
import { useEffect, useRef, useState } from 'react'
import { type DatasetInfo, type Metadata } from '../api/client'
import { ask, askFigure, clearNotices, MAX_UPLOAD_BYTES, submitPasscode, useInvestigation } from '../state/investigation'
import { setSelection } from '../state/selection'
import { datasetName } from './DatasetBadge'
import { ModelToggle } from './ModelToggle'
import { MicButton } from './VoiceInput'
import { onTranscript } from '../state/voice'
import { SpinningStructure } from './Viewer'
import { UnsupportedNotice } from './ReproduceView'

type FigureExample = { title: string; sub: string; kicker: string; dataset: string; figure: string; note: string; caption?: string; source: string }
type QuestionExample = { title: string; sub: string; dataset: string; question: string }

// What a sample sends to the AI (note) is an instruction; the page shows the card title, never "You asked".
const FIGURE_SAMPLE: FigureExample = {
  title: 'Reproduce a published figure', sub: 'See if our agent can recreate a result from the MDAnalysis guide.',
  kicker: 'Sample figure · MDAnalysis User Guide', dataset: 'adk_transition', figure: '/figures/userguide_q1q2_replot.png',
  note: 'Reproduce this figure.', source: 'MDAnalysis User Guide Q1 vs Q2 figure, regenerated from its published code (see assets/figures/ATTRIBUTION.md).',
}
const QUESTION_SAMPLE: QuestionExample = {
  title: 'Find changing interactions', sub: 'See which parts of the protein move and interact more or less.',
  dataset: 'adk_equilibrium', question: 'Which LID–CORE contacts change between the first and second half of the run?',
}
// An example the user has loaded into the box but not run yet. Editing the box turns it back into their own input.
type Loaded = { kind: 'figure'; ex: FigureExample; file: File; note: string } | { kind: 'question'; ex: QuestionExample }

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
        <span className="muted small">{question ? <>“{question}” will start right after.</> : 'Charts and the 3D view stay open without it.'}</span>
      </div>
      <input type="password" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Passcode" aria-label="Demo passcode" autoFocus />
      <button type="submit" className="primary" disabled={!code.trim() || checking}>{checking ? 'Checking…' : 'Unlock'}</button>
      {error && <span className="error-text small">{error}</span>}
    </form>
  )
}

export function StartScreen({ datasets, dataset, meta }: { datasets: DatasetInfo[]; dataset: string; meta: Metadata | null }) {
  const { phase, needPasscode, unsupported, declined } = useInvestigation()
  const [question, setQuestion] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [fileError, setFileError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const busy = phase === 'planning'
  useEffect(() => onTranscript((text) => { clearNotices(); setLoaded(null); setQuestion(text) }), []) // spoken question, to review

  const addFiles = (list: FileList | File[]) => {
    clearNotices()
    setLoaded(null)
    const picked = [...files, ...Array.from(list)].slice(-2)
    const bad = picked.find((f) => !['image/png', 'image/jpeg', 'application/pdf'].includes(f.type) || f.size > MAX_UPLOAD_BYTES)
    setFileError(bad ? `${bad.name}: use a PNG, JPEG or PDF under 10 MB` : null)
    if (!bad) setFiles(picked)
  }

  const clearBox = () => {
    setFiles([])
    setQuestion('')
    setLoaded(null)
  }

  // One button: with a figure attached the text is a note about it; without, it is the question.
  // A loaded example keeps its own heading ("Sample figure", "Example question"), never "You asked".
  const analyze = () => {
    if (busy) return
    if (loaded?.kind === 'figure') {
      const note = question.trim() ? `${loaded.note} ${question.trim()}` : loaded.note
      void askFigure([loaded.file], dataset, note, { label: loaded.ex.title, kicker: loaded.ex.kicker })
    } else if (files.length) {
      void askFigure(files, dataset, question.trim())
    } else if (question.trim()) {
      void ask(question.trim(), dataset, false, false, loaded ? 'Example question' : 'You asked')
    } else return
    clearBox()
  }

  // An example fills the box and switches to its dataset; the user reviews it and presses Analyze.
  // A sample figure ships with its caption and methods text when the image alone does not carry them.
  const loadFigureExample = async (ex: FigureExample) => {
    if (busy) return
    clearNotices()
    setFileError(null)
    try {
      const blob = await (await fetch(ex.figure)).blob()
      const file = new File([blob], ex.figure.split('/').pop() ?? 'figure.png', { type: 'image/png' })
      const caption = ex.caption ? await (await fetch(ex.caption)).text() : ''
      if (ex.dataset !== dataset) setSelection({ dataset: ex.dataset })
      setFiles([file])
      setQuestion('')
      setLoaded({ kind: 'figure', ex, file, note: caption ? `${ex.note} ${caption}` : ex.note })
      input.current?.focus()
    } catch {
      setFileError('Could not load the sample figure. Try again.')
    }
  }
  const loadQuestionExample = (ex: QuestionExample) => {
    if (busy) return
    clearNotices()
    setFileError(null)
    if (ex.dataset !== dataset) setSelection({ dataset: ex.dataset })
    setFiles([])
    setQuestion(ex.question)
    setLoaded({ kind: 'question', ex })
    input.current?.focus()
  }

  return (
    <section className="start">
      <div className="start-main">
        {needPasscode && <PasscodePrompt />}
        <h1 className="start-title">Turn scientific figures into <span className="hl">reproducible insights.</span></h1>
        <p className="start-lede">
          Upload a figure from a paper or ask a question. An AI agent analyzes molecular simulations, recreates the
          results, and explains what it finds.
        </p>

        <div
          className={`composer ${dragging ? 'dragging' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files) }}
        >
          <label className="dz-row">
            <input type="file" accept="image/png,image/jpeg,application/pdf" multiple hidden disabled={busy}
              onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = '' }} />
            <span className="dz-icon" aria-hidden>⤒</span>
            {files.length === 0
              ? <span className="dz-text"><span>Drop a figure here or <u>choose a file</u></span><span className="muted small">PNG, JPEG, or PDF</span></span>
              : <span className="attached">
                  {files.map((f) => (
                    <span key={f.name} className="pill">
                      {f.name}{' '}
                      <button type="button" className="link" onClick={(e) => { e.preventDefault(); setLoaded(null); setFiles(files.filter((x) => x !== f)) }} aria-label={`Remove ${f.name}`}>×</button>
                    </span>
                  ))}
                  <span className="muted small">{loaded ? 'Sample figure loaded' : 'Add the methods PDF too, if you have it'}</span>
                </span>}
          </label>
          <div className="or-line"><span>or</span></div>
          <form className="ask" onSubmit={(e) => { e.preventDefault(); analyze() }}>
            <span className="ask-icon" aria-hidden>◌</span>
            <input
              ref={input} className="ask-input" value={question} disabled={busy}
              onChange={(e) => { clearNotices(); if (loaded?.kind === 'question') setLoaded(null); setQuestion(e.target.value) }}
              placeholder={files.length ? 'Optional: what should the agent take from this figure?' : 'Ask a question, e.g., “Which interactions change during the simulation?”'}
              aria-label={files.length ? 'Note about the figure' : 'Question'}
            />
            <MicButton />
            <button type="submit" className="primary" disabled={busy || (!files.length && !question.trim())}>{busy ? 'Working…' : 'Analyze →'}</button>
          </form>
        </div>
        {loaded && (
          <p className="loaded-note muted small">
            Example loaded. Review it, then press Analyze. <button type="button" className="link" onClick={clearBox}>Clear</button>
          </p>
        )}
        {fileError && <span className="error-text small">{fileError}</span>}
        {unsupported && <UnsupportedNotice message={unsupported.message} figure={unsupported.figure} onDismiss={clearNotices} />}
        {declined && (
          <div className="declined" role="status">
            <p><span className="muted">You asked “{declined.question}”.</span> {declined.message}</p>
            {!!declined.choices?.length && (
              <div className="example-row">
                {declined.choices.map((c) => (
                  <button key={c.label} className="example-chip" disabled={busy} title={c.question}
                    onClick={() => void ask(c.question, dataset, declined.followUp)}>{c.label}</button>
                ))}
              </div>
            )}
            {!!declined.examples.length && (
              <div className="example-row">
                {declined.examples.map((q) => (
                  <button key={q} className="example-chip" disabled={busy}
                    onClick={() => loadQuestionExample({ title: q, sub: '', dataset: 'adk_equilibrium', question: q })}>{q}</button>
                ))}
              </div>
            )}
            {declined.ask_anyway && (
              <p className="small"><button className="link" disabled={busy} onClick={() => void ask(declined.question, dataset, declined.followUp, true)}>
                Ask anyway</button> <span className="muted">if this is about the simulation, and I'll propose a plan you can check.</span></p>
            )}
          </div>
        )}

        <div className="try">
          <h2 className="try-title">Try an example</h2>
          <div className="example-cards">
            <button className={`example-card ${loaded?.ex === FIGURE_SAMPLE ? 'selected' : ''}`} disabled={busy}
              onClick={() => void loadFigureExample(FIGURE_SAMPLE)} title={FIGURE_SAMPLE.source}>
              <span className="ec-icon fig" aria-hidden>⟋</span>
              <span className="ec-body"><span className="ec-title">{FIGURE_SAMPLE.title}</span><span className="ec-sub">{FIGURE_SAMPLE.sub}</span></span>
              <span className="ec-go" aria-hidden>›</span>
            </button>
            <button className={`example-card ${loaded?.ex === QUESTION_SAMPLE ? 'selected' : ''}`} disabled={busy}
              onClick={() => loadQuestionExample(QUESTION_SAMPLE)} title={QUESTION_SAMPLE.question}>
              <span className="ec-icon q" aria-hidden>⚯</span>
              <span className="ec-body"><span className="ec-title">{QUESTION_SAMPLE.title}</span><span className="ec-sub">{QUESTION_SAMPLE.sub}</span></span>
              <span className="ec-go" aria-hidden>›</span>
            </button>
          </div>
        </div>

        <div className="start-foot">
          <span className="small real-data">✓ Real simulation data: two public trajectories of adenylate kinase</span>
          <details className="advanced">
            <summary className="small">Advanced settings ›</summary>
            <div className="ds-picker">
              <label className="muted small" htmlFor="ds">Data</label>
              <select id="ds" value={dataset} onChange={(e) => { clearNotices(); setSelection({ dataset: e.target.value }) }} disabled={busy}>
                {datasets.map((d) => <option key={d.id} value={d.id}>AdK · {datasetName(d.id)}</option>)}
              </select>
              <span className="muted small">Model</span>
              <ModelToggle disabled={busy} />
            </div>
          </details>
        </div>
      </div>
      <div className="start-art">
        {meta && <SpinningStructure meta={meta} />}
      </div>
    </section>
  )
}
