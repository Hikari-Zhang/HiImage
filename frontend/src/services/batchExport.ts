export function uniqueOutputName(sourceName: string, used: Set<string>): string {
  const stem = sourceName.replace(/\.[^.]+$/, '') || 'image'
  const base = `${stem}_upscaled`
  let name = `${base}.png`
  let suffix = 2
  while (used.has(name)) {
    name = `${base}_${suffix}.png`
    suffix += 1
  }
  used.add(name)
  return name
}
