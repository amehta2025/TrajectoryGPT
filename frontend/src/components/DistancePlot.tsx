// Min heavy-atom distance vs time for the selected pair. Clicking a point jumps the viewer.
import { useEffect, useMemo, useState } from 'react'
import type { Data, Shape } from 'plotly.js'
import { api, type Distances, type Metadata } from '../api/client'
import { setSelection, useSelection } from '../state/selection'
import { residueLabel } from '../state/residueKey'
import { frameTimeLabel, timeAxisTitle } from '../state/time'
import { Plot, axisStyle, baseLayout } from './Plot'

const CUTOFF = 4.5 // matches the heatmap until cutoff becomes an approved plan parameter (Day 4)

export function DistancePlot({ meta }: { meta: Metadata }) {
  const { dataset, pair, frame } = useSelection()
  const [data, setData] = useState<Distances | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setData(null)
    setError(null)
    if (!pair) return
    let cancelled = false
    api.distances(dataset, pair.keyA, pair.keyB).then(
      (d) => !cancelled && setData(d),
      (e) => !cancelled && setError((e as Error).message),
    )
    return () => {
      cancelled = true
    }
  }, [dataset, pair])

  const x = useMemo(() => (data ? (data.time_ns ?? data.frames) : []), [data])

  const traces = useMemo<Data[]>(() => {
    if (!data) return []
    return [{
      type: 'scattergl', mode: 'lines+markers', x, y: data.distance, customdata: data.frames,
      line: { color: '#5ac8e8', width: 1 }, marker: { size: 3, color: '#5ac8e8' },
      hovertemplate: `frame %{customdata} · %{x}${data.time_ns ? ' ns' : ''}<br>%{y:.2f} Å<extra>click to jump</extra>`,
    }] as Data[]
  }, [data, x])

  const shapes = useMemo<Partial<Shape>[]>(() => {
    if (!data) return []
    const xf = meta.dt_ns === null ? frame : frame * meta.dt_ns
    return [
      { type: 'line', xref: 'paper', x0: 0, x1: 1, y0: CUTOFF, y1: CUTOFF, line: { color: '#ff9f43', dash: 'dash', width: 1.5 } },
      { type: 'line', yref: 'paper', y0: 0, y1: 1, x0: xf, x1: xf, line: { color: '#ffffff', width: 1 } },
    ]
  }, [data, frame, meta.dt_ns])

  return (
    <section className="card">
      <header className="card-head">
        <h2>Distance over time</h2>
        {pair && <span className="muted">{residueLabel(pair.keyA)} – {residueLabel(pair.keyB)} · min heavy-atom distance</span>}
      </header>
      <p className="card-sub">
        Dashed line: cutoff {CUTOFF} Å · white line: viewer frame {frame} ({frameTimeLabel(meta, frame)}) · click any point to jump
        {pair?.excluded && <span className="warn"> · this pair is sequence-local (excluded from contact counts)</span>}
      </p>
      {error && <div className="error">{error}</div>}
      <div className="plot-wrap distance-wrap">
        {!pair && <div className="overlay">Click a heatmap cell to plot that pair.</div>}
        {pair && !data && !error && <div className="overlay">Loading distances…</div>}
        {data && (
          <Plot
            data={traces}
            layout={{
              ...baseLayout,
              hovermode: 'x',
              xaxis: { ...axisStyle, title: { text: timeAxisTitle(meta) } },
              yaxis: { ...axisStyle, title: { text: 'distance (Å)' }, rangemode: 'tozero' },
              shapes,
            }}
            config={{ displaylogo: false, responsive: true }}
            useResizeHandler
            style={{ width: '100%', height: '100%' }}
            onClick={(ev) => {
              const f = ev.points[0]?.customdata
              if (typeof f === 'number') setSelection({ frame: f })
            }}
          />
        )}
      </div>
    </section>
  )
}
