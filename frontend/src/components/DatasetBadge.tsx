// Plain-language names for the datasets, and the read-only top-bar readout of the active one.
// The dataset is picked in one place only: the start screen.
import type { DatasetInfo, Metadata } from '../api/client'

export const DATASET_TEXT: Record<string, { name: string; line: string }> = {
  adk_equilibrium: {
    name: 'Equilibrium run · 1 µs',
    line: 'A 1 µs unbiased simulation of adenylate kinase (AdK) with nothing bound: 4,187 frames, 0.24 ns apart. Statistics are available.',
  },
  adk_transition: {
    name: 'Closed → open path · 98 steps',
    line: 'A 98-step path of AdK opening from closed to open, built with a biased method (DIMS). Steps are not evenly spaced in time, so results are descriptive only, with no statistics.',
  },
}

export function datasetName(id: string): string {
  return DATASET_TEXT[id]?.name ?? id
}

export function datasetLine(info: DatasetInfo | undefined): string {
  if (!info) return ''
  return DATASET_TEXT[info.id]?.line ?? info.description
}

export function DatasetBadge({ meta, dataset }: { meta: Metadata | null; dataset: string }) {
  const descriptive = meta && meta.dataset === dataset && !meta.stats_valid
  return (
    <span className="ds-readout" title={dataset}>
      AdK · {datasetName(dataset)}
      {descriptive && <span className="tag">descriptive only</span>}
    </span>
  )
}
