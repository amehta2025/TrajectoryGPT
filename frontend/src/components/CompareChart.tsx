// Per-pair change in contact frequency between two windows, with autocorrelation-corrected CI bars.
// Supported pairs (BH q < FDR, CI excludes 0, enough events) are coloured; the rest are greyed noise.
// Supported pairs that are provisional (few independent chunks of data) are drawn hollow.
// Clicking a bar selects that pair, so the heatmap, distance plot and viewer follow.
import { useEffect, useMemo, useState } from 'react'
import type { Data } from 'plotly.js'
import { api, type Compare, type CompareRow, type Metadata } from '../api/client'
import { setSelection, useSelection } from '../state/selection'
import { residueLabel } from '../state/residueKey'
import { frameTimeLabel } from '../state/time'
import { EXCLUDED_GREY } from '../theme'
import { Plot, axisStyle, baseLayout } from './Plot'

const MAX_ROWS = 40
const UP = '#3ecf8e'
const DOWN = '#ff7a59'

/** Default windows when no plan has set them: first half vs second half. */
function halves(nFrames: number): [[number, number], [number, number]] {
  const mid = Math.floor(nFrames / 2)
  return [[0, mid], [mid, nFrames]]
}

function windowLabel(meta: Metadata, [start, stop]: [number, number]): string {
  return `frames ${start}–${stop - 1} (${frameTimeLabel(meta, start)} – ${frameTimeLabel(meta, stop - 1)})`
}

// Calibrated threshold comes from the API (min_blocks_firm); see SPEC §8a and notes/calibration/.

/** τ in ns where the dataset has physical time, otherwise in frames (path steps). */
function tauText(r: CompareRow, side: 'a' | 'b'): string {
  const tau = side === 'a' ? r.tau_a : r.tau_b
  const ns = side === 'a' ? r.tau_ns_a : r.tau_ns_b
  if (tau === null) return 'constant'
  return ns !== null ? `${ns.toFixed(1)} ns` : `${tau.toFixed(1)} steps`
}

function nEffText(r: CompareRow, side: 'a' | 'b'): string {
  const n = side === 'a' ? r.n_eff_a : r.n_eff_b
  return n === null ? '–' : n.toFixed(0)
}

function statusLabel(r: CompareRow): string {
  if (r.supported) return r.provisional ? 'supported (slow — provisional)' : 'supported'
  if (r.too_few_events) return 'too few events'
  if (r.too_few_blocks) return 'too few independent blocks'
  return 'not supported'
}

function chunksText(r: CompareRow): string {
  const f = (c: number | null) => (c === null ? '–' : c.toFixed(0))
  return `${f(r.n_blocks_a)} → ${f(r.n_blocks_b)} independent chunks`
}

function provisionalHelp(minChunks: number): string {
  return (
    `Provisional: fewer than ${minChunks} independent chunks of data in a window → less certain. ` +
    `A chunk is about 2τ of trajectory, the time a contact takes to forget its state. ` +
    `In our coverage test on synthetic data with a known answer, 95% intervals built from fewer than ` +
    `${minChunks} chunks contained the true value less than 90% of the time. ` +
    `A provisional "supported" result may not hold up with more sampling.`
  )
}

