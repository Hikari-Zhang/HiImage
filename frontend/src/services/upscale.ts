export function resolveOutscale(override: number | null | undefined, defaultOutscale: number): number {
  return override ?? defaultOutscale
}
