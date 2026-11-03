// The report in two layers.
// Main view: a plain headline and one card per contact, each a plain sentence plus its before -> after
// bar. Clicking a card highlights the pair in 3D and jumps to a frame that shows the change.
// "Show the science" (one toggle for the whole page) adds the technical layer: the rule for "supported",
// residue codes, badges, CI, q, chunks, tau, the agent's own finding text with provenance on hover,
// sources, the statistics table, caveats, gaps, hypotheses and follow-ups.
// Every sentence and number comes from stored tool results; nothing here changes a claim.
// A needs_review run shows a plain line, with its unresolved problems in the science layer.
// Verification is not shown until a verifier result exists (SPEC §9 step 7, Day 6).
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, type Finding, type Metadata, type RunView, type SandboxJob, type Segment, type TextItem } from '../api/client'
import { ask, toggleScience, useInvestigation } from '../state/investigation'
import { getSelection, setSelection, useSelection } from '../state/selection'
import { hisNote, hisText } from '../state/residueKey'
import { buildCards, touchAnswer, locatedSentence, locateNotes, markersFor, plainSentence, type Card, type Located } from '../state/cards'
import { MicButton } from './VoiceInput'
import { onTranscript } from '../state/voice'

type CompareData = {
  group_a: string; group_b: string; n_tested: number; n_filtered: number; n_supported: number
  n_supported_firm: number; n_supported_provisional: number; fdr: number; ci_level: number
  window_a: [number, number]; window_b: [number, number]; rows: { supported: boolean; delta: number }[]
}
type ContactsData = { group_a: string; group_b: string; any_contact_frequency?: number; window?: { start: number; stop: number | null } }
type SensitivityData = { cutoffs: number[]; n_robust: number; rows: unknown[] } // n_tested is per cutoff

