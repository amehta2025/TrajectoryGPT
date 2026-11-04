import { describe, expect, it } from 'vitest'
import type { FigureDisplay } from '../api/client'
import { colorRange, orientMap, timeAxis, withUnits } from './paperFigure'

const key = (g: string, r: number) => `adk_equilibrium:4AKE:${r}:${g}`
const lid = [122, 123, 160].map((r) => key('GLY', r)) // group A
const core = [99, 100, 121].map((r) => key('ALA', r)) // group B
const m = [[1, 2, 3], [4, 5, 6], [7, 8, 9]] // m[a][b]
const disp = (over: Partial<FigureDisplay> = {}): FigureDisplay => ({
  x: { title: 'LID residue number', group: 'LID', residue_min: 122, residue_max: 159 },
  y: { title: 'CORE residue number', group: 'CORE', residue_min: 100, residue_max: 121 },
  color_min: 0, color_max: 1, colormap: 'viridis', time_unit: 'ns', reference_line: null, y_min: null, y_max: null, ...over,
})

describe('orientMap', () => {
  it('puts the paper’s x group on x and crops each axis to the paper’s residue range', () => {
    const o = orientMap(lid, core, m, 'LID', 'CORE', disp())
    expect(o.xGroup).toBe('LID')
    expect(o.x).toEqual([122, 123]) // 160 is outside LID 122–159
    expect(o.y).toEqual([100, 121]) // 99 is outside CORE 100–121
    expect(o.z).toEqual([[2, 5], [3, 6]]) // z[y=CORE][x=LID] = m[lid][core]
  })
  it('without display info: group B on x, nothing cropped', () => {
    const o = orientMap(lid, core, m, 'LID', 'CORE', null)
    expect(o.xGroup).toBe('CORE')
    expect(o.z).toEqual(m)
  })
  it('keeps group B on x when the paper does too', () => {
    const d = disp({ x: { title: 'CORE', group: 'CORE', residue_min: 100, residue_max: 121 },
      y: { title: 'LID', group: 'LID', residue_min: null, residue_max: null } })
    const o = orientMap(lid, core, m, 'LID', 'CORE', d)
    expect(o.xGroup).toBe('CORE')
    expect(o.x).toEqual([100, 121])
    expect(o.y).toEqual([122, 123, 160])
    expect(o.z).toEqual([[2, 3], [5, 6], [8, 9]])
  })
})

describe('colorRange', () => {
  it('uses the paper’s colour-bar limits, else 0–1 or a symmetric range', () => {
    expect(colorRange(disp({ color_min: -0.6, color_max: 0.6 }), [0.2], true)).toEqual([-0.6, 0.6])
    expect(colorRange(null, [0.2], false)).toEqual([0, 1])
    expect(colorRange(null, [0.2, -0.4], true)).toEqual([-0.4, 0.4])
  })
})

describe('timeAxis and units', () => {
  it('converts to the paper’s time unit, or frames without physical time', () => {
    expect(timeAxis([0, 1], [0, 240], 'us').t).toEqual([0, 0.24])
    expect(timeAxis([0, 1], null, 'ns').label).toBe('path step (frame)')
  })
  it('writes Å for angstroms', () => {
    expect(withUnits('8 A radius')).toBe('8 Å radius')
    expect(withUnits('cutoff 4.5 angstroms, CA atoms')).toBe('cutoff 4.5 Å, CA atoms')
    expect(withUnits('NMP 30–59 and residue A55')).toBe('NMP 30–59 and residue A55')
  })
})
