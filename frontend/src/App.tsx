import { useEffect, useState } from 'react'
import { api, type DatasetInfo, type Metadata } from './api/client'
import { useSelection } from './state/selection'
import { reset, useInvestigation } from './state/investigation'
import { DatasetBadge } from './components/DatasetBadge'
import { Heatmap } from './components/Heatmap'
import { CompareChart } from './components/CompareChart'
import { DistancePlot } from './components/DistancePlot'
import { Viewer } from './components/Viewer'
import { StartScreen } from './components/StartScreen'
import { PlanCard } from './components/PlanCard'
import { StepLog } from './components/StepLog'
import { Timeline } from './components/Timeline'
import { ReportView } from './components/ReportView'
import { CostMeter } from './components/CostMeter'
import { DOMAIN_COLORS } from './theme'

function Results({ meta }: { meta: Metadata }) {
  return (
    <div className="results">
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
      <CompareChart meta={meta} />
      <Heatmap meta={meta} />
      <DistancePlot meta={meta} />
    </div>
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
        <button className="brand link" onClick={reset} title="New investigation">
          <span className="logo">◆</span> TrajectoryGPT
          <span className="muted small">adenylate kinase · MD research copilot</span>
        </button>
        <div className="topbar-right">
          <CostMeter />
          <DatasetBadge datasets={datasets} meta={meta} dataset={dataset} />
        </div>
      </header>
      {error && <div className="error banner">{error}</div>}
      {inv.error && !asking && <div className="error banner">{inv.error}</div>}

      {!asking && <StartScreen datasets={datasets} dataset={dataset} />}
      {asking && (
        <main className={started ? 'inv-layout' : 'inv-layout plan-only'}>
          <div className="inv-col">
            <StartScreen datasets={datasets} dataset={dataset} compact />
            <div className="question card"><span className="muted small">Question</span>{inv.question}</div>
            <PlanCard />
            <ReportView />
            <StepLog />
            <Timeline />
          </div>
          {started && (meta ? <Results meta={meta} /> : <div className="muted loading">Loading {dataset}…</div>)}
        </main>
      )}

      <footer className="foot muted small">
        {meta && <>Data: {meta.citation} ({meta.license}). Domains: {meta.groups_source}</>}
      </footer>
    </div>
  )
}
