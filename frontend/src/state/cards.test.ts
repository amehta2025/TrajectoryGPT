import { describe, expect, it } from 'vitest'
import type { Finding, RunView } from '../api/client'
import { buildCards, domainOf, plainSentence, windowNames, type Card } from './cards'

const K = (r: string) => `adk_equilibrium:ADK:${r.slice(3)}:${r.slice(0, 3)}`
const finding = (over: Partial<Finding>): Finding => ({
  id: 'f1', text: '', claim: 'supported', evidence_ids: [], rendered: '', segments: [], badge: 'supported',
  badge_label: 'supported', pairs: [], robustness: [], ...over,
})
const row = (a: string, b: string, fa: number, fb: number, supported: boolean) => ({
  key_a: K(a), key_b: K(b), freq_a: fa, freq_b: fb, delta: fb - fa, ci_low: fb - fa - 0.05, ci_high: fb - fa + 0.05, q: 0.01,
  supported, provisional: false, n_blocks_a: 500, n_blocks_b: 480, tau_ns_a: 0.4, tau_ns_b: 0.5,
})

function run(findings: Finding[], results: RunView['results']): RunView {
  return { results, report: { findings } } as unknown as RunView
}

describe('contact cards', () => {
  it('splits a two-pair finding into one card per contact, each with its own stored test', () => {
    const r = run(
      [finding({ pairs: ['GLY122-ASP118', 'GLY122-ARG119'], evidence_ids: ['r1.GLY122-ASP118', 'r1.GLY122-ARG119'] })],
      {
        r1: {
          status: 'ok', tool: 'compare_intervals', provenance_id: 'r1', params: {}, units: {}, summary_for_model: '',
          pairs: { 'GLY122-ASP118': { key_a: K('GLY122'), key_b: K('ASP118') }, 'GLY122-ARG119': { key_a: K('GLY122'), key_b: K('ARG119') } },
          data_for_ui: { window_a: [0, 2093], window_b: [2093, 4187], rows: [row('GLY122', 'ASP118', 0.3, 0.28, true), row('GLY122', 'ARG119', 0.5, 0.53, false)] },
        },
      },
    )
    const { cards, notes } = buildCards(r, 4187, true)
    expect(cards.map((c) => c.label)).toEqual(['GLY122-ASP118', 'GLY122-ARG119'])
    expect(cards[0].freqs).toEqual([{ name: '1st half', freq: 0.3 }, { name: '2nd half', freq: 0.28 }])
    expect(cards[0].badge).toBe('supported')
    expect(cards[1].badge).toBe('unsupported')
    expect(notes).toHaveLength(0)
  })

  it('reads before/after from the cited contact maps when a descriptive finding names a pair in its text', () => {
    const map = (w: [number, number], v: number) => ({
      status: 'ok' as const, tool: 'calculate_contacts', provenance_id: '', params: {}, units: {}, summary_for_model: '', pairs: {},
      data_for_ui: { keys_a: [K('LYS157')], keys_b: [K('ASP54')], frequency: [[v]], window: w },
    })
    const r = run(
      [finding({ badge: 'descriptive', badge_label: 'descriptive — not statistically tested', text: 'LYS157-ASP54 drops along the path', evidence_ids: ['r2', 'r3'] }),
        finding({ id: 'f2', badge: 'descriptive', text: 'The dataset is a 98-frame path.', evidence_ids: ['r2'] })],
      { r2: map([0, 49], 1.0), r3: map([49, 98], 0.306) },
    )
    const { cards, notes } = buildCards(r, 98, false)
    expect(cards).toHaveLength(1)
    expect(cards[0].freqs).toEqual([{ name: 'early path', freq: 1.0 }, { name: 'late path', freq: 0.306 }])
    expect(cards[0].focusWindow).toEqual([49, 98])
    expect(notes.map((n) => n.id)).toEqual(['f2'])
  })

  it('reads before/after from a cited difference map', () => {
    const r = run(
      [finding({ badge: 'descriptive', badge_label: 'descriptive — not statistically tested', text: 'LYS157-ASP54 is lost', evidence_ids: ['r1'] })],
      { r1: { status: 'ok', tool: 'contact_difference_map', provenance_id: 'r1', params: {}, units: {}, summary_for_model: '',
        pairs: { 'LYS157-ASP54': { key_a: K('LYS157'), key_b: K('ASP54') } },
        data_for_ui: { keys_a: [K('LYS157')], keys_b: [K('ASP54')], freq_a: [[0.9]], freq_b: [[0.2]], window_a: [0, 49], window_b: [49, 98] } } },
    )
    const { cards } = buildCards(r, 98, false)
    expect(cards[0].freqs).toEqual([{ name: 'early path', freq: 0.9 }, { name: 'late path', freq: 0.2 }])
  })

  it('merges findings about the same contact into one card, keeping the before/after one', () => {
    const map = (w: [number, number], v: number) => ({
      status: 'ok' as const, tool: 'calculate_contacts', provenance_id: '', params: {}, units: {}, summary_for_model: '', pairs: {},
      data_for_ui: { keys_a: [K('LYS157')], keys_b: [K('ASP54')], frequency: [[v]], window: w },
    })
    const r = run(
      [finding({ id: 'f1', badge: 'descriptive', text: 'LYS157-ASP54 early', evidence_ids: ['r2'] }),
        finding({ id: 'f2', badge: 'descriptive', text: 'LYS157-ASP54 drops', evidence_ids: ['r2', 'r3'] })],
      { r2: map([0, 49], 1.0), r3: map([49, 98], 0.3) },
    )
    const { cards } = buildCards(r, 98, false)
    expect(cards).toHaveLength(1)
    expect(cards[0].freqs).toHaveLength(2)
    expect([cards[0].finding.id, ...cards[0].others.map((f) => f.id)].sort()).toEqual(['f1', 'f2'])
  })

  it('names windows', () => {
    expect(windowNames([0, 50], [50, 100], 100, true)).toEqual(['1st half', '2nd half'])
    expect(windowNames([0, 30], [60, 100], 100, true)).toEqual(['window A', 'window B'])
  })
})

