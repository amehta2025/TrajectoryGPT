// Figure mode: their figure on the left, ours on the right, the figure type, the result (an exact numeric
// match against the published code, or the "method applied" label for a transfer), and what the AI read
// from the figure. It appears as soon as the figure is read, while the plan is still being drafted.
// Settings the AI inferred are marked; one "Confirm & run" on the plan accepts them all (the backend
// refuses a run until every low-confidence setting is confirmed).
import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { Data, Layout } from 'plotly.js'
import { api, type Contacts, type Distances, type Figure, type FigureDisplay, type FigureSpec, type RunView } from '../api/client'
import { useInvestigation } from '../state/investigation'
import { colorRange, colorscaleFor, orientMap, timeAxis, withUnits } from '../state/paperFigure'
import { residueLabel } from '../state/residueKey'
import { lastData, type DiffData, type FlexData } from './AnalysisViews'
import { Plot } from './Plot'

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
  radius?: number
  changed?: Record<string, { published: unknown; ours: unknown }> // settings the user changed from the published ones
}
type NativeData = { frames: number[]; time_ns: number[] | null; q: number[][]; ref_frames: number[]; radius: number }

// ---------- our result, drawn the way papers draw theirs (white, black axes, matplotlib's default blue) ----------
// Each figure type has its own drawing, chosen from the figure's type; the axes, residue ranges, colour scale
// and reference line come from what the AI read off the published figure (spec.display), with plain defaults.

const INK = '#111'
const LINE = '#1f77b4'
const paperAxis = { showgrid: false, zeroline: false, showline: true, mirror: true, linecolor: INK, ticks: 'outside' as const, tickcolor: INK, color: INK }
const paperLayout: Partial<Layout> = {
  autosize: true, paper_bgcolor: '#fff', plot_bgcolor: '#fff', showlegend: false,
  font: { family: 'Inter, Arial, sans-serif', size: 11, color: INK }, margin: { l: 58, r: 14, t: 14, b: 44 },
  hoverlabel: { bgcolor: '#fff', bordercolor: '#888', font: { color: INK } },
}
const title = (text: string) => ({ text, font: { size: 11, color: INK } })

/** The published image's pixel size, so our panel can take the same aspect ratio. */
function useImageSize(src: string | null): { w: number; h: number } | null {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  useEffect(() => {
    if (!src) return
    const img = new Image()
    img.onload = () => img.naturalWidth && setSize({ w: img.naturalWidth, h: img.naturalHeight })
    img.src = src
  }, [src])
  return size
}

function useWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(0)
  useEffect(() => {
    if (!ref.current) return
    setW(ref.current.clientWidth) // at once: ResizeObserver only reports on the next rendered frame
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

// Matplotlib's defaults (10 pt text on a 6.4 in figure) make text about 2.2% of the figure's width; the paper's
// image is shown at the column width, so text at 2.2% of our width matches its apparent size. The paper's exact
// font size cannot be read off a picture; this is the usual default.
const FONT_SHARE = 0.022

/** A white, paper-style plot the same width and aspect ratio as the published figure beside it (PaperSize). */
function PaperPlot({ data, layout }: { data: Data[]; layout: Partial<Layout> }) {
  const size = useContext(PaperSize)
  const [ref, width] = useWidth()
  const height = size && width ? Math.round((width * size.h) / size.w) : 270
  const f = Math.max(8, Math.min(16, Math.round((width || 480) * FONT_SHARE)))
  const font = { size: f, color: INK }
  const axes = Object.fromEntries(Object.entries(layout).filter(([k]) => /^[xy]axis\d*$/.test(k)).map(([k, ax]) => {
    const a = (ax ?? {}) as Partial<NonNullable<Layout['xaxis']>>
    const t = typeof a.title === 'object' ? a.title : { text: a.title }
    return [k, { ...a, tickfont: font, title: { ...t, font } }]
  }))
  const sized = data.map((t) => ('colorbar' in t && t.colorbar
    ? { ...t, colorbar: { ...t.colorbar, thickness: Math.round(f * 1.2), tickfont: font, title: { ...(t.colorbar.title as object), font } } }
    : t)) as Data[]
  const legend = layout.legend ? { ...layout.legend, font } : undefined
  return (
    <div className="paper-plot" ref={ref}>
      {width > 0 && (
        <Plot data={sized} config={{ displaylogo: false, responsive: true }} style={{ width: '100%', height }}
          layout={{ ...paperLayout, font, ...layout, ...axes, ...(legend ? { legend } : {}), height,
            margin: { l: Math.round(f * 5.2), r: Math.round(f * 1.2), t: Math.round(f * (layout.showlegend && layout.legend?.orientation === 'h' ? 2.6 : 1.2)), b: Math.round(f * 3.8) } }} />
      )}
    </div>
  )
}
const PaperSize = createContext<{ w: number; h: number } | null>(null)
const EXCLUDED_GREY = '#d9d9d9'
const EXCLUDED_LABEL = 'excluded: too close in sequence'

/** Contact map or difference map: residues on both axes, laid out and cropped like the paper's. */
function MapFigure({ keysA, keysB, z, excluded, groupA, groupB, display, diverging }: {
  keysA: string[]; keysB: string[]; z: (number | null)[][]; excluded?: boolean[][]; groupA: string; groupB: string
  display?: FigureDisplay; diverging: boolean
}) {
  const o = useMemo(() => orientMap(keysA, keysB, z, groupA, groupB, display), [keysA, keysB, z, groupA, groupB, display])
  // Pairs skipped as too close in sequence (|Δresid| ≤ the approved exclusion): drawn light grey underneath, with a
  // legend entry, rather than left white like an empty cell.
  const ex = useMemo(() => {
    const m = excluded ?? z.map((row) => row.map((v) => v === null))
    return orientMap(keysA, keysB, m.map((row) => row.map((b) => (b ? 1 : null))), groupA, groupB, display).z
  }, [excluded, keysA, keysB, z, groupA, groupB, display])
  const anyExcluded = ex.some((row) => row.some((v) => v === 1))
  const [zmin, zmax] = colorRange(display, o.z.flat(), diverging)
  const text = o.keysY.map((ky) => o.keysX.map((kx) => `${residueLabel(kx)} – ${residueLabel(ky)}`))
  const data = [
    ...(anyExcluded ? [{
      type: 'heatmap', x: o.x, y: o.y, z: ex, text, colorscale: [[0, EXCLUDED_GREY], [1, EXCLUDED_GREY]], showscale: false,
      hovertemplate: `%{text}<br>${EXCLUDED_LABEL}<extra></extra>`, showlegend: false,
    }, {
      type: 'scatter', mode: 'markers', x: [null], y: [null], name: EXCLUDED_LABEL, showlegend: true, hoverinfo: 'skip',
      marker: { symbol: 'square', size: 11, color: EXCLUDED_GREY, line: { color: '#999', width: 1 } },
    }] : []),
    {
      type: 'heatmap', x: o.x, y: o.y, z: o.z, text, zmin, zmax, colorscale: colorscaleFor(display, diverging),
      hovertemplate: `%{text}<br>${diverging ? 'change' : 'frequency'} %{z:.2f}<extra></extra>`, xgap: 0, ygap: 0, showlegend: false,
      colorbar: { title: title(diverging ? 'Δ contact frequency (B − A)' : 'contact frequency'), outlinecolor: INK, tickcolor: INK },
    },
  ] as Data[]
  const label = (axis: FigureDisplay['x'] | undefined, g: string) => (axis && axis.group === g && axis.title ? axis.title : `${g} residue number`)
  return <PaperPlot data={data} layout={{
    showlegend: anyExcluded,
    legend: { orientation: 'h', x: 0, xanchor: 'left', y: 1.01, yanchor: 'bottom', bgcolor: 'rgba(0,0,0,0)' },
    xaxis: { ...paperAxis, title: title(label(display?.x, o.xGroup)) },
    yaxis: { ...paperAxis, title: title(label(display?.y, o.yGroup)) },
  }} />
}

/** A line over time (distance trace, native contacts), in the paper's time unit, with its reference line. */
function LineFigure({ frames, timeNs, series, yTitle, display, refLine, yRange }: {
  frames: number[]; timeNs: number[] | null; series: { y: number[]; name: string }[]; yTitle: string
  display?: FigureDisplay; refLine?: number | null; yRange?: [number, number] | null
}) {
  const { t, label } = timeAxis(frames, timeNs, display?.time_unit)
  const colors = [LINE, '#ff7f0e', '#2ca02c']
  const data = series.map((s, i) => ({ type: 'scatter', mode: 'lines', x: t, y: s.y, name: s.name,
    line: { color: colors[i % 3], width: 1 }, hovertemplate: `%{x:.1f}: %{y:.3f}<extra>${s.name}</extra>` })) as Data[]
  const shapes = refLine != null ? [{ type: 'line' as const, xref: 'paper' as const, x0: 0, x1: 1, y0: refLine, y1: refLine, line: { color: INK, width: 1, dash: 'dash' as const } }] : []
  return <PaperPlot data={data} layout={{
    showlegend: series.length > 1, legend: { x: 1, xanchor: 'right', y: 1, bgcolor: 'rgba(255,255,255,0.8)', bordercolor: '#ccc', borderwidth: 1 },
    shapes, xaxis: { ...paperAxis, title: title(label) },
    yaxis: { ...paperAxis, title: title(yTitle), ...(yRange ? { range: yRange } : {}) },
  }} />
}

function RmsfFigure({ d, display }: { d: FlexData; display?: FigureDisplay }) {
  const band = (x0: number, x1: number, color: string, name: string) => ({
    type: 'scatter', mode: 'lines', x: [x0, x1, x1, x0, x0], y: [0, 0, 1, 1, 0], yaxis: 'y2', fill: 'toself', fillcolor: color,
    line: { width: 0 }, name, hoverinfo: 'skip', showlegend: true,
  })
  const data = [
    band(30, 59, 'rgba(253,190,90,0.22)', 'NMP'), band(122, 159, 'rgba(170,90,180,0.2)', 'LID'),
    { type: 'scatter', mode: 'lines', x: d.resids, y: d.rmsf, text: d.keys.map(residueLabel), line: { color: LINE, width: 1.6 },
      showlegend: false, hovertemplate: '%{text}<br>RMSF %{y:.2f} Å<extra></extra>' },
  ] as Data[]
  const yRange: [number, number] | undefined = display?.y_min != null && display?.y_max != null ? [display.y_min, display.y_max] : undefined
  return <PaperPlot data={data} layout={{
    showlegend: true, legend: { x: 1, xanchor: 'right', y: 1, bordercolor: '#ccc', borderwidth: 1 },
    xaxis: { ...paperAxis, title: title('residue number') },
    yaxis: { ...paperAxis, title: title('RMSF (Å)'), ...(yRange ? { range: yRange } : {}) },
    yaxis2: { overlaying: 'y', range: [0, 1], visible: false, fixedrange: true },
  }} />
}

/** The User Guide's two panels (Q1 and Q2 vs frame; Q2 vs Q1), in the same paper style. */
function Q1Q2Figure({ d }: { d: Repro }) {
  const q1 = d.ours.map((q) => q[0])
  const q2 = d.ours.map((q) => q[q.length - 1])
  const data = [
    { type: 'scatter', mode: 'lines', x: d.frames, y: q1, name: 'Q1', line: { color: LINE, width: 1.5 } },
    { type: 'scatter', mode: 'lines', x: d.frames, y: q2, name: 'Q2', line: { color: '#ff7f0e', width: 1.5 } },
    { type: 'scatter', mode: 'lines', x: q1, y: q2, xaxis: 'x2', yaxis: 'y2', showlegend: false, line: { color: LINE, width: 1.2 },
      hovertemplate: 'Q1 %{x:.3f}<br>Q2 %{y:.3f}<extra></extra>' },
  ] as Data[]
  return <PaperPlot data={data} layout={{
    showlegend: true, legend: { x: 0.02, y: 0.02, bordercolor: '#ccc', borderwidth: 1 }, grid: { rows: 1, columns: 2, pattern: 'independent' },
    xaxis: { ...paperAxis, title: title('Frame') }, yaxis: { ...paperAxis, title: title('Fraction of native contacts') },
    xaxis2: { ...paperAxis, title: title('Q1') }, yaxis2: { ...paperAxis, title: title('Q2') },
  }} />
}

/** Our version of the figure: the same figure type as theirs, drawn from this run's stored results, at the
 *  published image's aspect ratio. */
export function Ours({ run }: { run: RunView | null }) {
  const { proposal } = useInvestigation()
  const figure = run?.figure ?? proposal?.figure
  const size = useImageSize(figure && figure.mime.startsWith('image/') ? api.figureUrl(figure.figure_id) : null)
  return <PaperSize.Provider value={size}><OursBody run={run} figure={figure ?? null} /></PaperSize.Provider>
}

function OursBody({ run, figure }: { run: RunView | null; figure: Figure | null }) {
  const display = figure?.spec.display
  const kind = figure?.spec.kind
  const repro = lastData<Repro>(run, 'reproduction_check')
  const contacts = lastData<Contacts>(run, 'calculate_contacts')
  const diff = lastData<DiffData>(run, 'contact_difference_map')
  const dist = lastData<Distances>(run, 'distance_over_time')
  const native = lastData<NativeData>(run, 'native_contacts')
  const flex = lastData<FlexData>(run, 'residue_flexibility')
  const yRange = display?.y_min != null && display?.y_max != null ? [display.y_min, display.y_max] as [number, number] : null

  if (kind === 'native_contacts' && repro && figure?.spec.mode === 'exact') return <Q1Q2Figure d={repro} />
  if (kind === 'native_contacts' && native) {
    const many = native.ref_frames.length > 1
    return <LineFigure frames={native.frames} timeNs={native.time_ns} display={display} yRange={yRange}
      yTitle={display?.y.title || 'fraction of native contacts Q'}
      series={native.ref_frames.map((f, k) => ({ y: native.q.map((row) => row[k]), name: many ? `reference frame ${f}` : 'Q' }))} />
  }
  if (kind === 'contact_map' && contacts) {
    return <MapFigure keysA={contacts.keys_a} keysB={contacts.keys_b} z={contacts.frequency} excluded={contacts.excluded} groupA={contacts.group_a}
      groupB={contacts.group_b} display={display} diverging={false} />
  }
  if (kind === 'difference_contact_map' && diff) {
    return <MapFigure keysA={diff.keys_a} keysB={diff.keys_b} z={diff.delta} excluded={(diff as DiffData & { excluded?: boolean[][] }).excluded}
      groupA={diff.group_a} groupB={diff.group_b}
      display={display} diverging />
  }
  if (kind === 'distance_trace' && dist) {
    return <LineFigure frames={dist.frames} timeNs={dist.time_ns} display={display} yRange={yRange}
      refLine={display?.reference_line ?? run?.plan?.cutoff ?? null} yTitle={display?.y.title || 'minimum distance (Å)'}
      series={[{ y: dist.distance, name: `${residueLabel(dist.key_a)}–${residueLabel(dist.key_b)}` }]} />
  }
  if (kind === 'rmsf' && flex) return <RmsfFigure d={flex} display={display} />
  const finished = run && ['awaiting_verification', 'verified', 'flagged', 'needs_review', 'error'].includes(run.status)
  return <div className="muted small placeholder">{!run ? 'Appears once the plan runs.'
    : finished ? `This run didn’t produce a ${KIND_LABEL[kind ?? 'other']} to draw.` : 'Computing…'}</div>
}

/** The published figure: the uploaded image, or the figure the backend took out of the PDF. A PDF with no
 *  embedded figure cannot be shown as an image, so it gets a link instead of a broken picture. */
export function PublishedFigure({ figure }: { figure: Figure }) {
  const url = api.figureUrl(figure.figure_id)
  if (figure.mime.startsWith('image/')) return <img src={url} alt={`Uploaded figure: ${figure.spec.summary}`} />
  return <div className="muted small placeholder"><a href={url} target="_blank" rel="noreferrer">Open the uploaded PDF</a> (no figure image found in it)</div>
}

/** "contact distance 6 Å (published: 8 Å)" for each setting the user changed from the published figure's. */
function changedText(changed: NonNullable<Repro['changed']>): string {
  const show = (k: string, v: unknown) => k === 'radius' ? `${v} Å`
    : k === 'selection' ? (v === 'name CA' ? 'Cα atoms' : String(v))
    : Array.isArray(v) ? `frames ${v.join(', ')}` : String(v)
  const name: Record<string, string> = { radius: 'contact distance', selection: 'atoms', ref_frames: 'reference frames' }
  return Object.entries(changed).map(([k, c]) => `${name[k] ?? k} ${show(k, c.ours)} (published: ${show(k, c.published)})`).join('; ')
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
      <span className="spec-value">{withUnits(f.value)}</span>
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
          <PublishedFigure figure={figure} />
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
            : repro.changed && Object.keys(repro.changed).length
              ? <>✗ No longer matches the published code: this run used {changedText(repro.changed)}.</>
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
