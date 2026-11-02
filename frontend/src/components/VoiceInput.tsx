// Push-to-talk controls: the hold-M key handler and status toast (mounted once), the mic button next to
// each question box, and the hint near the viewer. All hidden when the server has no voice input.
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { dismissVoiceMessage, isTypingTarget, startListening, stopListening, useVoice } from '../state/voice'

export function useVoiceAvailable(): boolean {
  const [ok, setOk] = useState(false)
  useEffect(() => {
    api.health().then((h) => setOk(!!h.voice && !!navigator.mediaDevices?.getUserMedia), () => setOk(false))
  }, [])
  return ok
}

/** Hold M anywhere except a text field; release to stop. Also stops if the window loses focus. */
export function VoiceLayer() {
  const available = useVoiceAvailable()
  const v = useVoice()
  useEffect(() => {
    if (!available) return
    const isM = (e: KeyboardEvent) => (e.key === 'm' || e.key === 'M') && !e.ctrlKey && !e.metaKey && !e.altKey
    const down = (e: KeyboardEvent) => {
      if (!isM(e) || e.repeat || isTypingTarget(e.target)) return
      e.preventDefault()
      void startListening()
    }
    const up = (e: KeyboardEvent) => { if (isM(e)) void stopListening() }
    const blur = () => void stopListening()
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', blur)
    }
  }, [available])
  if (!available || v.status === 'idle') return null
  return (
    <div className={`voice-toast ${v.status}`} role="status" aria-live="polite">
      {v.status === 'listening' && <><span className="rec-dot" aria-hidden /> Listening… release to stop</>}
      {v.status === 'transcribing' && <>Turning your words into text…</>}
      {v.status === 'error' && <>{v.message} <button className="link" onClick={dismissVoiceMessage} aria-label="Dismiss">×</button></>}
    </div>
  )
}

/** Hold to speak; the text lands in the question box for review. */
export function MicButton() {
  const available = useVoiceAvailable()
  const v = useVoice()
  if (!available) return null
  return (
    <button type="button" className={`mic ${v.status === 'listening' ? 'on' : ''}`} title="Hold to speak (or hold M)"
      aria-label="Hold to speak" disabled={v.status === 'transcribing'}
      onPointerDown={(e) => { e.preventDefault(); void startListening() }}
      onPointerUp={() => void stopListening()} onPointerLeave={() => void stopListening()}>
      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden><path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z" /></svg>
    </button>
  )
}

export function VoiceHint() {
  return useVoiceAvailable() ? <span className="voice-hint muted small">Hold <kbd>M</kbd> to ask by voice</span> : null
}
