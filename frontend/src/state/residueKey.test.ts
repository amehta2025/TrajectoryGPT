import { describe, expect, it } from 'vitest'
import { parseResidueKey, residueKeyToNglSelection, residueLabel } from './residueKey'

describe('residue keys', () => {
  it('parses a key', () => {
    expect(parseResidueKey('adk_equilibrium:ADK:40:LYS')).toEqual({
      dataset: 'adk_equilibrium', segid: 'ADK', resid: 40, resname: 'LYS',
    })
  })

  it('rejects malformed keys', () => {
    expect(() => parseResidueKey('adk:ADK:40')).toThrow()
    expect(() => parseResidueKey('adk:ADK:forty:LYS')).toThrow()
  })

  it('maps to NGL selections', () => {
    expect(residueKeyToNglSelection('adk_transition:4AKE:158:ASP')).toBe('158 and .CA')
    expect(residueKeyToNglSelection('adk_equilibrium:ADK:74:GLN', 'sidechain')).toBe('74 and sidechainAttached')
  })

  it('labels', () => {
    expect(residueLabel('adk_equilibrium:ADK:134:HSE')).toBe('HSE134')
  })
})
