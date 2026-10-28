import { useEffect, useState } from 'react'
import { api, type DatasetInfo, type Metadata } from './api/client'
import { useSelection } from './state/selection'
import { DatasetBadge } from './components/DatasetBadge'
import { Heatmap } from './components/Heatmap'
import { DistancePlot } from './components/DistancePlot'
import { Viewer } from './components/Viewer'
import { DOMAIN_COLORS } from './theme'

export default function App() {
  const { dataset } = useSelection()
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

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◆</span> TrajectoryGPT
          <span className="muted small">adenylate kinase · MD research copilot</span>
        </div>
        <DatasetBadge datasets={datasets} meta={meta} dataset={dataset} />
      </header>
      {error && <div className="error banner">{error}</div>}
      {!meta && !error && <div className="muted loading">Loading {dataset}…</div>}
      {meta && (
        <main className="layout">
          <div className="col-left">
            <Heatmap meta={meta} />
            <DistancePlot meta={meta} />
          </div>
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
        </main>
      )}
      <footer className="foot muted small">
        {meta && <>Data: {meta.citation} ({meta.license}). Domains: {meta.groups_source}</>}
      </footer>
    </div>
  )
}
