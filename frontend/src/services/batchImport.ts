export function batchFileKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}:${file.type}`
}

export function prepareBatchFiles(
  files: File[],
  existingKeys: Set<string>,
  remainingLimit: number,
): { accepted: File[]; keys: string[] } {
  const accepted: File[] = []
  const keys: string[] = []
  const seen = new Set(existingKeys)
  for (const file of files) {
    if (accepted.length >= remainingLimit || !file.type.startsWith('image/')) continue
    const key = batchFileKey(file)
    if (seen.has(key)) continue
    seen.add(key)
    accepted.push(file)
    keys.push(key)
  }
  return { accepted, keys }
}
