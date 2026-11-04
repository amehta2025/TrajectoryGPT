import { useEffect, useState } from 'react'
import { api, type DatasetInfo, type Metadata } from './api/client'
import { useSelection } from './state/selection'
import { reset, useInvestigation } from './state/investigation'
import { DatasetBadge } from './components/DatasetBadge'
import { PasscodePrompt, StartScreen } from './components/StartScreen'
import { ErrorNote, PlanCard } from './components/PlanCard'
import { Headline } from './components/ReportView'
import { hisText } from './state/residueKey'
import { VoiceLayer } from './components/VoiceInput'
import { FigureSetup, ResultTabs, RunningView, Stepper } from './components/Workflow'

const WAKING_AFTER_MS = 2000

export default function App() {
  const { dataset } = useSelection()
  const inv = useInvestigation()
  const [datasets, setDatasets] = useState<DatasetInfo[]>([])
  const [meta, setMeta] = useState<Metadata | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [waking, setWaking] = useState(false)

  useEffect(() => {
    reset() // never show a previous result or error on load (also after a hot reload in development)
    // The backend scales to zero when idle; a cold start took about 6 s on Modal (measured 2026-11-02).
    const slow = window.setTimeout(() => setWaking(true), WAKING_AFTER_MS)
    api.datasets()
      .then(setDatasets, () => setError('Can’t reach the server right now. Try again in a minute.'))
      .finally(() => {
        window.clearTimeout(slow)
        setWaking(false)
      })
  }, [])

  useEffect(() => {
    let cancelled = false
    setMeta(null)
    api.metadata(dataset).then((m) => !cancelled && setMeta(m), () => !cancelled && setError('Couldn’t load the simulation data. Try again in a minute.'))
    return () => {
      cancelled = true
    }
  }, [dataset])

  const started = inv.phase === 'running' || inv.phase === 'finished'
  const asking = inv.phase !== 'idle'

  return (
    <div className="app">
      <header className="topbar">
        <button className="brand link" onClick={reset} title="Back to the start screen">
          <span className="brand-mark" aria-hidden>⟋⟋</span>TrajectoryGPT <span className="muted brand-sub">AI for molecular research</span>
        </button>
        <div className="topbar-right">
          {asking && <DatasetBadge meta={meta} dataset={dataset} />}
          {asking && <button className="new-q" onClick={reset}>New question</button>}
        </div>
      </header>
      <VoiceLayer />
      {waking && !error && (
        <div className="waking banner" role="status">Starting the server. After a quiet spell this takes a few seconds.</div>
      )}
      {error && <div className="error banner">{error}</div>}
      {inv.error && !asking && !inv.needPasscode && <ErrorNote className="error banner" error={inv.error} detail={inv.errorDetail} />}

      {!asking && <StartScreen datasets={datasets} dataset={dataset} meta={meta} />}
      {asking && (
        <main className="inv">
          <Stepper />
          <div className="inv-head">
            {inv.needPasscode && <PasscodePrompt />}
            {inv.notice && <p className="notice-line small" role="status">{inv.notice}</p>}
            {inv.proposal?.follow_up && (
              <span className="muted small follow-of">Following up on “{inv.proposal.follow_up.question}”
                {inv.proposal.follow_up.focus_pair && <> · about {hisText(inv.proposal.follow_up.focus_pair)}</>}</span>
            )}
            {(inv.run?.recorded_from || inv.proposal?.recorded) && (
              <span className="recorded-badge" title="Replayed from a real run recorded earlier; no AI model is called">Recorded run</span>
            )}
            {inv.proposal?.provider === 'baseten' && (
              <span className="open-badge" title={inv.proposal.model}>Open-weight model · Muse Spark Open on Baseten</span>
            )}
            <span className="muted small kicker">{inv.kicker}</span>
            <h1 className="inv-question">{inv.question}</h1>
          </div>
          {!started && (inv.proposal?.figure || inv.figureRead
            ? <FigureSetup meta={meta} />
            : <div className="plan-only"><PlanCard meta={meta} /></div>)}
          {inv.phase === 'running' && <RunningView meta={meta} />}
          {inv.phase === 'finished' && (
            <>
              <Headline meta={meta} />
              {meta ? <ResultTabs meta={meta} /> : <div className="muted loading">Loading…</div>}
            </>
          )}
        </main>
      )}

      <footer className="foot muted small">
        {meta && (
          <details>
            <summary>Data &amp; references</summary>
            <p><span className="foot-label">Data</span>{meta.citation} ({meta.license})</p>
            <p><span className="foot-label">Domains</span>{meta.groups_source}</p>
          </details>
        )}
      </footer>
    </div>
  )
}
