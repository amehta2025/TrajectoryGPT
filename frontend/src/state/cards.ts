// One card per contact for the report. A finding can cite several pairs; each pair gets its own card
// sharing that finding's text. Before -> after contact frequencies come only from stored tool results:
// compare_intervals rows, contact_statistics results, or the cited contact maps (descriptive datasets).
// Nothing here computes new statistics.
import type { Finding, Robustness, RunView } from '../api/client'
import { hisText, residueLabel } from './residueKey'

export type Freq = { name: string; freq: number }
export type PairStats = {
  delta: number; ci_low: number; ci_high: number; q: number; supported: boolean; provisional: boolean
  n_blocks_a: number | null; n_blocks_b: number | null; tau_ns_a: number | null; tau_ns_b: number | null
}
export type Card = {
  id: string
  label: string // backend pair label, e.g. "HSE134-ASP118"
  keyA: string
  keyB: string
  finding: Finding
  others: Finding[] // further findings about the same contact (merged into this card)
  badge: Finding['badge']
  badgeLabel: string
  robustness: Robustness | null
  freqs: Freq[] // in window order; two entries give a before -> after bar
  stats: PairStats | null
  /** Window that shows the change (window B), for picking the frame to show in 3D. */
  focusWindow: [number, number] | null
}

type CompareRow = PairStats & { key_a: string; key_b: string; freq_a: number; freq_b: number }
type CompareUi = { rows: CompareRow[]; window_a: [number, number]; window_b: [number, number] }
type MapUi = { keys_a: string[]; keys_b: string[]; frequency: number[][]; window?: unknown }
type StatUi = { key_a: string; key_b: string; frequency: number; window: { start: number; stop: number | null } }

const PAIR_IN_TEXT = /\b([A-Z]{3}\d+)[-–]([A-Z]{3}\d+)\b/g

function windowOf(w: unknown): [number, number] | null {
  if (Array.isArray(w) && w.length >= 2) return [Number(w[0]), Number(w[1])]
  if (w && typeof w === 'object' && 'start' in w) {
    const o = w as { start: number; stop: number | null }
    return [o.start, o.stop ?? Number.POSITIVE_INFINITY]
  }
  return null
}

/** "1st half" / "2nd half" when the windows split the run in two, "early path" / "late path" without time. */
export function windowNames(a: [number, number], b: [number, number], nFrames: number, hasTime: boolean): [string, string] {
  const mid = Math.floor(nFrames / 2)
  const halves = a[0] === 0 && a[1] === mid && b[0] === mid && (b[1] === nFrames || !Number.isFinite(b[1]))
  if (halves) return hasTime ? ['1st half', '2nd half'] : ['early path', 'late path']
  return hasTime ? ['window A', 'window B'] : ['earlier steps', 'later steps']
}

function sameRes(key: string, label: string): boolean {
  return hisText(residueLabel(key)) === hisText(label)
}

/** The pair's keys from a cited result, or by matching the residue names against a cited contact map. */
function resolve(run: RunView, f: Finding, label: string): { keyA: string; keyB: string } | null {
  for (const e of f.evidence_ids) {
    const p = run.results[e.split('.')[0]]?.pairs[label]
    if (p) return { keyA: p.key_a, keyB: p.key_b }
  }
  const [ra, rb] = label.split(/[-–]/)
  for (const e of f.evidence_ids) {
    const ui = run.results[e.split('.')[0]]?.data_for_ui as Partial<MapUi> | undefined
    const all = [...(ui?.keys_a ?? []), ...(ui?.keys_b ?? [])]
    const a = all.find((k) => sameRes(k, ra))
    const b = all.find((k) => sameRes(k, rb))
    if (a && b) return { keyA: a, keyB: b }
  }
  return null
}

function mapFreq(ui: MapUi, keyA: string, keyB: string): number | null {
  let i = ui.keys_a.indexOf(keyA)
  let j = ui.keys_b.indexOf(keyB)
  if (i < 0 || j < 0) {
    i = ui.keys_a.indexOf(keyB)
    j = ui.keys_b.indexOf(keyA)
  }
  return i < 0 || j < 0 ? null : ui.frequency[i][j]
}

