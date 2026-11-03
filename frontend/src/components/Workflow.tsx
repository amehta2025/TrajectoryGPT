// The investigation as three stages: Interpret figure (or Plan analysis) → Run analysis → Results.
// Each stage is one screen built from the existing pieces (plan card, our figure, report cards, charts, 3D
// viewer), so nothing the backend computes is lost; this file only arranges them. Every number shown
// comes from stored tool results (stepResult, the report's filled placeholders), never from model text.
import { useEffect, useState } from 'react'
import { api, type Figure, type Metadata } from '../api/client'
import { nextTurn, toggleScience, useInvestigation } from '../state/investigation'
import { PlanCard, STEP_DETAIL, stepText } from './PlanCard'
import { AgentTrace, stepResult } from './StepLog'
import { Ours, ReproduceView } from './ReproduceView'
import { ReportView, ScienceReport } from './ReportView'
import { CompareChart } from './CompareChart'
import { DistancePlot } from './DistancePlot'
import { Heatmap } from './Heatmap'
import { AnalysisCards } from './AnalysisViews'
import { Viewer } from './Viewer'
import { VoiceHint } from './VoiceInput'
import { DOMAIN_COLORS } from '../theme'

/** 1 Interpret figure (or Plan analysis) — 2 Run analysis — 3 Results. */
export function Stepper() {
  const { phase, proposal, figureRead } = useInvestigation()
  const figure = !!(proposal?.figure || figureRead)
  const stage = phase === 'finished' ? 2 : phase === 'running' ? 1 : 0
  const names = [figure ? 'Interpret figure' : 'Plan analysis', 'Run analysis', 'Results']
  return (
    <ol className="stepper" aria-label="Progress">
      {names.map((n, i) => (
        <li key={n} className={i < stage ? 'done' : i === stage ? 'current' : ''} aria-current={i === stage ? 'step' : undefined}>
          <span className="sp-dot">{i < stage ? '✓' : i + 1}</span>{n}
        </li>
      ))}
    </ol>
  )
}

const GOAL: Record<string, string> = {
  native_contacts: 'Track how often key contacts stay close as the protein changes shape',
  contact_map: 'Map which parts of the protein touch',
  difference_contact_map: 'Map which contacts are gained or lost',
  distance_trace: 'Track the distance between two parts of the protein',
  rmsf: 'Measure how much each part of the protein moves',
}

/** What the agent understood, in plain words; the exact values read from the figure are in each tooltip. */
function Understood({ figure }: { figure: Figure }) {
  const field = (name: string) => figure.spec.fields.find((f) => f.name === name)
  const atoms = field('atom selection')
  const rows: { label: string; value: string; raw?: string; inferred: boolean }[] = [
    { label: 'Goal', value: GOAL[figure.spec.kind] ?? figure.spec.summary, raw: field('analysis')?.value, inferred: false },
    { label: 'Protein', value: field('protein')?.value ?? '—', inferred: field('protein')?.confidence !== 'high' },
    { label: 'Atoms', value: atoms && /\bCA\b/.test(atoms.value) ? 'Cα atoms (backbone)' : atoms?.value ?? '—', raw: atoms?.value, inferred: atoms?.confidence !== 'high' },
    { label: 'Contact distance', value: field('cutoff or radius')?.value ?? '—', raw: 'Two atoms count as in contact within this distance', inferred: field('cutoff or radius')?.confidence !== 'high' },
    { label: 'Reference states', value: field('reference frames or structures')?.value ?? '—', inferred: field('reference frames or structures')?.confidence !== 'high' },
  ]
  return (
    <ul className="understood">
      {rows.map((r) => (
        <li key={r.label} title={r.raw}>
          <span className={`u-mark ${r.inferred ? 'inferred' : 'read'}`} aria-label={r.inferred ? 'inferred' : 'read from the figure'}>{r.inferred ? '!' : '✓'}</span>
          <span className="u-label">{r.label}</span>
          <span className="u-value">{r.value}{r.inferred && <span className="u-tag">inferred</span>}</span>
        </li>
      ))}
    </ul>
  )
}