/** The last successful result of a tool, with its UI data. */
function lastResult<T>(run: RunView, tool: string): T | null {
  const hits = Object.values(run.results).filter((r) => r.tool === tool && r.status === 'ok' && r.data_for_ui)
  return hits.length ? (hits[hits.length - 1].data_for_ui as T) : null
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
const range = (cs: number[]) => `${Math.min(...cs).toFixed(1)}–${Math.max(...cs).toFixed(1)} Å`

function isHalves(c: CompareData, nFrames: number | undefined): boolean {
  const mid = Math.floor((nFrames ?? 0) / 2)
  return c.window_a[0] === 0 && c.window_a[1] === mid && c.window_b[0] === mid
}

// Plain wording for the deterministic checks' refusals (record_finding / submit_report) and the run's own problems.
const PLAIN_PROBLEMS: [RegExp, string][] = [
  [/no accepted report within \d+ turns/, 'The agent ran out of steps before its report passed the checks.'],
  [/has no pair/, 'A finding pointed to a contact the analysis didn’t list.'],
  [/does not resolve/, 'A finding used a number the analysis didn’t produce.'],
  [/claims support|not supported/, 'A finding called a change real when the statistics didn’t back it.'],
  [/bare number|numeral/, 'A finding wrote a number by hand instead of using a computed one.'],
  [/robust/, 'A finding said a result holds at every threshold without that check.'],
  [/evidence|cites/, 'A finding cited evidence that doesn’t exist or didn’t run.'],
]

export function plainProblems(run: RunView): string[] {
  const raw: string[] = [...run.problems]
  for (const t of run.turns) {
    if (t.status !== 'refused') continue
    try { raw.push(...((JSON.parse(t.summary) as { problems?: string[] }).problems ?? [])) } catch { /* not JSON */ }
  }
  const out = raw.map((p) => PLAIN_PROBLEMS.find(([re]) => re.test(p))?.[1] ?? 'A finding failed an automatic check.')
  return [...new Set(out)]
}

/** One plain headline. Counts come from stored tool results; no units, codes or statistics words. */
export function Headline({ meta }: { meta: Metadata | null }) {
  const { run, phase } = useInvestigation()
  if (!run || phase !== 'finished') return null
  if (!run.report) {
    const why = plainProblems(run)
    return (
      <div className="headline">
        <p className="headline-main">This report couldn’t be finished: the automatic checks did not accept it.</p>
        {why.length > 0 && <ul className="plain why">{why.map((w) => <li key={w}>{w}</li>)}</ul>}
      </div>
    )
  }
  const cmp = lastResult<CompareData>(run, 'compare_intervals')
  const sens = lastResult<SensitivityData>(run, 'cutoff_sensitivity')
  const findings = run.report.findings
  let main: ReactNode
  // A run that measured whether two groups touch answers the question directly (no comparison needed).
  const touch = lastResult<ContactsData>(run, 'calculate_contacts')
  if (!cmp && touch && typeof touch.any_contact_frequency === 'number') {
    const w = touch.window
    const whole = !w || (w.start === 0 && (w.stop === null || w.stop >= (meta?.n_frames ?? Infinity)))
    const scope = whole ? (meta?.dt_ns === null ? 'the path' : 'the run') : 'this part of the run'
    main = touchAnswer(run.question, touch.group_a, touch.group_b, touch.any_contact_frequency, scope)
  } else if (cmp) {
    const sup = cmp.rows.filter((r) => r.supported)
    const weaker = sup.filter((r) => r.delta < 0).length
    const stronger = sup.length - weaker
    const when = isHalves(cmp, meta?.n_frames) ? 'between the first and second half' : 'between the two windows'
    main = cmp.n_supported === 0
      ? <>None of the {cmp.n_tested} {cmp.group_a}–{cmp.group_b} contacts we tested changed by more than random variation.</>
      : <>
          {cmp.n_supported} of {cmp.n_tested} {cmp.group_a}–{cmp.group_b} contacts changed {when}:{' '}
          {weaker > 0 && <span className="lost">{weaker} weakened</span>}
          {weaker > 0 && stronger > 0 && ', '}
          {stronger > 0 && <span className="gained">{stronger} strengthened</span>}.
          {sens && <> {sens.n_robust} of the {sens.rows.length} we double-checked hold at every distance threshold we tried.</>}
        </>
  } else {
    const count = (...b: Finding['badge'][]) => findings.filter((f) => b.includes(f.badge)).length
    const descriptive = count('descriptive')
    if (descriptive === findings.length) {
      main = meta?.stats_valid === false
        ? <>{plural(findings.length, 'observation')} from a path that isn’t evenly spaced in time, so nothing here is statistically tested.</>
        : <>{plural(findings.length, 'observation')}; no statistical test was part of this question.</>
    } else {
      const parts = [
        count('supported', 'proven') && `${count('supported', 'proven')} hold up`,
        count('unsupported', 'null') && `${count('unsupported', 'null')} within random variation`,
        descriptive && `${descriptive} descriptive`,
      ].filter(Boolean)
      main = <>{plural(findings.length, 'finding')}: {parts.join(', ')}.</>
    }
  }
  // Muse's own answer when it passed the checks; the code-built sentence above is the fallback.
  const written = run.report.headline
  const interp = run.report.interpretation
  const v = run.verification?.status
  const flagged = v === 'flagged' // a material issue the second AI raised; nothing in the app resolves it
  const repro = lastResult<{ n_values: number; match: boolean }>(run, 'reproduction_check')
  const unresolved = 'A second AI flagged a finding, and it is unresolved: review it below before relying on this report.'
  const lead = repro
    ? repro.match
      ? flagged ? `The numbers match the published result. ${unresolved}` : 'We reproduced the published result and it matches the original.'
      : 'We could not reproduce the published result: the values differ from the original.'
    : flagged ? unresolved : null
  return (
    <section className={`card result-banner headline ${flagged || (repro && !repro.match) ? 'warn' : v === 'verified' ? 'ok' : ''}`}>
      <header className="rb-head">
        <span className="rb-icon" aria-hidden>{flagged || (repro && !repro.match) ? '!' : '✓'}</span>
        <div className="rb-titles">
          <h2>{flagged ? 'Analysis complete: needs your review' : 'Analysis complete'}</h2>
          {lead && <p className="rb-lead">{lead}</p>}
        </div>
        {repro && <span className={`pill ${repro.match ? 'ok' : 'no'}`} title="Every computed value compared with the published code's output">
          {repro.match ? `✓ Match: ${repro.n_values}/${repro.n_values} values` : '✕ Values differ'}</span>}
      </header>
      <div className="rb-grid">
        <Tiles run={run} meta={meta} />
        <div className="rb-finding">
          <h3>Key finding</h3>
          <p className="headline-main">{written ? <Filled segments={written.segments} /> : main}</p>
        </div>
      </div>
      {(run.plan?.notes ?? []).map((n) => <p key={n} className="plan-note">Note: {n}</p>)}
      {interp && (
        <p className="headline-interp"><span className="interp-label">Interpretation, not computed:</span> {interp.text}</p>
      )}
      <p className="headline-note">{meta?.stats_valid === false
        ? 'These results describe one biased simulation path between two shapes, not an equilibrium ensemble, so nothing here is statistically tested.'
        : 'Based on one simulation: findings describe this run, not the protein in general.'}</p>
      <VerifierLine run={run} cardOf={cardNumbers(run, meta)} />
    </section>
  )
}

/** Two or three headline numbers, all taken from stored tool results. */
function Tiles({ run, meta }: { run: RunView; meta: Metadata | null }) {
  const nc = lastResult<{ n_native: number[]; frames: number[]; ref_frames: number[] }>(run, 'native_contacts')
  const cmp = lastResult<CompareData>(run, 'compare_intervals')
  const sens = lastResult<SensitivityData>(run, 'cutoff_sensitivity')
  const tiles: [string, string][] = []
  if (nc?.n_native?.length) tiles.push([nc.n_native[0].toLocaleString(), nc.ref_frames?.[0] === 0 ? 'Contacts in the starting shape' : 'Contacts in the reference shape'])
  if (nc?.frames?.length) tiles.push([nc.frames.length.toLocaleString(), meta?.dt_ns === null ? 'Steps along the path' : 'Simulation frames'])
  if (cmp) {
    tiles.push([String(cmp.n_supported), 'Contacts that changed'])
    tiles.push([String(cmp.n_tested), 'Contacts tested'])
    if (sens) tiles.push([String(sens.n_robust), 'Hold at every distance threshold'])
  }
  if (!tiles.length) return null
  return (
    <div className="tiles">
      {tiles.slice(0, cmp ? 3 : 2).map(([n, label]) => <div key={label} className="tile"><span className="tile-n">{n}</span><span className="tile-label">{label}</span></div>)}
    </div>
  )
}

/** Finding id -> the number on its card and 3D marker (as in the main view), when it has one. */
function cardNumbers(run: RunView, meta: Metadata | null): Map<string, number> {
  const out = new Map<string, number>()
  if (!meta || !run.report) return out
  const built = buildCards(run, meta.n_frames, meta.dt_ns !== null)
  const located = locateNotes(built.notes).located
  built.cards.forEach((c, i) => [c.finding, ...c.others].forEach((f) => !out.has(f.id) && out.set(f.id, i + 1)))
  located.forEach((l, i) => out.set(l.finding.id, built.cards.length + i + 1))
  return out
}

/** The second model's check, in plain words (SPEC §9 step 7). Details are under "Show the science". */
// The backend gives up after 2 minutes; this backstop covers a lost connection (red-team E).
const VERIFY_GIVE_UP_S = 150

function VerifierLine({ run, cardOf }: { run: RunView; cardOf: Map<string, number> }) {
  const v = run.verification
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (v?.status !== 'pending') return
    const t = window.setInterval(() => setNow(Date.now() / 1000), 5000)
    return () => window.clearInterval(t)
  }, [v?.status])
  const stale = v?.status === 'pending' && !!v.started_at && now - v.started_at > VERIFY_GIVE_UP_S
  // One line per flagged finding (several flags on one finding say one thing in plain words).
  const byFinding = new Map<string, string>()
  for (const f of (v?.flags ?? []).filter((x) => x.severity === 'major')) {
    if (!byFinding.has(f.finding_id)) byFinding.set(f.finding_id, f.plain || f.problem)
  }
  let line: ReactNode
  if (v?.status === 'timeout' || stale) line = <span className="verify muted">The second AI check couldn't finish, so this report has not been double-checked.</span>
  else if (!v || v.status === 'pending') line = <span className="verify pending">A second AI model is checking this report…</span>
  else if (v.status === 'verified') line = <span className="verify ok">✓ A second AI model checked this report: no problems found.</span>
  else if (v.status === 'flagged') {
    const n = byFinding.size
    line = (
      <span className="verify flagged">⚠ A second AI model checked this report and flagged {n === 1 ? 'one finding' : `${n} findings`}:{' '}
        {[...byFinding].map(([fid, text]) => (
          <span key={fid} className="flag-item">
            {cardOf.has(fid) && <span className="marker-num" aria-label={`marker ${cardOf.get(fid)}`}>{cardOf.get(fid)}</span>}
            {hisText(text)}{' '}
          </span>
        ))}
      </span>
    )
  } else line = <span className="verify muted">The second AI check could not run, so this report has not been double-checked.</span>
  if (run.status === 'needs_review') return null
  return (
    <p className="verify-line">
      {line}
      {run.planted && <span className="planted-note"> This run includes a deliberately wrong finding, added to show the check catching it.</span>}
    </p>
  )
}