type DiffUi = { keys_a: string[]; keys_b: string[]; freq_a: number[][]; freq_b: number[][]; window_a: [number, number]; window_b: [number, number] }

/** Both windows' frequencies for a pair from a contact_difference_map result this finding cites. */
function diffFor(run: RunView, f: Finding, keyA: string, keyB: string) {
  for (const e of f.evidence_ids) {
    const r = run.results[e.split('.')[0]]
    if (!r || r.tool !== 'contact_difference_map' || r.status !== 'ok' || !r.data_for_ui) continue
    const ui = r.data_for_ui as DiffUi
    const fa = mapFreq({ keys_a: ui.keys_a, keys_b: ui.keys_b, frequency: ui.freq_a }, keyA, keyB)
    const fb = mapFreq({ keys_a: ui.keys_a, keys_b: ui.keys_b, frequency: ui.freq_b }, keyA, keyB)
    if (fa !== null && fb !== null) return { fa, fb, window_a: ui.window_a, window_b: ui.window_b }
  }
  return null
}

export function buildCards(run: RunView, nFrames: number, hasTime: boolean): { cards: Card[]; notes: Finding[] } {
  const cards: Card[] = []
  const notes: Finding[] = []
  const results = Object.values(run.results).filter((r) => r.status === 'ok' && r.data_for_ui)
  const compares = results.filter((r) => r.tool === 'compare_intervals').map((r) => r.data_for_ui as CompareUi)

  for (const f of run.report?.findings ?? []) {
    const labels = f.pairs.length ? f.pairs : [...new Set([...f.text.matchAll(PAIR_IN_TEXT)].map((m) => `${m[1]}-${m[2]}`))]
    let added = 0
    for (const label of labels) {
      const keys = resolve(run, f, label)
      if (!keys) continue
      const { keyA, keyB } = keys
      let freqs: Freq[] = []
      let stats: PairStats | null = null
      let focusWindow: [number, number] | null = null

      const cmp = compares.find((c) => c.rows.some((r) => (r.key_a === keyA && r.key_b === keyB) || (r.key_a === keyB && r.key_b === keyA)))
      const row = cmp?.rows.find((r) => (r.key_a === keyA && r.key_b === keyB) || (r.key_a === keyB && r.key_b === keyA))
      if (cmp && row) {
        const [na, nb] = windowNames(cmp.window_a, cmp.window_b, nFrames, hasTime)
        freqs = [{ name: na, freq: row.freq_a }, { name: nb, freq: row.freq_b }]
        stats = row
        focusWindow = cmp.window_b
      } else if (diffFor(run, f, keyA, keyB)) {
        // A cited difference map holds both windows' frequencies (raw, untested).
        const d = diffFor(run, f, keyA, keyB)!
        const [na, nb] = windowNames(d.window_a, d.window_b, nFrames, hasTime)
        freqs = [{ name: na, freq: d.fa }, { name: nb, freq: d.fb }]
        focusWindow = d.window_b
      } else {
        // Single-pair statistics or contact maps cited by this finding, in window order.
        const pts: { w: [number, number]; freq: number }[] = []
        for (const e of f.evidence_ids) {
          const r = run.results[e.split('.')[0]]
          if (!r || r.status !== 'ok' || !r.data_for_ui) continue
          if (r.tool === 'contact_statistics') {
            const ui = r.data_for_ui as StatUi
            const w = windowOf(ui.window)
            if (w && ((ui.key_a === keyA && ui.key_b === keyB) || (ui.key_a === keyB && ui.key_b === keyA))) pts.push({ w, freq: ui.frequency })
          } else if (r.tool === 'calculate_contacts') {
            const ui = r.data_for_ui as MapUi
            const w = windowOf(ui.window)
            const v = mapFreq(ui, keyA, keyB)
            if (w && v !== null && !pts.some((p) => p.w[0] === w[0] && p.w[1] === w[1])) pts.push({ w, freq: v })
          }
        }
        pts.sort((a, b) => a.w[0] - b.w[0])
        if (pts.length >= 2) {
          const [first, last] = [pts[0], pts[pts.length - 1]]
          const [na, nb] = windowNames(first.w, last.w, nFrames, hasTime)
          freqs = [{ name: na, freq: first.freq }, { name: nb, freq: last.freq }]
          focusWindow = last.w
        } else if (pts.length === 1) {
          // Only one window is cited: name it, so a lone bar is never read as the whole run.
          const mid = Math.floor(nFrames / 2)
          const [first, second] = windowNames([0, mid], [mid, nFrames], nFrames, hasTime)
          const w = pts[0].w
          const whole = w[0] === 0 && w[1] >= nFrames
          const name = whole ? (hasTime ? 'whole run' : 'whole path') : w[0] === 0 && w[1] === mid ? first : w[0] === mid ? second : 'part of the run'
          freqs = [{ name, freq: pts[0].freq }]
          focusWindow = w
        }
      }

      // A finding's badge applies to the pairs it names; where a pair has its own stored test, show that.
      let badge = f.badge === 'proven' ? 'supported' : f.badge
      let badgeLabel = f.badge === 'proven' ? 'supported' : f.badge_label
      if (stats && f.pairs.length > 1) {
        badge = stats.supported ? 'supported' : 'unsupported'
        badgeLabel = stats.supported ? 'supported' : 'not supported — consistent with noise'
      }
      if (stats?.supported && stats.provisional && badge === 'supported') badgeLabel = 'supported · slow, provisional'
      cards.push({
        id: `${f.id}:${label}`, label, keyA, keyB, finding: f, badge, badgeLabel,
        robustness: f.robustness.find((r) => r.pair === label) ?? null,
        freqs, stats, focusWindow, others: [],
      })
      added++
    }
    if (!added) notes.push(f)
  }
  // One card per contact: when several findings name the same pair, keep the card with the most windows
  // (a before -> after bar beats a single bar) and carry the other findings along for the science layer.
  const merged = new Map<string, Card>()
  for (const c of cards) {
    const key = [c.keyA, c.keyB].sort().join('|')
    const prev = merged.get(key)
    if (!prev) merged.set(key, c)
    else if (c.freqs.length > prev.freqs.length) merged.set(key, { ...c, others: [prev.finding, ...prev.others, ...c.others] })
    else prev.others.push(c.finding)
  }
  return { cards: [...merged.values()], notes }
}

