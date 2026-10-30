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

// CHARMM names histidine by protonation state; people read it as HIS. Keys keep the CHARMM name.
const HIS_STATES: Record<string, string> = {
  HSD: 'histidine protonated on Nδ (CHARMM name HSD)',
  HSE: 'histidine protonated on Nε (CHARMM name HSE)',
  HSP: 'histidine protonated on both Nδ and Nε (CHARMM name HSP)',
}

/** Short display label, e.g. "LYS40"; CHARMM histidines show as HIS ("HSE134" -> "HIS134"). */
export function residueLabel(key: string): string {
  const k = parseResidueKey(key)
  return `${HIS_STATES[k.resname] ? 'HIS' : k.resname}${k.resid}`
}

/** Tooltip for a residue: the CHARMM histidine state, or the plain label. */
export function residueTitle(key: string): string {
  const k = parseResidueKey(key)
  return HIS_STATES[k.resname] ? `${k.resname}${k.resid}: ${HIS_STATES[k.resname]}` : `${k.resname}${k.resid}`
}

/** Display text with CHARMM histidine names replaced ("HSE134-ASP118" -> "HIS134-ASP118"). */
export function hisText(text: string): string {
  return text.replace(/\bHS[DEP](\d+)/g, 'HIS$1')
}

/** True if the text names a CHARMM histidine, so callers can add the explanatory tooltip. */
export function hisNote(text: string): string | null {
  const m = text.match(/\b(HS[DEP])(\d+)/)
  return m ? `${m[1]}${m[2]}: ${HIS_STATES[m[1]]}` : null
}

/** NGL selection for a residue (single chain, so resid is enough). */
export function residueKeyToNglSelection(key: string, mode: 'ca' | 'sidechain' = 'ca'): string {
  const { resid } = parseResidueKey(key)
  return mode === 'ca' ? `${resid} and .CA` : `${resid} and sidechainAttached`
}
