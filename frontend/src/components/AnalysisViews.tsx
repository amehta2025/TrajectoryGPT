// Views for the §10b tools when a run produced them: the contact difference map (window B − window A,
// diverging orange/blue for weaker/stronger, pairs not supported by compare_intervals faded; "not
// statistically tested" on a dataset without statistics) and per-residue flexibility (C-alpha RMSF).
// Clicking a difference-map cell selects that pair, so the 3D view, distance plot and cards follow.
import { useMemo } from 'react'
import type { Data } from 'plotly.js'
import type { RunView } from '../api/client'
import { parseResidueKey, residueLabel } from '../state/residueKey'
import { setSelection, useSelection } from '../state/selection'
import { CHANGE_COLORS, DOMAIN_COLORS } from '../theme'
import { Plot, axisStyle, baseLayout } from './Plot'

export type DiffData = {
  group_a: string; group_b: string; keys_a: string[]; keys_b: string[]; delta: (number | null)[][]
  freq_a: number[][]; freq_b: number[][]; window_a: [number, number]; window_b: [number, number]
  cutoff: number; tested: boolean; stats_valid: boolean; supported_pairs: [string, string][] | null
}
export type FlexData = { keys: string[]; resids: number[]; rmsf: number[]; note: string; n_frames: number }

export function lastData<T>(run: RunView | null, tool: string): T | null {
  if (!run) return null
  const hits = Object.values(run.results).filter((r) => r.tool === tool && r.status === 'ok' && r.data_for_ui)
  return hits.length ? (hits[hits.length - 1].data_for_ui as T) : null
}

const DIVERGING: [number, string][] = [[0, CHANGE_COLORS.lost], [0.5, '#151a24'], [1, CHANGE_COLORS.gained]]

export function DifferenceMap({ d, height = 320 }: { d: DiffData; height?: number }) {
  const { pair } = useSelection()
  const supported = useMemo(() => new Set((d.supported_pairs ?? []).map(([a, b]) => `${a}|${b}`)), [d])
  const traces = useMemo<Data[]>(() => {
    const labA = d.keys_a.map(residueLabel)
    const labB = d.keys_b.map(residueLabel)
    const max = Math.max(0.05, ...d.delta.flat().map((v) => Math.abs(v ?? 0)))
    // Where statistics exist, pairs that are not supported are drawn at a third of their change.
    const z = d.delta.map((row, i) => row.map((v, j) =>
      v === null ? null : d.tested && !supported.has(`${d.keys_a[i]}|${d.keys_b[j]}`) ? v / 3 : v))
    const text = labA.map((a, i) => labB.map((b, j) => {
      const v = d.delta[i][j]
      if (v === null) return `${a} – ${b}<br>excluded (sequence-local)`
      const status = !d.stats_valid ? 'not statistically tested' : !d.tested ? 'not tested (run compare_intervals)'
        : supported.has(`${d.keys_a[i]}|${d.keys_b[j]}`) ? 'supported change' : 'not supported (noise)'
      return `${a} – ${b}<br>${d.freq_a[i][j].toFixed(3)} → ${d.freq_b[i][j].toFixed(3)} (Δ ${v >= 0 ? '+' : ''}${v.toFixed(3)})<br>${status}`
    }))
    return [{
      type: 'heatmap', z, text, zmin: -max, zmax: max, colorscale: DIVERGING, hoverongaps: false,
      hovertemplate: '%{text}<extra></extra>',
      colorbar: { title: { text: 'Δ freq', side: 'right' }, thickness: 10, outlinewidth: 0 },
    }] as Data[]
  }, [d, supported])
  const sel = pair ? [d.keys_a.indexOf(pair.keyA), d.keys_b.indexOf(pair.keyB)] : [-1, -1]
  const tick = (keys: string[]) => {
    const every = Math.max(1, Math.ceil(keys.length / 12))
    const idx = keys.map((_, i) => i).filter((i) => i % every === 0)
    return { tickvals: idx, ticktext: idx.map((i) => String(parseResidueKey(keys[i]).resid)) }
  }
  return (
    <div className="plot-wrap" style={{ height }}>
      <Plot
        data={traces}
        layout={{
          ...baseLayout, margin: { l: 56, r: 10, t: 6, b: 46 },
          xaxis: { ...axisStyle, title: { text: `${d.group_b} residue`, font: { color: DOMAIN_COLORS[d.group_b] } }, ...tick(d.keys_b), showgrid: false },
          yaxis: { ...axisStyle, title: { text: `${d.group_a} residue`, font: { color: DOMAIN_COLORS[d.group_a] } }, ...tick(d.keys_a), showgrid: false },
          shapes: sel[0] >= 0 && sel[1] >= 0
            ? [{ type: 'rect', x0: sel[1] - 0.5, x1: sel[1] + 0.5, y0: sel[0] - 0.5, y1: sel[0] + 0.5, line: { color: '#ffffff', width: 2 } }]
            : [],
        }}
        config={{ displaylogo: false, responsive: true, modeBarButtonsToRemove: ['select2d', 'lasso2d'] }}
        useResizeHandler
        style={{ width: '100%', height: '100%' }}
        onClick={(ev) => {
          const p = ev.points[0]
          if (!p) return
          const i = p.y as number
          const j = p.x as number
          if (d.delta[i]?.[j] === null) return
          setSelection({ pair: { keyA: d.keys_a[i], keyB: d.keys_b[j], excluded: false } })
        }}
      />
    </div>
  )
}