/** Science layer: the verifier's full result. */
function VerifierDetail({ run }: { run: RunView }) {
  const v = run.verification
  if (!v || v.status === 'pending') return null
  return (
    <div className="verifier-detail">
      <h3>Second-model check <span className="badge">{v.status}</span></h3>
      {v.summary && <p>{hisText(v.summary)}</p>}
      {(v.flags ?? []).length > 0 && (
        <ul className="plain">
          {v.flags!.map((f, i) => (
            <li key={i}><span className={`badge ${f.severity === 'major' ? 'unsupported' : 'descriptive'}`}>{f.severity}</span>{' '}
              {f.finding_id}: “{hisText(f.quote)}” — {hisText(f.problem)}</li>
          ))}
        </ul>
      )}
      {v.error && <p className="muted small">{v.error}</p>}
      {run.planted && <p className="small">Planted for the demo (development toggle): finding {run.planted}.</p>}
      <p className="muted small">
        {v.usage?.model ?? 'muse-wonder-1.0'} · effort {v.usage?.effort ?? 'high'} · read the report, the cited values and{' '}
        {v.figures?.length ?? 0} plot{v.figures?.length === 1 ? '' : 's'} ({v.figures?.join(', ')}) · {v.wall_s ?? '?'} s ·{' '}
        {v.usage?.cost_usd != null ? `$${v.usage.cost_usd.toFixed(4)}` : 'cost n/a'} (logged separately)
      </p>
    </div>
  )
}

