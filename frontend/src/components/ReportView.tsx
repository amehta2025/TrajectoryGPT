// The report: a headline built by code from the stored tool results, then findings with
// proven/unsupported badges, robustness badges, values filled by code with provenance on hover,
// expandable citations (click -> highlight in 3D + show provenance). Statistics, hypotheses,
// caveats, gaps and follow-ups follow, the longer ones collapsed with their counts shown.
// A needs_review run shows its unresolved problems instead.
// Verification is not shown until a verifier result exists (SPEC §9 step 7, Day 6).
import { useEffect, useState } from 'react'
import { api, type Finding, type Metadata, type RunView, type Segment, type TextItem } from '../api/client'
import { useInvestigation } from '../state/investigation'
import { getSelection, setSelection } from '../state/selection'

type CompareData = {
  group_a: string; group_b: string; n_tested: number; n_filtered: number; n_supported: number
  n_supported_firm: number; n_supported_provisional: number; fdr: number; ci_level: number
  window_b: [number, number]; rows: { supported: boolean; delta: number }[]
}
type SensitivityData = { cutoffs: number[]; n_robust: number; rows: unknown[] } // n_tested is per cutoff

/** The last successful result of a tool, with its UI data. */
function lastResult<T>(run: RunView, tool: string): T | null {
  const hits = Object.values(run.results).filter((r) => r.tool === tool && r.status === 'ok' && r.data_for_ui)
  return hits.length ? (hits[hits.length - 1].data_for_ui as T) : null
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
const range = (cs: number[]) => `${Math.min(...cs).toFixed(1)}–${Math.max(...cs).toFixed(1)} Å`

/** One plain-language headline and a method line. Every number comes from a stored tool result. */
export function Headline() {
  const { run, phase } = useInvestigation()
  if (!run || phase !== 'finished') return null
  if (!run.report) {
    return (
      <div className="headline">
        <p className="headline-main">No report: the automatic checks did not accept one.</p>
        <p className="headline-method">The unresolved problems are listed below. Nothing unchecked is shown.</p>
      </div>
    )
  }
  const cmp = lastResult<CompareData>(run, 'compare_intervals')
  const sens = lastResult<SensitivityData>(run, 'cutoff_sensitivity')
  const findings = run.report.findings
  if (cmp) {
    const sup = cmp.rows.filter((r) => r.supported)
    const weaker = sup.filter((r) => r.delta < 0).length
    const stronger = sup.length - weaker
    return (
      <div className="headline">
        <p className="headline-main">
          {cmp.n_supported === 0
            ? <>No supported change among {plural(cmp.n_tested, `tested ${cmp.group_a}–${cmp.group_b} contact`)}.</>
            : <>
                {cmp.n_supported} of {cmp.n_tested} tested {cmp.group_a}–{cmp.group_b} contacts changed:{' '}
                {weaker > 0 && <span className="lost">{weaker} weaker</span>}
                {weaker > 0 && stronger > 0 && ', '}
                {stronger > 0 && <span className="gained">{stronger} stronger</span>}.
                {sens && <> {sens.n_robust} of {sens.rows.length} checked hold at every cutoff across {range(sens.cutoffs)}.</>}
              </>}
        </p>
        <p className="headline-method">
          Supported means FDR-corrected q &lt; {cmp.fdr} and a {Math.round(cmp.ci_level * 100)}% interval, corrected for
          autocorrelation, that excludes zero · {cmp.n_supported_firm} firm, {cmp.n_supported_provisional} provisional ·{' '}
          {cmp.n_filtered.toLocaleString()} rarely-formed pairs not tested
        </p>
      </div>
    )
  }
  const count = (b: Finding['badge']) => findings.filter((f) => f.badge === b).length
  const descriptive = count('descriptive')
  if (descriptive === findings.length) {
    return (
      <div className="headline">
        <p className="headline-main">{plural(findings.length, 'descriptive finding')}, with no statistics on this dataset.</p>
        <p className="headline-method">The path is biased and not evenly spaced in time, so frequencies are described, not tested.</p>
      </div>
    )
  }
  const parts = [
    count('proven') && `${count('proven')} supported`,
    count('unsupported') + count('null') && `${count('unsupported') + count('null')} not supported`,
    descriptive && `${descriptive} descriptive`,
  ].filter(Boolean)
  return (
    <div className="headline">
      <p className="headline-main">{plural(findings.length, 'finding')}: {parts.join(', ')}.</p>
      <p className="headline-method">Supported means FDR-corrected q &lt; 0.05 and an autocorrelation-corrected interval that excludes zero.</p>
    </div>
  )
}

/**
 * When a report opens, point the 3D view at its top finding: highlight that pair and jump to a
 * window-B frame that shows the change (apart if the contact weakened, in contact if it strengthened),
 * the frame with the median distance among those. The caption says why this frame was picked.
 */
function useFocusTopFinding(run: RunView | null, meta: Metadata | null) {
  useEffect(() => {
    if (!run?.report || !meta) return
    const f = run.report.findings.find((x) => x.badge === 'proven' && x.pairs.length) ?? run.report.findings.find((x) => x.pairs.length)
    if (!f) return
    const label = f.pairs[0]
    const pid = f.evidence_ids.map((e) => e.split('.')[0]).find((p) => run.results[p]?.pairs[label])
    const keys = pid && run.results[pid].pairs[label]
    if (!keys) return
    setSelection({ pair: { keyA: keys.key_a, keyB: keys.key_b, excluded: false } })
    const delta = (keys as { delta?: number }).delta
    let cancelled = false
    api.distances(meta.dataset, keys.key_a, keys.key_b).then((d) => {
      if (cancelled) return
      const { windows, cutoff } = getSelection()
      const mid = Math.floor(meta.n_frames / 2)
      const [w0, w1] = windows ? windows[1] : [mid, meta.n_frames]
      const apart = delta === undefined ? null : delta < 0
      let idx = d.frames.map((_, i) => i).filter((i) => d.frames[i] >= w0 && d.frames[i] < w1)
      if (apart !== null) {
        const want = idx.filter((i) => (apart ? d.distance[i] > cutoff : d.distance[i] <= cutoff))
        if (want.length) idx = want
      }
      if (!idx.length) return
      idx.sort((a, b) => d.distance[a] - d.distance[b])
      const i = idx[Math.floor(idx.length / 2)]
      const where = w0 === mid && w1 === meta.n_frames ? 'Second-half' : 'Window B'
      const state = d.distance[i] > cutoff ? 'apart' : 'in contact'
      setSelection({ frame: d.frames[i], focus: { frame: d.frames[i], text: `${where} frame where ${label} is ${state} (${d.distance[i].toFixed(1)} Å)` } })
    }, () => {})
    return () => {
      cancelled = true
    }
  }, [run?.run_id, run?.report, meta]) // eslint-disable-line react-hooks/exhaustive-deps
}

function Segments({ segs, run }: { segs: Segment[]; run: RunView }) {
  return (
    <>
      {segs.map((s, i) => {
        if (!s.pid) return <span key={i}>{s.text}</span>
        const r = run.results[s.pid]
        return (
          <span key={i} className="value" title={`${s.path} — from ${s.pid}: ${r?.tool ?? '?'} (${r?.status ?? '?'})`}>
            {s.text}
          </span>
        )
      })}
    </>
  )
}

function Citation({ id, run, onShow }: { id: string; run: RunView; onShow: (id: string) => void }) {
  const [pid, pair] = id.split(/\.(.+)/)
  const res = run.results[pid]
  const keys = pair && res?.pairs[pair]
  return (
    <button className="citation" onClick={() => {
      if (keys) setSelection({ pair: { keyA: keys.key_a, keyB: keys.key_b, excluded: false } })
      onShow(pid)
    }} title={keys ? 'Highlight this pair in 3D and show provenance' : 'Show provenance'}>
      {id} <span className="muted small">{res?.tool}</span>
    </button>
  )
}

function Provenance({ pid, run }: { pid: string; run: RunView }) {
  const r = run.results[pid]
  if (!r) return null
  return (
    <div className="provenance">
      <b>{pid}</b> · <code>{r.tool}</code> · {r.status}
      <pre>{JSON.stringify(r.params, null, 1)}</pre>
    </div>
  )
}

function FindingCard({ f, run }: { f: Finding; run: RunView }) {
  const [open, setOpen] = useState(false)
  const [shown, setShown] = useState<string | null>(null)
  return (
    <li className={`finding ${f.badge}`}>
      <div className="badges">
        <span className={`badge ${f.badge}`}>{f.badge_label}</span>
        {f.robustness.map((r) => (
          <span key={r.pair} className={`badge rob-${r.verdict.replace(' ', '-')}`} title={`${r.pair}${r.pid ? ` · from ${r.pid}` : ''}`}>
            {f.robustness.length > 1 ? `${r.pair}: ` : ''}{r.label}
          </span>
        ))}
        <button className="link small cite-toggle" onClick={() => setOpen(!open)}>
          {f.evidence_ids.length} source{f.evidence_ids.length === 1 ? '' : 's'} {open ? '▾' : '▸'}
        </button>
      </div>
      <p><Segments segs={f.segments} run={run} /></p>
      {open && (
        <div className="citations">
          {f.evidence_ids.map((e) => <Citation key={e} id={e} run={run} onShow={setShown} />)}
          {shown && <Provenance pid={shown} run={run} />}
        </div>
      )}
    </li>
  )
}

function Fold({ title, items, run, open = false }: { title: string; items: TextItem[]; run: RunView; open?: boolean }) {
  if (!items.length) return null
  return (
    <details className="fold" open={open}>
      <summary>{title} <span className="muted">({items.length})</span></summary>
      <ul className="plain">{items.map((x, i) => <li key={i}><Segments segs={x.segments} run={run} /></li>)}</ul>
    </details>
  )
}

export function ReportView({ meta }: { meta: Metadata | null }) {
  const { run, phase } = useInvestigation()
  useFocusTopFinding(phase === 'finished' ? run : null, meta)
  if (!run || phase !== 'finished') return null
  const rep = run.report
  if (!rep) {
    return (
      <section className="card report">
        <header className="card-head"><h2>{run.status === 'needs_review' ? 'Needs review' : 'Run ended'}</h2></header>
        <div className="review">
          <p>{run.status === 'needs_review'
            ? 'The agent’s report failed the automatic checks and was not accepted. These problems were left unresolved:'
            : `The run stopped before a report: ${run.error ?? run.status}.`}</p>
          {run.problems.length > 0 && <ul className="plain">{run.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>}
          <p className="muted small">The analyses that did run are still in the charts and in the agent trace above.</p>
        </div>
      </section>
    )
  }
  return (
    <section className="card report">
      <ul className="findings">{rep.findings.map((f) => <FindingCard key={f.id} f={f} run={run} />)}</ul>
      {rep.background.length > 0 && (
        <div className="background">
          <h3>Background <span className="badge">{rep.background_label}</span></h3>
          <ul className="plain">{rep.background.map((x, i) => <li key={i}><Segments segs={x.segments} run={run} /></li>)}</ul>
        </div>
      )}
      {rep.statistical_support.length > 0 && (
        <>
          <h3>Statistical support</h3>
          <div className="table-wrap">
            <table className="stats">
              <thead><tr><th>Pair</th><th>Δ [95% CI]</th><th>q</th><th title="independent chunks of data (N / 2τ) per window">chunks A / B</th><th title="autocorrelation time per window">τ A / B</th><th>status</th></tr></thead>
              <tbody>
                {rep.statistical_support.map((s) => (
                  <tr key={`${s.finding}-${s.pair}`} className={s.supported ? '' : 'greyed'}>
                    <td className="nowrap">{s.pair}</td><td className="nowrap">{s.delta} {s.ci}</td><td>{s.q}</td>
                    <td className="nowrap">{s.n_blocks_a} / {s.n_blocks_b}</td>
                    <td className="nowrap">{s.tau_ns_a} / {s.tau_ns_b}</td>
                    <td>{s.supported ? (s.provisional ? 'supported (slow — provisional)' : 'supported') : 'not supported'}
                      {s.robustness && <div className="muted small">{s.robustness}</div>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <div className="folds">
        <Fold title="Caveats" items={rep.caveats} run={run} />
        <Fold title="Not established" items={rep.gaps.length ? rep.gaps : [{ text: 'None listed.', segments: [{ text: 'None listed.' }] }]} run={run} />
        <Fold title="Possible explanations (hypotheses)" items={rep.hypotheses} run={run} />
        {rep.follow_ups.length > 0 && (
          <details className="fold">
            <summary>Follow-ups <span className="muted">({rep.follow_ups.length})</span></summary>
            <ul className="plain">{rep.follow_ups.map((fu, i) => <li key={i}>{fu.pair && <b>{fu.pair}: </b>}{fu.rationale} <span className="tool-tag">{fu.tool}</span></li>)}</ul>
          </details>
        )}
      </div>
    </section>
  )
}
