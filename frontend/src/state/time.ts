// Frame -> time label. Real ns only where the dataset has physical time; never invent ns.
import type { Metadata } from '../api/client'

export function hasTime(meta: Pick<Metadata, 'dt_ns'>): boolean {
  return meta.dt_ns !== null
}

export function frameTimeLabel(meta: Pick<Metadata, 'dt_ns' | 'n_frames'>, frame: number): string {
  if (meta.dt_ns === null) return `path step ${frame} of ${meta.n_frames - 1}`
  return `${(frame * meta.dt_ns).toFixed(2)} ns`
}

export function timeAxisTitle(meta: Pick<Metadata, 'dt_ns'>): string {
  return meta.dt_ns === null ? 'path step (biased path; no physical time)' : 'time (ns)'
}
