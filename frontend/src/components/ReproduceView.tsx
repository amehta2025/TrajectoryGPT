// Figure mode: their figure on the left, ours on the right, the figure type, the result (an exact numeric
// match against the published code, or the "method applied" label for a transfer), and what was read
// from the figure. Every low-confidence setting must be ticked before the plan can run; the backend
// refuses the run otherwise.
import { useMemo } from 'react'
import type { Data } from 'plotly.js'
import { api, type Figure, type FigureSpec, type RunView } from '../api/client'
import { toggleConfirmed, useInvestigation } from '../state/investigation'
import { DifferenceMap, FlexibilityPlot, lastData, type DiffData, type FlexData } from './AnalysisViews'
import { Plot, axisStyle, baseLayout } from './Plot'

export const KIND_LABEL: Record<FigureSpec['kind'], string> = {
  native_contacts: 'native contacts', contact_map: 'contact map', difference_contact_map: 'difference contact map',
  distance_trace: 'distance trace', rmsf: 'RMSF (flexibility)', other: 'not a supported type',
}

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

function NativeContactsFigure({ d }: { d: Repro }) {
  const traces = useMemo<Data[]>(() => {
    const q1 = d.ours.map((q) => q[0])
    const q2 = d.ours.map((q) => q[1])
    return [
      { type: 'scatter', mode: 'lines', x: d.frames, y: q1, name: 'Q1', line: { color: '#dde2ea', width: 1.5 } },
      { type: 'scatter', mode: 'lines', x: d.frames, y: q2, name: 'Q2', line: { color: '#9cc3e6', width: 1.5, dash: 'dot' } },
      { type: 'scatter', mode: 'lines', x: q1, y: q2, xaxis: 'x2', yaxis: 'y2', showlegend: false,
        line: { color: '#dde2ea', width: 1.2 }, hovertemplate: 'Q1 %{x:.3f}<br>Q2 %{y:.3f}<extra></extra>' },
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

/** Our version of the figure, from whichever result the run produced. */
function Ours({ run }: { run: RunView | null }) {
  const repro = lastData<Repro>(run, 'reproduction_check')
  const diff = lastData<DiffData>(run, 'contact_difference_map')
  const flex = lastData<FlexData>(run, 'residue_flexibility')
  if (repro) return <NativeContactsFigure d={repro} />
  if (diff) return <DifferenceMap d={diff} height={260} />
  if (flex) return <FlexibilityPlot d={flex} height={260} />
  if (run && Object.values(run.results).some((r) => r.status === 'ok' && (r.tool === 'calculate_contacts' || r.tool === 'distance_over_time'))) {
    return <div className="muted small placeholder">Shown in the contact map and distance plot below.</div>
  }
  return <div className="muted small placeholder">{run ? 'Computing…' : 'Appears once the plan runs.'}</div>
}

const CONF_TEXT = { high: 'read clearly', medium: 'probably right', low: 'unsure: please check' }

/** What was read from the figure; low-confidence settings get a tick box until a run starts. */
function SpecList({ figure, editable }: { figure: Figure; editable: boolean }) {
  const { confirmed } = useInvestigation()
  return (
    <ul className="spec-list">
      {figure.spec.fields.map((f) => {
        const low = f.confidence === 'low'
        const ok = confirmed.includes(f.name)
        return (
          <li key={f.name} className={`spec-row conf-${f.confidence} ${low && !ok ? 'needs-check' : ''}`}>
            <span className="spec-name">{f.name}</span>
            <span className="spec-value">{f.value}</span>
            <span className="spec-meta" title={`confidence: ${f.confidence} · read from: ${f.source}`}>{CONF_TEXT[f.confidence]} · {f.source}</span>
            {low
              ? (
                <label className="spec-check">
                  <input type="checkbox" checked={ok} disabled={!editable} onChange={() => toggleConfirmed(f.name)} /> Looks right
                </label>
              )
              : <span />}
          </li>
        )
      })}
    </ul>
  )
}

export function ReproduceView() {
  const { proposal, run, phase } = useInvestigation()
  const figure = run?.figure ?? proposal?.figure
  if (!figure) return null
  const repro = lastData<Repro>(run ?? null, 'reproduction_check')
  const exact = figure.spec.mode === 'exact'
  const nLow = figure.spec.fields.filter((f) => f.confidence === 'low').length

  return (
    <section className="card reproduce">
      <header className="card-head">
        <h2>Reproduce a figure</h2>
        <span className="repro-tags">
          <span className="tag">{KIND_LABEL[figure.spec.kind]}</span>
          <span className={`tag ${exact ? 'tag-strong' : ''}`}>{exact ? `exact reproduction · same data` : 'method transfer'}</span>
        </span>
      </header>
      <p className="repro-summary">{figure.spec.summary}{figure.spec.pdf_fallback ? ' (methods PDF read as text)' : ''}</p>
      <div className="repro-grid">
        <figure>
          <figcaption className="muted small">Theirs · {figure.filename}</figcaption>
          <img src={api.figureUrl(figure.figure_id)} alt={`Uploaded figure: ${figure.spec.summary}`} />
        </figure>
        <figure>
          <figcaption className="muted small">Ours</figcaption>
          <Ours run={run ?? null} />
        </figure>
      </div>
      {exact && repro && (
        <div className={repro.match ? 'match ok' : 'match no'}>
          {repro.match ? '✓ Matches the published code' : '✗ Does not match the published code'}: largest difference{' '}
          {repro.max_abs_diff.toExponential(1)} over {repro.n_values} values (tolerance {repro.tolerance.toExponential(0)}).
          <span className="muted small"> Reference: {repro.reference_method}</span>
        </div>
      )}
      {!exact && <div className="match transfer">Method applied to your data — numbers are not expected to match.</div>}
      <h3 className="spec-head">
        What we read from the figure
        {nLow > 0 && phase === 'plan' && <span className="muted small"> · tick the {nLow} unsure {nLow === 1 ? 'setting' : 'settings'} before running</span>}
      </h3>
      <SpecList figure={figure} editable={phase === 'plan'} />
      <details className="fold">
        <summary>Why {exact ? 'exact reproduction' : 'method transfer'}</summary>
        <p className="small">{figure.spec.mode_reason}</p>
      </details>
    </section>
  )
}

/** Start screen notice when a figure was read but is not a type we can run yet. */
export function UnsupportedNotice({ message, figure }: { message: string; figure: Figure }) {
  return (
    <div className="unsupported">
      <img src={api.figureUrl(figure.figure_id)} alt="" />
      <div>
        <b>{message}</b>
        <p className="muted small">What we saw: {figure.spec.summary} Nothing was planned or run.</p>
      </div>
    </div>
  )
}