export function FlexibilityPlot({ d, height = 240 }: { d: FlexData; height?: number }) {
  const traces = useMemo<Data[]>(() => {
    const color = d.resids.map((r) => {
      const g = r >= 122 && r <= 159 ? 'LID' : r >= 30 && r <= 59 ? 'NMP' : 'CORE'
      return DOMAIN_COLORS[g]
    })
    return [{
      type: 'bar', x: d.resids, y: d.rmsf, marker: { color }, text: d.keys.map(residueLabel),
      hovertemplate: '%{text}<br>RMSF %{y:.2f} Å<extra></extra>',
    }] as Data[]
  }, [d])
  return (
    <div className="plot-wrap" style={{ height }}>
      <Plot
        data={traces}
        layout={{
          ...baseLayout, margin: { l: 50, r: 10, t: 6, b: 40 }, bargap: 0,
          xaxis: { ...axisStyle, title: { text: 'residue' } },
          yaxis: { ...axisStyle, title: { text: 'Cα RMSF (Å)' }, rangemode: 'tozero' },
        }}
        config={{ displaylogo: false, responsive: true }}
        useResizeHandler
        style={{ width: '100%', height: '100%' }}
      />
    </div>
  )
}

/** Evidence cards for whichever §10b results this run has. */
export function AnalysisCards({ run }: { run: RunView | null }) {
  const diff = lastData<DiffData>(run, 'contact_difference_map')
  const flex = lastData<FlexData>(run, 'residue_flexibility')
  if (!diff && !flex) return null
  return (
    <>
      {diff && (
        <section className="card">
          <header className="card-head"><h2>Contact difference map</h2>
            <span className="muted small">{diff.tested ? 'faded = not supported by the comparison' : diff.stats_valid ? 'raw differences, not yet tested' : 'not statistically tested'}</span>
          </header>
          <p className="card-sub">
            Contact frequency, later window minus earlier, cutoff {diff.cutoff} Å ·{' '}
            <span className="swatch" style={{ background: CHANGE_COLORS.lost }} />weaker
            <span className="swatch" style={{ background: CHANGE_COLORS.gained, marginLeft: 10 }} />stronger · click a cell to show the pair
          </p>
          <DifferenceMap d={diff} />
        </section>
      )}
      {flex && (
        <section className="card">
          <header className="card-head"><h2>Flexibility (Cα RMSF)</h2><span className="muted small">{flex.n_frames} frames</span></header>
          <p className="card-sub">{flex.note}</p>
          <FlexibilityPlot d={flex} />
        </section>
      )}
    </>
  )
}