/** Filled placeholder text; each computed value shows where it came from on hover. */
function Filled({ segments }: { segments: Segment[] }) {
  return <>{segments.map((s, i) => (s.pid ? <span key={i} className="filled" title={`computed: ${s.path}`}>{s.text}</span> : s.text))}</>
}

/** Muse's plain sentence for a finding about one contact; the code-built sentence for anything else. */
function cardSentence(card: Card, groups: Metadata['groups']): string {
  return card.finding.pairs.length <= 1 && card.finding.plain ? card.finding.plain.text : plainSentence(card, groups)
}

/**
 * Point the 3D view at a contact: highlight the pair and jump to a frame in the window that shows the
 * change (apart if the contact weakened, in contact if it strengthened; the median-distance frame among
 * those). The caption says why that frame was picked, in technical and in plain words.
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
  const apart = d.distance[i] > cutoff
  const text = `${where[0].toUpperCase()}${where.slice(1)}: ${hisText(card.label)} ${apart ? 'apart' : 'in contact'} (${d.distance[i].toFixed(1)} Å)`
  const plainWhere = { '2nd half': ' in the second half', 'late path': ' late in the path', 'window B': ' in the second window' }[where] ?? ''
  const plain = `A moment${plainWhere} when this contact is ${apart ? 'broken' : 'formed'}`
  setSelection({ frame: d.frames[i], focus: { frame: d.frames[i], text, plain } })
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
const BAR_NAME: Record<string, string> = { '1st half': 'first half', '2nd half': 'second half', 'window A': 'first window', 'window B': 'second window' }

/** Before -> after share of time in contact as two thin bars; the after bar is coloured only for a supported change. */
function FreqBars({ card }: { card: Card }) {
  const changed = (card.badge === 'supported' || card.badge === 'proven') && card.freqs.length === 2
  const lost = card.freqs.length === 2 && card.freqs[1].freq < card.freqs[0].freq
  return (
    <div className="fbars" aria-label={`in contact ${card.freqs.map((f) => `${f.name} ${pct(f.freq)}`).join(', ')}`}>
      {card.freqs.map((f, i) => (
        <div key={i} className="fbar">
          <span className="fbar-name">{BAR_NAME[f.name] ?? f.name}</span>
          <span className="fbar-track">
            <span className={`fbar-fill ${i === 1 && changed ? (lost ? 'lost-bg' : 'gained-bg') : ''}`} style={{ width: `${Math.max(1, f.freq * 100)}%` }} />
          </span>
          <span className="fbar-val">{pct(f.freq)}</span>
        </div>
      ))}
    </div>
  )
}

