import { describe, expect, it } from 'vitest'
import { ENTRANCE_COLORS, claimUniqueEntranceColor } from '../electron/qianchuan-colors'

describe('claimUniqueEntranceColor', () => {
  it('keeps a surviving entrance color and reuses a genuinely free color for a new page', () => {
    const used = new Set<string>()

    expect(claimUniqueEntranceColor(used, ENTRANCE_COLORS[1])).toBe(ENTRANCE_COLORS[1])
    expect(claimUniqueEntranceColor(used)).toBe(ENTRANCE_COLORS[0])
  })

  it('repairs duplicate saved colors and remains unique beyond the fixed palette', () => {
    const used = new Set<string>()
    const colors = Array.from({ length: 50 }, (_, index) => (
      claimUniqueEntranceColor(used, index < 2 ? ENTRANCE_COLORS[0] : undefined)
    ))

    expect(new Set(colors).size).toBe(50)
    expect(colors[0]).toBe(ENTRANCE_COLORS[0])
    expect(colors[1]).not.toBe(ENTRANCE_COLORS[0])
  })
})
