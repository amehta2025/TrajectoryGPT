// NGL 3D viewer (SPEC §10a). Loads the reference PDB, then a strided set of playback
// frames; any other frame (jump-to-frame) is fetched exactly. Highlights the selected pair.
// A failed load retries once by itself, then offers a Retry button.
import { useEffect, useRef, useState } from 'react'
import * as NGL from 'ngl'
import { api, type Metadata } from '../api/client'
import { getSelection, setSelection, useSelection } from '../state/selection'
import { residueKeyToNglSelection, residueLabel } from '../state/residueKey'
import { frameTimeLabel } from '../state/time'
import { DOMAIN_COLORS, PAIR_COLORS } from '../theme'

type Playback = { frames: number[]; nAtoms: number; coords: Float32Array }
type Rep = { setSelection: (s: string) => void; setVisibility: (v: boolean) => void }

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
  const { pair, frame, focus } = useSelection()
  const host = useRef<HTMLDivElement>(null)
  const stage = useRef<NGL.Stage | null>(null)
  const comp = useRef<NGL.StructureComponent | null>(null)
  const reps = useRef<{ a: Rep; b: Rep; label: Rep } | null>(null)
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
    const ro = new ResizeObserver(() => s.handleResize())
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
          sele: NONE, labelType: 'format', labelFormat: '%(resname)s%(resno)s',
          color: '#ffffff', xOffset: 0.6, fixedSize: true, radiusType: 'size', radiusSize: 1.6,
          showBorder: true, borderColor: '#000000', borderWidth: 0.4,
        })
        comp.current = c
        reps.current = { a, b, label }
        c.autoView()

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
    r.label.setSelection(`(${residueKeyToNglSelection(pair.keyA)}) or (${residueKeyToNglSelection(pair.keyB)})`)
  }, [pair, loadedFor, meta.dataset])

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
        {pair && (
          <div className="pair-chips">
            <span className="chip" style={{ borderColor: PAIR_COLORS.a }}>{residueLabel(pair.keyA)}</span>
            <span className="chip" style={{ borderColor: PAIR_COLORS.b }}>{residueLabel(pair.keyB)}</span>
          </div>
        )}
        {focus && focus.frame === frame && shownFrame === frame && <div className="focus-caption">{focus.text}</div>}
        <div className="frame-readout" data-testid="frame-readout">
          {shownFrame === null ? '—' : <>Frame <b>{shownFrame}</b> · {frameTimeLabel(meta, shownFrame)}</>}
          {shownFrame !== null && shownFrame !== frame && <span className="muted"> (loading {frame})</span>}
        </div>
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
        <span className="muted small" title={`Playback shows every ${meta.playback_stride}th frame; jumps load the exact frame`}>{pb ? `${pb.frames.length} frames for playback` : ''}</span>
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
