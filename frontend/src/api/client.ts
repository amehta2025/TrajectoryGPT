// All backend calls live here. The UI never calls an LLM or any other service directly.
const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export type Units = { distance: string; time: string; dt_ns: number | null }

export type Metadata = {
  dataset: string
  n_atoms: number
  n_viewer_atoms: number
  n_residues: number
  n_frames: number
  dt_ns: number | null
  time_label: string
  stats_valid: boolean
  playback_stride: number
  pbc: string
  groups: Record<string, { ranges: [number, number][]; selection: string }>
  groups_source: string
  sequence_exclusion_default: number
  contact_definition: string
  license: string
  citation: string
}

export type DatasetInfo = { id: string; description: string; stats_valid: boolean; time_label: string }

export type Contacts = {
  dataset: string
  group_a: string
  group_b: string
  keys_a: string[]
  keys_b: string[]
  frequency: number[][]
  excluded: boolean[][]
  sequence_exclusion: number
  n_frames: number
  cutoff: number
  contact_definition: string
  units: Units
}

export type Distances = {
  dataset: string
  key_a: string
  key_b: string
  frames: number[]
  time_ns: number[] | null
  distance: number[]
  units: Units
}

export type Frames = { frames: number[]; nAtoms: number; coords: Float32Array }

async function get(path: string, params: Record<string, string | number | undefined> = {}): Promise<Response> {
  const url = new URL(path, BASE)
  for (const [k, v] of Object.entries(params)) if (v !== undefined) url.searchParams.set(k, String(v))
  const r = await fetch(url)
  if (!r.ok) {
    let detail = r.statusText
    try {
      detail = JSON.stringify((await r.json()).detail)
    } catch {
      /* not JSON */
    }
    throw new Error(`${r.status} ${detail}`)
  }
  return r
}

export const api = {
  datasets: async (): Promise<DatasetInfo[]> => (await get('/datasets')).json(),
  metadata: async (ds: string): Promise<Metadata> => (await get(`/metadata/${ds}`)).json(),
  contacts: async (ds: string, groupA: string, groupB: string, cutoff = 4.5): Promise<Contacts> =>
    (await get(`/contacts/${ds}`, { group_a: groupA, group_b: groupB, cutoff })).json(),
  distances: async (ds: string, keyA: string, keyB: string): Promise<Distances> =>
    (await get(`/distances/${ds}`, { key_a: keyA, key_b: keyB })).json(),
  structure: async (ds: string): Promise<string> => (await get(`/structure/${ds}`)).text(),
  frames: async (ds: string, start: number, stop: number, stride: number): Promise<Frames> => {
    const r = await get(`/frames/${ds}`, { start, stop, stride })
    const frames = (r.headers.get('X-Frames') ?? '').split(',').filter(Boolean).map(Number)
    const nAtoms = Number(r.headers.get('X-N-Atoms'))
    return { frames, nAtoms, coords: new Float32Array(await r.arrayBuffer()) }
  },
}
