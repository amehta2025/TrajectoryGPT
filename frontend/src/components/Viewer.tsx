// NGL 3D viewer (SPEC §10a). Loads the reference PDB, then a strided set of playback
// frames; any other frame (jump-to-frame) is fetched exactly. Highlights the selected pair.
// A failed load retries once by itself, then offers a Retry button.
import { useEffect, useRef, useState } from 'react'
import * as NGL from 'ngl'
import { api, type Metadata } from '../api/client'
import { getSelection, setSelection, useSelection } from '../state/selection'
import { useInvestigation } from '../state/investigation'
import { residueKeyToNglSelection, residueLabel } from '../state/residueKey'
import { frameTimeLabel } from '../state/time'
import { DOMAIN_COLORS, PAIR_COLORS } from '../theme'

type Playback = { frames: number[]; nAtoms: number; coords: Float32Array }
type Rep = { setSelection: (s: string) => void; setVisibility: (v: boolean) => void; setParameters: (p: object) => void }

const NONE = 'none'

function domainScheme(meta: Metadata): string {
  const data: [string, string, undefined][] = Object.entries(meta.groups).map(([g, { ranges }]) => [
    DOMAIN_COLORS[g] ?? '#999999',
    ranges.map(([a, b]) => `${a}-${b}`).join(' or '),
    undefined,
  ])
  data.push(['#999999', '*', undefined])
  return NGL.ColormakerRegistry.addSelectionScheme(data, `domains-${meta.dataset}`)
}

