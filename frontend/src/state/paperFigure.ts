// Pure helpers for drawing our result the way the published figure is drawn (figure mode): which group goes
// on which axis, the residue ranges the paper showed, its colour scale, and its time unit. Display only:
// the numbers come from stored tool results; cropping a map hides pairs, it never changes a value.
import type { FigureDisplay } from '../api/client'
import { parseResidueKey } from './residueKey'

export type OrientedMap = { x: number[]; y: number[]; z: (number | null)[][]; xGroup: string; yGroup: string; keysX: string[]; keysY: string[] }

const inRange = (r: number, lo: number | null | undefined, hi: number | null | undefined) =>
  (lo == null || r >= lo) && (hi == null || r <= hi)

/** A [a][b] matrix (rows: group A residues, columns: group B) as a heatmap laid out like the paper:
 *  the group the paper put on x goes on x, and each axis keeps only the residues the paper's axis spans.
 *  Without display info, group B is on x and nothing is cropped. z is indexed [y][x], as Plotly wants. */
export function orientMap(keysA: string[], keysB: string[], m: (number | null)[][], groupA: string, groupB: string,
  display?: FigureDisplay | null): OrientedMap {
  const swap = display ? display.x.group === groupA || display.y.group === groupB : false
  const [kx, ky] = swap ? [keysA, keysB] : [keysB, keysA]
  const [gx, gy] = swap ? [groupA, groupB] : [groupB, groupA]
  const ax = display?.x, ay = display?.y
  // An axis range applies only when the paper's axis is this group (or names no group).
  const fits = (g: string, axis: FigureDisplay['x'] | undefined) => !!axis && (axis.group === g || axis.group === 'none')
  const ix = kx.map((k, i) => [i, parseResidueKey(k).resid] as const)
    .filter(([, r]) => !fits(gx, ax) || inRange(r, ax!.residue_min, ax!.residue_max))
  const iy = ky.map((k, i) => [i, parseResidueKey(k).resid] as const)
    .filter(([, r]) => !fits(gy, ay) || inRange(r, ay!.residue_min, ay!.residue_max))
  const at = (yi: number, xi: number) => (swap ? m[xi]?.[yi] : m[yi]?.[xi]) ?? null
  return {
    x: ix.map(([, r]) => r), y: iy.map(([, r]) => r),
    z: iy.map(([yi]) => ix.map(([xi]) => at(yi, xi))),
    xGroup: gx, yGroup: gy, keysX: ix.map(([i]) => kx[i]), keysY: iy.map(([i]) => ky[i]),
  }
}

// matplotlib's scales as Plotly colour scales (RdBu as matplotlib's RdBu_r: blue low, red high).
const SCALES: Record<string, string | [number, string][]> = {
  viridis: 'Viridis', magma: [[0, '#000004'], [0.25, '#51127c'], [0.5, '#b73779'], [0.75, '#fc8961'], [1, '#fcfdbf']],
  greys: [[0, '#ffffff'], [1, '#000000']], blues: 'Blues', reds: 'Reds',
  red_blue: [[0, '#053061'], [0.25, '#4393c3'], [0.5, '#f7f7f7'], [0.75, '#d6604d'], [1, '#67001f']],
}

/** The paper's colour scale; a sequential map defaults to viridis, a difference map to red/blue. */
export function colorscaleFor(display: FigureDisplay | null | undefined, diverging: boolean) {
  const name = display && display.colormap !== 'other' ? display.colormap : diverging ? 'red_blue' : 'viridis'
  return SCALES[name]
}

/** The paper's colour-bar limits, else [0, 1] for frequencies or a symmetric range around 0 for changes. */
export function colorRange(display: FigureDisplay | null | undefined, values: (number | null)[], diverging: boolean): [number, number] {
  if (display?.color_min != null && display?.color_max != null && display.color_max > display.color_min) {
    return [display.color_min, display.color_max]
  }
  if (!diverging) return [0, 1]
  const m = Math.max(0.05, ...values.map((v) => Math.abs(v ?? 0)))
  return [-m, m]
}

/** Time axis in the paper's unit (ns, µs, ps) or frames when the data has no physical time. */
export function timeAxis(frames: number[], timeNs: number[] | null, unit: FigureDisplay['time_unit'] | undefined): { t: number[]; label: string } {
  if (!timeNs || unit === 'frame') return { t: frames, label: timeNs ? 'frame' : 'path step (frame)' }
  if (unit === 'us') return { t: timeNs.map((v) => v / 1000), label: 'time (µs)' }
  if (unit === 'ps') return { t: timeNs.map((v) => v * 1000), label: 'time (ps)' }
  return { t: timeNs, label: 'time (ns)' }
}

/** "8 A radius" → "8 Å radius"; "4.5 angstroms" → "4.5 Å". */
export function withUnits(text: string): string {
  return text.replace(/(\d(?:\.\d+)?)\s*(?:Å|A|Ang|angstroms?|Angstroms?)(?![A-Za-z])/g, '$1 Å')
}
