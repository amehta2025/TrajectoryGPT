import { describe, expect, it, vi } from 'vitest'

const health = vi.hoisted(() => ({ locked: false }))

vi.mock('../api/client', async (orig) => {
  const real = await orig<typeof import('../api/client')>()
  const figure = { figure_id: 'abc', filename: 'rmsd.png', mime: 'image/png', spec: { summary: 'RMSD versus time.' } }
  return {
    ...real,
    api: {
      ...real.api,
      figurePlan: vi.fn(async () => { throw new real.UnsupportedFigure("This figure type isn't supported yet.", figure.spec as never, figure as never) }),
      plan: vi.fn(async () => { throw new Error('planner must not be called') }),
      health: vi.fn(async () => ({ passcode_required: health.locked })),
    },
  }
})

const { ask, askFigure, clearNotices, getState, reset } = await import('./investigation')
const { api } = await import('../api/client')

describe('start-screen notices never outlive their moment', () => {
  const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'rmsd.png', { type: 'image/png' })

  it('an unsupported figure shows a notice, and New question clears it', async () => {
    await askFigure([file], 'adk_equilibrium', '')
    expect(getState().phase).toBe('idle')
    expect(getState().unsupported?.message).toContain("isn't supported yet")
    reset()
    expect(getState().unsupported).toBeNull()
  })

  it('any new action clears it too', async () => {
    await askFigure([file], 'adk_equilibrium', '')
    clearNotices()
    expect(getState().unsupported).toBeNull()
    expect(getState().error).toBeNull()
  })
})

describe('passcode first', () => {
  it('asks for the passcode before any planning call when the server needs one', async () => {
    health.locked = true
    await ask('Which LID–CORE contacts change?', 'adk_equilibrium')
    expect(getState().needPasscode).toBe(true)
    expect(getState().phase).toBe('idle')
    expect(api.plan).not.toHaveBeenCalled()
    health.locked = false
    reset()
  })
})

describe('turned-away questions', () => {
  it('keeps the residue choices, and Ask anyway re-plans with the scope check skipped', async () => {
    const declined = { verdict: 'check_residue', message: 'Residue 143 is GLU, not HSE.', examples: [],
      choices: [{ label: 'HSE134', question: 'Does HSE134 touch ASP118?' }] }
    vi.mocked(api.plan).mockResolvedValueOnce({ declined } as never)
    await ask('Does HSE143 touch ASP118?', 'adk_equilibrium')
    expect(getState().phase).toBe('idle')
    expect(getState().declined?.choices?.[0].question).toBe('Does HSE134 touch ASP118?')
    expect(getState().declined?.followUp).toBe(false)

    vi.mocked(api.plan).mockResolvedValueOnce({ declined: { verdict: 'unclear', message: 'm', examples: [] } } as never)
    await ask('contacts', 'adk_equilibrium', false, true)
    expect(vi.mocked(api.plan).mock.lastCall?.[2]).toMatchObject({ skipScope: true })
    reset()
  })
})
