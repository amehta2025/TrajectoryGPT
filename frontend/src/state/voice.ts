// Push-to-talk voice input (SPEC §9d). Hold M (outside text boxes) or hold the mic button: the browser
// records raw PCM, encodes a mono 16-bit WAV at 24 kHz itself (the only kind the transcribe endpoint
// takes), and the backend returns text for the question box. Nothing is sent until the user presses Ask.
import { useSyncExternalStore } from 'react'
import { api, PasscodeRequired } from '../api/client'
import { askForPasscode } from './investigation'

export const TARGET_RATE = 24000
const ACCEPTED_RATES = [16000, 24000]
const MAX_SECONDS = 60

export type VoiceState = { status: 'idle' | 'listening' | 'transcribing' | 'error'; message: string }

let state: VoiceState = { status: 'idle', message: '' }
const listeners = new Set<() => void>()
const transcriptListeners = new Set<(text: string) => void>()

function set(next: VoiceState) {
  state = next
  listeners.forEach((l) => l())
}

export function useVoice(): VoiceState {
  return useSyncExternalStore((l) => {
    listeners.add(l)
    return () => listeners.delete(l)
  }, () => state)
}

/** The question box that is on screen subscribes; it receives the text to review before sending. */
export function onTranscript(fn: (text: string) => void): () => void {
  transcriptListeners.add(fn)
  return () => transcriptListeners.delete(fn)
}

/** Linear resampling to the endpoint's rate (only used when the browser ignores the requested rate). */
export function resample(x: Float32Array, from: number, to: number): Float32Array {
  if (from === to) return x
  const n = Math.max(1, Math.round((x.length * to) / from))
  const out = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    const t = (i * from) / to
    const j = Math.floor(t)
    const f = t - j
    out[i] = (x[j] ?? 0) * (1 - f) + (x[j + 1] ?? x[j] ?? 0) * f
  }
  return out
}

/** RIFF/WAVE, mono, 16-bit little-endian PCM; samples are clipped to [-1, 1]. */
export function encodeWav(samples: Float32Array, rate: number): Uint8Array {
  const buf = new ArrayBuffer(44 + samples.length * 2)
  const v = new DataView(buf)
  const ascii = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)))
  ascii(0, 'RIFF')
  v.setUint32(4, 36 + samples.length * 2, true)
  ascii(8, 'WAVE')
  ascii(12, 'fmt ')
  v.setUint32(16, 16, true) // fmt chunk size
  v.setUint16(20, 1, true) // PCM
  v.setUint16(22, 1, true) // mono
  v.setUint32(24, rate, true)
  v.setUint32(28, rate * 2, true) // byte rate
  v.setUint16(32, 2, true) // block align
  v.setUint16(34, 16, true) // bits per sample
  ascii(36, 'data')
  v.setUint32(40, samples.length * 2, true)
  samples.forEach((s, i) => {
    const c = Math.max(-1, Math.min(1, s))
    v.setInt16(44 + i * 2, c < 0 ? c * 0x8000 : c * 0x7fff, true)
  })
  return new Uint8Array(buf)
}

function toBase64(bytes: Uint8Array): string {
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(bin)
}

/** True when a key press belongs to a text field (M must type an "m" there, not start recording). */
export function isTypingTarget(el: EventTarget | null): boolean {
  const t = el as { tagName?: string; isContentEditable?: boolean } | null
  if (!t || typeof t !== 'object') return false
  return !!t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName ?? '')
}

type Recording = { stream: MediaStream; ctx: AudioContext; node: ScriptProcessorNode; chunks: Float32Array[]; stopAt: number }
let rec: Recording | null = null
let starting = false
let releasedWhileStarting = false

export async function startListening() {
  if (rec || starting || state.status === 'transcribing') return
  starting = true
  releasedWhileStarting = false
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
    let ctx: AudioContext
    try {
      ctx = new AudioContext({ sampleRate: TARGET_RATE })
    } catch {
      ctx = new AudioContext() // some browsers refuse a chosen rate; we resample on stop
    }
    const source = ctx.createMediaStreamSource(stream)
    // ScriptProcessor is deprecated but needs no separate worklet file; a few seconds of speech is fine.
    const node = ctx.createScriptProcessor(4096, 1, 1)
    const chunks: Float32Array[] = []
    node.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)))
    source.connect(node)
    node.connect(ctx.destination) // the output buffer stays silent; the connection keeps the node running
    rec = { stream, ctx, node, chunks, stopAt: Date.now() + MAX_SECONDS * 1000 }
    set({ status: 'listening', message: '' })
    window.setTimeout(() => { if (rec && Date.now() >= rec.stopAt) void stopListening() }, MAX_SECONDS * 1000)
  } catch (e) {
    const denied = (e as DOMException)?.name === 'NotAllowedError'
    set({ status: 'error', message: denied ? 'Microphone access was blocked. Allow it in the browser, or type the question.' : `Could not start the microphone: ${(e as Error).message}` })
  } finally {
    starting = false
  }
  if (releasedWhileStarting) void stopListening() // the key was released before the microphone opened
}

export async function stopListening() {
  if (starting) {
    releasedWhileStarting = true
    return
  }
  const r = rec
  if (!r) return
  rec = null
  r.node.disconnect()
  r.stream.getTracks().forEach((t) => t.stop())
  const rate = r.ctx.sampleRate
  void r.ctx.close()
  const total = r.chunks.reduce((n, c) => n + c.length, 0)
  const pcm = new Float32Array(total)
  let o = 0
  for (const c of r.chunks) {
    pcm.set(c, o)
    o += c.length
  }
  const outRate = ACCEPTED_RATES.includes(rate) ? rate : TARGET_RATE
  const samples = resample(pcm, rate, outRate)
  if (samples.length < outRate * 0.3) {
    set({ status: 'error', message: 'Nothing recorded: hold the key (or the mic button) while you speak.' })
    return
  }
  set({ status: 'transcribing', message: '' })
  try {
    const { text } = await api.transcribe(toBase64(encodeWav(samples, outRate)))
    if (!text) {
      set({ status: 'error', message: 'No speech was recognised. Try again, a little closer to the microphone.' })
      return
    }
    transcriptListeners.forEach((fn) => fn(text))
    set({ status: 'idle', message: '' })
  } catch (e) {
    if (e instanceof PasscodeRequired) {
      askForPasscode()
      set({ status: 'error', message: 'Enter the passcode first, then try again.' })
    } else set({ status: 'error', message: `Transcription failed: ${(e as Error).message}` })
  }
}

export function dismissVoiceMessage() {
  if (state.status === 'error') set({ status: 'idle', message: '' })
}
