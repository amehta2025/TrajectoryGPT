import { useEffect, useState } from 'react'
import { api, type DatasetInfo, type Metadata } from './api/client'
import { useSelection } from './state/selection'
import { reset, useInvestigation } from './state/investigation'
import { DatasetBadge } from './components/DatasetBadge'
import { Heatmap } from './components/Heatmap'
import { CompareChart } from './components/CompareChart'
import { DistancePlot } from './components/DistancePlot'
import { Viewer } from './components/Viewer'
import { PasscodePrompt, StartScreen } from './components/StartScreen'
import { PlanCard } from './components/PlanCard'
import { AgentTrace } from './components/StepLog'
import { Headline, ReportView, ScienceReport, ScienceToggle } from './components/ReportView'
import { CostMeter } from './components/CostMeter'
import { ReproduceView } from './components/ReproduceView'
import { AnalysisCards } from './components/AnalysisViews'
import { DOMAIN_COLORS } from './theme'
import { hisText } from './state/residueKey'
import { VoiceHint, VoiceLayer } from './components/VoiceInput'

function Structure({ meta }: { meta: Metadata }) {
  return (
    <section className="card viewer-card">
      <header className="card-head">
        <h2>Structure</h2>
        <VoiceHint />
        <div className="legend">
          {Object.entries(meta.groups).map(([g, { ranges }]) => (
            <span key={g} title={ranges.map(([a, b]) => `${a}–${b}`).join(', ')}>
              <i style={{ background: DOMAIN_COLORS[g] }} /> {g}
            </span>
          ))}
        </div>
      </header>
      <Viewer meta={meta} />
    </section>
  )
}

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
      .then(setDatasets, (e) => setError(`Backend unreachable: ${(e as Error).message}`))
      .finally(() => {
        window.clearTimeout(slow)
        setWaking(false)
      })
  }, [])

  useEffect(() => {
    let cancelled = false
    setMeta(null)
    api.metadata(dataset).then((m) => !cancelled && setMeta(m), (e) => !cancelled && setError((e as Error).message))
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
          TrajectoryGPT <span className="muted brand-sub">MD research copilot</span>
        </button>
        <div className="topbar-right">
          <CostMeter />
          {asking && <DatasetBadge meta={meta} dataset={dataset} />}
          {asking && <button className="new-q" onClick={reset}>New question</button>}
        </div>
      </header>
      <VoiceLayer />
      {waking && !error && (
        <div className="waking banner" role="status">Starting the server. After a quiet spell this takes a few seconds.</div>
      )}
      {error && <div className="error banner">{error}</div>}
      {inv.error && !asking && !inv.needPasscode && <div className="error banner">{inv.error}</div>}

      {!asking && <StartScreen datasets={datasets} dataset={dataset} meta={meta} />}
      {asking && (
        <main className="inv">
          <div className="inv-head">
            {inv.needPasscode && <PasscodePrompt />}
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
            <Headline meta={meta} />
            <AgentTrace meta={meta} />
          </div>
          {!started && (
            <div className={inv.proposal?.figure || inv.figureRead ? 'plan-only with-figure' : 'plan-only'}>
              <ReproduceView />
              <PlanCard meta={meta} />
            </div>
          )}
          {started && (
            <>
              <span id="evidence" />
              {inv.run?.figure && <ReproduceView />}
              <div className="inv-body">
                {meta ? <Structure meta={meta} /> : <div className="muted loading">Loading…</div>}
                <div className="inv-col">
                  {!inv.run?.figure && <ReproduceView />}
                  <ReportView meta={meta} />
                  {inv.phase === 'running' && <PlanCard meta={meta} />}
                </div>
              </div>
              <ScienceToggle />
              {inv.science && meta && (
                <div className="science">
                  <ScienceReport meta={meta} />
                  <div className="evidence-more">
                    <CompareChart meta={meta} />
                    <div className="inv-col">
                      <AnalysisCards run={inv.run} />
                      <DistancePlot meta={meta} />
                      <Heatmap meta={meta} />
                    </div>
                  </div>
                </div>
              )}
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
