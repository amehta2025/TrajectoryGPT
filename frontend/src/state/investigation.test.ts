import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', async (orig) => {
  const real = await orig<typeof import('../api/client')>()
  const figure = { figure_id: 'abc', filename: 'rmsd.png', mime: 'image/png', spec: { summary: 'RMSD versus time.' } }
  return {
    ...real,
    api: { ...real.api, figurePlan: vi.fn(async () => { throw new real.UnsupportedFigure("This figure type isn't supported yet.", figure.spec as never, figure as never) }) },
  }
})

const { askFigure, clearNotices, getState, reset } = await import('./investigation')

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
