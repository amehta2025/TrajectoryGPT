// Which model runs the investigation: Muse Spark on the Meta API (default) or the open-weight Muse Spark
// Open on Baseten. Shown only when the server has both; the second check is always Muse Wonder.
import { useEffect, useState } from 'react'
import { api, type Provider } from '../api/client'
import { getProvider, setProvider } from '../state/investigation'

const OPTIONS: { id: Provider; label: string; title: string }[] = [
  { id: 'meta', label: 'Muse Spark', title: 'Muse Spark 1.3 on the Meta Model API' },
  { id: 'baseten', label: 'Muse Spark Open', title: 'Open-weight Muse Spark Open, hosted on Baseten' },
]

export function ModelToggle({ disabled }: { disabled?: boolean }) {
  const [available, setAvailable] = useState<Provider[]>([])
  const [chosen, setChosen] = useState<Provider>(getProvider() ?? 'meta')
  useEffect(() => {
    api.health()
      .then((h) => {
        if (h.mode !== 'live') return
        setAvailable(h.providers ?? [])
        if (!getProvider() && h.default_provider) setChosen(h.default_provider)
      })
      .catch(() => {})
  }, [])
  if (!available.includes('baseten') || !available.includes('meta')) return null
  const pick = (p: Provider) => {
    setProvider(p)
    setChosen(p)
  }
  return (
    <>
      <span className="muted small">Model</span>
      <span className="model-toggle" role="group" aria-label="Model">
        {OPTIONS.map((o) => (
          <button key={o.id} type="button" aria-pressed={chosen === o.id} title={o.title} disabled={disabled} onClick={() => pick(o.id)}>
            {o.label}
          </button>
        ))}
      </span>
      {chosen === 'baseten' && <span className="muted small">open weights</span>}
    </>
  )
}