/** Stage 1 for a figure: their figure beside what the agent understood, one Run button, settings folded away. */
export function FigureSetup({ meta }: { meta: Metadata | null }) {
  const { proposal, figureRead, phase } = useInvestigation()
  const [editing, setEditing] = useState(false)
  const figure = proposal?.figure ?? figureRead
  if (!figure) return null
  const inferred = figure.spec.fields.some((f) => f.confidence !== 'high')
  return (
    <div className="setup">
      <section className="card setup-figure">
        <h2>Your figure</h2>
        <img src={api.figureUrl(figure.figure_id)} alt={`Uploaded figure: ${figure.spec.summary}`} />
        <p className="muted small">{figure.spec.mode === 'exact'
          ? 'Made from the same simulation we have, so the numbers should match exactly.'
          : 'Made from other data: the method is applied to our simulation, and numbers are not expected to match.'}</p>
      </section>
      <section className="card setup-understood">
        <header className="card-head">
          <h2>What I understood from your figure</h2>
          {proposal && !proposal.recorded && <button className="small" onClick={() => setEditing(true)}>Edit</button>}
        </header>
        <Understood figure={figure} />
        {inferred && <p className="inferred-note">⚠ I inferred some of these settings from the figure and paper. Check them, and edit them under Scientific settings if needed.</p>}
        {phase === 'planning' && <p className="muted plan-status">Drafting the analysis plan…</p>}
        {proposal && <PlanCard meta={meta} compact settingsOpen={editing} key={String(editing)} />}
        <details className="fold">
          <summary>Everything read from the figure ({figure.spec.fields.length} settings)</summary>
          <ul className="plain small">{figure.spec.fields.map((f) => <li key={f.name}><b>{f.name}:</b> {f.value} <span className="muted">({f.confidence}, from {f.source})</span></li>)}</ul>
        </details>
      </section>
    </div>
  )
}

type StepState = 'done' | 'running' | 'pending' | 'skipped' | 'failed'
const STATE_ICON: Record<StepState, string> = { done: '✓', running: '', pending: '', skipped: '–', failed: '✕' }

/** Stage 2: the plan's steps with what each one found, the live result, and a progress bar by steps done. */
export function RunningView({ meta }: { meta: Metadata | null }) {
  const { run, proposal, paused } = useInvestigation()
  const plan = proposal?.plan
  const items = run?.checklist ?? plan?.steps.map((s, i) => ({ item: i, tool: s.tool, status: 'pending' as const, text: '', note: '' })) ?? []
  const current = items.find((c) => c.status === 'in_progress') ?? items.find((c) => c.status === 'pending')
  const state = (c: (typeof items)[number]): StepState => {
    const results = run ? Object.values(run.results).filter((r) => r.tool === c.tool) : []
    if (c.status === 'done') return 'done'
    if (c.status === 'skipped') return 'skipped'
    if (results.length && !results.some((r) => r.status === 'ok')) return 'failed'
    return c === current && !paused ? 'running' : 'pending'
  }
  const done = items.filter((c) => c.status === 'done' || c.status === 'skipped').length
  const figure = !!(run?.figure ?? proposal?.figure)
  return (
    <div className="running">
      <h2 className="stage-title">Running analysis…</h2>
      <p className="stage-sub">{figure ? 'Recreating the figure from simulation data.' : 'Working through the approved plan on the simulation data.'}</p>
      <div className="running-grid">
        <section className="card run-steps">
          <ol>
            {items.map((c) => {
              const st = state(c)
              const found = run && st === 'done' ? stepResult(run, c.tool) : null
              return (
                <li key={c.item} className={`rs ${st}`}>
                  <span className="rs-icon" aria-label={st}>{STATE_ICON[st]}</span>
                  <span className="rs-body">
                    <span className="rs-title">{stepText(c.tool, plan ?? null)}
                      {st === 'skipped' && <span className="rs-tag">Skipped</span>}
                      {st === 'failed' && <span className="rs-tag failed">Couldn’t run</span>}</span>
                    <span className="rs-detail">{found ? `Found: ${found}` : STEP_DETAIL[c.tool] ?? ''}</span>
                  </span>
                </li>
              )
            })}
          </ol>
          {paused && <button className="primary violet" onClick={() => void nextTurn()}>Next step →</button>}
        </section>
        <section className="card run-live">
          {figure ? <Ours run={run} /> : meta && run && Object.values(run.results).some((r) => r.tool === 'compare_intervals' && r.status === 'ok')
            ? <CompareChart meta={meta} />
            : <div className="muted placeholder">Results appear here as each step finishes.</div>}
        </section>
      </div>
      <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={items.length} aria-valuenow={done}>
        <span className="progress-label">{current ? `${stepText(current.tool, null)}…` : 'Finishing the report…'}</span>
        <span className="progress-track"><span className="progress-fill" style={{ width: `${items.length ? (100 * done) / items.length : 0}%` }} /></span>
        <span className="progress-count">Step {Math.min(done + 1, items.length)} of {items.length}</span>
      </div>
    </div>
  )
}