function ContactCard({ card, n, run, meta, selected, science }: { card: Card; n: number; run: RunView; meta: Metadata; selected: boolean; science: boolean }) {
  const s = card.stats
  return (
    <li className={`ccard ${card.badge} ${selected ? 'selected' : ''}`}>
      <button className="ccard-main" onClick={() => void focusCard(card, meta)} title="Show this contact in 3D">
        <span className="ccard-sentence"><span className="marker-num" aria-label={`marker ${n}`}>{n}</span>{cardSentence(card, meta.groups)}</span>
        {card.freqs.length > 0 && <FreqBars card={card} />}
      </button>
      {science && (
        <div className="ccard-detail">
          <div className="ccard-head">
            <span className="ccard-pair" title={hisNote(card.label) ?? undefined}>{hisText(card.label)}</span>
            <span className="badges">
              <span className={`badge ${card.badge}`}>{card.badgeLabel}</span>
              {card.robustness && (
                <span className={`badge rob-${card.robustness.verdict.replace(' ', '-')}`} title={card.robustness.pid ? `from ${card.robustness.pid}` : undefined}>
                  {card.robustness.label}
                </span>
              )}
            </span>
          </div>
          {s && (
            <dl className="kv">
              <dt>Δ frequency</dt><dd>{signed(s.delta)}</dd>
              <dt>95% CI</dt><dd>[{signed(s.ci_low)}, {signed(s.ci_high)}]</dd>
              <dt>q (FDR)</dt><dd>{s.q.toPrecision(2)}</dd>
              <dt title="independent chunks of data (N / 2τ) per window">chunks</dt><dd>{s.n_blocks_a?.toFixed(0) ?? '–'} / {s.n_blocks_b?.toFixed(0) ?? '–'}</dd>
              <dt>τ</dt><dd>{s.tau_ns_a?.toFixed(1) ?? '–'} / {s.tau_ns_b?.toFixed(1) ?? '–'} ns</dd>
            </dl>
          )}
          {[card.finding, ...card.others].map((f) => (
            <div key={f.id}>
              <p className="ccard-text"><Segments segs={f.segments} run={run} /></p>
              <Sources f={f} run={run} />
            </div>
          ))}
        </div>
      )}
    </li>
  )
}

