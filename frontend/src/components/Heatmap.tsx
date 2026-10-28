// Contact-frequency heatmap for two groups. Clicking a cell selects that residue pair.
// Sequence-local pairs (|Δresid| <= sequence_exclusion) are drawn grey, not as zero.
import { useEffect, useMemo, useState } from 'react'
import type { Data, Shape } from 'plotly.js'
import { api, type Contacts, type Metadata } from '../api/client'
import { setSelection, useSelection } from '../state/selection'
import { parseResidueKey, residueLabel } from '../state/residueKey'
import { DOMAIN_COLORS, EXCLUDED_GREY } from '../theme'
import { Plot, axisStyle, baseLayout } from './Plot'

function ticks(keys: string[], every: number) {
  const idx = keys.map((_, i) => i).filter((i) => i % every === 0)
  return { tickvals: idx, ticktext: idx.map((i) => String(parseResidueKey(keys[i]).resid)) }
}

export function Heatmap({ meta }: { meta: Metadata }) {
  const { dataset, groupA, groupB, pair } = useSelection()
  const [data, setData] = useState<Contacts | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const groups = Object.keys(meta.groups)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api.contacts(dataset, groupA, groupB).then(
      (c) => !cancelled && (setData(c), setLoading(false)),
      (e) => !cancelled && (setError((e as Error).message), setLoading(false)),
    )
    return () => {
      cancelled = true
    }
  }, [dataset, groupA, groupB])

  const traces = useMemo<Data[]>(() => {
    if (!data || data.dataset !== dataset) return []
    const labA = data.keys_a.map(residueLabel)
    const labB = data.keys_b.map(residueLabel)
    const z = data.frequency.map((row, i) => row.map((v, j) => (data.excluded[i][j] ? null : v)))
    const zExcl = data.excluded.map((row) => row.map((e) => (e ? 1 : null)))
    const text = labA.map((a) => labB.map((b) => `${a} – ${b}`))
    return [
      {
        type: 'heatmap', z, text, colorscale: 'Viridis', zmin: 0, zmax: 1, hoverongaps: false,
        hovertemplate: '%{text}<br>contact frequency %{z:.3f}<extra></extra>',
        colorbar: { title: { text: 'freq', side: 'right' }, thickness: 10, outlinewidth: 0 },
      },
      {
        type: 'heatmap', z: zExcl, text, showscale: false, hoverongaps: false,
        colorscale: [[0, EXCLUDED_GREY], [1, EXCLUDED_GREY]],
        hovertemplate: `%{text}<br>excluded: |Δresid| ≤ ${data.sequence_exclusion}<extra></extra>`,
      },
    ] as Data[]
  }, [data, dataset])

  const shapes = useMemo<Partial<Shape>[]>(() => {
    if (!data || !pair) return []
    const i = data.keys_a.indexOf(pair.keyA)
    const j = data.keys_b.indexOf(pair.keyB)
    if (i < 0 || j < 0) return []
    return [{ type: 'rect', x0: j - 0.5, x1: j + 0.5, y0: i - 0.5, y1: i + 0.5, line: { color: '#ffffff', width: 2 } }]
  }, [data, pair])

  const nPairs = data ? data.excluded.flat().filter((e) => !e).length : 0

  return (
    <section className="card">
      <header className="card-head">
        <h2>Contact frequency</h2>
        <div className="controls">
          <label>
            Group A
            <select value={groupA} onChange={(e) => setSelection({ groupA: e.target.value })}>
              {groups.map((g) => <option key={g} value={g} disabled={g === groupB}>{g}</option>)}
            </select>
          </label>
          <span className="muted">vs</span>
          <label>
            Group B
            <select value={groupB} onChange={(e) => setSelection({ groupB: e.target.value })}>
              {groups.map((g) => <option key={g} value={g} disabled={g === groupA}>{g}</option>)}
            </select>
          </label>
        </div>
      </header>
      <p className="card-sub">
        {meta.contact_definition}, cutoff {data?.cutoff ?? 4.5} Å · {data ? `${data.n_frames} frames · ${nPairs} pairs` : '…'} ·{' '}
        <span className="swatch" style={{ background: EXCLUDED_GREY }} /> excluded (|Δresid| ≤ {data?.sequence_exclusion ?? meta.sequence_exclusion_default})
      </p>
      {error && <div className="error">{error}</div>}
      <div className="plot-wrap heatmap-wrap">
        {loading && <div className="overlay">Computing contacts… (first run on a new pair can take ~15 s)</div>}
        {data && (
          <Plot
            data={traces}
            layout={{
              ...baseLayout,
              margin: { l: 56, r: 10, t: 6, b: 46 },
              xaxis: { ...axisStyle, title: { text: `${groupB} residue`, font: { color: DOMAIN_COLORS[groupB] } }, ...ticks(data.keys_b, 10), showgrid: false },
              yaxis: { ...axisStyle, title: { text: `${groupA} residue`, font: { color: DOMAIN_COLORS[groupA] } }, ...ticks(data.keys_a, 5), showgrid: false },
              shapes,
            }}
            config={{ displaylogo: false, responsive: true, modeBarButtonsToRemove: ['select2d', 'lasso2d'] }}
            useResizeHandler
            style={{ width: '100%', height: '100%' }}
            onClick={(ev) => {
              const p = ev.points[0]
              if (!p || !data) return
              const i = p.y as number
              const j = p.x as number
              setSelection({ pair: { keyA: data.keys_a[i], keyB: data.keys_b[j], excluded: data.excluded[i][j] } })
            }}
          />
        )}
      </div>
    </section>
  )
}
