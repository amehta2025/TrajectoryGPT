// The report: findings with proven/unsupported badges, robustness badges, values filled by code with
// provenance on hover, expandable citations (click -> highlight in 3D + show provenance), Gaps.
// A needs_review run shows its unresolved problems instead.
import { useState } from 'react'
import type { Finding, RunView, Segment, TextItem } from '../api/client'
import { useInvestigation } from '../state/investigation'
import { setSelection } from '../state/selection'

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
      </div>
      <p><Segments segs={f.segments} run={run} /></p>
      <button className="link small" onClick={() => setOpen(!open)}>
        {open ? '▾' : '▸'} {f.evidence_ids.length} citation{f.evidence_ids.length === 1 ? '' : 's'}
      </button>
      {open && (
        <div className="citations">
          {f.evidence_ids.map((e) => <Citation key={e} id={e} run={run} onShow={setShown} />)}
          {shown && <Provenance pid={shown} run={run} />}
        </div>
      )}
    </li>
  )
}

function Section({ title, items, run }: { title: string; items: TextItem[]; run: RunView }) {
  if (!items.length) return null
  return (
    <>
      <h3>{title}</h3>
      <ul className="plain">{items.map((x, i) => <li key={i}><Segments segs={x.segments} run={run} /></li>)}</ul>
    </>
  )
}

export function ReportView() {
  const { run, phase } = useInvestigation()
  if (!run || phase !== 'finished') return null
  const rep = run.report
  return (
    <section className="card report">
      <header className="card-head">
        <h2>Report</h2>
        <span className={`pill ${rep ? 'ok' : 'no'}`}>{run.status.replace('_', ' ')}</span>
      </header>
      {!rep && (
        <div className="refusal">
          <b>{run.status === 'needs_review' ? 'Needs review — unresolved problems:' : `Run ended: ${run.error ?? run.status}`}</b>
          <ul>{run.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
        </div>
      )}
      {rep && (
        <>
          <h3>Observed</h3>
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
              <table className="stats">
                <thead><tr><th>Pair</th><th>Δ</th><th>95% CI</th><th>q</th><th>chunks A/B</th><th>τ A/B</th><th>status</th><th>cutoffs</th></tr></thead>
                <tbody>
                  {rep.statistical_support.map((s) => (
                    <tr key={`${s.finding}-${s.pair}`} className={s.supported ? '' : 'greyed'}>
                      <td>{s.pair}</td><td>{s.delta}</td><td>{s.ci}</td><td>{s.q}</td>
                      <td title="independent chunks of data (N / 2τ) per window">{s.n_blocks_a}/{s.n_blocks_b}</td>
                      <td>{s.tau_ns_a}/{s.tau_ns_b}</td>
                      <td>{s.supported ? (s.provisional ? 'supported (slow — provisional)' : 'supported') : 'not supported'}</td>
                      <td>{s.robustness}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          <Section title="Possible explanations (hypotheses)" items={rep.hypotheses} run={run} />
          <Section title="Caveats" items={rep.caveats} run={run} />
          <Section title="Gaps — not established" items={rep.gaps.length ? rep.gaps : [{ text: 'None listed.', segments: [{ text: 'None listed.' }] }]} run={run} />
          {rep.follow_ups.length > 0 && (
            <>
              <h3>Follow-ups</h3>
              <ul className="plain">{rep.follow_ups.map((fu, i) => <li key={i}><code>{fu.tool}</code> {fu.pair} — {fu.rationale}</li>)}</ul>
            </>
          )}
        </>
      )}
    </section>
  )
}