/** A finding that names residues but no single pair: its number (also drawn in 3D) and a plain sentence. */
function LocatedCard({ l, n, run, science }: { l: Located; n: number; run: RunView; science: boolean }) {
  return (
    <li className="ccard located">
      <span className="ccard-sentence"><span className="marker-num" aria-label={`marker ${n}`}>{n}</span>{l.finding.plain?.text ?? locatedSentence(l, run)}</span>
      {science && (
        <div className="ccard-detail">
          <p className="ccard-text"><Segments segs={l.finding.segments} run={run} /></p>
          <Sources f={l.finding} run={run} />
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

/** Main view: one plain card per contact (click: highlight in 3D and jump to its frame). */
export function ReportView({ meta }: { meta: Metadata | null }) {
  const { run, phase, science } = useInvestigation()
  const { pair } = useSelection()
  const built = useMemo(
    () => (run?.report && meta ? buildCards(run, meta.n_frames, meta.dt_ns !== null) : null),
    [run, meta],
  )
  const located = useMemo(() => (built ? locateNotes(built.notes).located : []), [built])
  const markers = useMemo(() => (built ? markersFor(built.cards, located) : []), [built, located])
  const top = built?.cards.find((c) => c.badge === 'supported' || c.badge === 'proven') ?? built?.cards[0]
  useEffect(() => {
    if (top && meta && phase === 'finished') void focusCard(top, meta).catch(() => {})
  }, [run?.run_id, top?.id, meta, phase]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    setSelection({ markers: phase === 'finished' ? markers : [] })
  }, [markers, phase])

  if (!run || phase !== 'finished' || !meta || !built) return null
  if (!built.cards.length && !located.length) {
    return (
      <section className="report">
        <p className="muted report-empty">None of these findings points to one place in the protein; they are under “Details &amp; methods”.</p>
        <NextQuestions run={run} meta={meta} />
        <Reproduce run={run} science={science} />
      </section>
    )
  }
  return (
    <section className="report">
      <ul className="ccards">
        {built.cards.map((c, i) => (
          <ContactCard key={c.id} card={c} n={i + 1} run={run} meta={meta} science={science}
            selected={pair?.keyA === c.keyA && pair?.keyB === c.keyB} />
        ))}
        {located.map((l, i) => <LocatedCard key={l.finding.id} l={l} n={built.cards.length + i + 1} run={run} science={science} />)}
      </ul>
      <NextQuestions run={run} meta={meta} />
      <Reproduce run={run} science={science} />
    </section>
  )
}

/** One click: a script that recomputes every number; on the deployed app, also re-run it in a clean sandbox. */
function Reproduce({ run, science }: { run: RunView; science: boolean }) {
  const [canSandbox, setCanSandbox] = useState(false)
  const [job, setJob] = useState<SandboxJob>({ status: 'none' })
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    api.health().then((h) => alive && setCanSandbox(h.sandbox), () => {})
    api.sandbox(run.run_id).then((j) => alive && setJob(j), () => {})
    return () => { alive = false }
  }, [run.run_id])
  useEffect(() => {
    if (job.status !== 'running') return
    const t = setInterval(() => void api.sandbox(run.run_id).then(setJob, () => {}), 3000)
    return () => clearInterval(t)
  }, [job.status, run.run_id])
  const check = async () => {
    setErr(null)
    try { setJob(await api.sandboxStart(run.run_id)) } catch (e) { setErr((e as Error).message) }
  }
  return (
    <div className="reproduce">
      <a className="button-like" href={api.exportUrl(run.run_id)} download>Download a script that recomputes every number</a>
      {canSandbox && job.status !== 'running' && (
        <button onClick={() => void check()}>{job.status === 'none' ? 'Check it in a clean cloud sandbox' : 'Check again'}</button>
      )}
      {job.status === 'running' && <span className="muted">Re-running the analysis from the raw trajectory in a clean sandbox… (about a minute)</span>}
      {job.status === 'done' && (
        <span className={job.reproduced ? 'repro-ok' : 'repro-bad'}>
          {job.reproduced
            ? `Reproduced in a clean sandbox: all ${job.n_values} numbers match.`
            : `Not reproduced: ${job.n_match ?? '?'} of ${job.n_values ?? '?'} numbers match.`}
        </span>
      )}
      {job.status === 'error' && <span className="repro-bad">The sandbox check could not run.</span>}
      {err && <span className="repro-bad">{err}</span>}
      {science && job.output && (
        <details className="fold"><summary>Sandbox output</summary><pre className="repro-out">{job.output}</pre></details>
      )}
      {science && job.error && <p className="muted small">{job.error}</p>}
    </div>
  )
}

/** Suggested next questions (written by Muse, checked by code) and a follow-up box. Each goes through plan approval. */
function NextQuestions({ run, meta }: { run: RunView; meta: Metadata }) {
  const [text, setText] = useState('')
  useEffect(() => onTranscript(setText), []) // a spoken follow-up lands here for review
  const suggestions = run.report?.next_questions ?? []
  const go = (q: string) => q.trim() && void ask(q.trim(), meta.dataset, true)
  return (
    <div className="next-qs">
      <h3>Ask next</h3>
      {suggestions.length > 0 && (
        <div className="next-q-buttons">
          {suggestions.map((q) => <button key={q} className="next-q" onClick={() => go(q)}>{hisText(q)}</button>)}
        </div>
      )}
      <form className="follow-up" onSubmit={(e) => { e.preventDefault(); go(text) }}>
        <input value={text} onChange={(e) => setText(e.target.value)} maxLength={2000}
          placeholder="Ask a follow-up, e.g. “Does that persist?”" aria-label="Follow-up question" />
        <MicButton />
        <button type="submit" disabled={!text.trim()}>Ask</button>
      </form>
      <p className="muted small">Follow-ups use this investigation’s settings and the contact you selected. You approve the plan before it runs.</p>
    </div>
  )
}

/** The one toggle for all technical detail on the page. */
export function ScienceToggle() {
  const { science, phase } = useInvestigation()
  if (phase !== 'running' && phase !== 'finished') return null
  return (
    <button className="science-toggle" onClick={toggleScience} aria-expanded={science}>
      {science ? 'Hide the science ▴' : 'Show the science ▾'}
    </button>
  )
}

/** Science layer of the report: the "supported" rule, findings not tied to one contact, statistics and the folds. */
export function ScienceReport({ meta }: { meta: Metadata | null }) {
  const { run, phase } = useInvestigation()
  const built = useMemo(
    () => (run?.report && meta ? buildCards(run, meta.n_frames, meta.dt_ns !== null) : null),
    [run, meta],
  )
  if (!run || phase !== 'finished') return null
  const rep = run.report
  if (!rep) {
    return (
      <section className="card report-science">
        <h2>Why the report was not accepted</h2>
        <p>{run.status === 'needs_review'
          ? 'The agent’s report failed the automatic checks. These problems were left unresolved:'
          : `The run stopped before a report: ${run.error ?? run.status}.`}</p>
        {run.problems.length > 0 && <ul className="plain review-problems">{run.problems.map((p, i) => <li key={i}>{hisText(p)}</li>)}</ul>}
      </section>
    )
  }
  const cmp = lastResult<CompareData>(run, 'compare_intervals')
  const sens = lastResult<SensitivityData>(run, 'cutoff_sensitivity')
  return (
    <section className="card report-science">
      <VerifierDetail run={run} />
      <h2>Findings in detail</h2>
      {cmp && (
        <p className="small muted">
          Supported means FDR-corrected q &lt; {cmp.fdr} and a {Math.round(cmp.ci_level * 100)}% interval, corrected for
          autocorrelation, that excludes zero · {cmp.n_supported_firm} firm, {cmp.n_supported_provisional} provisional ·{' '}
          {cmp.n_filtered.toLocaleString()} rarely-formed pairs not tested
          {sens && <> · robustness checked at {range(sens.cutoffs)}</>}
        </p>
      )}
      {built && locateNotes(built.notes).unlocated.length > 0 && (
        <ul className="plain notes">
          {locateNotes(built.notes).unlocated.map((f) => (
            <li key={f.id}><span className={`badge ${f.badge}`}>{f.badge === 'proven' ? 'supported' : f.badge_label}</span>{' '}
              <Segments segs={f.segments} run={run} /> <Sources f={f} run={run} /></li>
          ))}
        </ul>
      )}
      {rep.background.length > 0 && (
        <div className="background">
          <h3>Background <span className="badge">{rep.background_label}</span></h3>
          <ul className="plain">{rep.background.map((x, i) => <li key={i}><Segments segs={x.segments} run={run} /></li>)}</ul>
        </div>
      )}
      {rep.statistical_support.length > 0 && (
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
      )}
      <div className="folds">
        <Fold title="Caveats" items={rep.caveats} run={run} open />
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
