// The report: a headline built by code from the stored tool results, then one card per contact
// (pair, before -> after bar, supported / not supported badge, robustness badge; CI, q, the finding
// text with provenance on hover, and sources under Details). Clicking a card highlights the pair in 3D
// and jumps to a frame that shows the change. Statistics, hypotheses, caveats, gaps and follow-ups
// follow, collapsed with their counts shown.
// A needs_review run shows its unresolved problems instead.
// Verification is not shown until a verifier result exists (SPEC §9 step 7, Day 6).
import { useEffect, useMemo, useState } from 'react'
import { api, type Finding, type Metadata, type RunView, type Segment, type TextItem } from '../api/client'
import { useInvestigation } from '../state/investigation'
import { getSelection, setSelection, useSelection } from '../state/selection'
import { hisNote, hisText } from '../state/residueKey'
import { buildCards, type Card } from '../state/cards'

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
    count('supported') + count('proven') && `${count('supported') + count('proven')} supported`,
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
 * Point the 3D view at a contact: highlight the pair and jump to a frame in the window that shows the
 * change (apart if the contact weakened, in contact if it strengthened; the median-distance frame among
 * those). The caption says why that frame was picked.
 */
async function focusCard(card: Card, meta: Metadata) {
  setSelection({ pair: { keyA: card.keyA, keyB: card.keyB, excluded: false } })
  const d = await api.distances(meta.dataset, card.keyA, card.keyB)
  const { cutoff, pair } = getSelection()
  if (pair?.keyA !== card.keyA || pair?.keyB !== card.keyB) return // the user picked something else meanwhile
  const mid = Math.floor(meta.n_frames / 2)
  const [w0, w1] = card.focusWindow ?? [mid, meta.n_frames]
  const change = card.stats ? card.stats.delta : card.freqs.length === 2 ? card.freqs[1].freq - card.freqs[0].freq : 0
  let idx = d.frames.map((_, i) => i).filter((i) => d.frames[i] >= w0 && d.frames[i] < w1)
  if (change !== 0) {
    const want = idx.filter((i) => (change < 0 ? d.distance[i] > cutoff : d.distance[i] <= cutoff))
    if (want.length) idx = want
  }
  if (!idx.length) return
  idx.sort((a, b) => d.distance[a] - d.distance[b])
  const i = idx[Math.floor(idx.length / 2)]
  const where = card.freqs.length === 2 ? card.freqs[1].name : 'Frame'
  const state = d.distance[i] > cutoff ? 'apart' : 'in contact'
  const text = `${where[0].toUpperCase()}${where.slice(1)}: ${hisText(card.label)} ${state} (${d.distance[i].toFixed(1)} Å)`
  setSelection({ frame: d.frames[i], focus: { frame: d.frames[i], text } })
}

