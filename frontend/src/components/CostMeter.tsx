// Small $ readout for the current investigation (planning + run), from backend usage logs.
import { useInvestigation } from '../state/investigation'

export function CostMeter() {
  const { proposal, run } = useInvestigation()
  if (!proposal) return null
  const planCost = proposal.usage.reduce((s, u) => s + (u.cost_usd ?? 0), 0)
  const total = run?.cost_usd ?? planCost
  const time = run && run.wall_s ? ` · ${run.wall_s.toFixed(0)} s` : ''
  return (
    <span className="pill" title={`Estimated from token usage · ${proposal.model}`}>
      ${total.toFixed(4)} this run{time}
    </span>
  )
}