function ProteinPanel({ meta, tall = false }: { meta: Metadata; tall?: boolean }) {
  return (
    <section className={`card viewer-card ${tall ? 'tall' : ''}`}>
      <header className="card-head">
        <h2>Protein structure</h2>
        <VoiceHint />
        <div className="legend">
          {Object.entries(meta.groups).map(([g, { ranges }]) => (
            <span key={g} title={ranges.map(([a, b]) => `${a}–${b}`).join(', ')}><i style={{ background: DOMAIN_COLORS[g] }} /> {g}</span>
          ))}
        </div>
      </header>
      <Viewer meta={meta} />
    </section>
  )
}

type Tab = 'figure' | 'interactions' | 'protein' | 'methods'

/** Stage 3 below the result banner: the evidence in four tabs. */
export function ResultTabs({ meta }: { meta: Metadata }) {
  const { run, science } = useInvestigation()
  const figure = !!run?.figure
  const [tab, setTab] = useState<Tab>(figure ? 'figure' : 'interactions')
  useEffect(() => setTab(figure ? 'figure' : 'interactions'), [run?.run_id, figure])
  const tabs: [Tab, string, string][] = [
    ...(figure ? [['figure', '◩', 'Figure comparison'] as [Tab, string, string]] : []),
    ['interactions', '⚯', 'Changing interactions'], ['protein', '⬡', 'Protein structure'], ['methods', '▤', 'Details & methods'],
  ]
  return (
    <div className="result-tabs">
      <div className="tabs" role="tablist">
        {tabs.map(([id, icon, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>
            <span aria-hidden>{icon}</span> {label}
          </button>
        ))}
      </div>
      {tab === 'figure' && <ReproduceView />}
      {tab === 'interactions' && (
        <div className="tab-body">
          <div className="inv-body">
            <ProteinPanel meta={meta} />
            <div className="inv-col"><ReportView meta={meta} /></div>
          </div>
          <button className="science-toggle" onClick={toggleScience} aria-expanded={science}>
            {science ? 'Hide statistics ▴' : 'Show statistics on each finding ▾'}
          </button>
          <div className="evidence-more">
            <CompareChart meta={meta} />
            <div className="inv-col">
              <DistancePlot meta={meta} />
              <Heatmap meta={meta} />
            </div>
          </div>
        </div>
      )}
      {tab === 'protein' && (
        <div className="tab-body protein-tab">
          <ProteinPanel meta={meta} tall />
          <div className="inv-col"><AnalysisCards run={run} /></div>
        </div>
      )}
      {tab === 'methods' && (
        <div className="tab-body">
          <ScienceReport meta={meta} />
          <AgentTrace meta={meta} open />
        </div>
      )}
    </div>
  )
}