// ---------- plain language (no numbers, units, residue codes or status words) ----------

const WINDOW_WORDS: Record<string, string> = {
  '1st half': 'the first half', '2nd half': 'the second half', 'early path': 'early in the path', 'late path': 'late in the path',
  'window A': 'the first window', 'window B': 'the second window', 'earlier steps': 'earlier steps', 'later steps': 'later steps',
  'whole run': 'during the run', 'whole path': 'along the path', 'part of the run': 'in part of the run',
}

/** Domain of a residue (e.g. "LID") from the dataset's group ranges; null if it is in none. */
export function domainOf(key: string, groups: Record<string, { ranges: [number, number][] }>): string | null {
  const resid = Number(key.split(':')[2])
  for (const [g, { ranges }] of Object.entries(groups)) if (ranges.some(([a, b]) => resid >= a && resid <= b)) return g
  return null
}

/** One plain sentence for a card, built only from its stored badge, direction and robustness verdict. */
export function plainSentence(card: Card, groups: Record<string, { ranges: [number, number][] }>): string {
  const a = domainOf(card.keyA, groups)
  const b = domainOf(card.keyB, groups)
  const what = a && b ? (a === b ? `This ${a} contact` : `This ${a}–${b} contact`) : 'This contact'
  const later = card.freqs.length === 2 ? WINDOW_WORDS[card.freqs[1].name] ?? card.freqs[1].name : null
  const dir = card.freqs.length === 2 ? Math.sign(card.freqs[1].freq - card.freqs[0].freq) : 0
  if (card.badge === 'supported' || card.badge === 'proven') {
    let s = `${what} formed ${dir < 0 ? 'less' : 'more'} often in ${later ?? 'the later window'}.`
    if (card.robustness?.verdict === 'robust') s += ' This holds at every distance threshold we tried.'
    else if (card.robustness?.verdict === 'cutoff-sensitive') s += ' It holds at some distance thresholds but not all.'
    if (card.stats?.provisional) s += ' It changes slowly, so this is less certain.'
    return s
  }
  if (card.badge === 'unsupported' || card.badge === 'null') return `${what} shifted, but no more than random variation would explain.`
  const inWin = (w: string) => (w.startsWith('the ') ? `in ${w}` : w)
  if (card.freqs.length === 2) {
    return dir === 0 ? `${what} is present about as often ${inWin(later!)}.` : `${what} is present ${dir < 0 ? 'less' : 'more'} often ${inWin(later!)}.`
  }
  if (card.freqs.length === 1) {
    // How often, in words: the bar shows the number.
    const f = card.freqs[0].freq
    const often = f < 0.1 ? 'rarely' : f < 0.5 ? 'sometimes' : f < 0.9 ? 'often' : 'almost always'
    return `${what} is ${often} formed ${inWin(WINDOW_WORDS[card.freqs[0].name] ?? 'in this window')}.`
  }
  return `${what} was observed.`
}