export function Viewer({ meta }: { meta: Metadata }) {
  const { pair, frame, focus, markers } = useSelection()
  const { science } = useInvestigation() // residue labels, frame numbers and times only with "Show the science"
  const host = useRef<HTMLDivElement>(null)
  const stage = useRef<NGL.Stage | null>(null)
  const comp = useRef<NGL.StructureComponent | null>(null)
  const reps = useRef<{ a: Rep; b: Rep; label: Rep; marks: Rep; dots: Rep } | null>(null)
  const playback = useRef<Playback | null>(null)
  const exact = useRef(new Map<number, Float32Array>())
  const [status, setStatus] = useState('Loading structure…')
  const [shownFrame, setShownFrame] = useState<number | null>(null)
  const [playing, setPlaying] = useState(false)
  const [loadedFor, setLoadedFor] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [failed, setFailed] = useState(false)

  // One Stage for the component's life.
  useEffect(() => {
    const el = host.current
    if (!el) return
    const s = new NGL.Stage(el, { backgroundColor: '#0c0f15', tooltip: true })
    stage.current = s
    if (import.meta.env.DEV) (window as unknown as { __nglStage: NGL.Stage }).__nglStage = s // for manual checks
    // Re-fit the protein when the box changes size (layout settling, window resize), so it never draws tiny.
    const ro = new ResizeObserver(() => {
      s.handleResize()
      if (comp.current) fitProtein(s, comp.current)
    })
    ro.observe(el)
    return () => {
      ro.disconnect()
      s.dispose()
      el.replaceChildren() // dispose() leaves the canvas in the DOM
      stage.current = null
    }
  }, [])

  // Load structure + playback frames whenever the dataset changes.
  useEffect(() => {
    const s = stage.current
    if (!s) return
    let cancelled = false
    s.removeAllComponents()
    comp.current = null
    reps.current = null
    playback.current = null
    exact.current = new Map()
    setLoadedFor(null)
    setShownFrame(null)
    setPlaying(false)
    setFailed(false)
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    ;(async () => {
      try {
        setStatus('Loading structure…')
        const pdb = await api.structure(meta.dataset)
        if (cancelled) return
        const c = (await s.loadFile(new Blob([pdb], { type: 'text/plain' }), { ext: 'pdb' })) as NGL.StructureComponent
        if (cancelled) return
        c.addRepresentation('cartoon', { color: domainScheme(meta), opacity: 0.9 })
        const a = c.addRepresentation('licorice', { sele: NONE, color: PAIR_COLORS.a, radius: 0.35 })
        const b = c.addRepresentation('licorice', { sele: NONE, color: PAIR_COLORS.b, radius: 0.35 })
        const label = c.addRepresentation('label', {
          sele: NONE, labelType: 'text', labelText: [] as string[],
          color: '#ffffff', xOffset: 0.6, fixedSize: true, radiusType: 'size', radiusSize: 1.6,
          showBorder: true, borderColor: '#000000', borderWidth: 0.4,
        })
        comp.current = c
        // Numbered markers: the finding's number on each of its residues (C-alpha), over a small white dot.
        const dots = c.addRepresentation('spacefill', { sele: NONE, color: '#ffffff', radiusType: 'size', radiusSize: 0.9, opacity: 0.85 })
        const marks = c.addRepresentation('label', {
          sele: NONE, labelType: 'text', labelText: [] as string[], color: '#0b0e14', fixedSize: true, radiusType: 'size',
          radiusSize: 1.5, showBackground: true, backgroundColor: '#ffffff', backgroundOpacity: 1, zOffset: 2,
        })
        reps.current = { a, b, label, marks, dots }
        fitProtein(s, c)

        const stride = meta.playback_stride
        setStatus('Loading the trajectory…')
        const fr = await api.frames(meta.dataset, 0, meta.n_frames, stride)
        if (cancelled) return
        if (fr.nAtoms !== c.structure.atomCount) throw new Error(`atom count mismatch: frames ${fr.nAtoms}, PDB ${c.structure.atomCount}`)
        playback.current = fr
        setStatus('')
        setLoadedFor(meta.dataset)
      } catch (e) {
        if (cancelled) return
        if (attempt === 0) {
          setStatus('Loading failed; retrying…')
          retryTimer = setTimeout(() => setAttempt(1), 1500)
        } else {
          setStatus(`The 3D view could not load: ${(e as Error).message}`)
          setFailed(true)
        }
      }
    })()
    return () => {
      cancelled = true
      clearTimeout(retryTimer)
    }
  }, [meta, attempt])

  // Highlight the selected pair.
  useEffect(() => {
    const r = reps.current
    if (!r || loadedFor !== meta.dataset) return
    if (!pair) {
      r.a.setSelection(NONE)
      r.b.setSelection(NONE)
      r.label.setSelection(NONE)
      return
    }
    r.a.setSelection(residueKeyToNglSelection(pair.keyA, 'sidechain'))
    r.b.setSelection(residueKeyToNglSelection(pair.keyB, 'sidechain'))
    // Label text set per atom so CHARMM histidines read HIS134, as everywhere else in the UI.
    const labelText: string[] = []
    for (const key of [pair.keyA, pair.keyB]) {
      const c = comp.current
      if (!c) continue
      for (const i of c.structure.getAtomIndices(new NGL.Selection(residueKeyToNglSelection(key))) ?? []) labelText[i] = residueLabel(key)
    }
    r.label.setParameters({ labelText })
    r.label.setSelection(`(${residueKeyToNglSelection(pair.keyA)}) or (${residueKeyToNglSelection(pair.keyB)})`)
    r.label.setVisibility(science)
  }, [pair, loadedFor, meta.dataset, science])

  // Draw the numbered markers of located findings; findings with no location have none.
  useEffect(() => {
    const r = reps.current
    const c = comp.current
    if (!r || !c || loadedFor !== meta.dataset) return
    if (!markers.length) {
      r.marks.setSelection(NONE)
      r.dots.setSelection(NONE)
      return
    }
    const byResid = new Map<number, number[]>()
    for (const m of markers) for (const rid of m.resids) byResid.set(rid, [...(byResid.get(rid) ?? []), m.n])
    const labelText: string[] = []
    for (const [rid, ns] of byResid) {
      for (const i of c.structure.getAtomIndices(new NGL.Selection(`${rid} and .CA`)) ?? []) labelText[i] = ns.join(',')
    }
    const sele = `(${[...byResid.keys()].join(' or ')}) and .CA` // parentheses: 'and' binds tighter than 'or'
    r.marks.setParameters({ labelText })
    r.marks.setSelection(`(${sele})`)
    r.dots.setSelection(`(${sele})`)
  }, [markers, loadedFor, meta.dataset])

  // Show the requested frame: from the playback set if present, otherwise fetch it exactly.
  useEffect(() => {
    const pb = playback.current
    const c = comp.current
    if (!pb || !c || loadedFor !== meta.dataset) return
    let cancelled = false
    const apply = (coords: Float32Array) => {
      c.structure.updatePosition(coords)
      c.updateRepresentations({ position: true })
      setShownFrame(frame)
    }
    const i = pb.frames.indexOf(frame)
    if (i >= 0) {
      apply(pb.coords.subarray(i * pb.nAtoms * 3, (i + 1) * pb.nAtoms * 3))
    } else if (exact.current.has(frame)) {
      apply(exact.current.get(frame)!)
    } else {
      api.frames(meta.dataset, frame, frame + 1, 1).then(
        (fr) => {
          exact.current.set(frame, fr.coords)
          if (!cancelled) apply(fr.coords)
        },
        (e) => !cancelled && setStatus(`Frame ${frame}: ${(e as Error).message}`),
      )
    }
    return () => {
      cancelled = true
    }
  }, [frame, loadedFor, meta.dataset])

  // Playback steps through the strided frames.
  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => {
      const pb = playback.current
      if (!pb) return
      const cur = getFrameIndex(pb.frames, getSelection().frame)
      setSelection({ frame: pb.frames[(cur + 1) % pb.frames.length] })
    }, 100)
    return () => clearInterval(id)
  }, [playing])

  const pb = playback.current
  const sliderIdx = pb ? getFrameIndex(pb.frames, frame) : 0
  const ready = loadedFor === meta.dataset && pb

  return (
    <div className="viewer">
      <div ref={host} className="viewer-canvas" />
      {status && (
        <div className="viewer-status">
          {status}
          {failed && <button className="small" style={{ marginLeft: 8 }} onClick={() => setAttempt((a) => a + 1)}>Retry</button>}
        </div>
      )}
      <div className="viewer-hud">
        {pair && science && (
          <div className="pair-chips">
            <span className="chip" style={{ borderColor: PAIR_COLORS.a }}>{residueLabel(pair.keyA)}</span>
            <span className="chip" style={{ borderColor: PAIR_COLORS.b }}>{residueLabel(pair.keyB)}</span>
          </div>
        )}
        {focus && focus.frame === frame && shownFrame === frame && <div className="focus-caption">{science ? focus.text : focus.plain}</div>}
        {science && (
          <div className="frame-readout" data-testid="frame-readout">
            {shownFrame === null ? '—' : <>Frame <b>{shownFrame}</b> · {frameTimeLabel(meta, shownFrame)}</>}
            {shownFrame !== null && shownFrame !== frame && <span className="muted"> (loading {frame})</span>}
          </div>
        )}
      </div>
      <div className="viewer-controls">
        <button disabled={!ready} onClick={() => setPlaying((p) => !p)} title="Play through the run" aria-label={playing ? 'Pause' : 'Play'}>
          {playing ? '❚❚' : '▶'}
        </button>
        <input
          type="range"
          min={0}
          max={pb ? pb.frames.length - 1 : 0}
          value={sliderIdx}
          disabled={!ready}
          onChange={(e) => pb && setSelection({ frame: pb.frames[Number(e.target.value)] })}
        />
        {science && <span className="muted small" title={`Playback shows every ${meta.playback_stride}th frame; jumps load the exact frame`}>{pb ? `${pb.frames.length} frames for playback` : ''}</span>}
      </div>
    </div>
  )
}

