export type RandomUuid = (() => string) | null

let fallbackSequence = 0

export function createTaskId(
  randomUuid: RandomUuid | undefined = globalThis.crypto?.randomUUID?.bind(globalThis.crypto),
): string {
  if (randomUuid) return randomUuid()
  fallbackSequence += 1
  return `task-${Date.now().toString(36)}-${fallbackSequence.toString(36)}-${Math.random().toString(36).slice(2)}`
}
