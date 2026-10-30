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
import { Headline, ReportView } from './components/ReportView'
import { CostMeter } from './components/CostMeter'
import { ReproduceView } from './components/ReproduceView'
import { DOMAIN_COLORS } from './theme'

function Structure({ meta }: { meta: Metadata }) {
  return (
    <section className="card viewer-card">
      <header className="card-head">
        <h2>Structure</h2>
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

export default function App() {
  const { dataset } = useSelection()
  const inv = useInvestigation()
  const [datasets, setDatasets] = useState<DatasetInfo[]>([])
  const [meta, setMeta] = useState<Metadata | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.datasets().then(setDatasets, (e) => setError(`Backend unreachable: ${(e as Error).message}`))
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
      {error && <div className="error banner">{error}</div>}
      {inv.error && !asking && !inv.needPasscode && <div className="error banner">{inv.error}</div>}

      {!asking && <StartScreen datasets={datasets} dataset={dataset} meta={meta} />}
      {asking && (
        <main className="inv">
          <div className="inv-head">
            {inv.needPasscode && <PasscodePrompt />}
            <span className="muted small">You asked</span>
            <h1 className="inv-question">{inv.question}</h1>
            <Headline />
            <AgentTrace meta={meta} />
          </div>
          {!started && <div className="plan-only"><PlanCard meta={meta} /></div>}
          {started && (
            <>
              <div className="inv-body">
                {meta ? <Structure meta={meta} /> : <div className="muted loading">Loading {dataset}…</div>}
                <div className="inv-col">
                  <ReproduceView />
                  <ReportView meta={meta} />
                  {inv.phase === 'running' && <PlanCard meta={meta} />}
                </div>
              </div>
              {meta && (
                <div className="evidence-more">
                  <CompareChart meta={meta} />
                  <div className="inv-col">
                    <DistancePlot meta={meta} />
                    <Heatmap meta={meta} />
                  </div>
                </div>
              )}
            </>
          )}
        </main>
      )}

      <footer className="foot muted small">
        {meta && (
          <>
            <p><span className="foot-label">Data</span>{meta.citation} ({meta.license})</p>
            <p><span className="foot-label">Domains</span>{meta.groups_source}</p>
          </>
        )}
      </footer>
    </div>
  )
}
