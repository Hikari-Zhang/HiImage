import { exportMask, exportCombinedMask } from '../services/maskExport'

/** Exports own their workers and finish independently of the page lifecycle. */
export function useMaskExport() {
  return { exportMask, exportCombinedMask }
}