function Segments({ segs, run }: { segs: Segment[]; run: RunView }) {
  return (
    <>
      {segs.map((s, i) => {
        if (!s.pid) return <span key={i} title={hisNote(s.text) ?? undefined}>{hisText(s.text)}</span>
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
      {hisText(id)} <span className="muted small">{res?.tool}</span>
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

function Sources({ f, run }: { f: Finding; run: RunView }) {
  const [shown, setShown] = useState<string | null>(null)
  return (
    <div className="citations">
      {f.evidence_ids.map((e) => <Citation key={e} id={e} run={run} onShow={setShown} />)}
      {shown && <Provenance pid={shown} run={run} />}
    </div>
  )
}

const pct = (x: number) => `${Math.round(x * 100)}%`
const signed = (x: number) => `${x < 0 ? '−' : '+'}${Math.abs(x).toFixed(3)}`

/** Before -> after contact frequency as two thin bars; the after bar is coloured only for a supported change. */
function FreqBars({ card }: { card: Card }) {
  const changed = card.badge === 'supported' && card.freqs.length === 2
  const lost = card.freqs.length === 2 && card.freqs[1].freq < card.freqs[0].freq
  return (
    <div className="fbars" aria-label={`in contact ${card.freqs.map((f) => `${f.name} ${pct(f.freq)}`).join(', ')}`}>
      {card.freqs.map((f, i) => (
        <div key={i} className="fbar">
          <span className="fbar-name">{f.name}</span>
          <span className="fbar-track">
            <span className={`fbar-fill ${i === 1 && changed ? (lost ? 'lost-bg' : 'gained-bg') : ''}`} style={{ width: `${Math.max(1, f.freq * 100)}%` }} />
          </span>
          <span className="fbar-val">{pct(f.freq)}</span>
        </div>
      ))}
    </div>
  )
}

function ContactCard({ card, run, meta, selected }: { card: Card; run: RunView; meta: Metadata; selected: boolean }) {
  const [open, setOpen] = useState(false)
  const s = card.stats
  return (
    <li className={`ccard ${card.badge} ${selected ? 'selected' : ''}`}>
      <button className="ccard-main" onClick={() => void focusCard(card, meta)} title="Show this contact in 3D">
        <span className="ccard-head">
          <span className="ccard-pair" title={hisNote(card.label) ?? undefined}>{hisText(card.label)}</span>
          <span className={`badge ${card.badge}`}>{card.badgeLabel}</span>
        </span>
        {card.freqs.length > 0 && <FreqBars card={card} />}
        {card.robustness && (
          <span className={`badge rob-${card.robustness.verdict.replace(' ', '-')}`} title={card.robustness.pid ? `from ${card.robustness.pid}` : undefined}>
            {card.robustness.label}
          </span>
        )}
      </button>
      <button className="link small ccard-more" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? 'Hide details ▾' : 'Details ▸'}
      </button>
      {open && (
        <div className="ccard-detail">
          {s && (
            <dl className="kv">
              <dt>Δ frequency</dt><dd>{signed(s.delta)}</dd>
              <dt>95% CI</dt><dd>[{signed(s.ci_low)}, {signed(s.ci_high)}]</dd>
              <dt>q (FDR)</dt><dd>{s.q.toPrecision(2)}</dd>
              <dt title="independent chunks of data (N / 2τ) per window">chunks</dt><dd>{s.n_blocks_a?.toFixed(0) ?? '–'} / {s.n_blocks_b?.toFixed(0) ?? '–'}</dd>
              <dt>τ</dt><dd>{s.tau_ns_a?.toFixed(1) ?? '–'} / {s.tau_ns_b?.toFixed(1) ?? '–'} ns</dd>
            </dl>
          )}
          <p className="ccard-text"><Segments segs={card.finding.segments} run={run} /></p>
          <Sources f={card.finding} run={run} />
        </div>
      )}
    </li>
  )
}

function NoteCard({ f, run }: { f: Finding; run: RunView }) {
  const [open, setOpen] = useState(false)
  return (
    <li className={`ccard note-card ${f.badge}`}>
      <div className="ccard-head"><span className={`badge ${f.badge}`}>{f.badge === 'proven' ? 'supported' : f.badge_label}</span></div>
      <p className="ccard-text"><Segments segs={f.segments} run={run} /></p>
      <button className="link small ccard-more" onClick={() => setOpen(!open)} aria-expanded={open}>
        {f.evidence_ids.length} source{f.evidence_ids.length === 1 ? '' : 's'} {open ? '▾' : '▸'}
      </button>
      {open && <Sources f={f} run={run} />}
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

/** One card per contact (click: highlight in 3D and jump to its frame), then notes, statistics and the folds. */
export function ReportView({ meta }: { meta: Metadata | null }) {
  const { run, phase } = useInvestigation()
  const { pair } = useSelection()
  const built = useMemo(
    () => (run?.report && meta ? buildCards(run, meta.n_frames, meta.dt_ns !== null) : null),
    [run, meta],
  )
  const top = built?.cards.find((c) => c.badge === 'supported') ?? built?.cards[0]
  useEffect(() => {
    if (top && meta && phase === 'finished') void focusCard(top, meta).catch(() => {})
  }, [run?.run_id, top?.id, meta, phase]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!run || phase !== 'finished' || !meta) return null
  const rep = run.report
  if (!rep || !built) {
    return (
      <section className="card report">
        <header className="card-head"><h2>{run.status === 'needs_review' ? 'Needs review' : 'Run ended'}</h2></header>
        <div className="review">
          <p>{run.status === 'needs_review'
            ? 'The agent’s report failed the automatic checks and was not accepted. These problems were left unresolved:'
            : `The run stopped before a report: ${run.error ?? run.status}.`}</p>
          {run.problems.length > 0 && <ul className="plain">{run.problems.map((p, i) => <li key={i}>{hisText(p)}</li>)}</ul>}
          <p className="muted small">The analyses that did run are still in the charts and in the agent trace above.</p>
        </div>
      </section>
    )
  }
  return (
    <section className="report">
      <ul className="ccards">
        {built.cards.map((c) => (
          <ContactCard key={c.id} card={c} run={run} meta={meta} selected={pair?.keyA === c.keyA && pair?.keyB === c.keyB} />
        ))}
        {built.notes.map((f) => <NoteCard key={f.id} f={f} run={run} />)}
      </ul>
      {rep.background.length > 0 && (
        <div className="background">
          <h3>Background <span className="badge">{rep.background_label}</span></h3>
          <ul className="plain">{rep.background.map((x, i) => <li key={i}><Segments segs={x.segments} run={run} /></li>)}</ul>
        </div>
      )}
      <div className="folds">
        {rep.statistical_support.length > 0 && (
          <details className="fold">
            <summary>Show statistics <span className="muted">({rep.statistical_support.length} {rep.statistical_support.length === 1 ? 'pair' : 'pairs'})</span></summary>
            <div className="table-wrap">
              <table className="stats">
                <thead><tr><th>Pair</th><th>Δ [95% CI]</th><th>q</th><th title="independent chunks of data (N / 2τ) per window">chunks A / B</th><th title="autocorrelation time per window">τ A / B</th><th>status</th></tr></thead>
                <tbody>
                  {rep.statistical_support.map((s) => (
                    <tr key={`${s.finding}-${s.pair}`} className={s.supported ? '' : 'greyed'}>
                      <td className="nowrap" title={hisNote(s.pair) ?? undefined}>{hisText(s.pair)}</td><td className="nowrap">{s.delta} {s.ci}</td><td>{s.q}</td>
                      <td className="nowrap">{s.n_blocks_a} / {s.n_blocks_b}</td>
                      <td className="nowrap">{s.tau_ns_a} / {s.tau_ns_b}</td>
                      <td>{s.supported ? (s.provisional ? 'supported (slow — provisional)' : 'supported') : 'not supported'}
                        {s.robustness && <div className="muted small">{s.robustness}</div>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}
        <Fold title="Caveats" items={rep.caveats} run={run} />
        <Fold title="Not established" items={rep.gaps.length ? rep.gaps : [{ text: 'None listed.', segments: [{ text: 'None listed.' }] }]} run={run} />
        <Fold title="Possible explanations (hypotheses)" items={rep.hypotheses} run={run} />
        {rep.follow_ups.length > 0 && (
          <details className="fold">
            <summary>Follow-ups <span className="muted">({rep.follow_ups.length})</span></summary>
            <ul className="plain">{rep.follow_ups.map((fu, i) => <li key={i}>{fu.pair && <b>{hisText(fu.pair)}: </b>}{hisText(fu.rationale)} <span className="tool-tag">{fu.tool}</span></li>)}</ul>
          </details>
        )}
      </div>
    </section>
  )
}
