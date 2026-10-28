// Active dataset, its time unit, and whether statistics are allowed on it.
import type { DatasetInfo, Metadata } from '../api/client'
import { setSelection } from '../state/selection'

export function DatasetBadge({ datasets, meta, dataset }: { datasets: DatasetInfo[]; meta: Metadata | null; dataset: string }) {
  return (
    <div className="dataset-badge">
      <select value={dataset} onChange={(e) => setSelection({ dataset: e.target.value })} aria-label="Dataset">
        {datasets.map((d) => <option key={d.id} value={d.id}>{d.id}</option>)}
      </select>
      {meta && meta.dataset === dataset && (
        <>
          <span className="pill">{meta.n_frames.toLocaleString()} frames</span>
          <span className="pill">{meta.dt_ns === null ? 'path steps (no physical time)' : `${meta.dt_ns} ns / frame`}</span>
          <span className={`pill ${meta.stats_valid ? 'ok' : 'no'}`} title={meta.stats_valid ? 'Unbiased equilibrium sampling' : 'Biased, non-stationary path'}>
            {meta.stats_valid ? 'stats valid' : 'stats refused'}
          </span>
        </>
      )}
    </div>
  )
}