export function CompareChart({ meta }: { meta: Metadata }) {
  const { dataset, groupA, groupB, pair, cutoff, windows } = useSelection()
  const [data, setData] = useState<Compare | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [winA, winB] = windows ?? halves(meta.n_frames)

  useEffect(() => {
    setData(null)
    setError(null)
    let cancelled = false
    const [a, b] = windows ?? halves(meta.n_frames)
    api.compare(dataset, groupA, groupB, a, b, cutoff).then(
      (d) => !cancelled && setData(d),
      (e) => !cancelled && setError((e as Error).message),
    )
    return () => {
      cancelled = true
    }
  }, [dataset, groupA, groupB, meta.n_frames, cutoff, windows])

  const rows = useMemo(() => (data?.status === 'ok' ? data.rows.slice(0, MAX_ROWS).reverse() : []), [data])

  const traces = useMemo<Data[]>(() => {
    if (!rows.length) return []
    const labels = rows.map((r) => `${residueLabel(r.key_a)}–${residueLabel(r.key_b)}`)
    const colors = rows.map((r) => (r.supported ? (r.delta > 0 ? UP : DOWN) : EXCLUDED_GREY))
    const selected = rows.map((r) => pair?.keyA === r.key_a && pair?.keyB === r.key_b)
    const hollow = rows.map((r) => r.supported && r.provisional)
    return [{
      type: 'scatter', mode: 'markers', orientation: 'h',
      x: rows.map((r) => r.delta), y: labels,
      customdata: rows.map((_, i) => i),
      error_x: {
        type: 'data', symmetric: false,
        array: rows.map((r) => r.ci_high - r.delta), arrayminus: rows.map((r) => r.delta - r.ci_low),
        color: '#8b909c', thickness: 1.5, width: 3,
      },
      marker: {
        size: rows.map((_, i) => (selected[i] ? 12 : 8)), color: colors,
        symbol: hollow.map((h) => (h ? 'circle-open' : 'circle')),
        line: { color: rows.map((_, i) => (selected[i] ? '#ffffff' : hollow[i] ? colors[i] : 'rgba(0,0,0,0)')), width: 2 },
      },
      text: rows.map((r) =>
        [
          `freq ${r.freq_a.toFixed(3)} → ${r.freq_b.toFixed(3)}`,
          `Δ ${r.delta.toFixed(3)} [${r.ci_low.toFixed(3)}, ${r.ci_high.toFixed(3)}]`,
          `q = ${r.q.toPrecision(2)} · ${statusLabel(r)}`,
          `N_eff ${nEffText(r, 'a')} → ${nEffText(r, 'b')} · τ ${tauText(r, 'a')} → ${tauText(r, 'b')}`,
          `${chunksText(r)} · ${r.transitions} transitions${r.provisional ? ' · provisional (few chunks)' : ''}`,
        ].join('<br>'),
      ),
      hovertemplate: '%{y}<br>%{text}<extra>click to select</extra>',
    }] as Data[]
  }, [rows, pair])

  const ok = data?.status === 'ok' ? data : null

  return (
    <section className="card">
      <header className="card-head">
        <h2>Change between windows</h2>
        {ok && (
          <span className="muted small">
            {ok.n_tested} tested · {ok.n_filtered} filtered out (rare or never in contact) · FDR {ok.fdr * 100}% ·{' '}
            <b style={{ color: 'var(--text)' }}>{ok.n_supported} supported</b>: {ok.n_supported_firm} firm ·{' '}
            {ok.n_supported_provisional} slow — provisional{' '}
            <span
              className="info"
              tabIndex={0}
              role="img"
              aria-label={provisionalHelp(ok.min_blocks_firm)}
              title={provisionalHelp(ok.min_blocks_firm)}
            >
              ⓘ
            </span>
          </span>
        )}
      </header>
      <p className="card-sub">
        {groupA}–{groupB} · cutoff {cutoff} Å · Δ = window B − window A, {ok ? ok.ci_level * 100 : 95}% CI (autocorrelation-corrected) ·
        A: {windowLabel(meta, winA)} · B: {windowLabel(meta, winB)}
        <br />
        <span className="swatch" style={{ background: UP }} />more contact
        <span className="swatch" style={{ background: DOWN, marginLeft: 8 }} />less contact
        <span className="swatch hollow" style={{ borderColor: DOWN, marginLeft: 8 }} />supported (slow — provisional)
        <span className="swatch" style={{ background: EXCLUDED_GREY, marginLeft: 8 }} />not supported (noise)
        {ok && ok.n_tested > MAX_ROWS && <> · top {MAX_ROWS} of {ok.n_tested} shown</>}
      </p>
      {error && <div className="error">{error}</div>}
      {data?.status === 'refused' && <div className="refusal">Statistics refused: {data.reason}</div>}
      {data?.status !== 'refused' && (
        <div className="plot-wrap" style={{ height: Math.max(160, rows.length * 18 + 60) }}>
          {!data && !error && <div className="overlay">Computing statistics…</div>}
          {ok && !rows.length && <div className="overlay">No pairs passed the frequency filter.</div>}
          {rows.length > 0 && (
            <Plot
              data={traces}
              layout={{
                ...baseLayout,
                margin: { ...baseLayout.margin, l: 110 },
                xaxis: { ...axisStyle, title: { text: 'Δ contact frequency (B − A)' }, zeroline: true, zerolinecolor: '#5d6880' },
                yaxis: { ...axisStyle, automargin: true },
                showlegend: false,
              }}
              config={{ displaylogo: false, responsive: true }}
              useResizeHandler
              style={{ width: '100%', height: '100%' }}
              onClick={(ev) => {
                const i = ev.points[0]?.customdata
                if (typeof i !== 'number') return
                setSelection({ pair: { keyA: rows[i].key_a, keyB: rows[i].key_b, excluded: false } })
              }}
            />
          )}
        </div>
      )}
    </section>
  )
}