// Index of the last playback frame <= f (slider position for off-grid frames).
function getFrameIndex(frames: number[], f: number): number {
  let lo = 0
  for (let i = 0; i < frames.length; i++) if (frames[i] <= f) lo = i
  return lo
}

/**
 * Zoom so the protein fills the view. NGL's autoView fits a loose box (its longest side plus a margin to the
 * canvas height), which leaves the protein small in a wide canvas. This fits the C-alpha extent as seen in the
 * current orientation to both the width and the height, with a small margin. `spin`: the protein turns about
 * the vertical axis, so the horizontal extent covers depth too.
 */
export function fitProtein(s: NGL.Stage, c: NGL.StructureComponent, opts: { margin?: number; spin?: boolean } = {}) {
  const { margin = 0.88, spin = false } = opts
  const center = c.getCenter()
  const q = s.viewerControls.rotation
  const tanHalf = Math.tan((s.viewer.perspectiveCamera.fov * Math.PI) / 360) * margin
  const aspect = s.viewer.width / s.viewer.height
  if (!s.viewer.width || !s.viewer.height) return c.autoView()
  const v = new NGL.Vector3()
  // Camera at distance d looks down -z at the centre; an atom at view depth z (towards the camera) shows at
  // |y| / (d - z), so it fits when d >= z + |y| / tanHalf (and likewise for x over the aspect ratio).
  let d = 0
  c.structure.eachAtom((ap) => {
    v.set(ap.x - center.x, ap.y - center.y, ap.z - center.z).applyQuaternion(q)
    const across = spin ? Math.hypot(v.x, v.z) : Math.abs(v.x) // spinning about y: x and z trade places
    const toward = spin ? across : Math.abs(v.z) // either side of the centre plane: safe whichever way the camera faces
    d = Math.max(d, toward + Math.max(Math.abs(v.y), across / aspect) / tanHalf)
  }, new NGL.Selection('.CA'))
  if (!d) return c.autoView()
  s.viewerControls.center(center)
  s.viewerControls.distance(d)
}

/** Start screen: the AdK cartoon, domain-coloured, turning slowly. Structure only (no trajectory frames). */
export function SpinningStructure({ meta }: { meta: Metadata }) {
  const host = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = host.current
    if (!el) return
    let cancelled = false
    const s = new NGL.Stage(el, { backgroundColor: '#0b0e14', tooltip: false })
    const ro = new ResizeObserver(() => s.handleResize())
    ro.observe(el)
    ;(async () => {
      try {
        const pdb = await api.structure(meta.dataset)
        if (cancelled) return
        const c = (await s.loadFile(new Blob([pdb], { type: 'text/plain' }), { ext: 'pdb' })) as NGL.StructureComponent
        if (cancelled) return
        c.addRepresentation('cartoon', { color: domainScheme(meta), opacity: 0.95 })
        fitProtein(s, c, { spin: true })
        const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
        if (!reduce) {
          s.setSpin(true)
          ;(s.spinAnimation as unknown as { angle: number }).angle = 0.004
        }
      } catch {
        // Decorative only: if it fails to load, the start screen simply shows no structure.
      }
    })()
    return () => {
      cancelled = true
      ro.disconnect()
      s.dispose()
      el.replaceChildren()
    }
  }, [meta])
  return <div ref={host} className="spin-structure" aria-hidden="true" />
}
