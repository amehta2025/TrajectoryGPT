// Residue key `dataset:segid:resid:resname` (mirrors backend app/science/residues.py).
// The UI parses keys only here, and maps them to NGL only here.

export type ResidueKey = { dataset: string; segid: string; resid: number; resname: string }

export function parseResidueKey(key: string): ResidueKey {
  const parts = key.split(':')
  if (parts.length !== 4) throw new Error(`Residue key must be dataset:segid:resid:resname, got ${key}`)
  const [dataset, segid, resid, resname] = parts
  if (!/^-?\d+$/.test(resid)) throw new Error(`resid must be an integer in ${key}`)
  return { dataset, segid, resid: Number(resid), resname }
}

/** Short display label, e.g. "LYS40". */
export function residueLabel(key: string): string {
  const k = parseResidueKey(key)
  return `${k.resname}${k.resid}`
}

/** NGL selection for a residue (single chain, so resid is enough). */
export function residueKeyToNglSelection(key: string, mode: 'ca' | 'sidechain' = 'ca'): string {
  const { resid } = parseResidueKey(key)
  return mode === 'ca' ? `${resid} and .CA` : `${resid} and sidechainAttached`
}
