import { fileToDataUrl } from './fileService'

export type ImportIsCurrent = () => boolean
export type BeginSourceImport = () => ImportIsCurrent

/** Keep file selection order across page unmounts and asynchronous reads. */
export function createSourceImporter(getRevision: () => number, canApply = () => true) {
  let latest = 0
  function begin(): ImportIsCurrent {
    const selection = ++latest
    const revision = getRevision()
    return () => selection === latest && revision === getRevision() && canApply()
  }

  async function read(file: File, apply: (image: string) => void, isCurrent = begin()) {
    if (!isCurrent()) return
    let image: string
    try {
      image = await fileToDataUrl(file)
    } catch (error) {
      if (isCurrent()) throw error
      return
    }
    if (isCurrent()) apply(image)
  }

  return { begin, read }
}
