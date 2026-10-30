// Figure mode: their figure on the left, ours on the right, the extracted spec table, and the result:
// an exact numeric match against the published code, or the "method applied" label for a transfer.
import { useMemo } from 'react'
import type { Data } from 'plotly.js'
import { api, type Figure, type RunView } from '../api/client'
import { useInvestigation } from '../state/investigation'
import { Plot, axisStyle, baseLayout } from './Plot'

type Repro = {
  frames: number[]
  ours: number[][] // [frame][Q1, Q2]
  reference: number[][]
  match: boolean
  max_abs_diff: number
  n_values: number
  tolerance: number
  reference_method: string
}

function OurFigure({ d }: { d: Repro }) {
  const traces = useMemo<Data[]>(() => {
    const q1 = d.ours.map((q) => q[0])
    const q2 = d.ours.map((q) => q[1])
    return [
      { type: 'scatter', mode: 'lines', x: d.frames, y: q1, name: 'Q1', line: { color: '#5ac8e8', width: 1.5 } },
      { type: 'scatter', mode: 'lines', x: d.frames, y: q2, name: 'Q2', line: { color: '#e0a03a', width: 1.5 } },
      { type: 'scatter', mode: 'lines', x: q1, y: q2, xaxis: 'x2', yaxis: 'y2', showlegend: false,
        line: { color: '#5ac8e8', width: 1.2 }, hovertemplate: 'Q1 %{x:.3f}<br>Q2 %{y:.3f}<extra></extra>' },
    ] as Data[]
  }, [d])
  return (
    <Plot
      data={traces}
      layout={{
        ...baseLayout, height: 260, margin: { l: 50, r: 10, t: 10, b: 40 }, showlegend: true,
        legend: { x: 0.02, y: 0.05, bgcolor: 'rgba(0,0,0,0)' },
        grid: { rows: 1, columns: 2, pattern: 'independent' },
        xaxis: { ...axisStyle, title: { text: 'Frame' } },
        yaxis: { ...axisStyle, title: { text: 'Fraction of native contacts' } },
        xaxis2: { ...axisStyle, title: { text: 'Q1' } },
        yaxis2: { ...axisStyle, title: { text: 'Q2' } },
      }}
      config={{ displaylogo: false, responsive: true }}
      style={{ width: '100%' }}
      useResizeHandler
    />
  )
}

function SpecTable({ figure }: { figure: Figure }) {
  const s = figure.spec
  return (
    <table className="stats spec">
      <thead><tr><th>Setting</th><th>Value</th><th>Confidence</th><th>Read from</th></tr></thead>
      <tbody>
        {s.fields.map((f) => (
          <tr key={f.name} className={f.source === 'assumed' ? 'greyed' : ''}>
            <td>{f.name}</td><td>{f.value}</td><td>{f.confidence}</td><td>{f.source}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function ReproduceView() {
  const { proposal, run } = useInvestigation()
  const figure = run?.figure ?? proposal?.figure
  if (!figure) return null
  const repro = run ? Object.values(run.results).find((r) => r.tool === 'reproduction_check' && r.status === 'ok') : undefined
  const d = (repro as (RunView['results'][string] & { data_for_ui?: Repro }) | undefined)?.data_for_ui
  const exact = figure.spec.mode === 'exact'

  return (
    <section className="card reproduce">
      <header className="card-head">
        <h2>Reproduce a figure</h2>
        <span className={`pill ${exact ? 'ok' : ''}`}>{exact ? `exact reproduction · ${figure.spec.dataset}` : 'method transfer'}</span>
      </header>
      <p className="muted small">{figure.spec.summary}{figure.spec.pdf_fallback ? ' (methods PDF read as text)' : ''}</p>
      <div className="repro-grid">
        <figure>
          <figcaption className="muted small">Their figure ({figure.filename})</figcaption>
          <img src={api.figureUrl(figure.figure_id)} alt={`Uploaded figure: ${figure.spec.summary}`} />
        </figure>
        <figure>
          <figcaption className="muted small">Ours</figcaption>
          {d ? <OurFigure d={d} /> : <div className="muted small placeholder">{run ? 'Computing…' : 'Appears once the plan runs.'}</div>}
        </figure>
      </div>
      {exact && d && (
        <div className={d.match ? 'match ok' : 'match no'}>
          {d.match ? '✓ Matches the published code' : '✗ Does not match the published code'}: largest difference{' '}
          {d.max_abs_diff.toExponential(1)} over {d.n_values} values (tolerance {d.tolerance.toExponential(0)}).
          <span className="muted small"> Reference: {d.reference_method}</span>
        </div>
      )}
      {!exact && <div className="match transfer">Method applied to your data — numbers are not expected to match.</div>}
      <details>
        <summary className="small">Extracted spec ({figure.spec.fields.length} settings) · {figure.spec.mode_reason}</summary>
        <SpecTable figure={figure} />
      </details>
    </section>
  )
}
