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
          const name = w[0] === 0 && w[1] === mid ? first : w[0] === mid ? second : `frames ${w[0]}–${Math.min(w[1], nFrames) - 1}`
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
        freqs, stats, focusWindow,
      })
      added++
    }
    if (!added) notes.push(f)
  }
  return { cards, notes }
}
