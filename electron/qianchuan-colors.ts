export const ENTRANCE_COLORS = [
  '#8b6cff', '#16d5b3', '#4ca7ff', '#f7b955', '#ff6b89',
  '#5fd17d', '#d57cff', '#ff8a4c', '#35c4e8', '#a4d65e',
]

function generatedColor(index: number): string {
  const hue = Math.round((index * 137.508 + 24) % 360)
  return `hsl(${hue} 72% 58%)`
}

export function claimUniqueEntranceColor(usedColors: Set<string>, preferred?: string): string {
  const normalizedPreferred = preferred?.trim().toLowerCase()
  if (normalizedPreferred && !usedColors.has(normalizedPreferred)) {
    usedColors.add(normalizedPreferred)
    return preferred!.trim()
  }

  for (const color of ENTRANCE_COLORS) {
    if (!usedColors.has(color)) {
      usedColors.add(color)
      return color
    }
  }

  for (let index = 0; ; index += 1) {
    const color = generatedColor(index)
    if (!usedColors.has(color)) {
      usedColors.add(color)
      return color
    }
  }
}