// ---------- markers ----------

/** A finding not tied to one pair but naming residues (e.g. "the most flexible residues are THR149, VAL148"). */
export type Located = { finding: Finding; resids: number[] }
export type Marker = { n: number; resids: number[] }

const RESIDUE_IN_TEXT = /\b(?:ALA|ARG|ASN|ASP|CYS|GLN|GLU|GLY|HIS|HSD|HSE|HSP|ILE|LEU|LYS|MET|PHE|PRO|SER|THR|TRP|TYR|VAL)(\d{1,3})\b/g

/** Residue numbers named in a finding's own text (resolved values excluded), in order, without repeats. */
export function residuesNamed(f: Finding): number[] {
  const text = f.segments.filter((s) => !s.pid).map((s) => s.text).join(' ') || f.text
  return [...new Set([...text.matchAll(RESIDUE_IN_TEXT)].map((m) => Number(m[1])))]
}

/** Split leftover notes into located ones (they name residues) and notes with no location. */
export function locateNotes(notes: Finding[]): { located: Located[]; unlocated: Finding[] } {
  const located: Located[] = []
  const unlocated: Finding[] = []
  for (const f of notes) {
    const resids = residuesNamed(f)
    if (resids.length) located.push({ finding: f, resids })
    else unlocated.push(f)
  }
  return { located, unlocated }
}

/** Numbers 1..n: contact cards first, then located findings. Unlocated findings get none. */
export function markersFor(cards: Card[], located: Located[]): Marker[] {
  const res = (k: string) => Number(k.split(':')[2])
  return [
    ...cards.map((c, i) => ({ n: i + 1, resids: [res(c.keyA), res(c.keyB)] })),
    ...located.map((l, i) => ({ n: cards.length + i + 1, resids: l.resids })),
  ]
}

/** Plain sentence for a located finding, from the tool it cites. */
export function locatedSentence(l: Located, run: RunView): string {
  const tools = new Set(l.finding.evidence_ids.map((e) => run.results[e.split('.')[0]]?.tool))
  if (tools.has('residue_flexibility')) return 'These parts of the protein move the most.'
  if (tools.has('contact_difference_map')) return 'These residues are where the contacts change most.'
  if (tools.has('calculate_contacts') || tools.has('compare_intervals')) return 'These residues take part in the contacts described here.'
  return 'This finding is about the marked residues.'
}

// ---------- direct answer for "do these parts touch / how often" ----------

const YES_NO = /^\s*(does|do|did|is|are|was|were|can|will)\b/i

/**
 * One sentence that answers the question from the stored share of frames in which two groups touch at all
 * (calculate_contacts' any_contact_frequency). Yes/no questions get a leading Yes or No; others get the share.
 */
export function touchAnswer(question: string, a: string, b: string, share: number, scope: string): string {
  const pct = share > 0 && share < 0.005 ? 'less than 1%' : `${Math.round(share * 100)}%`
  const fact = share === 0 ? `${a} and ${b} never touch in ${scope}` : `${a} and ${b} touch in ${pct} of ${scope}`
  if (!YES_NO.test(question)) return `${fact[0].toUpperCase()}${fact.slice(1)}.`
  if (share === 0) return `No: ${fact}.`
  const how = share < 0.1 ? 'Yes, but rarely' : share < 0.5 ? 'Yes, some of the time' : 'Yes, most of the time'
  return `${how}: ${fact}.`
}