describe('plain sentences', () => {
  const groups = { NMP: { ranges: [[30, 59]] as [number, number][] }, LID: { ranges: [[122, 159]] as [number, number][] }, CORE: { ranges: [[1, 29], [60, 121], [160, 214]] as [number, number][] } }
  const card = (over: Partial<Card>): Card => ({
    id: 'x', label: 'HSE134-ASP118', keyA: K('HSE134'), keyB: K('ASP118'), finding: finding({}), badge: 'supported', badgeLabel: '',
    robustness: null, freqs: [{ name: '1st half', freq: 0.53 }, { name: '2nd half', freq: 0.4 }], stats: null, focusWindow: null, others: [], ...over,
  })
  const NO_JARGON = /\d|Å|\bns\b|\b[A-Z]{3}\d|q-?value|\bCI\b|\b(supported|provisional|robust|frames?)\b/

  it('says what happened in words only', () => {
    const s = plainSentence(card({ robustness: { pair: 'HSE134-ASP118', verdict: 'robust', pid: 'r2', label: '' } }), groups)
    expect(s).toBe('This LID–CORE contact formed less often in the second half. This holds at every distance threshold we tried.')
    expect(s).not.toMatch(NO_JARGON)
  })

  it('covers noise, slow changes and descriptive paths without jargon', () => {
    const noise = plainSentence(card({ badge: 'unsupported' }), groups)
    const slow = plainSentence(card({ stats: { provisional: true } as Card['stats'], robustness: { pair: '', verdict: 'cutoff-sensitive', pid: null, label: '' } }), groups)
    const path = plainSentence(card({ badge: 'descriptive', keyA: K('LYS157'), keyB: K('ASP54'), freqs: [{ name: 'early path', freq: 1 }, { name: 'late path', freq: 0.3 }] }), groups)
    expect(noise).toContain('random variation')
    expect(slow).toContain('less certain')
    expect(path).toBe('This LID–NMP contact is present less often late in the path.')
    for (const s of [noise, slow, path]) expect(s).not.toMatch(NO_JARGON)
  })

  it('finds the domain of a residue', () => {
    expect(domainOf(K('ASP54'), groups)).toBe('NMP')
    expect(domainOf(K('ASP118'), groups)).toBe('CORE')
  })
})
