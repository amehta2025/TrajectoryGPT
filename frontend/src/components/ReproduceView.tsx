// Figure mode: their figure on the left, ours on the right, the figure type, the result (an exact numeric
// match against the published code, or the "method applied" label for a transfer), and what the AI read
// from the figure. It appears as soon as the figure is read, while the plan is still being drafted.
// Settings the AI inferred are marked; one "Confirm & run" on the plan accepts them all (the backend
// refuses a run until every low-confidence setting is confirmed).
import { useMemo } from 'react'
import type { Data } from 'plotly.js'
import { api, type Figure, type FigureSpec, type RunView } from '../api/client'
import { useInvestigation } from '../state/investigation'
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
      { type: 'scatter', mode: 'lines', x: d.frames, y: q1, name: 'Closed (Q1)', line: { color: '#5aa5f0', width: 1.5 } },
      { type: 'scatter', mode: 'lines', x: d.frames, y: q2, name: 'Open (Q2)', line: { color: '#f0a04f', width: 1.5 } },
      { type: 'scatter', mode: 'lines', x: q1, y: q2, xaxis: 'x2', yaxis: 'y2', showlegend: false,
        line: { color: '#5aa5f0', width: 1.2 }, hovertemplate: 'Closed-state %{x:.3f}<br>Open-state %{y:.3f}<extra></extra>' },
    ] as Data[]
  }, [d])
  return (
    <Plot
      data={traces}
      layout={{
        ...baseLayout, height: 270, margin: { l: 50, r: 10, t: 26, b: 40 }, showlegend: true,
        legend: { orientation: 'h', x: 0, y: 1.12, bgcolor: 'rgba(0,0,0,0)' },
        grid: { rows: 1, columns: 2, pattern: 'independent' },
        xaxis: { ...axisStyle, title: { text: 'Step along the path (frame)' } },
        yaxis: { ...axisStyle, title: { text: 'Fraction of reference contacts kept' } },
        xaxis2: { ...axisStyle, title: { text: 'Closed-state (Q1)' } },
        yaxis2: { ...axisStyle, title: { text: 'Open-state (Q2)' } },
      }}
      config={{ displaylogo: false, responsive: true }}
      style={{ width: '100%' }}
      useResizeHandler
    />
  )
}

/** Our version of the figure, from whichever result the run produced. */
export function Ours({ run }: { run: RunView | null }) {
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

const CONF_TEXT = { high: 'read clearly', medium: 'probably right', low: 'unsure' }
// Plain names for the settings the figure reader fills in (the technical value stays as read).
const FIELD_LABEL: Record<string, string> = {
  analysis: 'Analysis', protein: 'Protein', 'data source': 'Data', 'atom selection': 'Atoms compared',
  'cutoff or radius': 'Contact distance', 'reference frames or structures': 'Reference shapes',
  'contact definition': 'Contact rule', 'x axis': 'Horizontal axis', 'y axis': 'Vertical axis',
}
const MAIN_FIELDS = new Set(['analysis', 'protein', 'data source', 'cutoff or radius', 'reference frames or structures'])

/** What the AI read from the figure: the main settings, with the rest one click away. Inferred ones are marked. */
function SpecList({ figure, science }: { figure: Figure; science: boolean }) {
  const row = (f: Figure['spec']['fields'][number]) => (
    <li key={f.name} className={`spec-row conf-${f.confidence}`}>
      <span className="spec-name">{FIELD_LABEL[f.name] ?? f.name}</span>
      <span className="spec-value">{f.value}</span>
      <span className="spec-meta" title={`confidence: ${f.confidence} · read from: ${f.source}`}>
        {science ? `${CONF_TEXT[f.confidence]} · ${f.source}` : f.confidence === 'high' ? '' : 'inferred'}
      </span>
    </li>
  )
  const fields = figure.spec.fields
  const main = fields.filter((f) => MAIN_FIELDS.has(f.name) || f.confidence === 'low')
  const rest = fields.filter((f) => !main.includes(f))
  return (
    <>
      <ul className="spec-list">{main.map(row)}</ul>
      {rest.length > 0 && (
        <details className="fold spec-more">
          <summary>All settings read from the figure ({fields.length})</summary>
          <ul className="spec-list">{rest.map(row)}</ul>
        </details>
      )}
    </>
  )
}

export function ReproduceView() {
  const { proposal, run, phase, science, figureRead } = useInvestigation()
  const figure = run?.figure ?? proposal?.figure ?? figureRead
  if (!figure) return null
  const repro = lastData<Repro>(run ?? null, 'reproduction_check')
  const exact = figure.spec.mode === 'exact'
  const reviewing = phase === 'planning' || phase === 'plan'

  return (
    <section className="card reproduce">
      <header className="card-head">
        <h2>{reviewing ? 'Figure interpreted' : exact ? 'Reproducing a published figure' : 'Applying a figure’s method to our data'}</h2>
        {science && (
          <span className="repro-tags">
            <span className="tag">{KIND_LABEL[figure.spec.kind]}</span>
            <span className={`tag ${exact ? 'tag-strong' : ''}`}>{exact ? 'exact reproduction · same data' : 'method transfer'}</span>
          </span>
        )}
      </header>
      {science && <p className="repro-summary">{figure.spec.summary}{figure.spec.pdf_fallback ? ' (methods PDF read as text)' : ''}</p>}
      <div className="repro-grid">
        <figure>
          <figcaption className="muted small" title={figure.filename}>Published (from paper)</figcaption>
          <img src={api.figureUrl(figure.figure_id)} alt={`Uploaded figure: ${figure.spec.summary}`} />
        </figure>
        <figure>
          <figcaption className="muted small">Our result (reproduced)</figcaption>
          <Ours run={run ?? null} />
        </figure>
      </div>
      {exact && repro && (
        <div className={repro.match ? 'match ok' : 'match no'}>
          {repro.match
            ? <>✓ Reproduced: {repro.n_values} of {repro.n_values} values match the published code’s output.</>
            : <>✗ Not reproduced: some of the {repro.n_values} values differ from the published code’s output.</>}
          <span className="muted small"> Largest difference {repro.max_abs_diff.toExponential(1)} (allowed {repro.tolerance.toExponential(0)}).
            {science && <> Reference: {repro.reference_method}</>}</span>
        </div>
      )}
      {!exact && <div className="match transfer">Method applied to your data — numbers are not expected to match.</div>}
      {(reviewing || science) && (
        <>
          {reviewing && <p className="spec-intro">The AI read these settings from the figure. Review them before running{
            figure.spec.fields.some((f) => f.confidence !== 'high') ? '; the ones marked “inferred” are its best guess.' : '.'}</p>}
          <SpecList figure={figure} science={science} />
          {phase === 'planning' && <p className="muted small drafting">Drafting the analysis plan…</p>}
        </>
      )}
      {science && (
        <details className="fold">
          <summary>Why {exact ? 'exact reproduction' : 'method transfer'}</summary>
          <p className="small">{figure.spec.mode_reason}</p>
        </details>
      )}
    </section>
  )
}

/** Start screen notice when a figure was read but is not a type we can run yet. */
export function UnsupportedNotice({ message, figure, onDismiss }: { message: string; figure: Figure; onDismiss: () => void }) {
  return (
    <div className="unsupported" role="status">
      <img src={api.figureUrl(figure.figure_id)} alt="" />
      <div>
        <b>{message.split('. ')[0].replace(/\.$/, '')}.</b>
        <p className="muted small">Nothing was planned or run.</p>
      </div>
      <button className="link dismiss" onClick={onDismiss} aria-label="Dismiss">×</button>
    </div>
  )
}
