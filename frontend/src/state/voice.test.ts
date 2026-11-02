import { describe, expect, it } from 'vitest'
import { encodeWav, isTypingTarget, resample } from './voice'

describe('voice input', () => {
  it('encodes mono 16-bit PCM WAV with a correct header', () => {
    const wav = encodeWav(new Float32Array([0, 1, -1, 2]), 24000)
    const v = new DataView(wav.buffer)
    const text = (o: number) => String.fromCharCode(...wav.subarray(o, o + 4))
    expect([text(0), text(8), text(12), text(36)]).toEqual(['RIFF', 'WAVE', 'fmt ', 'data'])
    expect(v.getUint16(20, true)).toBe(1) // PCM
    expect(v.getUint16(22, true)).toBe(1) // mono
    expect(v.getUint32(24, true)).toBe(24000)
    expect(v.getUint16(34, true)).toBe(16)
    expect(v.getUint32(40, true)).toBe(8)
    expect(v.getUint32(4, true)).toBe(wav.length - 8)
    expect([0, 1, 2, 3].map((i) => v.getInt16(44 + 2 * i, true))).toEqual([0, 32767, -32768, 32767]) // clipped
  })

  it('resamples to the target rate', () => {
    expect(resample(new Float32Array(48000), 48000, 24000).length).toBe(24000)
    const up = resample(new Float32Array([0, 1]), 1, 2)
    expect(Array.from(up)).toEqual([0, 0.5, 1, 1])
    const same = new Float32Array([1, 2])
    expect(resample(same, 24000, 24000)).toBe(same)
  })

  it('leaves M alone inside text fields', () => {
    const el = (tagName: string, isContentEditable = false) => ({ tagName, isContentEditable }) as unknown as EventTarget
    expect(isTypingTarget(el('INPUT'))).toBe(true)
    expect(isTypingTarget(el('TEXTAREA'))).toBe(true)
    expect(isTypingTarget(el('DIV', true))).toBe(true)
    expect(isTypingTarget(el('BUTTON'))).toBe(false)
    expect(isTypingTarget(null)).toBe(false)
  })
})
